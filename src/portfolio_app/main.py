from __future__ import annotations

import sqlite3
from pathlib import Path

import flet as ft

from portfolio_app import db
from portfolio_app.service import (
    ImportRejected,
    export_holdings,
    export_transactions,
    parse_statement,
    refresh_navs,
    save_header_profile,
    save_statement,
)

NARRATION_KINDS = (
    "opening",
    "closing",
    "billing",
    "voluntary",
    "employer",
    "tier2",
    "switch_out",
    "switch_in",
    "other",
)
CASH_KINDS = {"billing", "voluntary", "employer", "tier2", "switch_out", "switch_in", "other"}


def main() -> None:
    ft.run(start)


def start(page: ft.Page) -> None:
    page.title = "NPS portfolio"
    page.window.width = 1100
    page.window.height = 760
    page.padding = 16
    page.scroll = ft.ScrollMode.AUTO
    picker = ft.FilePicker()
    connection = db.connect()
    app = PortfolioApp(page, connection, picker)
    page.add(app.toolbar, app.status, app.body)
    app.show_holdings()


class PortfolioApp:
    def __init__(self, page: ft.Page, connection: sqlite3.Connection, picker: ft.FilePicker):
        self.page = page
        self.connection = connection
        self.picker = picker
        self.pending_path: Path | None = None
        self.pending = None
        self.selected_import: int | None = None
        self.catalog = []
        self.narration_inputs: dict[str, ft.Dropdown] = {}
        self.header_inputs: dict[tuple[str, str], ft.Dropdown] = {}
        self.confirm_year = ft.Checkbox(label="The filename year and the dates disagree. Save using the dates.", value=False)
        self.status = ft.Text("")
        self.body = ft.Column(spacing=12)
        self.toolbar = ft.Row(
            wrap=True,
            controls=[
                ft.FilledButton("Holdings", on_click=self.show_holdings),
                ft.OutlinedButton("Load statement", on_click=self.load_statement),
                ft.OutlinedButton("Refresh NAVs", on_click=self.refresh_prices),
                ft.OutlinedButton("Export holdings", on_click=self.export_holdings_file),
                ft.OutlinedButton("Export transactions", on_click=self.export_transactions_file),
            ],
        )

    def show_holdings(self, _event=None) -> None:
        self.selected_import = None
        self._render_portfolio()

    def _render_portfolio(self) -> None:
        years = db.list_years(self.connection)
        if self.selected_import is None:
            rows = db.current_holding_rows(self.connection)
            title = "Current units"
        else:
            rows = db.holdings_for_import(self.connection, self.selected_import)
            title = "Statement holdings"
        controls: list = [ft.Text(title, size=22, weight=ft.FontWeight.W_600)]
        if years:
            controls.append(
                ft.Row(
                    wrap=True,
                    controls=[ft.Text("Year")]
                    + [
                        ft.TextButton(
                            f"{_tier_label(row['tier'])} {row['financial_year']}",
                            data=row["id"],
                            on_click=self._select_year,
                        )
                        for row in years
                    ]
                    + [ft.TextButton("Latest", on_click=self.show_holdings)],
                )
            )
        if not rows:
            controls.append(ft.Text("Load a transaction statement CSV downloaded from the NPS website."))
        else:
            for tier in ("nps_tier1", "nps_tier2"):
                tier_rows = [row for row in rows if row["tier"] == tier]
                if not tier_rows:
                    continue
                total = sum(_row_value(row) for row in tier_rows)
                controls.append(ft.Text(f"{_tier_label(tier)}  Rs {total:,.2f}", size=18))
                controls.append(_holding_table(tier_rows))
            controls.append(ft.Text(f"Combined  Rs {sum(_row_value(row) for row in rows):,.2f}", size=18))
            missing = [row["scheme_name"] for row in rows if not row["latest_nav"]]
            if missing:
                controls.append(ft.Text("Latest NAV is not stored yet for: " + "; ".join(missing)))
        if self.selected_import is not None:
            controls.extend(self._movement_section(self.selected_import))
        controls.extend(self._scheme_code_section(rows))
        self.body.controls = controls
        self.page.update()

    def _movement_section(self, import_id: int) -> list:
        rows = db.transactions_for_import(self.connection, import_id)
        cash = sum(float(row["amount"] or 0) for row in rows if row["narration_kind"] in CASH_KINDS)
        table_rows = []
        for row in rows:
            table_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(row["txn_date"]),
                        ft.DataCell(row["scheme_name"]),
                        ft.DataCell(row["narration_kind"]),
                        ft.DataCell(row["description"]),
                        ft.DataCell("" if row["amount"] is None else f"{row['amount']:,.2f}"),
                        ft.DataCell("" if row["units"] is None else f"{row['units']:,.4f}"),
                    ]
                )
            )
        return [
            ft.Text(f"Scheme movements. Cash-flow total excludes opening and closing balances: Rs {cash:,.2f}"),
            ft.DataTable(
                columns=[
                    ft.DataColumn("Date"),
                    ft.DataColumn("Scheme"),
                    ft.DataColumn("Kind"),
                    ft.DataColumn("Description"),
                    ft.DataColumn("Amount", numeric=True),
                    ft.DataColumn("Units", numeric=True),
                ],
                rows=table_rows,
            ),
        ]

    def _scheme_code_section(self, rows) -> list:
        pending = sorted({row["scheme_name"] for row in rows if not row["scheme_code"]})
        if not pending:
            return []
        if not self.catalog:
            return [ft.Text("Refresh NAVs, then choose a scheme code for any name that did not match.")]
        options = [
            ft.DropdownOption(key=fund.scheme_code, text=f"{fund.scheme_code}  {fund.scheme_name}")
            for fund in self.catalog
        ]
        controls = [ft.Text("Choose a scheme code")]
        for name in pending:
            dropdown = ft.Dropdown(label=name, options=options, width=640)
            controls.append(dropdown)
            controls.append(
                ft.FilledButton(
                    "Save code",
                    data=(name, dropdown),
                    on_click=self._save_scheme_code,
                )
            )
        return controls

    def _save_scheme_code(self, event) -> None:
        name, dropdown = event.control.data
        if not dropdown.value:
            self._message("Choose a scheme code first.")
            return
        db.set_scheme_code(self.connection, name, dropdown.value)
        self._message(f"Saved {dropdown.value} for {name}.")
        self._render_portfolio()

    def _select_year(self, event) -> None:
        self.selected_import = event.control.data
        self._render_portfolio()

    async def load_statement(self, _event=None) -> None:
        files = await self.picker.pick_files(
            dialog_title="NPS transaction statement",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["csv"],
        )
        if not files or not files[0].path:
            return
        path = Path(files[0].path)
        try:
            result = parse_statement(self.connection, path)
        except Exception as error:
            self._message(str(error))
            return
        self.pending_path = path
        self.pending = result
        self._show_review()

    def _show_review(self) -> None:
        result = self.pending
        self.narration_inputs = {}
        self.header_inputs = {}
        controls = [
            ft.Text(f"Review {self.pending_path.name}", size=22, weight=ft.FontWeight.W_600),
            ft.Text(
                f"{_tier_label(result.tier)}  {result.financial_year}  "
                f"as on {result.as_of_date.isoformat()}  "
                f"holdings Rs {result.holdings_value:,.2f}"
            ),
            ft.Text(
                f"{len(result.holdings)} schemes, {len(result.contributions)} contributions, "
                f"{len(result.transactions)} scheme rows. "
                "Contribution totals are kept separate from scheme movements."
            ),
        ]
        if result.holdings:
            controls.append(
                ft.DataTable(
                    columns=[
                        ft.DataColumn("Scheme"),
                        ft.DataColumn("Units", numeric=True),
                        ft.DataColumn("Statement NAV", numeric=True),
                        ft.DataColumn("Value", numeric=True),
                    ],
                    rows=[
                        ft.DataRow(
                            cells=[
                                ft.DataCell(row.scheme_name),
                                ft.DataCell(f"{row.units:,.4f}"),
                                ft.DataCell(f"{row.statement_nav:,.4f}"),
                                ft.DataCell(f"{row.market_value:,.2f}"),
                            ]
                        )
                        for row in result.holdings
                    ],
                )
            )
        if result.year_mismatch:
            self.confirm_year = ft.Checkbox(
                label="The filename year and the dates disagree. Save using the dates.",
                value=False,
            )
            controls.append(
                ft.Text(
                    f"Filename says {result.filename_year}. Dates say {result.financial_year}."
                )
            )
            controls.append(self.confirm_year)
        if result.header_mapping is not None:
            controls.append(ft.Text("These headers are not in the known CRA layout. Map each missing field."))
            for section, fields in result.header_mapping.missing_fields.items():
                headers = result.header_mapping.section_headers.get(section, [])
                options = [ft.DropdownOption(key=header, text=header) for header in headers]
                for field_name in fields:
                    dropdown = ft.Dropdown(label=f"{section}.{field_name}", options=options, width=640)
                    self.header_inputs[(section, field_name)] = dropdown
                    controls.append(dropdown)
        if result.unknown_narrations:
            controls.append(ft.Text("New narration text. Choose a kind. It will be reused next time."))
            options = [ft.DropdownOption(key=kind, text=kind) for kind in NARRATION_KINDS]
            for phrase in result.unknown_narrations:
                dropdown = ft.Dropdown(label=phrase, options=options, width=640)
                self.narration_inputs[_norm(phrase)] = dropdown
                controls.append(dropdown)
        for warning in result.warnings:
            controls.append(ft.Text(warning))
        controls.append(ft.FilledButton("Save statement", on_click=self.save_review))
        self.body.controls = controls
        self.page.update()

    def save_review(self, _event=None) -> None:
        result = self.pending
        path = self.pending_path
        if result is None or path is None:
            return
        if result.needs_header_map:
            extra: dict[str, dict[str, list[str]]] = {}
            for (section, field_name), dropdown in self.header_inputs.items():
                if not dropdown.value:
                    self._message(f"Map {section}.{field_name} before saving.")
                    return
                extra.setdefault(section, {})[field_name] = [dropdown.value]
            save_header_profile(self.connection, result.fingerprint, extra)
            try:
                self.pending = parse_statement(self.connection, path)
            except Exception as error:
                self._message(str(error))
                return
            self._message("Header mapping saved. Review the reading again, then save.")
            self._show_review()
            return
        narration_map = {}
        for phrase, dropdown in self.narration_inputs.items():
            if not dropdown.value:
                self._message("Choose a kind for each new narration.")
                return
            narration_map[phrase] = dropdown.value
        try:
            save_statement(
                self.connection,
                path,
                result,
                confirm_year=bool(self.confirm_year.value),
                narration_map=narration_map,
            )
        except ImportRejected as error:
            self._message(str(error))
            return
        self.pending = None
        self.pending_path = None
        self._message(f"Saved {path.name}.")
        self.show_holdings()

    async def refresh_prices(self, _event=None) -> None:
        self._message("Fetching NAVs from npsnav.in...")
        try:
            outcome = refresh_navs(self.connection)
        except Exception as error:
            self._message(f"NAV fetch failed: {error}")
            return
        self.catalog = outcome["catalog"]
        self._message(
            f"Linked {len(outcome['linked'])}. "
            f"Unmatched {len(outcome['unmatched'])}. "
            f"Ambiguous {len(outcome['ambiguous'])}."
        )
        self._render_portfolio()

    async def export_holdings_file(self, _event=None) -> None:
        target = await self.picker.save_file(
            dialog_title="Export holdings",
            file_name="holdings.xlsx",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx", "csv"],
        )
        if not target:
            return
        export_holdings(self.connection, Path(target), self.selected_import)
        self._message(f"Wrote {target}")

    async def export_transactions_file(self, _event=None) -> None:
        target = await self.picker.save_file(
            dialog_title="Export transactions",
            file_name="transactions.xlsx",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx", "csv"],
        )
        if not target:
            return
        export_transactions(self.connection, Path(target), self.selected_import)
        self._message(f"Wrote {target}")

    def _message(self, text: str) -> None:
        self.status.value = text
        self.page.update()


def _tier_label(tier: str) -> str:
    return "Tier 1 pension" if tier == "nps_tier1" else "Tier 2"


def _row_value(row) -> float:
    price = row["latest_nav"] if row["latest_nav"] is not None else row["statement_nav"]
    return float(row["units"]) * float(price)


def _holding_table(rows) -> ft.DataTable:
    return ft.DataTable(
        columns=[
            ft.DataColumn("Scheme"),
            ft.DataColumn("Units", numeric=True),
            ft.DataColumn("Statement NAV", numeric=True),
            ft.DataColumn("Latest NAV", numeric=True),
            ft.DataColumn("Value", numeric=True),
        ],
        rows=[
            ft.DataRow(
                cells=[
                    ft.DataCell(row["scheme_name"]),
                    ft.DataCell(f"{row['units']:,.4f}"),
                    ft.DataCell(f"{row['statement_nav']:,.4f}"),
                    ft.DataCell("" if row["latest_nav"] is None else f"{row['latest_nav']:,.4f}"),
                    ft.DataCell(f"{_row_value(row):,.2f}"),
                ]
            )
            for row in rows
        ],
    )


def _norm(value: str) -> str:
    return " ".join(value.lower().split())
