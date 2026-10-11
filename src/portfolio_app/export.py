from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_table(path: Path, rows: list[dict]) -> None:
    frame = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
        return
    if path.suffix.lower() in {".xlsx", ".xls"}:
        frame.to_excel(path, index=False)
        return
    raise ValueError("Export path must end in .csv or .xlsx")


def holding_records(rows) -> list[dict]:
    records = []
    for row in rows:
        units = float(row["units"])
        latest = row["latest_nav"]
        statement_nav = float(row["statement_nav"])
        price = float(latest) if latest is not None else statement_nav
        records.append(
            {
                "subscriber": row["subscriber_name"] if "subscriber_name" in row.keys() else "",
                "tier": _tier_label(row["tier"]),
                "financial_year": row["financial_year"],
                "scheme": row["scheme_name"],
                "asset_class": row["asset_class"],
                "units": units,
                "net_contribution": (
                    None
                    if row["net_contribution"] is None
                    else float(row["net_contribution"])
                ),
                "net_invested": (
                    row["net_invested"]
                    if "net_invested" in row.keys()
                    else row["net_contribution"]
                ),
                "invested_complete": (
                    row["invested_complete"]
                    if "invested_complete" in row.keys()
                    else row["net_contribution"] is not None
                ),
                "calculated_xirr": (
                    row["calculated_xirr"]
                    if "calculated_xirr" in row.keys()
                    else None
                ),
                "statement_xirr": (
                    row["reported_xirr"]
                    if "reported_xirr" in row.keys()
                    else None
                ),
                "statement_nav": statement_nav,
                "statement_nav_date": row["statement_nav_date"],
                "latest_nav": None if latest is None else float(latest),
                "latest_nav_date": row["latest_nav_date"],
                "value": round(units * price, 2),
                "scheme_code": row["scheme_code"],
            }
        )
    return records


def transaction_records(rows) -> list[dict]:
    records = []
    for row in rows:
        records.append(
            {
                "subscriber": row["subscriber_name"] if "subscriber_name" in row.keys() else "",
                "tier": _tier_label(row["tier"]),
                "financial_year": row["financial_year"] if "financial_year" in row.keys() else "",
                "date": row["txn_date"],
                "scheme": row["scheme_name"],
                "description": row["description"],
                "kind": row["narration_kind"],
                "amount": row["amount"],
                "nav": row["nav"],
                "units": row["units"],
            }
        )
    return records


def _tier_label(tier: str) -> str:
    return "Tier 1" if tier == "nps_tier1" else "Tier 2"
