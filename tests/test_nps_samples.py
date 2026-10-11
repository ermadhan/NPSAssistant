import csv
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from portfolio_app import db  # noqa: E402
from portfolio_app.nav import FundNav, match_scheme  # noqa: E402
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
)

SAMPLES = {
    "SAMPLE-ACCOUNT-001_Tier1.csv": ("nps_tier1", "2026-27", 7163296.15, None),
    "SAMPLE-ACCOUNT-001_Tier2.csv": ("nps_tier2", "2026-27", 1302905.98, None),
    "SAMPLE-ACCOUNT-001_Tier1_2025_26.csv": ("nps_tier1", "2025-26", 6417228.60, "2025-26"),
    "SAMPLE-ACCOUNT-001_Tier2_2025_26.csv": ("nps_tier2", "2025-26", 1144510.56, "2025-26"),
    "SAMPLE-ACCOUNT-001_Tier1_2024_25.csv": ("nps_tier1", "2024-25", 5793659.41, "2024-25"),
    "SAMPLE-ACCOUNT-001_Tier2_2024_25.csv": ("nps_tier2", "2024-25", 972941.40, "2024-25"),
}

FUNDS = [
    FundNav("SM007001", "ICICI PRUDENTIAL PENSION FUND SCHEME E - TIER I", 1, financial_year_date := __import__("datetime").date(2026, 9, 25)),
    FundNav("SM007002", "ICICI PRUDENTIAL PENSION FUND SCHEME C - TIER I", 1, financial_year_date),
    FundNav("SM007003", "ICICI PRUDENTIAL PENSION FUND SCHEME G - TIER I", 1, financial_year_date),
    FundNav("SM007004", "ICICI PRUDENTIAL PENSION FUND SCHEME E - TIER II", 1, financial_year_date),
    FundNav("SM007005", "ICICI PRUDENTIAL PENSION FUND SCHEME C - TIER II", 1, financial_year_date),
    FundNav("SM007006", "ICICI PRUDENTIAL PENSION FUND SCHEME G - TIER II", 1, financial_year_date),
    FundNav("SM007010", "ICICI PRUDENTIAL PENSION FUNDS SCHEME TAX SAVER TIER II", 1, financial_year_date),
]


class SampleTests(unittest.TestCase):
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
        for name, (tier, year, total, filename_year) in SAMPLES.items():
            result = parse_nps_file(ROOT / name)
            parsed[name] = result
            self.assertFalse(result.needs_header_map, result.header_mapping)
            self.assertEqual(result.unknown_narrations, [])
            self.assertEqual(result.warnings, [])
            self.assertEqual(result.tier, tier)
            self.assertEqual(result.financial_year, year)
            self.assertEqual(result.filename_year, filename_year)
            self.assertFalse(result.year_mismatch)
            self.assertAlmostEqual(result.holdings_value, total, places=2)
            self.assertAlmostEqual(sum(row.market_value for row in result.holdings), total, places=2)
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
            for name in ("SAMPLE-ACCOUNT-001_Tier1_2024_25.csv", "SAMPLE-ACCOUNT-001_Tier1.csv"):
                path = ROOT / name
                result = parse_nps_file(path)
                save_statement(connection, path, result, confirm_year=False, narration_map={})
            refresh_navs(connection, FUNDS)
            rows = db.current_holding_rows(connection)
            self.assertEqual({row["financial_year"] for row in rows}, {"2026-27"})
            self.assertTrue(all(row["scheme_code"] for row in rows))
            target = Path(folder) / "holdings.csv"
            export_holdings(connection, target)
            self.assertIn("SM007001", target.read_text(encoding="utf-8"))
            workbook = Path(folder) / "transactions.xlsx"
            export_transactions(connection, workbook)
            self.assertTrue(workbook.stat().st_size > 0)
            connection.close()

    def test_current_holdings_are_isolated_by_subscriber(self):
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "portfolio.db")
            result = parse_nps_file(ROOT / "SAMPLE-ACCOUNT-001_Tier1.csv")
            db.save_import(connection, result, "member-one.csv", "hash-member-one")

            member_two = replace(result, pran="member_two-PRAN", subscriber_name="member_two")
            db.save_import(connection, member_two, "member_two.csv", "hash-member_two")
            newer_wife = replace(member_two, as_of_date=date(2026, 10, 1))
            db.save_import(connection, newer_wife, "member_two-new.csv", "hash-member_two-new")

            rows = db.current_holding_rows(connection)
            self.assertEqual(
                {row["subscriber_name"] for row in rows},
                {result.subscriber_name, "member_two"},
            )
            self.assertEqual(
                {row["subscriber_name"] for row in db.list_years(connection)},
                {result.subscriber_name, "member_two"},
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM imports WHERE is_current = 1"
                ).fetchone()[0],
                2,
            )
            holdings_path = Path(folder) / "member_two-holdings.csv"
            export_holdings(connection, holdings_path, member_id="member_two-PRAN")
            with holdings_path.open(encoding="utf-8", newline="") as file:
                self.assertEqual(
                    {row["subscriber"] for row in csv.DictReader(file)},
                    {"member_two"},
                )
            transactions_path = Path(folder) / "member_two-transactions.csv"
            export_transactions(connection, transactions_path, member_id="member_two-PRAN")
            with transactions_path.open(encoding="utf-8", newline="") as file:
                self.assertEqual(
                    {row["subscriber"] for row in csv.DictReader(file)},
                    {"member_two"},
                )
            connection.close()


if __name__ == "__main__":
    unittest.main()
