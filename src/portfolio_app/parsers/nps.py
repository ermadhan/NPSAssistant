from __future__ import annotations

import csv
import hashlib
import json
import re
from io import StringIO
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from portfolio_app.models import (
    ContributionRow,
    HeaderMappingRequest,
    HoldingRow,
    ParseResult,
    SchemeTransactionRow,
)

_PROFILE_PATH = Path(__file__).with_name("nps_profile.json")
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def load_builtin_profile() -> dict:
    return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))


def parse_nps_file(
    path: Path,
    *,
    extra_aliases: Optional[dict] = None,
    narration_aliases: Optional[dict[str, str]] = None,
) -> ParseResult:
    text = path.read_text(encoding="utf-8-sig")
    return parse_nps_text(
        text,
        path.name,
        extra_aliases=extra_aliases,
        narration_aliases=narration_aliases,
    )


def parse_nps_text(
    text: str,
    filename: str,
    *,
    extra_aliases: Optional[dict] = None,
    narration_aliases: Optional[dict[str, str]] = None,
) -> ParseResult:
    profile = load_builtin_profile()
    aliases = _merge_aliases(profile["sections"], extra_aliases or {})
    narrations = dict(profile["narrations"])
    lines = [line.rstrip("\r\n") for line in text.splitlines()]
    sections = _split_sections(lines)
    tier = _tier_from_title(sections["preamble"][0] if sections["preamble"] else "")
    subscriber = _subscriber(sections["preamble"])

    summary_headers, summary_values = _header_and_values(sections["summary"])
    scheme_headers, scheme_rows = _table(sections["schemes"])
    contrib_headers, contrib_rows = _table(sections["contributions"])
    txn_blocks = _transaction_blocks(sections["transactions"])
    txn_headers = txn_blocks[0][1] if txn_blocks else []

    section_headers = {
        "summary": [_norm(cell) for cell in summary_headers if _norm(cell)],
        "schemes": [_norm(cell) for cell in scheme_headers if _norm(cell)],
        "contributions": [_norm(cell) for cell in contrib_headers if _norm(cell)],
        "transactions": [_norm(cell) for cell in txn_headers if _norm(cell)],
    }
    fingerprint = hashlib.sha256(
        json.dumps(section_headers, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    summary_map = _map_headers(summary_headers, aliases["summary"]["aliases"])
    scheme_map = _map_headers(scheme_headers, aliases["schemes"]["aliases"])
    contrib_map = _map_headers(contrib_headers, aliases["contributions"]["aliases"])
    txn_map = _map_headers(txn_headers, aliases["transactions"]["aliases"])
    legacy_holdings, legacy_nav_date = _legacy_scheme_holdings(scheme_headers, scheme_rows)
    legacy_transactions = _legacy_transactions(
        sections["transactions"],
        narrations,
        narration_aliases or {},
    )
    missing = {
        "summary": _missing(aliases["summary"]["required"], summary_map),
        "schemes": _missing(aliases["schemes"]["required"], scheme_map),
        "contributions": _missing(aliases["contributions"]["required"], contrib_map),
        "transactions": _missing(aliases["transactions"]["required"], txn_map),
    }
    if legacy_holdings is not None:
        missing.pop("schemes")
    if legacy_transactions is not None:
        missing.pop("transactions")
    missing = {key: value for key, value in missing.items() if value}
    header_mapping = None
    if missing:
        header_mapping = HeaderMappingRequest(fingerprint, section_headers, missing)

    as_of = _as_of_date(summary_headers, summary_map)
    nav_date = legacy_nav_date or _nav_date(scheme_headers, scheme_map)
    xirr = _xirr(summary_headers)
    holdings = (
        legacy_holdings
        if legacy_holdings is not None
        else _holdings(scheme_rows, scheme_map, tier) if not missing.get("schemes") else []
    )
    contributions = (
        _contributions(contrib_rows, contrib_map) if not missing.get("contributions") else []
    )
    if legacy_transactions is not None:
        transactions, unknown = legacy_transactions
    else:
        transactions, unknown = _transactions(
            txn_blocks,
            txn_map,
            narrations,
            narration_aliases or {},
        )
    txn_dates = [row.txn_date for row in transactions]
    period_start = min(txn_dates) if txn_dates else as_of
    period_end = max(txn_dates) if txn_dates else as_of
    financial_year = financial_year_for(as_of)
    filename_year = filename_financial_year(filename)
    warnings: list[str] = []
    if header_mapping is None:
        _check_tier(holdings, transactions, tier, warnings)
        _check_year(txn_dates, financial_year, warnings)

    return ParseResult(
        tier=tier,
        financial_year=financial_year,
        filename_year=filename_year,
        year_mismatch=bool(filename_year and filename_year != financial_year),
        pran=subscriber["pran"],
        subscriber_name=subscriber["name"],
        scheme_choice=subscriber["scheme_choice"],
        generation_date=subscriber["generated"],
        as_of_date=as_of,
        nav_date=nav_date,
        period_start=period_start,
        period_end=period_end,
        holdings_value=_money(_cell(summary_values, summary_map.get("holdings_value"))) or 0.0,
        contribution_count=int(_number(_cell(summary_values, summary_map.get("contribution_count"))) or 0),
        total_contribution=_money(_cell(summary_values, summary_map.get("total_contribution"))) or 0.0,
        total_withdrawal=_money(_cell(summary_values, summary_map.get("total_withdrawal"))) or 0.0,
        notional_gain=_money(_cell(summary_values, summary_map.get("notional_gain"))) or 0.0,
        charges=_money(_cell(summary_values, summary_map.get("charges"))) or 0.0,
        xirr=xirr,
        holdings=holdings,
        contributions=contributions,
        transactions=transactions,
        fingerprint=fingerprint,
        unknown_narrations=unknown,
        header_mapping=header_mapping,
        warnings=warnings,
    )


def financial_year_for(day: date) -> str:
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def filename_financial_year(filename: str) -> Optional[str]:
    match = re.search(r"(20\d{2})[_-](\d{2})(?:\.[^.]+)?$", filename)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    if end != (start + 1) % 100:
        return None
    return f"{start}-{end:02d}"


def classify_narration(description: str, narrations: dict, extra: dict[str, str]) -> str:
    text = _norm(description)
    if text in extra:
        return extra[text]
    ordered = (
        "switch_out",
        "switch_in",
        "opening",
        "closing",
        "billing",
        "tier2",
        "voluntary",
        "employer",
    )
    for kind in ordered:
        for phrase in narrations.get(kind, []):
            if phrase in text:
                return kind
    return "other"


def _merge_aliases(builtin: dict, extra: dict) -> dict:
    merged = json.loads(json.dumps(builtin))
    for section, fields in extra.items():
        slot = merged.setdefault(section, {"required": [], "aliases": {}})
        for field_name, phrases in fields.items():
            current = slot["aliases"].setdefault(field_name, [])
            for phrase in phrases:
                if phrase not in current:
                    current.append(phrase)
    return merged


def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    markers = (
        ("summary", "investment summary"),
        ("schemes", "investment details"),
        ("contributions", "contribution/redemption"),
        ("transactions", "transaction details"),
    )
    found: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        lowered = _compact(line)
        for key, marker in markers:
            if lowered.startswith(_compact(marker)) and not any(name == key for name, _ in found):
                found.append((key, index))
    sections = {"preamble": lines[: found[0][1] if found else len(lines)]}
    for position, (key, start) in enumerate(found):
        end = found[position + 1][1] if position + 1 < len(found) else len(lines)
        sections[key] = lines[start + 1 : end]
    for key, _ in markers:
        sections.setdefault(key, [])
    return sections


def _tier_from_title(line: str) -> str:
    lowered = _norm(line)
    if "tier ii" in lowered or "tier 2" in lowered:
        return "nps_tier2"
    if "tier i" in lowered or "tier 1" in lowered:
        return "nps_tier1"
    raise ValueError(f"Statement title does not name a tier: {line!r}")


def _subscriber(lines: list[str]) -> dict:
    pran = ""
    name = ""
    generated = None
    choice = ""
    for line in lines:
        lowered = _norm(line)
        if lowered.startswith("pran"):
            pran = line.split(",", 1)[1].strip().lstrip("'")
        elif lowered.startswith("subscriber name"):
            name = line.split(",", 1)[1].strip()
        elif "statement generation date" in lowered:
            generated = _generation_date(line)
        elif lowered.startswith("scheme choice"):
            choice = line.split("-", 1)[-1].strip()
    return {"pran": pran, "name": name, "generated": generated, "scheme_choice": choice}


def _header_and_values(lines: list[str]) -> tuple[list[str], list[str]]:
    rows = [_split_csv(line) for line in lines if line.strip()]
    header: list[str] = []
    values: list[str] = []
    for row in rows:
        joined = _norm(" ".join(row))
        if "value of your holdings" in joined:
            header = row
        elif header and _money(row[0] if row else "") is not None:
            values = row
            break
    return header, values


def _table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    rows = [_split_csv(line) for line in lines if line.strip()]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _transaction_blocks(lines: list[str]) -> list[tuple[str, list[str], list[list[str]]]]:
    blocks = []
    scheme = ""
    headers: list[str] = []
    body: list[list[str]] = []
    for line in lines:
        if not line.strip():
            continue
        if _is_scheme_line(line) and "," not in line:
            if scheme and headers:
                blocks.append((scheme, headers, body))
            scheme = line.strip()
            headers = []
            body = []
            continue
        cells = _split_csv(line)
        if scheme and not headers and _norm(cells[0] if cells else "") == "date":
            headers = cells
            continue
        if scheme and headers:
            body.append(cells)
    if scheme and headers:
        blocks.append((scheme, headers, body))
    return blocks


def _is_scheme_line(line: str) -> bool:
    lowered = _norm(line)
    return "scheme" in lowered and "tier" in lowered


def _holdings(rows: list[list[str]], mapping: dict[str, int], tier: str) -> list[HoldingRow]:
    holdings = []
    for row in rows:
        name = _cell(row, mapping.get("particulars")).strip()
        if not name or not _is_scheme_line(name):
            continue
        holdings.append(
            HoldingRow(
                scheme_name=name,
                asset_class=_asset_class(name),
                units=_number(_cell(row, mapping.get("units"))) or 0.0,
                statement_nav=_number(_cell(row, mapping.get("nav"))) or 0.0,
                market_value=_money(_cell(row, mapping.get("scheme_value"))) or 0.0,
            )
        )
        _ = tier
    return holdings


def _classify_transaction(
    description: str,
    amount: Optional[float],
    narrations: dict,
    extra: dict[str, str],
) -> str:
    kind = classify_narration(description, narrations, extra)
    if (
        kind == "other"
        and amount is not None
        and amount != 0
        and "rebalancing" in _norm(description)
    ):
        return "switch_in" if amount > 0 else "switch_out"
    return kind


def _legacy_scheme_holdings(
    headers: list[str],
    rows: list[list[str]],
) -> tuple[Optional[list[HoldingRow]], Optional[date]]:
    if len(headers) < 3 or _compact(headers[0]) != "particulars" or _compact(headers[1]) != "references":
        return None, None
    scheme_columns = [
        (index, name.strip())
        for index, name in enumerate(headers[2:], 2)
        if _is_scheme_line(name)
    ]
    if not scheme_columns:
        return None, None

    metrics = {}
    nav_date = None
    for row in rows:
        label = _compact(_cell(row, 0))
        if label.startswith("schemewisevalueofyourholdings"):
            metrics["market_value"] = row
        elif label.startswith("schemewisecontributioninyouraccount"):
            metrics["net_contribution"] = row
        elif label == "totalunits":
            metrics["units"] = row
        elif label.startswith("navason"):
            metrics["nav"] = row
            nav_date = _find_date(_cell(row, 0))
    if not {"market_value", "units", "nav"}.issubset(metrics):
        return None, None

    holdings = []
    for index, scheme_name in scheme_columns:
        market_value = _money(_cell(metrics["market_value"], index))
        units = _number(_cell(metrics["units"], index))
        nav = _number(_cell(metrics["nav"], index))
        if market_value is None or units is None or nav is None:
            raise ValueError(
                f"Legacy holdings summary is missing a numeric value for {scheme_name!r}."
            )
        holdings.append(
            HoldingRow(
                scheme_name=scheme_name,
                asset_class=_asset_class(scheme_name),
                units=units,
                statement_nav=nav,
                market_value=market_value,
                net_contribution=(
                    _money(_cell(metrics["net_contribution"], index))
                    if "net_contribution" in metrics
                    else None
                ),
            )
        )
    return holdings, nav_date


def _legacy_transactions(
    lines: list[str],
    narrations: dict,
    extra: dict[str, str],
) -> Optional[tuple[list[SchemeTransactionRow], list[str]]]:
    rows = [_split_csv(line) for line in lines if line.strip()]
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if len(row) > 3
            and _compact(_cell(row, 0)) == "date"
            and _compact(_cell(row, 1)) in {"particulars", "description"}
            and any(_is_scheme_line(cell) for cell in row[3:])
        ),
        None,
    )
    if header_index is None:
        return None

    header = rows[header_index]
    scheme_groups: dict[str, dict[str, int]] = {}
    for index, column in enumerate(header):
        match = re.match(r"(.+?)\s+(amount|nav|units)(?:\s*\(.*\))?\s*$", column, re.I)
        if not match or not _is_scheme_line(match.group(1)):
            continue
        scheme_groups.setdefault(match.group(1).strip(), {})[match.group(2).lower()] = index
    if not scheme_groups or any(not {"amount", "nav", "units"}.issubset(group) for group in scheme_groups.values()):
        return None

    transactions = []
    unknown = []
    for row in rows[header_index + 1 :]:
        raw_date = _cell(row, 0).strip()
        description = _cell(row, 1).strip()
        if not raw_date or not description or _is_note(raw_date):
            continue
        txn_date = _parse_date(raw_date)
        for scheme_name, columns in scheme_groups.items():
            amount = _money(_cell(row, columns["amount"]))
            nav = _number(_cell(row, columns["nav"]))
            units = _number(_cell(row, columns["units"]))
            if amount is None and nav is None and units is None:
                continue
            kind = _classify_transaction(description, amount, narrations, extra)
            if kind == "other" and description not in unknown:
                unknown.append(description)
            transactions.append(
                SchemeTransactionRow(
                    txn_date=txn_date,
                    scheme_name=scheme_name,
                    description=description,
                    narration_kind=kind,
                    amount=amount,
                    nav=nav,
                    units=units,
                )
            )
    return transactions, unknown


def _contributions(rows: list[list[str]], mapping: dict[str, int]) -> list[ContributionRow]:
    parsed = []
    for row in rows:
        raw_date = _cell(row, mapping.get("date")).strip()
        if not raw_date or _is_note(raw_date):
            continue
        parsed.append(
            ContributionRow(
                txn_date=_parse_date(raw_date),
                particulars=_cell(row, mapping.get("particulars")).strip(),
                uploaded_by=_cell(row, mapping.get("uploaded_by")).strip(),
                employee_amount=_money(_cell(row, mapping.get("employee"))) or 0.0,
                employer_amount=_money(_cell(row, mapping.get("employer"))) or 0.0,
                total=_money(_cell(row, mapping.get("total"))) or 0.0,
            )
        )
    return parsed


def _transactions(
    blocks: list[tuple[str, list[str], list[list[str]]]],
    mapping: dict[str, int],
    narrations: dict,
    extra: dict[str, str],
) -> tuple[list[SchemeTransactionRow], list[str]]:
    rows: list[SchemeTransactionRow] = []
    unknown: list[str] = []
    for scheme, _headers, body in blocks:
        for cells in body:
            raw_date = _cell(cells, mapping.get("date")).strip()
            description = _cell(cells, mapping.get("description")).strip()
            if not raw_date or not description:
                continue
            amount = _money(_cell(cells, mapping.get("amount")))
            kind = _classify_transaction(description, amount, narrations, extra)
            if kind == "other" and description not in unknown:
                unknown.append(description)
            rows.append(
                SchemeTransactionRow(
                    txn_date=_parse_date(raw_date),
                    scheme_name=scheme,
                    description=description,
                    narration_kind=kind,
                    amount=amount,
                    nav=_number(_cell(cells, mapping.get("nav"))),
                    units=_number(_cell(cells, mapping.get("units"))),
                )
            )
    return rows, unknown


def _map_headers(cells: list[str], aliases: dict[str, list[str]]) -> dict[str, int]:
    pairs: list[tuple[int, str, str]] = []
    for field_name, phrases in aliases.items():
        for phrase in phrases:
            pairs.append((len(phrase), phrase, field_name))
    pairs.sort(reverse=True)
    mapping: dict[str, int] = {}
    used_columns: set[int] = set()
    for _length, phrase, field_name in pairs:
        if field_name in mapping:
            continue
        for index, cell in enumerate(cells):
            if index in used_columns:
                continue
            if phrase in _norm(cell):
                mapping[field_name] = index
                used_columns.add(index)
                break
    return mapping


def _missing(required: list[str], mapping: dict[str, int]) -> list[str]:
    return [field_name for field_name in required if field_name not in mapping]


def _as_of_date(headers: list[str], mapping: dict[str, int]) -> date:
    text = _cell(headers, mapping.get("holdings_value")) or " ".join(headers)
    found = _find_date(text)
    if found is None:
        raise ValueError(f"Holdings header has no as-of date: {text!r}")
    return found


def _nav_date(headers: list[str], mapping: dict[str, int]) -> Optional[date]:
    text = _cell(headers, mapping.get("nav")) or " ".join(headers)
    return _find_date(text)


def _xirr(headers: list[str]) -> Optional[float]:
    for cell in headers:
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", cell or "")
        if match:
            return float(match.group(1))
    return None


def _find_date(text: str) -> Optional[date]:
    collapsed = _norm(text)
    match = re.search(r"(\d{1,2})-([a-z]{3})-(\d{4})", collapsed)
    if match:
        return date(int(match.group(3)), _MONTHS[match.group(2)], int(match.group(1)))
    match = re.search(r"([a-z]+)\s+(\d{1,2})\s+(\d{4})", collapsed)
    if match and match.group(1) in _MONTHS:
        return date(int(match.group(3)), _MONTHS[match.group(1)], int(match.group(2)))
    return None


def _generation_date(line: str) -> Optional[datetime]:
    collapsed = re.sub(r"\s+", " ", line)
    match = re.search(
        r"([A-Za-z]+ \d{1,2} \d{4})(?:\s+(\d{1,2}:\d{2}\s*[AP]M))?",
        collapsed,
        re.I,
    )
    if not match:
        return None
    day = _find_date(match.group(1))
    if day is None:
        return None
    if match.group(2):
        clock = datetime.strptime(match.group(2).upper().replace(" ", ""), "%I:%M%p")
        return datetime(day.year, day.month, day.day, clock.hour, clock.minute)
    return datetime(day.year, day.month, day.day)


def _parse_date(value: str) -> date:
    found = _find_date(value)
    if found is None:
        raise ValueError(f"Unrecognised date: {value!r}")
    return found


def _asset_class(name: str) -> str:
    match = re.search(r"scheme\s+([ecga])\b", _norm(name))
    if not match:
        return ""
    return match.group(1).upper()


def _named_tier(name: str) -> str:
    lowered = _norm(name)
    if "tier ii" in lowered or "tier 2" in lowered:
        return "nps_tier2"
    if re.search(r"tier i\b", lowered) or "tier 1" in lowered:
        return "nps_tier1"
    return ""


def _check_tier(holdings, transactions, tier: str, warnings: list[str]) -> None:
    for holding in holdings:
        named = _named_tier(holding.scheme_name)
        if named and named != tier:
            warnings.append(f"Scheme {holding.scheme_name} does not match {tier}")
    for row in transactions:
        named = _named_tier(row.scheme_name)
        if named and named != tier:
            warnings.append(f"Transaction scheme {row.scheme_name} does not match {tier}")


def _check_year(dates: list[date], financial_year: str, warnings: list[str]) -> None:
    for day in dates:
        if financial_year_for(day) != financial_year:
            warnings.append(f"{day.isoformat()} falls outside {financial_year}")
            return


def _split_csv(line: str) -> list[str]:
    return [cell.strip() for cell in next(csv.reader(StringIO(line)))]


def _cell(row: list[str], index: Optional[int]) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _is_note(value: str) -> bool:
    return _norm(value).startswith("note")


def _money(value: str) -> Optional[float]:
    if value is None:
        return None
    text = value.strip()
    if text in ("", "--"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("Rs", "").replace("rs", "").replace(",", "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _number(value: str) -> Optional[float]:
    return _money(value)
