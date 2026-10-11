from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from portfolio_app.models import ParseResult


def default_db_path() -> Path:
    base = Path.home() / "AppData" / "Local" / "NpsPortfolio"
    base.mkdir(parents=True, exist_ok=True)
    return base / "portfolio.db"


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = path or default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _migrate(connection)
    return connection


def _migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY,
            file_name TEXT NOT NULL,
            file_hash TEXT NOT NULL UNIQUE,
            tier TEXT NOT NULL,
            financial_year TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1,
            year_mismatch_confirmed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS account_snapshots (
            import_id INTEGER PRIMARY KEY REFERENCES imports(id),
            pran TEXT NOT NULL,
            subscriber_name TEXT NOT NULL,
            scheme_choice TEXT NOT NULL,
            holdings_value REAL NOT NULL,
            contribution_count INTEGER NOT NULL,
            total_contribution REAL NOT NULL,
            total_withdrawal REAL NOT NULL,
            notional_gain REAL NOT NULL,
            charges REAL NOT NULL,
            xirr REAL,
            as_of_date TEXT NOT NULL,
            nav_date TEXT,
            generation_date TEXT
        );
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL REFERENCES imports(id),
            scheme_name TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            tier TEXT NOT NULL,
            units REAL NOT NULL,
            statement_nav REAL NOT NULL,
            statement_nav_date TEXT,
            market_value REAL NOT NULL,
            net_contribution REAL,
            scheme_code TEXT
        );
        CREATE TABLE IF NOT EXISTS contributions (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL REFERENCES imports(id),
            txn_date TEXT NOT NULL,
            particulars TEXT NOT NULL,
            uploaded_by TEXT NOT NULL,
            employee_amount REAL NOT NULL,
            employer_amount REAL NOT NULL,
            total REAL NOT NULL,
            tier TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scheme_transactions (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL REFERENCES imports(id),
            txn_date TEXT NOT NULL,
            scheme_name TEXT NOT NULL,
            description TEXT NOT NULL,
            narration_kind TEXT NOT NULL,
            amount REAL,
            nav REAL,
            units REAL,
            tier TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS format_profiles (
            fingerprint TEXT PRIMARY KEY,
            mapping_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS narration_aliases (
            phrase TEXT PRIMARY KEY,
            kind TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scheme_links (
            statement_name TEXT PRIMARY KEY,
            scheme_code TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nav_quotes (
            scheme_code TEXT PRIMARY KEY,
            scheme_name TEXT NOT NULL,
            nav REAL NOT NULL,
            nav_date TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
        """
    )
    holding_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(holdings)")
    }
    if "net_contribution" not in holding_columns:
        connection.execute("ALTER TABLE holdings ADD COLUMN net_contribution REAL")
    connection.commit()


def file_hash_exists(connection: sqlite3.Connection, file_hash: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM imports WHERE file_hash = ?", (file_hash,)
    ).fetchone()
    return row is not None


def narration_alias_map(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute("SELECT phrase, kind FROM narration_aliases").fetchall()
    return {row["phrase"]: row["kind"] for row in rows}


def save_narration_alias(connection: sqlite3.Connection, phrase: str, kind: str) -> None:
    connection.execute(
        "INSERT INTO narration_aliases(phrase, kind) VALUES (?, ?) "
        "ON CONFLICT(phrase) DO UPDATE SET kind = excluded.kind",
        (phrase, kind),
    )


def profile_mapping(connection: sqlite3.Connection, fingerprint: str) -> Optional[str]:
    row = connection.execute(
        "SELECT mapping_json FROM format_profiles WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    return None if row is None else row["mapping_json"]


def save_profile(connection: sqlite3.Connection, fingerprint: str, mapping_json: str) -> None:
    connection.execute(
        "INSERT INTO format_profiles(fingerprint, mapping_json, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(fingerprint) DO UPDATE SET mapping_json = excluded.mapping_json",
        (fingerprint, mapping_json, datetime.now().isoformat(timespec="seconds")),
    )


def save_import(connection: sqlite3.Connection, result: ParseResult, file_name: str, file_hash: str) -> int:
    connection.execute(
        """
        UPDATE imports SET is_current = 0
        WHERE tier = ? AND financial_year = ? AND is_current = 1
          AND id IN (
              SELECT import_id FROM account_snapshots
              WHERE COALESCE(NULLIF(pran, ''), subscriber_name) = ?
          )
        """,
        (result.tier, result.financial_year, result.pran or result.subscriber_name),
    )
    cursor = connection.execute(
        """
        INSERT INTO imports (
            file_name, file_hash, tier, financial_year, period_start, period_end,
            fingerprint, imported_at, is_current, year_mismatch_confirmed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            file_name,
            file_hash,
            result.tier,
            result.financial_year,
            result.period_start.isoformat(),
            result.period_end.isoformat(),
            result.fingerprint,
            datetime.now().isoformat(timespec="seconds"),
            1 if result.year_mismatch else 0,
        ),
    )
    import_id = int(cursor.lastrowid)
    connection.execute(
        """
        INSERT INTO account_snapshots (
            import_id, pran, subscriber_name, scheme_choice, holdings_value,
            contribution_count, total_contribution, total_withdrawal, notional_gain,
            charges, xirr, as_of_date, nav_date, generation_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_id,
            result.pran,
            result.subscriber_name,
            result.scheme_choice,
            result.holdings_value,
            result.contribution_count,
            result.total_contribution,
            result.total_withdrawal,
            result.notional_gain,
            result.charges,
            result.xirr,
            result.as_of_date.isoformat(),
            None if result.nav_date is None else result.nav_date.isoformat(),
            None if result.generation_date is None else result.generation_date.isoformat(timespec="minutes"),
        ),
    )
    links = {
        row["statement_name"]: row["scheme_code"]
        for row in connection.execute("SELECT statement_name, scheme_code FROM scheme_links")
    }
    for holding in result.holdings:
        connection.execute(
            """
            INSERT INTO holdings (
                import_id, scheme_name, asset_class, tier, units, statement_nav,
                statement_nav_date, market_value, net_contribution, scheme_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_id,
                holding.scheme_name,
                holding.asset_class,
                result.tier,
                holding.units,
                holding.statement_nav,
                None if result.nav_date is None else result.nav_date.isoformat(),
                holding.market_value,
                holding.net_contribution,
                links.get(holding.scheme_name),
            ),
        )
    for row in result.contributions:
        connection.execute(
            """
            INSERT INTO contributions (
                import_id, txn_date, particulars, uploaded_by, employee_amount,
                employer_amount, total, tier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_id,
                row.txn_date.isoformat(),
                row.particulars,
                row.uploaded_by,
                row.employee_amount,
                row.employer_amount,
                row.total,
                result.tier,
            ),
        )
    for row in result.transactions:
        connection.execute(
            """
            INSERT INTO scheme_transactions (
                import_id, txn_date, scheme_name, description, narration_kind,
                amount, nav, units, tier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_id,
                row.txn_date.isoformat(),
                row.scheme_name,
                row.description,
                row.narration_kind,
                row.amount,
                row.nav,
                row.units,
                result.tier,
            ),
        )
    connection.commit()
    return import_id


def list_years(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT i.id, i.tier, i.financial_year, i.file_name, s.as_of_date, s.holdings_value,
               s.subscriber_name, s.pran, s.xirr,
               COALESCE(NULLIF(s.pran, ''), s.subscriber_name) AS member_id
        FROM imports i
        JOIN account_snapshots s ON s.import_id = i.id
        WHERE i.is_current = 1
        ORDER BY s.as_of_date DESC, s.subscriber_name, i.tier
        """
    ).fetchall()


def current_holding_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT h.*, s.as_of_date, s.subscriber_name, s.pran,
               COALESCE(NULLIF(s.pran, ''), s.subscriber_name) AS member_id,
               i.financial_year, q.nav AS latest_nav, q.nav_date AS latest_nav_date,
               q.nav_date AS nav_date
        FROM holdings h
        JOIN imports i ON i.id = h.import_id
        JOIN account_snapshots s ON s.import_id = i.id
        LEFT JOIN nav_quotes q ON q.scheme_code = h.scheme_code
        WHERE i.is_current = 1
          AND i.id IN (
              SELECT i2.id FROM imports i2
              JOIN account_snapshots s2 ON s2.import_id = i2.id
              WHERE i2.is_current = 1
                AND s2.as_of_date = (
                    SELECT MAX(s3.as_of_date) FROM account_snapshots s3
                    JOIN imports i3 ON i3.id = s3.import_id
                    WHERE i3.is_current = 1 AND i3.tier = i2.tier
                      AND COALESCE(NULLIF(s3.pran, ''), s3.subscriber_name) =
                          COALESCE(NULLIF(s2.pran, ''), s2.subscriber_name)
                )
                AND i2.tier = i.tier
                AND COALESCE(NULLIF(s2.pran, ''), s2.subscriber_name) =
                    COALESCE(NULLIF(s.pran, ''), s.subscriber_name)
          )
        ORDER BY h.tier, h.asset_class
        """
    ).fetchall()


def holdings_for_import(connection: sqlite3.Connection, import_id: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT h.*, s.as_of_date, s.subscriber_name, s.pran,
               COALESCE(NULLIF(s.pran, ''), s.subscriber_name) AS member_id,
               i.financial_year, q.nav AS latest_nav, q.nav_date AS latest_nav_date
        FROM holdings h
        JOIN imports i ON i.id = h.import_id
        JOIN account_snapshots s ON s.import_id = i.id
        LEFT JOIN nav_quotes q ON q.scheme_code = h.scheme_code
        WHERE h.import_id = ?
        ORDER BY h.asset_class
        """,
        (import_id,),
    ).fetchall()


def transactions_for_import(connection: sqlite3.Connection, import_id: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT t.*, s.subscriber_name
        FROM scheme_transactions t
        JOIN account_snapshots s ON s.import_id = t.import_id
        WHERE t.import_id = ?
        ORDER BY t.txn_date, t.scheme_name, t.id
        """,
        (import_id,),
    ).fetchall()


def all_current_transactions(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT t.*, i.financial_year, s.subscriber_name, s.pran,
               COALESCE(NULLIF(s.pran, ''), s.subscriber_name) AS member_id
        FROM scheme_transactions t
        JOIN imports i ON i.id = t.import_id
        JOIN account_snapshots s ON s.import_id = i.id
        WHERE i.is_current = 1
        ORDER BY t.txn_date, t.scheme_name, t.id
        """
    ).fetchall()


def unmatched_statement_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT scheme_name FROM holdings
        WHERE scheme_code IS NULL OR scheme_code = ''
        ORDER BY scheme_name
        """
    ).fetchall()
    return [row["scheme_name"] for row in rows]


def set_scheme_code(connection: sqlite3.Connection, statement_name: str, scheme_code: str) -> None:
    connection.execute(
        "INSERT INTO scheme_links(statement_name, scheme_code) VALUES (?, ?) "
        "ON CONFLICT(statement_name) DO UPDATE SET scheme_code = excluded.scheme_code",
        (statement_name, scheme_code),
    )
    connection.execute(
        "UPDATE holdings SET scheme_code = ? WHERE scheme_name = ?",
        (scheme_code, statement_name),
    )
    connection.commit()


def apply_saved_links(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE holdings
        SET scheme_code = (
            SELECT scheme_code FROM scheme_links WHERE statement_name = holdings.scheme_name
        )
        WHERE scheme_name IN (SELECT statement_name FROM scheme_links)
        """
    )
    connection.commit()


def save_nav_quote(
    connection: sqlite3.Connection,
    scheme_code: str,
    scheme_name: str,
    nav: float,
    nav_date: date,
    fetched_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO nav_quotes(scheme_code, scheme_name, nav, nav_date, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(scheme_code) DO UPDATE SET
            scheme_name = excluded.scheme_name,
            nav = excluded.nav,
            nav_date = excluded.nav_date,
            fetched_at = excluded.fetched_at
        """,
        (scheme_code, scheme_name, nav, nav_date.isoformat(), fetched_at.isoformat(timespec="seconds")),
    )
