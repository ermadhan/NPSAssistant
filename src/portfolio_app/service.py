from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from portfolio_app import db
from portfolio_app.export import holding_records, transaction_records, write_table
from portfolio_app.nav import FundNav, fetch_latest, match_scheme
from portfolio_app.parsers.nps import parse_nps_file


class ImportRejected(Exception):
    pass


def parse_statement(connection, path: Path):
    aliases = db.narration_alias_map(connection)
    result = parse_nps_file(path, narration_aliases=aliases)
    saved = db.profile_mapping(connection, result.fingerprint)
    if saved and result.needs_header_map:
        result = parse_nps_file(
            path,
            extra_aliases=json.loads(saved),
            narration_aliases=aliases,
        )
    return result


def save_statement(connection, path: Path, result, *, confirm_year: bool, narration_map: dict[str, str]):
    if result.needs_header_map:
        raise ImportRejected("Map the unrecognised headers before saving.")
    if result.unknown_narrations and set(_norm(item) for item in result.unknown_narrations) - set(narration_map):
        raise ImportRejected("Name each new narration before saving.")
    if result.year_mismatch and not confirm_year:
        raise ImportRejected("Confirm the financial year before saving.")
    if result.warnings:
        raise ImportRejected(" ".join(result.warnings))
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if db.file_hash_exists(connection, file_hash):
        raise ImportRejected("This file has already been imported.")
    for phrase, kind in narration_map.items():
        db.save_narration_alias(connection, phrase, kind)
        for row in result.transactions:
            if _norm(row.description) == phrase and row.narration_kind == "other":
                row.narration_kind = kind
    return db.save_import(connection, result, path.name, file_hash)


def save_header_profile(connection, fingerprint: str, extra_aliases: dict) -> None:
    db.save_profile(connection, fingerprint, json.dumps(extra_aliases))


def refresh_navs(connection, funds: list[FundNav] | None = None) -> dict:
    catalog = funds if funds is not None else fetch_latest()
    fetched_at = datetime.now()
    for fund in catalog:
        db.save_nav_quote(connection, fund.scheme_code, fund.scheme_name, fund.nav, fund.nav_date, fetched_at)
    db.apply_saved_links(connection)
    names = {
        row["scheme_name"]
        for row in connection.execute("SELECT DISTINCT scheme_name FROM holdings")
    }
    ambiguous = []
    unmatched = []
    linked = []
    for name in sorted(names):
        match = match_scheme(name, catalog)
        if match.unique is not None:
            db.set_scheme_code(connection, name, match.unique.scheme_code)
            linked.append(name)
        elif match.matches:
            ambiguous.append({"statement_name": name, "codes": [item.scheme_code for item in match.matches]})
        else:
            unmatched.append(name)
    connection.commit()
    return {"linked": linked, "ambiguous": ambiguous, "unmatched": unmatched, "catalog": catalog}


def export_holdings(
    connection,
    path: Path,
    import_id: int | None = None,
    member_id: str | None = None,
) -> None:
    rows = db.holdings_for_import(connection, import_id) if import_id else db.current_holding_rows(connection)
    if member_id is not None:
        rows = [row for row in rows if row["member_id"] == member_id]
    write_table(path, holding_records(rows))


def export_transactions(
    connection,
    path: Path,
    import_id: int | None = None,
    member_id: str | None = None,
) -> None:
    if import_id:
        rows = db.transactions_for_import(connection, import_id)
        year = connection.execute(
            "SELECT financial_year FROM imports WHERE id = ?", (import_id,)
        ).fetchone()
        records = transaction_records(rows)
        for record in records:
            record["financial_year"] = "" if year is None else year["financial_year"]
        write_table(path, records)
        return
    rows = db.all_current_transactions(connection)
    if member_id is not None:
        rows = [row for row in rows if row["member_id"] == member_id]
    write_table(path, transaction_records(rows))


def _norm(value: str) -> str:
    return " ".join(value.lower().split())
