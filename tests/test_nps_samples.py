import csv
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from portfolio_app import db  # noqa: E402
from portfolio_app.analytics import allocation_summary, family_portfolio, xirr  # noqa: E402
from portfolio_app.nav import FundNav, match_scheme  # noqa: E402
from portfolio_app.main import (  # noqa: E402
    PortfolioApp,
    _allocation_bucket_table,
    _allocation_total_table,
    _format_inr,
    _holdings_summary_table,
)
from portfolio_app.parsers.nps import (  # noqa: E402
    financial_year_for,
    parse_nps_file,
    parse_nps_text,
)
from portfolio_app.service import (  # noqa: E402
    export_holdings,
    export_transactions,
    refresh_navs,
    save_statement,
    save_security_disclosure,
)
from portfolio_app.securities import (
    build_security_exposure,
    parse_hdfc_disclosure,
    parse_icici_disclosure,
    parse_lic_disclosure,
)

SAMPLES = [
    (tier, year)
    for year in ("2024-25", "2025-26", "2026-27")
    for tier in ("nps_tier1", "nps_tier2")
]

NAV_DATE = date(2026, 9, 25)
FUNDS = [
    FundNav("SM007001", "ICICI PRUDENTIAL PENSION FUND SCHEME E - TIER I", 1, NAV_DATE),
    FundNav("SM007002", "ICICI PRUDENTIAL PENSION FUND SCHEME C - TIER I", 1, NAV_DATE),
    FundNav("SM007003", "ICICI PRUDENTIAL PENSION FUND SCHEME G - TIER I", 1, NAV_DATE),
    FundNav("SM007004", "ICICI PRUDENTIAL PENSION FUND SCHEME E - TIER II", 1, NAV_DATE),
    FundNav("SM007005", "ICICI PRUDENTIAL PENSION FUND SCHEME C - TIER II", 1, NAV_DATE),
    FundNav("SM007006", "ICICI PRUDENTIAL PENSION FUND SCHEME G - TIER II", 1, NAV_DATE),
    FundNav("SM007010", "ICICI PRUDENTIAL PENSION FUNDS SCHEME TAX SAVER TIER II", 1, NAV_DATE),
]


def _sample_filename(tier: str, year: str) -> str:
    tier_number = "Tier1" if tier == "nps_tier1" else "Tier2"
    return f"Sample_{tier_number}_{year.replace('-', '_')}.csv"


def _sample_statement(tier: str, year: str) -> str:
    year_start = int(year[:4])
    period_index = year_start - 2024
    tier_roman = "I" if tier == "nps_tier1" else "II"
    schemes = (
        ("E", 100.0, 5.0, 0.1, 10.0),
        ("C", 200.0, 8.0, 0.2, 5.0),
        ("G", 300.0, 12.0, 0.3, 3.0),
    )
    as_of = date(year_start + 1, 3, 31) if year_start < 2026 else date(2026, 9, 27)
    nav_date = date(as_of.year, as_of.month, min(as_of.day, 25))
    statement_holdings = []
    transaction_blocks = []
    for asset, starting_units, added_units, charge_units, initial_nav in schemes:
        opening_units = starting_units + period_index * (added_units - charge_units)
        current_units = opening_units + added_units - charge_units
        nav = initial_nav + period_index
        scheme = f"ICICI PRUDENTIAL PENSION FUND SCHEME {asset} - TIER {tier_roman}"
        value = current_units * nav
        statement_holdings.append((scheme, value, current_units, nav))
        opening_day = date(year_start, 4, 1).strftime("%d-%b-%Y")
        contribution_day = date(year_start, 5, 5).strftime("%d-%b-%Y")
        billing_day = date(year_start, 6, 5).strftime("%d-%b-%Y")
        transactions = [
            f"{opening_day},Opening balance,--,--,{opening_units:.4f}",
            f"{contribution_day},By Voluntary Contributions,{added_units * nav:.2f},{nav:.4f},{added_units:.4f}",
            f"{billing_day},Billing for quarterly service,-{charge_units * nav:.2f},{nav:.4f},-{charge_units:.4f}",
        ]
        if year_start < 2026:
            closing_day = date(year_start + 1, 3, 31).strftime("%d-%b-%Y")
            transactions.append(
                f"{closing_day},Closing balance,--,--,{current_units:.4f}"
            )
        transaction_blocks.append((scheme, transactions))

    holdings_value = sum(value for _, value, _, _ in statement_holdings)
    total_charges = sum(
        charge_units * (initial_nav + period_index)
        for _, _, _, charge_units, initial_nav in schemes
    )
    filename_date = as_of.strftime("%b %d %Y")
    date_text = as_of.strftime("%d-%b-%Y")
    nav_text = nav_date.strftime("%d-%b-%Y")
    lines = [
        f"NPS Transaction Statement for Tier {tier_roman} Account",
        "Subscriber Details",
        "PRAN,'SAMPLE-ACCOUNT-001",
        "Subscriber Name,Sample Member",
        f"Statement Generation Date :{filename_date} 08:00 AM",
        "Scheme Choice - ACTIVE CHOICE",
        "Investment Summary",
        (
            f"Value of your Holdings(Investments)as on {filename_date} (in Rs),"
            f"No of contributions,Total contribution in your account as on {filename_date} (in Rs),"
            f"Total withdrawal as on {filename_date} (in Rs),"
            f"Total notional gain/loss as on {filename_date} (in Rs),"
            f"Withdrawal/ deduction in units towards intermediary charges (in Rs),"
            "Return on Investment(XIRR),7.50%"
        ),
        f"Rs {holdings_value:.2f},1,Rs 250.00,Rs 0.00,Rs {holdings_value - 250:.2f},Rs {total_charges:.2f},",
        "Investment Details- Scheme Wise Summary",
        f"Particulars,Scheme wise Value,Total Units,NAV as on {nav_text}",
    ]
    lines.extend(
        f"{scheme},Rs {value:.2f},{units:.4f},{nav:.4f}"
        for scheme, value, units, nav in statement_holdings
    )
    lines.extend(
        [
            "Contribution/ Redemption Details",
            "Date,Particulars,Uploaded By,Employee Contribution(Rs),Employer's Contribution(Rs),Total(Rs)",
            f"{date(year_start, 5, 5).strftime('%d-%b-%Y')},By Voluntary Contribution,Sample Member,150.00,100.00,250.00",
            "Transaction Details",
        ]
    )
    for scheme, transactions in transaction_blocks:
        lines.extend(
            [
                scheme,
                "Date,Description,Amount,Nav,Units",
                *transactions,
            ]
        )
    return "\n".join(lines)


def _sample_result(tier: str, year: str):
    return parse_nps_text(
        _sample_statement(tier, year),
        _sample_filename(tier, year),
    )


def _write_sample(folder: Path, tier: str, year: str) -> Path:
    path = folder / _sample_filename(tier, year)
    path.write_text(_sample_statement(tier, year), encoding="utf-8")
    return path


def _security_workbook() -> bytes:
    rows = [
        ["Name of the Pension Fund", "ICICI Pension Fund"],
        ["Name of the scheme", "Scheme E- Tier I"],
        ["Portfolio Statement as on", datetime(2026, 9, 30)],
        [""] * 7,
        [""] * 7,
        ["Particulars", "ISIN No.", "Industry", "Quantity", "Market Value", "% of Portfolio", "Rating"],
        ["Equity Instruments"],
        ["Shares"],
        ["Sample Equity Ltd", "INE000A01000", "Technology", 10, 600.0, 0.6, ""],
        ["Debt Instruments"],
        ["Sample Corporate Bond", "INE000B01000", "Finance", 4, 300.0, 0.3, "AAA"],
        ["Cash/Cash equivalent & Net Current Assets", "", "", "", 100.0, 0.1, ""],
        ["GRAND TOTAL", "", "", 14, 1000.0, 1.0, ""],
    ]
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(
            writer,
            sheet_name="Scheme E - Tier l",
            index=False,
            header=False,
        )
    return buffer.getvalue()


def _hdfc_workbook() -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for asset, tier, instrument, isin, weight in (
            ("E", "I", "Sample HDFC Equity", "INE000A01000", 1.0),
            ("G", "II", "7% GOI 2030", "IN0020260001", 1.0),
        ):
            rows = [
                ["PENSION FUND MANAGER NAME : HDFC PENSION FUND"],
                ["STATEMENT AS ON", "30 SEPTEMBER 2026"],
                ["DETAILS OF PORTFOLIO FOR - SCHEME"],
                ["PARTICULARS", "ISIN NO.", "INDUSTRY", "QUANTITY", "TOTAL MARKET VALUE", "% OF PORTFOLIO", "RATINGS"],
                ["EQUITY INSTRUMENTS" if asset == "E" else "DEBT INSTRUMENTS"],
                [instrument, isin, "Industry", 10, 1000.0, weight, ""],
                ["GRAND TOTAL", "", "", 10, 1000.0, 1.0, ""],
            ]
            pd.DataFrame(rows).to_excel(
                writer,
                sheet_name=f"Scheme {asset}-Tier-{tier}",
                index=False,
                header=False,
            )
    return buffer.getvalue()


def _lic_workbook() -> bytes:
    rows = [
        ["Name of the Pension Fund : LIC PENSION FUND LIMITED"],
        ["Name of the scheme : LIC PENSION FUND SCHEME E - TIER I"],
        ["Portfolio Statement as on :", "30-09-2026"],
        [""] * 7,
        ["PARTICULARS", "ISIN NO.", "INDUSTRY", "QUANTITY", "TOTAL MARKET VALUE", "% OF PORTFOLIO", "RATINGS"],
        ["Government Securities and Related Investments"],
        ["Equity"],
        ["Sample LIC Equity", "INE000A01000", "Technology", 10, 600.0, 0.6, ""],
        ["", "", "Sub Total", 10, 600.0, 0.6, ""],
        ["Debt Instruments and Related Investments"],
        ["7% Corporate Bond", "INE000B01000", "Finance", 4, 200.0, 0.2, "AAA"],
        ["Government Securities"],
        ["7% GOI 2030", "IN0020260001", "Government", 2, 100.0, 0.1, "SOV"],
        ["Mutual Fund Units"],
        ["SBI Overnight Fund", "INF200K01TK5", "Mutual fund", 1, 100.0, 0.1, ""],
        ["", "GRAND TOTAL", "", 17, 1000.0, 1.0, ""],
        ["NAV PER UNIT (Rs.)", "", "", "", 99.0, "", ""],
    ]
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(
            writer,
            sheet_name="05",
            index=False,
            header=False,
        )
    return buffer.getvalue()


class SampleTests(unittest.TestCase):
    def test_ui_currency_uses_indian_numbering(self):
        self.assertEqual(_format_inr(12_345.67), "Rs 12,345.67")
        self.assertEqual(_format_inr(123_456.78), "Rs 1.23 Lakh")
        self.assertEqual(_format_inr(12_345_678.9), "Rs 1.23 Crore")
        self.assertEqual(_format_inr(-123_456.78), "-Rs 1.23 Lakh")
        self.assertEqual(_format_inr(123_456, decimals=0), "Rs 1.23 Lakh")

    def test_xirr_calculates_dated_annual_return(self):
        result = xirr(
            [
                (date(2021, 1, 1), -1000.0),
                (date(2022, 1, 1), 1100.0),
            ]
        )
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 0.10, places=6)
        self.assertIsNone(
            xirr(
                [
                    (date(2021, 1, 1), -100.0),
                    (date(2022, 1, 1), 230.0),
                    (date(2023, 1, 1), -132.0),
                ]
            )
        )

    def test_legacy_2020_statement_layout(self):
        statement = """NPS Transaction Statement for Tier I Account
Subscriber Details
PRAN,'SAMPLE-ACCOUNT-001
Subscriber Name,Sample Member
Statement Generation Date :September 17  2020 07:18 AM
Scheme Choice - ACTIVE CHOICE
Investment Summary
Value of your Holdings(Investments)as on September 17 2020 (in Rs),No of contributions,Total contribution in your account as on September 17 2020 (in Rs),Total withdrawal as on September 17 2020 (in Rs),Total notional gain/loss as on September 17 2020 (in Rs),Withdrawal/ deduction in units towards intermediary charges (in Rs),Return on Investment(XIRR),12.5%
Rs 300,1,Rs 200,Rs 0,Rs 100,Rs 2,
Investment Details- Scheme Wise Summary
Particulars,References,SBI PENSION FUND SCHEME E - TIER I,SBI PENSION FUND SCHEME C - TIER I,SBI PENSION FUND SCHEME G - TIER I,
Scheme wise Value of your Holdings(Investments) (in Rs),E=U*N,100,100,100,
Scheme wise Contribution in your account (in Rs),F,80,70,50,
Unrealized Gain/Loss (in Rs),,20,30,50,
Total Units,U,10,20,30,
NAV as on 16-Sep-2020,N,10,5,3.3333,
Note: Scheme wise Contribution in your account varies after scheme changes.
Contribution/ Redemption Details
Date,Particulars,Uploaded By,Employee Contribution(Rs),Employer's Contribution(Rs),Total(Rs)
01-Apr-2020,By Voluntary Contribution,Subscriber,100,100,200
Note: closing balance of Contribution/Redemption details
Transaction Details
Date,Particulars,Withdrawal/deduction in units towards intermediary charges (Rs),SBI PENSION FUND SCHEME E - TIER I Amount (Rs),SBI PENSION FUND SCHEME E - TIER I NAV (Rs),SBI PENSION FUND SCHEME E - TIER I Units,SBI PENSION FUND SCHEME C - TIER I Amount (Rs),SBI PENSION FUND SCHEME C - TIER I NAV (Rs),SBI PENSION FUND SCHEME C - TIER I Units,SBI PENSION FUND SCHEME G - TIER I Amount (Rs),SBI PENSION FUND SCHEME G - TIER I NAV (Rs),SBI PENSION FUND SCHEME G - TIER I Units
01-Apr-2020,By Voluntary Contribution,,100,10,10,100,5,20,100,3.3333,30
02-Apr-2020,On account of rebalancing of schemes,-,-5,10,-0.5,5,5,1,,,
"""
        result = parse_nps_text(statement, "legacy-2020.csv")
        self.assertFalse(result.needs_header_map, result.header_mapping)
        self.assertEqual(result.financial_year, "2020-21")
        self.assertEqual(result.xirr, 12.5)
        self.assertEqual(len(result.holdings), 3)
        self.assertAlmostEqual(sum(row.market_value for row in result.holdings), 300)
        self.assertEqual(
            [row.net_contribution for row in result.holdings],
            [80, 70, 50],
        )
        self.assertEqual(result.nav_date, date(2020, 9, 16))
        self.assertEqual(len(result.contributions), 1)
        self.assertEqual(len(result.transactions), 5)
        self.assertEqual(
            {row.narration_kind for row in result.transactions},
            {"voluntary", "switch_in", "switch_out"},
        )
        self.assertEqual(result.unknown_narrations, [])
        self.assertEqual(result.warnings, [])

    def test_legacy_net_contribution_is_saved_and_old_database_migrates(self):
        statement = """NPS Transaction Statement for Tier I Account
Subscriber Details
PRAN,'SAMPLE-ACCOUNT-001
Subscriber Name,Sample Member
Investment Summary
Value of your Holdings(Investments)as on September 17 2020 (in Rs),No of contributions,Total contribution in your account as on September 17 2020 (in Rs),Total withdrawal as on September 17 2020 (in Rs),Total notional gain/loss as on September 17 2020 (in Rs),Withdrawal/ deduction in units towards intermediary charges (in Rs),Return on Investment(XIRR),12.5%
Rs 300,1,Rs 200,Rs 0,Rs 100,Rs 2,
Investment Details- Scheme Wise Summary
Particulars,References,SBI PENSION FUND SCHEME E - TIER I,SBI PENSION FUND SCHEME C - TIER I,SBI PENSION FUND SCHEME G - TIER I,
Scheme wise Value of your Holdings(Investments) (in Rs),E=U*N,100,100,100,
Scheme wise Contribution in your account (in Rs),F,80,70,50,
Total Units,U,10,20,30,
NAV as on 16-Sep-2020,N,10,5,3.3333,
Contribution/ Redemption Details
Date,Particulars,Uploaded By,Employee Contribution(Rs),Employer's Contribution(Rs),Total(Rs)
01-Apr-2020,By Voluntary Contribution,Subscriber,100,100,200
Transaction Details
Date,Particulars,Charges (Rs),SBI PENSION FUND SCHEME E - TIER I Amount (Rs),SBI PENSION FUND SCHEME E - TIER I NAV (Rs),SBI PENSION FUND SCHEME E - TIER I Units
01-Apr-2020,By Voluntary Contribution,,100,10,10
"""
        result = parse_nps_text(statement, "legacy-2020.csv")
        with tempfile.TemporaryDirectory() as folder:
            database_path = Path(folder) / "portfolio.db"
            legacy = db.connect(database_path)
            legacy.execute(
                "ALTER TABLE holdings DROP COLUMN net_contribution"
            )
            legacy.close()
            connection = db.connect(database_path)
            import_id = db.save_import(
                connection, result, "legacy-2020.csv", "legacy-hash"
            )
            rows = connection.execute(
                "SELECT net_contribution FROM holdings ORDER BY id"
            ).fetchall()
            self.assertEqual([row["net_contribution"] for row in rows], [80, 70, 50])
            export_path = Path(folder) / "legacy-holdings.csv"
            export_holdings(connection, export_path, import_id)
            with export_path.open(encoding="utf-8", newline="") as file:
                self.assertEqual(
                    {
                        row["scheme"]: float(row["net_contribution"])
                        for row in csv.DictReader(file)
                    },
                    {
                        "SBI PENSION FUND SCHEME E - TIER I": 80,
                        "SBI PENSION FUND SCHEME C - TIER I": 70,
                        "SBI PENSION FUND SCHEME G - TIER I": 50,
                    },
                )
            connection.close()

    def test_holdings_totals_and_charges(self):
        parsed = {}
        for tier, year in SAMPLES:
            result = _sample_result(tier, year)
            parsed[(tier, year)] = result
            self.assertFalse(result.needs_header_map, result.header_mapping)
            self.assertEqual(result.unknown_narrations, [])
            self.assertEqual(result.warnings, [])
            self.assertEqual(result.tier, tier)
            self.assertEqual(result.financial_year, year)
            self.assertEqual(result.filename_year, year)
            self.assertFalse(result.year_mismatch)
            self.assertGreater(result.holdings_value, 0)
            self.assertAlmostEqual(
                sum(row.market_value for row in result.holdings),
                result.holdings_value,
                places=2,
            )
            billing = sum(-row.amount for row in result.transactions if row.narration_kind == "billing")
            self.assertAlmostEqual(billing, result.charges, places=2)
        self._assert_unit_chain(parsed)

    def _assert_unit_chain(self, parsed):
        closing = {}
        opening = {}
        for name, result in parsed.items():
            for row in result.transactions:
                key = (result.tier, row.scheme_name.split("SCHEME")[-1].strip()[0], result.financial_year)
                if row.narration_kind == "closing" and row.txn_date.month == 3 and row.txn_date.day == 31:
                    closing[key] = row.units
                if row.narration_kind == "opening":
                    opening[key] = row.units
        pairs = (("2024-25", "2025-26"), ("2025-26", "2026-27"))
        for tier in ("nps_tier1", "nps_tier2"):
            for letter in ("E", "C", "G"):
                for previous, current in pairs:
                    self.assertAlmostEqual(
                        closing[(tier, letter, previous)],
                        opening[(tier, letter, current)],
                        places=4,
                        msg=f"{tier} {letter} {previous}->{current}",
                    )

    def test_scheme_match_ignores_pop_and_prudential(self):
        expected = {
            "ICICI PENSION FUND SCHEME E - TIER I POP": "SM007001",
            "ICICI PENSION FUND SCHEME C - TIER I POP": "SM007002",
            "ICICI PENSION FUND SCHEME G - TIER I POP": "SM007003",
            "ICICI PENSION FUND SCHEME E - TIER II POP": "SM007004",
            "ICICI PENSION FUND SCHEME C - TIER II POP": "SM007005",
            "ICICI PENSION FUND SCHEME G - TIER II POP": "SM007006",
        }
        for name, code in expected.items():
            match = match_scheme(name, FUNDS)
            self.assertIsNotNone(match.unique)
            self.assertEqual(match.unique.scheme_code, code)

    def test_save_current_holdings_and_export(self):
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "portfolio.db")
            for year in ("2024-25", "2026-27"):
                path = _write_sample(Path(folder), "nps_tier1", year)
                result = parse_nps_file(path)
                save_statement(connection, path, result, confirm_year=False, narration_map={})
            refresh_navs(connection, FUNDS)
            rows = db.current_holding_rows(connection)
            self.assertEqual({row["financial_year"] for row in rows}, {"2026-27"})
            self.assertTrue(all(row["scheme_code"] for row in rows))
            target = Path(folder) / "holdings.csv"
            export_holdings(connection, target)
            self.assertIn("SM007001", target.read_text(encoding="utf-8"))
            with target.open(encoding="utf-8", newline="") as file:
                exported_columns = next(csv.reader(file))
            self.assertIn("net_invested", exported_columns)
            self.assertIn("calculated_xirr", exported_columns)
            self.assertIn("statement_xirr", exported_columns)
            workbook = Path(folder) / "transactions.xlsx"
            export_transactions(connection, workbook)
            self.assertTrue(workbook.stat().st_size > 0)
            connection.close()

    def test_current_holdings_are_isolated_by_subscriber(self):
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "portfolio.db")
            result = _sample_result("nps_tier1", "2026-27")
            db.save_import(connection, result, "member-one.csv", "hash-member-one")

            member_two = replace(
                result,
                pran="SAMPLE-ACCOUNT-002",
                subscriber_name="Sample Member Two",
            )
            db.save_import(connection, member_two, "member-two.csv", "hash-member-two")
            newer_member_two = replace(member_two, as_of_date=date(2026, 10, 1))
            db.save_import(
                connection,
                newer_member_two,
                "member-two-new.csv",
                "hash-member-two-new",
            )

            rows = db.current_holding_rows(connection)
            self.assertEqual(
                {row["subscriber_name"] for row in rows},
                {result.subscriber_name, member_two.subscriber_name},
            )
            self.assertEqual(
                {row["subscriber_name"] for row in db.list_years(connection)},
                {result.subscriber_name, member_two.subscriber_name},
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM imports WHERE is_current = 1"
                ).fetchone()[0],
                2,
            )
            holdings_path = Path(folder) / "member-two-holdings.csv"
            export_holdings(
                connection,
                holdings_path,
                member_id="SAMPLE-ACCOUNT-002",
            )
            with holdings_path.open(encoding="utf-8", newline="") as file:
                self.assertEqual(
                    {row["subscriber"] for row in csv.DictReader(file)},
                    {member_two.subscriber_name},
                )
            transactions_path = Path(folder) / "member-two-transactions.csv"
            export_transactions(
                connection,
                transactions_path,
                member_id="SAMPLE-ACCOUNT-002",
            )
            with transactions_path.open(encoding="utf-8", newline="") as file:
                self.assertEqual(
                    {row["subscriber"] for row in csv.DictReader(file)},
                    {member_two.subscriber_name},
                )
            connection.close()

    def test_family_allocation_rolls_up_member_tier_and_asset_values(self):
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "portfolio.db")
            result = _sample_result("nps_tier1", "2026-27")
            db.save_import(connection, result, "member-one.csv", "allocation-member-one")
            tier2 = _sample_result("nps_tier2", "2026-27")
            db.save_import(connection, tier2, "member-one-tier2.csv", "allocation-member-one-tier2")
            member_two = replace(
                result,
                pran="SAMPLE-ACCOUNT-002",
                subscriber_name="Sample Member Two",
            )
            db.save_import(
                connection,
                member_two,
                "member-two.csv",
                "allocation-member-two",
            )
            member_two_tier2 = replace(
                tier2,
                pran="SAMPLE-ACCOUNT-002",
                subscriber_name="Sample Member Two",
            )
            db.save_import(
                connection,
                member_two_tier2,
                "member-two-tier2.csv",
                "allocation-member-two-tier2",
            )

            portfolio = family_portfolio(connection)
            rows = allocation_summary(portfolio["positions"])
            by_name = {row["member"]: row for row in rows}
            expected_one = result.holdings_value + tier2.holdings_value
            self.assertAlmostEqual(
                by_name["Family total"]["current_value"],
                expected_one * 2,
                delta=0.1,
            )
            self.assertAlmostEqual(
                by_name[result.subscriber_name]["current_value"],
                expected_one,
                delta=0.1,
            )
            self.assertAlmostEqual(
                by_name[member_two.subscriber_name]["current_value"],
                expected_one,
                delta=0.1,
            )
            family = by_name["Family total"]
            bucket_table = _allocation_bucket_table(rows).controls[0]
            total_table = _allocation_total_table(rows).controls[0]
            self.assertEqual(len(bucket_table.columns), 6)
            self.assertEqual(len(total_table.columns), 6)
            self.assertEqual(len(bucket_table.rows), 6)

            summary_table = _holdings_summary_table(
                db.current_holding_rows(connection),
                include_family=True,
            )
            self.assertEqual(len(summary_table.rows), 3)
            self.assertEqual(summary_table.rows[0].cells[0].content.value, "Family")
            for asset in ("Equity", "Corporate", "Government"):
                self.assertGreater(family["asset_totals"][asset]["current_value"], 0)
                self.assertAlmostEqual(
                    family["asset_totals"][asset]["current_value"],
                    sum(
                        position["current_value"]
                        for position in portfolio["positions"]
                        if position["asset_class"] == asset
                    ),
                )
            self.assertAlmostEqual(
                sum(
                    value["current_value"]
                    for value in family["categories"].values()
                ),
                family["current_value"],
            )
            self.assertEqual(len(portfolio["reported_xirr"]), 4)
            connection.close()

    def test_account_xirr_uses_multi_year_transaction_history(self):
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "portfolio.db")
            previous = _sample_result("nps_tier1", "2024-25")
            current = _sample_result("nps_tier1", "2025-26")
            db.save_import(connection, previous, "previous.csv", "xirr-previous")
            db.save_import(connection, current, "current.csv", "xirr-current")

            portfolio = family_portfolio(connection)
            account_return = next(
                row
                for row in portfolio["calculated_xirr"]
                if row["member_id"] == current.pran
                and row["tier"] == current.tier
            )
            self.assertIsNotNone(account_return["calculated_xirr"])
            statement_return = next(
                row
                for row in portfolio["reported_xirr"]
                if row["member_id"] == current.pran
                and row["tier"] == current.tier
            )
            self.assertEqual(statement_return["reported_xirr"], current.xirr)
            self.assertTrue(
                all(
                    position["calculated_xirr"] is not None
                    for position in portfolio["positions"]
                )
            )
            connection.close()

    def test_icici_disclosure_parses_stocks_and_bonds_from_xlsx(self):
        equity_sheet = pd.DataFrame(
            [
                ["Name of the Pension Fund", "ICICI Pension Fund"],
                ["Name of the scheme", "Scheme E- Tier I"],
                ["Portfolio Statement as on", datetime(2026, 9, 30)],
                [""] * 7,
                [""] * 7,
                ["Particulars", "ISIN No.", "Industry", "Quantity", "Market Value", "% of Portfolio", "Rating"],
                ["Equity Instruments"],
                ["Shares"],
                ["Sample Equity Ltd", "INE000A01000", "Technology", 10, 600.0, 0.6, ""],
                ["Subtotal", "", "", 10, 600.0, 0.6, ""],
                ["Debt Instruments"],
                ["Sample Corporate Bond", "INE000B01000", "Finance", 4, 300.0, 0.3, "AAA"],
                ["Cash/Cash equivalent & Net Current Assets", "", "", "", 100.0, 0.1, ""],
                ["GRAND TOTAL", "", "", 14, 1000.0, 1.0, ""],
                ["Equity", "", "", "", "", "", ""],
                ["Duplicate summary row", "", "", "", 600.0, 0.6, ""],
            ]
        )
        government_sheet = pd.DataFrame(
            [
                ["Name of the Pension Fund", "ICICI Pension Fund"],
                ["Name of the scheme", "Scheme G- Tier I"],
                ["Portfolio Statement as on", datetime(2026, 9, 30)],
                [""] * 7,
                [""] * 7,
                ["Particulars", "ISIN No.", "Industry", "Quantity", "Market Value", "% of Portfolio", "Rating"],
                ["Government Securities"],
                ["Central Government Securities"],
                ["7% GS 2030", "IN0020260001", "Government", 10, 1000.0, 1.0, "SOV"],
                ["GRAND TOTAL", "", "", 10, 1000.0, 1.0, ""],
            ]
        )
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            equity_sheet.to_excel(writer, sheet_name="Scheme E - Tier l", index=False, header=False)
            government_sheet.to_excel(writer, sheet_name="Scheme G - Tier l", index=False, header=False)

        result = parse_icici_disclosure(buffer.getvalue(), "portfolio.xlsx")
        self.assertEqual(result.as_of_date, date(2026, 9, 30))
        self.assertEqual(len(result.positions), 4)
        equity = next(row for row in result.positions if row.isin == "INE000A01000")
        corporate = next(row for row in result.positions if row.isin == "INE000B01000")
        government = next(row for row in result.positions if row.isin == "IN0020260001")
        self.assertEqual(equity.instrument_type, "Equity")
        self.assertEqual(corporate.instrument_type, "Corporate bond")
        self.assertEqual(government.instrument_type, "Government bond")
        self.assertAlmostEqual(equity.portfolio_weight, 0.6)
        self.assertAlmostEqual(corporate.portfolio_weight, 0.3)

    def test_hdfc_disclosure_parses_current_month_workbook(self):
        result = parse_hdfc_disclosure(_hdfc_workbook(), "monthly.xlsx")
        self.assertEqual(result.fund_manager, "HDFC")
        self.assertEqual(result.as_of_date, date(2026, 9, 30))
        self.assertEqual(
            {(row.scheme_asset, row.tier) for row in result.positions},
            {("E", "nps_tier1"), ("G", "nps_tier2")},
        )
        government = next(row for row in result.positions if row.isin == "IN0020260001")
        self.assertEqual(government.instrument_type, "Government bond")

    def test_lic_disclosure_parses_single_scheme_file_and_stops_at_total(self):
        result = parse_lic_disclosure(
            _lic_workbook(),
            ("E", "nps_tier1"),
            "scheme.xlsx",
        )
        self.assertEqual(result.fund_manager, "LIC")
        self.assertEqual(result.as_of_date, date(2026, 9, 30))
        self.assertEqual(len(result.positions), 4)
        self.assertEqual(
            {row.instrument_type for row in result.positions},
            {"Equity", "Corporate bond", "Government bond", "Money market"},
        )
        self.assertAlmostEqual(
            sum(row.portfolio_weight for row in result.positions),
            1.0,
        )

    def test_security_disclosure_storage_and_account_lookthrough(self):
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "portfolio.db")
            disclosure = parse_icici_disclosure(
                _security_workbook(),
                "portfolio.xlsx",
            )
            save_security_disclosure(connection, disclosure)
            save_security_disclosure(
                connection,
                parse_hdfc_disclosure(_hdfc_workbook(), "monthly.xlsx"),
            )
            save_security_disclosure(
                connection,
                parse_lic_disclosure(
                    _lic_workbook(),
                    ("E", "nps_tier1"),
                    "scheme.xlsx",
                ),
            )
            saved = db.latest_security_disclosures(connection)
            holdings = [
                {
                    "scheme_name": "ICICI PENSION FUND SCHEME E - TIER I",
                    "tier": "nps_tier1",
                    "member_id": "SAMPLE-ACCOUNT-001",
                    "units": 10.0,
                    "latest_nav": 100.0,
                    "statement_nav": 100.0,
                },
                {
                    "scheme_name": "ICICI PENSION FUND SCHEME E - TIER I",
                    "tier": "nps_tier1",
                    "member_id": "SAMPLE-ACCOUNT-002",
                    "units": 5.0,
                    "latest_nav": 100.0,
                    "statement_nav": 100.0,
                },
                {
                    "scheme_name": "HDFC PENSION FUND SCHEME E - TIER I",
                    "tier": "nps_tier1",
                    "member_id": "SAMPLE-ACCOUNT-003",
                    "units": 10.0,
                    "latest_nav": 100.0,
                    "statement_nav": 100.0,
                },
                {
                    "scheme_name": "LIC PENSION FUND SCHEME E - TIER I",
                    "tier": "nps_tier1",
                    "member_id": "SAMPLE-ACCOUNT-004",
                    "units": 10.0,
                    "latest_nav": 100.0,
                    "statement_nav": 100.0,
                },
            ]
            family = build_security_exposure(holdings, saved)
            first_member = build_security_exposure(
                holdings,
                saved,
                member_id="SAMPLE-ACCOUNT-001",
            )
            self.assertAlmostEqual(family["total_value"], 3500)
            self.assertAlmostEqual(family["covered_value"], 3500)
            self.assertAlmostEqual(family["coverage"], 1.0)
            self.assertAlmostEqual(family["positions"][0]["exposure"], 2500)
            self.assertAlmostEqual(
                sum(position["exposure"] for position in family["positions"]),
                family["covered_value"],
            )
            self.assertAlmostEqual(first_member["positions"][0]["exposure"], 600)
            self.assertEqual(first_member["disclosure_date"], "2026-09-30")
            connection.close()

    def test_portfolio_ui_filters_member_tier_scheme_and_year(self):
        app = object.__new__(PortfolioApp)
        app.selected_member = "SAMPLE-ACCOUNT-001"
        app.selected_tier = "nps_tier1"
        app.selected_scheme = "ICICI SCHEME E"
        app.selected_financial_year = "2025-26"
        rows = [
            {
                "member_id": "SAMPLE-ACCOUNT-001",
                "tier": "nps_tier1",
                "scheme_name": "ICICI SCHEME E",
                "financial_year": "2025-26",
            },
            {
                "member_id": "SAMPLE-ACCOUNT-001",
                "tier": "nps_tier2",
                "scheme_name": "ICICI SCHEME E",
                "financial_year": "2025-26",
            },
            {
                "member_id": "SAMPLE-ACCOUNT-002",
                "tier": "nps_tier1",
                "scheme_name": "ICICI SCHEME E",
                "financial_year": "2025-26",
            },
            {
                "member_id": "SAMPLE-ACCOUNT-001",
                "tier": "nps_tier1",
                "scheme_name": "ICICI SCHEME C",
                "financial_year": "2025-26",
            },
            {
                "member_id": "SAMPLE-ACCOUNT-001",
                "tier": "nps_tier1",
                "scheme_name": "ICICI SCHEME E",
                "financial_year": "2024-25",
            },
        ]
        self.assertEqual(app._filter_rows(rows), rows[:1])


if __name__ == "__main__":
    unittest.main()
