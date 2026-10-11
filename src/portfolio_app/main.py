from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import flet as ft

from portfolio_app import db
from portfolio_app.analytics import (
    allocation_summary,
    family_portfolio,
)
from portfolio_app.service import (
    ImportRejected,
    export_holdings,
    export_transactions,
    parse_statement,
    refresh_navs,
    save_header_profile,
    save_statement,
)
from portfolio_app.nav import fetch_latest

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
    page.title = "NPS Family Portfolio"
    page.window.width = 1420
    page.window.height = 900
    page.padding = 0
    page.bgcolor = "#F3F5F9"
    page.theme_mode = ft.ThemeMode.LIGHT
    picker = ft.FilePicker()
    connection = db.connect()
    app = PortfolioApp(page, connection, picker)
    page.appbar = ft.AppBar(
        title=ft.Text("NPS Family Portfolio", weight=ft.FontWeight.W_600),
        bgcolor="#FFFFFF",
        actions=app.toolbar_buttons,
    )
    page.add(app.layout)
    app.show_holdings()


class PortfolioApp:
    def __init__(self, page: ft.Page, connection: sqlite3.Connection, picker: ft.FilePicker):
        self.page = page
        self.connection = connection
        self.picker = picker
        self.pending_path: Path | None = None
        self.pending = None
        self.selected_import: int | None = None
        self.selected_member: str | None = None
        self.active_view = "overview"
        self.catalog = []
        self.narration_inputs: dict[str, ft.Dropdown] = {}
        self.header_inputs: dict[tuple[str, str], ft.Dropdown] = {}
        self.busy = False
        self.confirm_year = ft.Checkbox(label="The filename year and the dates disagree. Save using the dates.", value=False)
        self.status = ft.Text("")
        self.progress = ft.ProgressRing(visible=False, width=18, height=18, stroke_width=2)
        self.status_area = ft.Row(controls=[self.progress, self.status], spacing=8)
        self.body = ft.Column(spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        self.toolbar_buttons = [
            ft.TextButton("Load statement", icon=ft.Icons.UPLOAD_FILE, on_click=self.load_statement),
            ft.TextButton("Refresh NAVs", icon=ft.Icons.REFRESH, on_click=self.refresh_prices),
            ft.TextButton("Export holdings", icon=ft.Icons.DOWNLOAD, on_click=self.export_holdings_file),
            ft.TextButton("Export transactions", icon=ft.Icons.RECEIPT_LONG, on_click=self.export_transactions_file),
        ]
        self.navigation = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=96,
            destinations=[
                ft.NavigationRailDestination(icon=ft.Icons.DASHBOARD_OUTLINED, selected_icon=ft.Icons.DASHBOARD, label="Overview"),
                ft.NavigationRailDestination(icon=ft.Icons.PIE_CHART_OUTLINE, selected_icon=ft.Icons.PIE_CHART, label="Allocation"),
                ft.NavigationRailDestination(icon=ft.Icons.ACCOUNT_BALANCE_OUTLINED, selected_icon=ft.Icons.ACCOUNT_BALANCE, label="Securities"),
                ft.NavigationRailDestination(icon=ft.Icons.HISTORY, label="Statements"),
            ],
            on_change=self._navigate,
        )
        self.status_area = ft.Container(
            content=self.status_area,
            padding=ft.Padding(left=4, right=4, bottom=8),
        )
        self.layout = ft.Row(
            controls=[
                self.navigation,
                ft.VerticalDivider(width=1, color="#E1E5ED"),
                ft.Container(
                    content=ft.Column(
                        controls=[
                            self.status_area,
                            ft.Container(content=self.body, expand=True),
                        ],
                        expand=True,
                    ),
                    padding=24,
                    expand=True,
                ),
            ],
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            spacing=0,
        )

    def _navigate(self, event) -> None:
        self.selected_import = None
        self.active_view = ("overview", "allocation", "securities", "statements")[
            event.control.selected_index
        ]
        if self.active_view == "overview":
            self.selected_member = None
            self.status.value = "Showing the family portfolio."
        else:
            self.status.value = f"Showing {self.active_view}."
        self._render_portfolio()

    def show_holdings(self, _event=None) -> None:
        self.selected_import = None
        self.selected_member = None
        self.active_view = "overview"
        self.navigation.selected_index = 0
        self.status.value = "Showing current holdings."
        self._render_portfolio()

    def _render_portfolio(self) -> None:
        if self.active_view == "allocation":
            self._render_allocation()
            return
        if self.active_view == "securities":
            self._render_securities()
            return
        if self.active_view == "statements" and self.selected_import is None:
            years = db.list_years(self.connection)
            self.body.controls = [
                ft.Text("Statement history", size=26, weight=ft.FontWeight.W_600),
                ft.Text("Choose an imported statement to review its holdings and transactions."),
                *[
                    ft.OutlinedButton(
                        f"{row['subscriber_name'] or 'NPS subscriber'} · "
                        f"{_tier_label(row['tier'])} · {row['financial_year']} · "
                        f"{row['as_of_date']}",
                        data=row["id"],
                        on_click=self._select_year,
                    )
                    for row in years
                ],
            ]
            if not years:
                self.body.controls.append(
                    ft.Text("No statements imported yet. Use Load statement to begin.")
                )
            self.page.update()
            return
        years = db.list_years(self.connection)
        if self.selected_import is None:
            rows = db.current_holding_rows(self.connection)
            title = "Family portfolio" if self.selected_member is None else "Member portfolio"
            current_rows = rows
        else:
            rows = db.holdings_for_import(self.connection, self.selected_import)
            title = "Statement holdings"
            current_rows = []
        if self.selected_import is None and self.selected_member is not None:
            rows = [row for row in rows if row["member_id"] == self.selected_member]
        controls: list = [ft.Text(title, size=22, weight=ft.FontWeight.W_600)]
        position_metrics = {}
        if self.selected_import is None and current_rows:
            analytics = family_portfolio(self.connection)
            position_metrics = {
                (item["member_id"], item["tier"], item["scheme_name"]): item
                for item in analytics["positions"]
            }
            scoped = [
                row
                for row in analytics["positions"]
                if self.selected_member is None
                or row["member_id"] == self.selected_member
            ]
            net_invested = sum(row["net_invested"] or 0 for row in scoped)
            incomplete = any(not row["invested_complete"] for row in scoped)
            calculated = [
                item
                for item in analytics["calculated_xirr"]
                if self.selected_member is None or item["member_id"] == self.selected_member
            ]
            reported = [
                item
                for item in analytics["reported_xirr"]
                if self.selected_member is None or item["member_id"] == self.selected_member
            ]
            controls.append(
                ft.Row(
                    wrap=True,
                    controls=[
                        _metric_card(
                            "Current value",
                            f"Rs {sum(row['current_value'] for row in scoped):,.2f}",
                            "Priced at latest NAV where available",
                        ),
                        _metric_card(
                            "Net invested",
                            f"Rs {net_invested:,.2f}" + (" *" if incomplete else ""),
                            "From statement contributions and scheme movements",
                        ),
                        _metric_card(
                            "Calculated XIRR",
                            _format_returns(calculated, "calculated_xirr"),
                            "Cash-flow return for imported coverage",
                        ),
                        _metric_card(
                            "Statement XIRR",
                            _format_returns(reported, "reported_xirr"),
                            "Latest reported Tier 1 / Tier 2 returns",
                        ),
                    ],
                )
            )
            if incomplete:
                controls.append(
                    ft.Text(
                        "* Net invested is partial where the earliest imported statement does not report scheme-level net contributions.",
                        color="#6B7280",
                    )
                )
        if self.selected_import is None:
            members = {}
            for row in current_rows:
                members.setdefault(row["member_id"], row["subscriber_name"])
            controls.append(
                ft.Row(
                    wrap=True,
                    controls=[
                        ft.TextButton("Family", on_click=self.show_holdings),
                        *[
                            ft.TextButton(
                                name or "NPS subscriber",
                                data=member_id,
                                on_click=self._select_member,
                            )
                            for member_id, name in members.items()
                        ],
                    ],
                )
            )
        if years:
            controls.append(
                ft.Row(
                    wrap=True,
                    controls=[ft.Text("Year")]
                    + [
                        ft.TextButton(
                            f"{row['subscriber_name'] or 'NPS subscriber'} "
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
        elif self.selected_import is None and self.selected_member is not None:
            self._append_member_holdings(controls, rows, position_metrics)
        else:
            if self.selected_import is None:
                controls.append(ft.Text("Consolidated across family accounts", size=18, weight=ft.FontWeight.W_600))
                self._append_tier_totals(controls, rows, include_combined=True)
                members = {}
                for row in rows:
                    members.setdefault(row["member_id"], []).append(row)
                for member_rows in members.values():
                    name = member_rows[0]["subscriber_name"] or "NPS subscriber"
                    controls.append(ft.Text(name, size=18, weight=ft.FontWeight.W_600))
                    self._append_member_holdings(
                        controls,
                        member_rows,
                        position_metrics,
                    )
            else:
                controls.append(ft.Text(rows[0]["subscriber_name"] or "NPS subscriber", size=18))
                self._append_member_holdings(controls, rows)
            missing = [row["scheme_name"] for row in rows if not row["latest_nav"]]
            if missing:
                controls.append(ft.Text("Latest NAV is not stored yet for: " + "; ".join(missing)))
        if self.selected_import is not None:
            controls.extend(self._movement_section(self.selected_import))
        controls.extend(self._scheme_code_section(rows))
        self.body.controls = controls
        self.page.update()

    def _append_member_holdings(
        self,
        controls: list,
        rows,
        position_metrics: dict | None = None,
    ) -> None:
        self._append_tier_totals(controls, rows, include_combined=True)
        for tier in ("nps_tier1", "nps_tier2"):
            tier_rows = [row for row in rows if row["tier"] == tier]
            if tier_rows:
                controls.append(ft.Text(_tier_label(tier), size=16))
                metrics = {}
                for holding in tier_rows:
                    key = (
                        holding["member_id"],
                        holding["tier"],
                        holding["scheme_name"],
                    )
                    if key in (position_metrics or {}):
                        metrics[key] = position_metrics[key]
                controls.append(_holding_table(tier_rows, metrics))

    def _append_tier_totals(self, controls: list, rows, *, include_combined: bool) -> None:
        for tier in ("nps_tier1", "nps_tier2"):
            total = sum(_row_value(row) for row in rows if row["tier"] == tier)
            controls.append(ft.Text(f"{_tier_label(tier)}  Rs {total:,.2f}", size=18))
        if include_combined:
            controls.append(ft.Text(f"Combined  Rs {sum(_row_value(row) for row in rows):,.2f}", size=18))

    def _render_allocation(self) -> None:
        data = family_portfolio(self.connection)
        positions = data["positions"]
        if self.selected_member is not None:
            positions = [
                row for row in positions if row["member_id"] == self.selected_member
            ]
        rows = allocation_summary(positions)
        if self.selected_member is not None:
            rows = [row for row in rows if row["member_id"] == self.selected_member]
        controls = [
            ft.Text("Portfolio allocation", size=26, weight=ft.FontWeight.W_600),
            ft.Row(
                wrap=True,
                controls=[
                    ft.OutlinedButton("Family", on_click=self._select_family),
                    *[
                        ft.OutlinedButton(
                            member["name"],
                            data=member["member_id"],
                            on_click=self._select_member,
                        )
                        for member in data["members"]
                    ],
                ],
            ),
            ft.Text(
                "Scheme-level values from each statement and the latest available NAV. "
                "Net invested uses reported scheme contributions where available and dated transactions otherwise."
            ),
        ]
        if not positions:
            controls.append(
                ft.Text("Import NPS statements to see member and family allocations.")
            )
        else:
            controls.append(
                ft.Text(
                    "By tier and asset class",
                    size=19,
                    weight=ft.FontWeight.W_600,
                )
            )
            controls.append(_allocation_bucket_table(rows))
            controls.append(
                ft.Text(
                    "Asset-class and portfolio totals",
                    size=19,
                    weight=ft.FontWeight.W_600,
                )
            )
            controls.append(_allocation_total_table(rows))
            if any(not row["invested_complete"] for row in rows):
                controls.append(
                    ft.Text(
                        "Net invested is partial for members whose imported history does not include an initial scheme-contribution balance.",
                        color="#8A5A00",
                    )
                )
        self.body.controls = controls
        self.page.update()

    def _render_securities(self) -> None:
        self.body.controls = [
            ft.Text("Underlying stocks and bonds", size=26, weight=ft.FontWeight.W_600),
            ft.Text(
                "This view looks through NPS schemes to the securities disclosed by the pension fund. "
                "Exposures are estimates based on each scheme's current value and the latest published portfolio weights."
            ),
            ft.Container(
                content=ft.Text(
                    "Official disclosure sources and coverage are being verified. "
                    "Holdings will be shown with their disclosure date; unavailable fund data will be identified, not estimated from unrelated sources."
                ),
                bgcolor="#FFF8E6",
                padding=20,
                border_radius=12,
            ),
        ]
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
        self.selected_member = None
        self.status.value = "Showing selected statement."
        self._render_portfolio()

    def _select_member(self, event) -> None:
        self.selected_member = event.control.data
        self.status.value = "Showing selected NPS account."
        self._render_portfolio()

    def _select_family(self, _event=None) -> None:
        self.selected_member = None
        self.status.value = "Showing family allocation."
        self._render_portfolio()

    async def load_statement(self, _event=None) -> None:
        if not self._begin_busy("Choose an NPS statement CSV..."):
            return
        try:
            files = await self.picker.pick_files(
                dialog_title="NPS transaction statement",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["csv"],
            )
            if not files or not files[0].path:
                self.status.value = "Statement import cancelled."
                return
            path = Path(files[0].path)
            self.status.value = f"Reading {path.name}..."
            self.page.update()
            await asyncio.sleep(0)
            result = parse_statement(self.connection, path)
        except Exception as error:
            self.status.value = f"Could not load statement: {error}"
        else:
            self.pending_path = path
            self.pending = result
            self._show_review()
        finally:
            self._end_busy()

    def _show_review(self) -> None:
        result = self.pending
        self.narration_inputs = {}
        self.header_inputs = {}
        account = result.subscriber_name or "NPS subscriber"
        account += (
            f" (PRAN ending {result.pran[-4:]})"
            if result.pran
            else " (PRAN not detected; matched by subscriber name)"
        )
        controls = [
            ft.Text(f"Review {self.pending_path.name}", size=22, weight=ft.FontWeight.W_600),
            ft.Text(f"Account: {account}"),
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

    async def save_review(self, _event=None) -> None:
        if not self._begin_busy("Saving statement..."):
            return
        try:
            await asyncio.sleep(0)
            self._save_review()
        finally:
            self._end_busy()

    def _save_review(self) -> None:
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
        if not self._begin_busy("Fetching latest NAVs from npsnav.in..."):
            return
        try:
            catalog = await asyncio.to_thread(fetch_latest)
            self.status.value = "Saving NAVs and matching schemes..."
            self.page.update()
            outcome = refresh_navs(self.connection, catalog)
        except Exception as error:
            self.status.value = f"NAV refresh failed: {error}"
        else:
            self.catalog = outcome["catalog"]
            self.status.value = (
                f"Linked {len(outcome['linked'])}. "
                f"Unmatched {len(outcome['unmatched'])}. "
                f"Ambiguous {len(outcome['ambiguous'])}."
            )
            self._render_portfolio()
        finally:
            self._end_busy()

    async def export_holdings_file(self, _event=None) -> None:
        if not self._begin_busy("Choose where to save holdings..."):
            return
        try:
            target = await self.picker.save_file(
                dialog_title="Export holdings",
                file_name="holdings.xlsx",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["xlsx", "csv"],
            )
            if not target:
                self.status.value = "Holdings export cancelled."
                return
            self.status.value = "Writing holdings export..."
            self.page.update()
            await asyncio.sleep(0)
            export_holdings(
                self.connection,
                Path(target),
                self.selected_import,
                None if self.selected_import is not None else self.selected_member,
            )
            self.status.value = f"Wrote {target}"
        except Exception as error:
            self.status.value = f"Holdings export failed: {error}"
        finally:
            self._end_busy()

    async def export_transactions_file(self, _event=None) -> None:
        if not self._begin_busy("Choose where to save transactions..."):
            return
        try:
            target = await self.picker.save_file(
                dialog_title="Export transactions",
                file_name="transactions.xlsx",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["xlsx", "csv"],
            )
            if not target:
                self.status.value = "Transactions export cancelled."
                return
            self.status.value = "Writing transactions export..."
            self.page.update()
            await asyncio.sleep(0)
            export_transactions(
                self.connection,
                Path(target),
                self.selected_import,
                None if self.selected_import is not None else self.selected_member,
            )
            self.status.value = f"Wrote {target}"
        except Exception as error:
            self.status.value = f"Transactions export failed: {error}"
        finally:
            self._end_busy()

    def _begin_busy(self, message: str) -> bool:
        if self.busy:
            return False
        self.busy = True
        self.status.value = message
        self.progress.visible = True
        for button in self.toolbar_buttons:
            button.disabled = True
        self.page.update()
        return True

    def _end_busy(self) -> None:
        self.busy = False
        self.progress.visible = False
        for button in self.toolbar_buttons:
            button.disabled = False
        self.page.update()

    def _message(self, text: str) -> None:
        self.status.value = text
        self.page.update()


def _tier_label(tier: str) -> str:
    return "Tier 1 pension" if tier == "nps_tier1" else "Tier 2"


def _row_value(row) -> float:
    price = row["latest_nav"] if row["latest_nav"] is not None else row["statement_nav"]
    return float(row["units"]) * float(price)


def _holding_table(rows, position_metrics: dict | None = None) -> ft.DataTable:
    columns = [
        ft.DataColumn("Scheme"),
        ft.DataColumn("Units", numeric=True),
        ft.DataColumn("Statement NAV", numeric=True),
        ft.DataColumn("Latest NAV", numeric=True),
        ft.DataColumn("Current value", numeric=True),
    ]
    if position_metrics:
        columns.extend(
            [
                ft.DataColumn("Net invested", numeric=True),
                ft.DataColumn("Calculated XIRR", numeric=True),
                ft.DataColumn("Statement XIRR", numeric=True),
            ]
        )
    table_rows = []
    for row in rows:
        cells = [
            ft.DataCell(row["scheme_name"]),
            ft.DataCell(f"{row['units']:,.4f}"),
            ft.DataCell(f"{row['statement_nav']:,.4f}"),
            ft.DataCell("" if row["latest_nav"] is None else f"{row['latest_nav']:,.4f}"),
            ft.DataCell(f"{_row_value(row):,.2f}"),
        ]
        if position_metrics:
            metric = position_metrics.get(
                (row["member_id"], row["tier"], row["scheme_name"])
            )
            if metric is None or metric["net_invested"] is None:
                invested = "—"
            else:
                invested = f"Rs {metric['net_invested']:,.2f}"
                if not metric["invested_complete"]:
                    invested = "~" + invested
            calculated = (
                "—"
                if metric is None or metric["calculated_xirr"] is None
                else f"{metric['calculated_xirr'] * 100:.2f}%"
            )
            reported = (
                "—"
                if metric is None or metric["reported_xirr"] is None
                else f"{metric['reported_xirr']:.2f}%"
            )
            cells.extend(
                [
                    ft.DataCell(invested),
                    ft.DataCell(calculated),
                    ft.DataCell(reported),
                ]
            )
        table_rows.append(ft.DataRow(cells=cells))
    return ft.DataTable(
        columns=columns,
        rows=table_rows,
    )


def _metric_card(title: str, value: str, detail: str) -> ft.Container:
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text(title, size=13, color="#657084"),
                ft.Text(value, size=21, weight=ft.FontWeight.W_600, color="#17243A"),
                ft.Text(detail, size=11, color="#7B8494"),
            ],
            spacing=5,
        ),
        padding=18,
        width=270,
        bgcolor="#FFFFFF",
        border=ft.Border.all(1, "#E4E8EF"),
        border_radius=14,
    )


def _format_returns(rows, field: str) -> str:
    formatted = []
    for row in rows:
        value = row[field]
        if value is None:
            continue
        label = row["member"]
        if row["tier"]:
            label += " " + _tier_label(row["tier"])
        percentage = float(value)
        if field == "calculated_xirr":
            percentage *= 100
        formatted.append(f"{label}: {percentage:.2f}%")
    return " · ".join(formatted) if formatted else "Not enough history"


def _allocation_bucket_table(rows) -> ft.Row:
    assets = ("Equity", "Corporate", "Government", "Alternative")
    columns = [ft.DataColumn("Member")]
    for tier in ("nps_tier1", "nps_tier2"):
        prefix = "T1" if tier == "nps_tier1" else "T2"
        for asset in assets:
            columns.extend(
                [
                    ft.DataColumn(f"{prefix} {asset} value", numeric=True),
                    ft.DataColumn(f"{prefix} {asset} invested", numeric=True),
                ]
            )
    table_rows = []
    for row in rows:
        cells = [ft.DataCell(row["member"])]
        for tier in ("nps_tier1", "nps_tier2"):
            for asset in assets:
                amounts = row["categories"][(tier, asset)]
                cells.extend(
                    [
                        ft.DataCell(f"Rs {amounts['current_value']:,.0f}"),
                        ft.DataCell(_format_invested(amounts["net_invested"], row["invested_complete"])),
                    ]
                )
        table_rows.append(ft.DataRow(cells=cells))
    return ft.Row(
        controls=[ft.DataTable(columns=columns, rows=table_rows)],
        scroll=ft.ScrollMode.ALWAYS,
    )


def _allocation_total_table(rows) -> ft.Row:
    assets = ("Equity", "Corporate", "Government", "Alternative")
    columns = [ft.DataColumn("Member")]
    for asset in assets:
        columns.extend(
            [
                ft.DataColumn(f"{asset} value", numeric=True),
                ft.DataColumn(f"{asset} invested", numeric=True),
            ]
        )
    columns.extend(
        [
            ft.DataColumn("Portfolio value", numeric=True),
            ft.DataColumn("Net invested", numeric=True),
        ]
    )
    table_rows = []
    for row in rows:
        cells = [ft.DataCell(row["member"])]
        for asset in assets:
            amounts = row["asset_totals"][asset]
            cells.extend(
                [
                    ft.DataCell(f"Rs {amounts['current_value']:,.0f}"),
                    ft.DataCell(_format_invested(amounts["net_invested"], row["invested_complete"])),
                ]
            )
        cells.extend(
            [
                ft.DataCell(f"Rs {row['current_value']:,.0f}"),
                ft.DataCell(_format_invested(row["net_invested"], row["invested_complete"])),
            ]
        )
        table_rows.append(ft.DataRow(cells=cells))
    return ft.Row(
        controls=[ft.DataTable(columns=columns, rows=table_rows)],
        scroll=ft.ScrollMode.ALWAYS,
    )


def _format_invested(value: float, complete: bool) -> str:
    if not complete and value == 0:
        return "—"
    prefix = "" if complete else "~"
    return f"{prefix}Rs {value:,.0f}"


def _norm(value: str) -> str:
    return " ".join(value.lower().split())
