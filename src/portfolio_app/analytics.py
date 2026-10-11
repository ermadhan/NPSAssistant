from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

from portfolio_app import db

ASSET_CLASSES = {
    "E": "Equity",
    "C": "Corporate",
    "G": "Government",
    "A": "Alternative",
}
CONTRIBUTION_KINDS = {"voluntary", "employer", "tier2"}


def family_portfolio(connection) -> dict:
    years = db.list_years(connection)
    current = db.current_holding_rows(connection)
    transactions = db.all_current_transactions(connection)
    histories = defaultdict(list)
    for row in transactions:
        histories[(row["member_id"], row["tier"], row["scheme_name"])].append(row)

    member_names = {}
    for row in current:
        member_names[row["member_id"]] = row["subscriber_name"] or "NPS subscriber"

    first_snapshots = {}
    for row in years:
        member_id = row["member_id"]
        key = (member_id, row["tier"])
        if key not in first_snapshots or row["as_of_date"] < first_snapshots[key]["as_of_date"]:
            first_snapshots[key] = row

    positions = []
    for row in current:
        key = (row["member_id"], row["tier"])
        baseline_import = first_snapshots.get(key)
        baseline_date = date.fromisoformat(baseline_import["as_of_date"]) if baseline_import else None
        baseline_rows = (
            db.holdings_for_import(connection, baseline_import["id"])
            if baseline_import is not None
            else []
        )
        baseline = next(
            (
                item
                for item in baseline_rows
                if item["scheme_name"] == row["scheme_name"]
            ),
            None,
        )
        transaction_rows = histories[
            (row["member_id"], row["tier"], row["scheme_name"])
        ]
        basis, complete = _net_invested(
            baseline,
            baseline_date,
            transaction_rows,
        )
        current_value = _holding_value(row)
        asset_class = ASSET_CLASSES.get(row["asset_class"], "Other")
        positions.append(
            {
                "member_id": row["member_id"],
                "member": member_names[row["member_id"]],
                "tier": row["tier"],
                "scheme_name": row["scheme_name"],
                "scheme_code": row["scheme_code"],
                "asset_class": asset_class,
                "units": float(row["units"]),
                "current_value": current_value,
                "net_invested": basis,
                "invested_complete": complete,
                "reported_xirr": None,
                "calculated_xirr": None,
                "as_of_date": row["as_of_date"],
                "nav_date": row["latest_nav_date"] or row["as_of_date"],
            }
        )

    for position in positions:
        position["calculated_xirr"] = _position_xirr(
            position,
            first_snapshots,
            connection,
            histories,
        )

    account_returns = _account_returns(
        current, years, histories, include_switches=False
    )
    reported_returns = _reported_returns(years)
    reported_by_account = {
        (row["member_id"], row["tier"]): row["reported_xirr"]
        for row in reported_returns
    }
    for position in positions:
        position["reported_xirr"] = reported_by_account.get(
            (position["member_id"], position["tier"])
        )
    family_flows = [
        flow
        for row in account_returns
        for flow in row["_cashflows"]
    ]
    family_returns = [
        {
            "member_id": "family",
            "member": "Family",
            "tier": None,
            "calculated_xirr": xirr(family_flows),
        }
    ]
    for tier in ("nps_tier1", "nps_tier2"):
        flows = [
            flow
            for row in account_returns
            if row["tier"] == tier
            for flow in row["_cashflows"]
        ]
        if flows:
            family_returns.append(
                {
                    "member_id": "family",
                    "member": "Family",
                    "tier": tier,
                    "calculated_xirr": xirr(flows),
                }
            )
    for row in account_returns:
        row.pop("_cashflows")

    return {
        "positions": positions,
        "members": [
            {"member_id": member_id, "name": name}
            for member_id, name in member_names.items()
        ],
        "reported_xirr": reported_returns,
        "calculated_xirr": account_returns + family_returns,
    }


def allocation_summary(positions: list[dict]) -> list[dict]:
    assets = ["Equity", "Corporate", "Government", "Alternative"]
    assets.extend(
        sorted(
            {
                position["asset_class"]
                for position in positions
                if position["asset_class"] not in assets
            }
        )
    )
    category_keys = [
        (tier, asset)
        for tier in ("nps_tier1", "nps_tier2")
        for asset in assets
    ]
    totals = {}
    for position in positions:
        member_id = position["member_id"]
        member = position["member"]
        for key in (("family", "Family total"), (member_id, member)):
            values = totals.setdefault(
                key,
                {
                    "member_id": key[0],
                    "member": key[1],
                    "current_value": 0.0,
                    "net_invested": 0.0,
                    "invested_complete": True,
                    "categories": {
                        category: {"current_value": 0.0, "net_invested": 0.0}
                        for category in category_keys
                    },
                    "asset_totals": {
                        asset: {"current_value": 0.0, "net_invested": 0.0}
                        for asset in assets
                    },
                },
            )
            category = values["categories"][(position["tier"], position["asset_class"])]
            amount = position["net_invested"]
            category["current_value"] += position["current_value"]
            values["current_value"] += position["current_value"]
            values["invested_complete"] &= position["invested_complete"]
            if amount is not None:
                category["net_invested"] += amount
                values["net_invested"] += amount
            asset_total = values["asset_totals"][position["asset_class"]]
            asset_total["current_value"] += position["current_value"]
            if amount is not None:
                asset_total["net_invested"] += amount
    return [
        *[row for row in totals.values() if row["member_id"] != "family"],
        *[row for row in totals.values() if row["member_id"] == "family"],
    ]


def xirr(cashflows: list[tuple[date, float]]) -> float | None:
    flows = [(day, amount) for day, amount in cashflows if amount]
    if len(flows) < 2 or not any(amount < 0 for _, amount in flows) or not any(
        amount > 0 for _, amount in flows
    ):
        return None
    origin = min(day for day, _ in flows)
    dated = [
        ((day - origin).days / 365.0, amount)
        for day, amount in flows
    ]

    def npv(log_rate: float) -> float:
        total = 0.0
        for years, amount in dated:
            exponent = max(-700.0, min(700.0, -log_rate * years))
            total += amount * math.exp(exponent)
        return total

    boundaries = [-9.21, -6.91, -4.61, -2.30, -1.39, -0.69, -0.29, 0.0, 0.1, 0.22, 0.41, 0.69, 1.1, 1.79, 2.4, 3.4, 4.6, 6.9]
    roots = []
    previous_rate = boundaries[0]
    previous_value = npv(previous_rate)
    for rate in boundaries[1:]:
        value = npv(rate)
        if value == 0:
            roots.append(rate)
        elif previous_value and value and (previous_value < 0) != (value < 0):
            low, high = previous_rate, rate
            low_value = previous_value
            for _ in range(120):
                middle = (low + high) / 2
                middle_value = npv(middle)
                if abs(middle_value) < 1e-9:
                    low = high = middle
                    break
                if (low_value < 0) != (middle_value < 0):
                    high = middle
                else:
                    low = middle
                    low_value = middle_value
            roots.append((low + high) / 2)
        previous_rate, previous_value = rate, value
    if len(roots) != 1:
        return None
    result = math.expm1(roots[0])
    return result if math.isfinite(result) else None


def _net_invested(baseline, baseline_date, transactions) -> tuple[float | None, bool]:
    known_basis = (
        None
        if baseline is None or baseline["net_contribution"] is None
        else float(baseline["net_contribution"])
    )
    invested = known_basis or 0.0
    has_flows = False
    has_untracked_flow = False
    for row in transactions:
        day = date.fromisoformat(row["txn_date"])
        if known_basis is not None and baseline_date is not None and day <= baseline_date:
            continue
        amount = _basis_change(row)
        if amount is not None:
            invested += amount
            has_flows = True
        elif (
            known_basis is not None
            and row["amount"] is not None
            and row["narration_kind"] not in {"opening", "closing", "billing"}
        ):
            has_untracked_flow = True
    if known_basis is None and not has_flows:
        return None, False
    return invested, known_basis is not None and not has_untracked_flow


def _basis_change(row) -> float | None:
    amount = row["amount"]
    if amount is None:
        return None
    kind = row["narration_kind"]
    if kind in CONTRIBUTION_KINDS:
        return float(amount)
    if kind == "switch_in":
        return abs(float(amount))
    if kind == "switch_out":
        return -abs(float(amount))
    if kind == "other" and _is_withdrawal(row["description"]):
        return -abs(float(amount))
    return None


def _position_xirr(position, first_snapshots, connection, histories) -> float | None:
    key = (position["member_id"], position["tier"])
    baseline_import = first_snapshots.get(key)
    if baseline_import is None:
        return None
    baseline_rows = db.holdings_for_import(connection, baseline_import["id"])
    baseline = next(
        (row for row in baseline_rows if row["scheme_name"] == position["scheme_name"]),
        None,
    )
    end_date = date.fromisoformat(position["as_of_date"])
    coverage_start = date.fromisoformat(baseline_import["as_of_date"])
    flows = (
        [(coverage_start, -_statement_value(baseline))]
        if baseline is not None
        else []
    )
    for row in histories[(position["member_id"], position["tier"], position["scheme_name"])]:
        day = date.fromisoformat(row["txn_date"])
        if (baseline is not None and day <= coverage_start) or day < coverage_start or day > end_date:
            continue
        flow = _cash_flow(row, include_switches=True)
        if flow:
            flows.append((day, flow))
    if not flows:
        return None
    flows.append((end_date, position["current_value"]))
    return xirr(flows)


def _reported_returns(years) -> list[dict]:
    latest = {}
    for row in years:
        key = (row["member_id"], row["tier"])
        if key not in latest or row["as_of_date"] > latest[key]["as_of_date"]:
            latest[key] = row
    return [
        {
            "member_id": row["member_id"],
            "member": row["subscriber_name"] or "NPS subscriber",
            "tier": row["tier"],
            "reported_xirr": row["xirr"],
            "as_of_date": row["as_of_date"],
        }
        for row in latest.values()
    ]


def _account_returns(current, years, histories, *, include_switches: bool) -> list[dict]:
    current_by_account = defaultdict(list)
    for row in current:
        current_by_account[(row["member_id"], row["tier"])].append(row)
    earliest = {}
    for row in years:
        key = (row["member_id"], row["tier"])
        if key not in earliest or row["as_of_date"] < earliest[key]["as_of_date"]:
            earliest[key] = row
    results = []
    for key, end_rows in current_by_account.items():
        start = earliest.get(key)
        if start is None:
            continue
        start_day = date.fromisoformat(start["as_of_date"])
        end_day = max(date.fromisoformat(row["as_of_date"]) for row in end_rows)
        flows = []
        # Seed the performance period with the account value at its earliest imported snapshot.
        flows.append((start_day, -float(start["holdings_value"])))
        for scheme in {row["scheme_name"] for row in end_rows}:
            for row in histories[(key[0], key[1], scheme)]:
                day = date.fromisoformat(row["txn_date"])
                if day <= start_day or day > end_day:
                    continue
                flow = _cash_flow(row, include_switches=include_switches)
                if flow:
                    flows.append((day, flow))
        end_value = sum(_holding_value(row) for row in end_rows)
        flows.append((end_day, end_value))
        results.append(
            {
                "member_id": key[0],
                "member": end_rows[0]["subscriber_name"] or "NPS subscriber",
                "tier": key[1],
                "calculated_xirr": xirr(flows),
                "start_date": start_day.isoformat(),
                "end_date": end_day.isoformat(),
                "_cashflows": flows,
            }
        )
    return results


def _cash_flow(row, *, include_switches: bool) -> float:
    amount = row["amount"]
    if amount is None:
        return 0.0
    value = float(amount)
    kind = row["narration_kind"]
    if kind in CONTRIBUTION_KINDS:
        return -value
    if kind in {"switch_in", "switch_out"}:
        return -value if include_switches else 0.0
    if kind == "other" and _is_withdrawal(row["description"]):
        return abs(value)
    return 0.0


def _is_withdrawal(description: str) -> bool:
    lowered = description.lower()
    return "withdrawal" in lowered or "redemption" in lowered


def _holding_value(row) -> float:
    nav = row["latest_nav"]
    price = float(nav) if nav is not None else float(row["statement_nav"])
    return float(row["units"]) * price


def _statement_value(row) -> float:
    return float(row["units"]) * float(row["statement_nav"])
