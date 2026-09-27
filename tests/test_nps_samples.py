import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from portfolio_app import db  # noqa: E402
from portfolio_app.nav import FundNav, match_scheme  # noqa: E402
from portfolio_app.parsers.nps import financial_year_for, parse_nps_file  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
