from __future__ import annotations

import asyncio
import inspect
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
    save_security_disclosure,
    save_statement,
)
from portfolio_app.nav import fetch_latest
from portfolio_app.securities import (
    build_security_exposure,
    fetch_latest_icici_disclosure,
    fetch_latest_hdfc_disclosure,
    fetch_latest_lic_disclosure,
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
        actions=[app.action_menu],
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
        self.selected_tier: str | None = None
        self.selected_scheme: str | None = None
        self.selected_financial_year: str | None = None
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
        self.action_menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            tooltip="Portfolio actions",
            items=[
                ft.PopupMenuItem(
                    content="Load statement",
                    icon=ft.Icons.UPLOAD_FILE,
                    on_click=self.load_statement,
                ),
                ft.PopupMenuItem(
                    content="Refresh NAVs",
                    icon=ft.Icons.REFRESH,
                    on_click=self.refresh_prices,
                ),
                ft.PopupMenuItem(
                    content="Export holdings",
                    icon=ft.Icons.DOWNLOAD,
                    on_click=self.export_holdings_file,
                ),
                ft.PopupMenuItem(
                    content="Export transactions",
                    icon=ft.Icons.RECEIPT_LONG,
                    on_click=self.export_transactions_file,
                ),
            ],
        )
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
                    padding=18,
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
            self.status.value = "Showing the family portfolio."
        else:
            self.status.value = f"Showing {self.active_view}."
        self._render_portfolio()

    def show_holdings(self, _event=None) -> None:
        self.selected_import = None
        self.selected_member = None
        self.selected_tier = None
        self.selected_scheme = None
        self.selected_financial_year = None
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
            filtered_imports = {
                row["id"]
                for row in years
                if self._matches_filters(row)
                and (
                    self.selected_scheme is None
                    or any(
                        holding["scheme_name"] == self.selected_scheme
                        for holding in db.holdings_for_import(self.connection, row["id"])
                    )
                )
            }
            self.body.controls = [
                ft.Text("Statement history", size=26, weight=ft.FontWeight.W_600),
                ft.Text("Choose an imported statement to review its holdings and transactions."),
                self._filter_bar(),
            ]
            if not filtered_imports:
                self.body.controls.append(
                    ft.Text(
                        "No statements match these filters."
                        if years
                        else "No statements imported yet. Use Load statement to begin."
                    )
                )
            else:
                self.body.controls.append(
                    _table_view(
                        ft.DataTable(
                            columns=[
                                ft.DataColumn("Account"),
                                ft.DataColumn("Tier"),
                                ft.DataColumn("Financial year"),
                                ft.DataColumn("As of"),
                                ft.DataColumn("Review"),
                            ],
                            rows=[
                                ft.DataRow(
                                    cells=[
                                        ft.DataCell(
                                            _bounded_text(
                                                row["subscriber_name"] or "NPS subscriber",
                                                width=320,
                                            )
                                        ),
                                        ft.DataCell(_tier_label(row["tier"])),
                                        ft.DataCell(row["financial_year"]),
                                        ft.DataCell(row["as_of_date"]),
                                        ft.DataCell(
                                            ft.TextButton(
                                                "Open",
                                                data=row["id"],
                                                on_click=self._select_year,
                                            )
                                        ),
                                    ]
                                )
                                for row in years
                                if row["id"] in filtered_imports
                            ],
                        )
                    )
                )
            self.page.update()
            return
        years = db.list_years(self.connection)
        if self.selected_import is None:
            source_rows = self._rows_for_financial_year(years)
            rows = self._filter_rows(source_rows)
            title = "Portfolio"
            current_rows = source_rows
        else:
            rows = db.holdings_for_import(self.connection, self.selected_import)
            title = "Statement holdings"
            current_rows = []
        controls: list = [
            ft.Text(title, size=22, weight=ft.FontWeight.W_600),
        ]
        if self.selected_import is None:
            controls.append(self._filter_bar())
        position_metrics = {}
        if (
            self.selected_import is None
            and self.selected_financial_year is None
            and rows
        ):
            analytics = family_portfolio(self.connection)
            position_metrics = {
                (item["member_id"], item["tier"], item["scheme_name"]): item
                for item in analytics["positions"]
            }
            visible_keys = {
                (row["member_id"], row["tier"], row["scheme_name"])
                for row in rows
            }
            scoped = [
                row
                for row in analytics["positions"]
                if (row["member_id"], row["tier"], row["scheme_name"]) in visible_keys
            ]
            net_invested = sum(row["net_invested"] or 0 for row in scoped)
            incomplete = any(not row["invested_complete"] for row in scoped)
            calculated = self._filtered_returns(analytics["calculated_xirr"])
            reported = [
                item
                for item in analytics["reported_xirr"]
                if self._matches_filters(item, include_scheme=False)
            ]
            controls.append(
                ft.Row(
                    controls=[
                        _metric_card(
                            "Current value",
                            _format_inr(sum(row["current_value"] for row in scoped)),
                            "Priced at latest NAV where available",
                        ),
                        _metric_card(
                            "Net invested",
                            _format_inr(net_invested) + (" *" if incomplete else ""),
                            "From statement contributions and scheme movements",
                        ),
                        _returns_card(
                            _format_returns(calculated, "calculated_xirr"),
                            _format_returns(reported, "reported_xirr"),
                        ),
                    ],
                    spacing=12,
                )
            )
            if incomplete:
                controls.append(
                    ft.Text(
                        "* Net invested is partial where the earliest imported statement does not report scheme-level net contributions.",
                        color="#6B7280",
                    )
                )
        elif (
            self.selected_import is None
            and self.selected_financial_year is not None
            and rows
        ):
            reported = [
                {
                    "member": row["subscriber_name"] or "NPS subscriber",
                    "reported_xirr": row["xirr"],
                    "member_id": row["member_id"],
                    "tier": row["tier"],
                }
                for row in years
                if row["financial_year"] == self.selected_financial_year
                and self._matches_filters(row, include_year=False, include_scheme=False)
            ]
            controls.append(
                ft.Row(
                    controls=[
                        _metric_card(
                            "Statement value",
                            _format_inr(sum(_row_value(row) for row in rows)),
                            f"Snapshot from FY {self.selected_financial_year}",
                        ),
                        _returns_card(
                            "—",
                            _format_returns(reported, "reported_xirr"),
                            statement_only=True,
                        ),
                    ],
                    spacing=12,
                )
            )
        if self.selected_import is not None:
            controls.insert(
                1,
                ft.TextButton(
                    "Back to portfolio",
                    icon=ft.Icons.ARROW_BACK,
                    on_click=self.show_holdings,
                ),
            )
        if not rows:
            controls.append(
                ft.Text(
                    "No holdings match the selected filters."
                    if current_rows
                    else "Load a transaction statement CSV downloaded from the NPS website."
                )
            )
        else:
            if self.selected_import is None:
                controls.append(
                    ft.Text("Account balances", size=17, weight=ft.FontWeight.W_600)
                )
                controls.append(
                    _table_view(
                        _holdings_summary_table(
                            rows,
                            include_family=self.selected_member is None,
                        )
                    )
                )
                members = {}
                for row in rows:
                    members.setdefault(row["member_id"], []).append(row)
                for member_rows in members.values():
                    name = member_rows[0]["subscriber_name"] or "NPS subscriber"
                    if self.selected_member is None:
                        controls.append(
                            ft.Container(
                                content=_bounded_text(
                                    name,
                                    width=520,
                                    size=15,
                                    weight=ft.FontWeight.W_600,
                                ),
                                bgcolor="#EAF0F8",
                                padding=ft.Padding(left=12, top=8, right=12, bottom=8),
                                border_radius=8,
                            )
                        )
                    self._append_member_holdings(
                        controls,
                        member_rows,
                        position_metrics,
                    )
            else:
                controls.append(
                    ft.Container(
                        content=_bounded_text(
                            rows[0]["subscriber_name"] or "NPS subscriber",
                            width=520,
                            size=15,
                            weight=ft.FontWeight.W_600,
                        ),
                        bgcolor="#EAF0F8",
                        padding=ft.Padding(left=12, top=8, right=12, bottom=8),
                        border_radius=8,
                    )
                )
                self._append_member_holdings(controls, rows)
            missing = [row["scheme_name"] for row in rows if not row["latest_nav"]]
            if missing:
                controls.append(ft.Text("Latest NAV is not stored yet for: " + "; ".join(missing)))
        if self.selected_import is not None:
            controls.extend(self._movement_section(self.selected_import))
        if self.selected_import is not None:
            controls.extend(self._scheme_code_section(rows))
        self.body.controls = controls
        self.page.update()

    def _append_member_holdings(
        self,
        controls: list,
        rows,
        position_metrics: dict | None = None,
    ) -> None:
        tiers = (self.selected_tier,) if self.selected_tier else ("nps_tier1", "nps_tier2")
        for tier in tiers:
            tier_rows = [row for row in rows if row["tier"] == tier]
            if tier_rows:
                controls.append(
                    ft.Text(
                        _tier_label(tier),
                        size=14,
                        weight=ft.FontWeight.W_600,
                        color="#52627A",
                    )
                )
                metrics = {}
                for holding in tier_rows:
                    key = (
                        holding["member_id"],
                        holding["tier"],
                        holding["scheme_name"],
                    )
                    if key in (position_metrics or {}):
                        metrics[key] = position_metrics[key]
                controls.append(
                    _table_view(
                        _holding_table(
                            tier_rows,
                            metrics,
                            value_label=(
                                "Statement value"
                                if self.selected_financial_year is not None
                                else "Current value"
                            ),
                        )
                    )
                )

    def _filter_bar(self) -> ft.Row:
        years = db.list_years(self.connection)
        members = {}
        for row in years:
            members.setdefault(
                row["member_id"],
                row["subscriber_name"] or "NPS subscriber",
            )
        schemes = set()
        for row in years:
            schemes.update(
                holding["scheme_name"]
                for holding in db.holdings_for_import(self.connection, row["id"])
            )
        available_years = {row["financial_year"] for row in years}
        if self.selected_member not in members:
            self.selected_member = None
        if self.selected_scheme not in schemes:
            self.selected_scheme = None
        if self.selected_financial_year not in available_years:
            self.selected_financial_year = None
        return ft.Row(
            wrap=True,
            spacing=10,
            controls=[
                self._filter_dropdown(
                    "Name",
                    "member",
                    self.selected_member,
                    [ft.DropdownOption(key=key, text=name) for key, name in members.items()],
                    "All members",
                    width=220,
                ),
                self._filter_dropdown(
                    "Tier",
                    "tier",
                    self.selected_tier,
                    [
                        ft.DropdownOption(key="nps_tier1", text="Tier 1"),
                        ft.DropdownOption(key="nps_tier2", text="Tier 2"),
                    ],
                    "All tiers",
                    width=150,
                ),
                self._filter_dropdown(
                    "Scheme",
                    "scheme",
                    self.selected_scheme,
                    [
                        ft.DropdownOption(key=scheme, text=scheme)
                        for scheme in sorted(schemes)
                    ],
                    "All schemes",
                    width=300,
                ),
                self._filter_dropdown(
                    "Financial year",
                    "year",
                    self.selected_financial_year,
                    [
                        ft.DropdownOption(key=year, text=year)
                        for year in sorted(
                            {row["financial_year"] for row in years},
                            reverse=True,
                        )
                    ],
                    "Latest",
                    width=170,
                ),
            ],
        )

    def _filter_dropdown(
        self,
        label: str,
        filter_name: str,
        selected: str | None,
        options: list,
        all_label: str,
        *,
        width: int,
    ) -> ft.Dropdown:
        return ft.Dropdown(
            label=label,
            value=selected or "__all__",
            options=[
                ft.DropdownOption(key="__all__", text=all_label),
                *options,
            ],
            data=filter_name,
            **{
                (
                    "on_select"
                    if "on_select" in inspect.signature(ft.Dropdown).parameters
                    else "on_change"
                ): self._on_filter_change
            },
            width=width,
            dense=True,
            content_padding=10,
        )

    def _on_filter_change(self, event) -> None:
        value = event.control.value
        value = None if value == "__all__" else value
        filter_name = event.control.data
        if filter_name == "member":
            self.selected_member = value
        elif filter_name == "tier":
            self.selected_tier = value
        elif filter_name == "scheme":
            self.selected_scheme = value
        elif filter_name == "year":
            self.selected_financial_year = value
        self.selected_import = None
        self._render_portfolio()

    def _rows_for_financial_year(self, years):
        if self.selected_financial_year is None:
            return db.current_holding_rows(self.connection)
        rows = []
        for statement in years:
            if statement["financial_year"] != self.selected_financial_year:
                continue
            rows.extend(
                {
                    **dict(row),
                    "latest_nav": None,
                    "latest_nav_date": None,
                }
                for row in db.holdings_for_import(self.connection, statement["id"])
            )
        return rows

    def _filter_rows(self, rows):
        return [row for row in rows if self._matches_filters(row)]

    def _matches_filters(
        self,
        row,
        *,
        include_year: bool = True,
        include_scheme: bool = True,
    ) -> bool:
        if self.selected_member is not None and row["member_id"] != self.selected_member:
            return False
        if self.selected_tier is not None and row["tier"] != self.selected_tier:
            return False
        if (
            include_scheme
            and self.selected_scheme is not None
            and "scheme_name" in row.keys()
            and row["scheme_name"] != self.selected_scheme
        ):
            return False
        if (
            include_year
            and self.selected_financial_year is not None
            and row["financial_year"] != self.selected_financial_year
        ):
            return False
        return True

    def _filtered_returns(self, returns):
        if self.selected_scheme is None:
            if self.selected_member is None:
                returns = [row for row in returns if row["member_id"] == "family"]
                if self.selected_tier is None:
                    returns = [row for row in returns if row["tier"] is None]
            else:
                returns = [
                    row for row in returns if row["member_id"] == self.selected_member
                ]
            return [
                row
                for row in returns
                if self._matches_filters(row, include_scheme=False)
            ]
        data = family_portfolio(self.connection)
        return [
            row
            for row in data["positions"]
            if row["scheme_name"] == self.selected_scheme
            and (self.selected_member is None or row["member_id"] == self.selected_member)
            and (self.selected_tier is None or row["tier"] == self.selected_tier)
        ]

    def _render_allocation(self) -> None:
        data = family_portfolio(self.connection)
        years = db.list_years(self.connection)
        visible_rows = self._filter_rows(self._rows_for_financial_year(years))
        analytics_by_key = {
            (row["member_id"], row["tier"], row["scheme_name"]): row
            for row in data["positions"]
        }
        asset_names = {
            "E": "Equity",
            "C": "Corporate",
            "G": "Government",
            "A": "Alternative",
        }
        positions = []
        for holding in visible_rows:
            key = (
                holding["member_id"],
                holding["tier"],
                holding["scheme_name"],
            )
            position = dict(analytics_by_key.get(key, {}))
            position.update(
                {
                    "member_id": holding["member_id"],
                    "member": holding["subscriber_name"] or "NPS subscriber",
                    "tier": holding["tier"],
                    "scheme_name": holding["scheme_name"],
                    "asset_class": asset_names.get(holding["asset_class"], "Other"),
                    "current_value": _row_value(holding),
                    "net_invested": (
                        position.get("net_invested")
                        if self.selected_financial_year is None
                        else 0.0
                    ),
                    "invested_complete": (
                        position.get("invested_complete", False)
                        if self.selected_financial_year is None
                        else True
                    ),
                }
            )
            positions.append(position)
        rows = allocation_summary(positions)
        controls = [
            ft.Text("Portfolio allocation", size=26, weight=ft.FontWeight.W_600),
            self._filter_bar(),
            ft.Text(
                f"Statement snapshot for FY {self.selected_financial_year}."
                if self.selected_financial_year
                else (
                    "Scheme-level values from each statement and the latest available NAV. "
                    "Net invested uses reported scheme contributions where available and dated transactions otherwise."
                )
            ),
        ]
        if not positions:
            controls.append(
                ft.Text(
                    "No holdings match the selected filters."
                    if visible_rows
                    else "Import NPS statements to see member and family allocations."
                )
            )
        else:
            controls.append(
                ft.Text(
                    "By tier and asset class",
                    size=19,
                    weight=ft.FontWeight.W_600,
                )
            )
            controls.append(
                _allocation_bucket_table(
                    rows,
                    show_invested=self.selected_financial_year is None,
                    tiers=(
                        (self.selected_tier,)
                        if self.selected_tier
                        else ("nps_tier1", "nps_tier2")
                    ),
                )
            )
            controls.append(
                ft.Text(
                    "Asset-class and portfolio totals",
                    size=19,
                    weight=ft.FontWeight.W_600,
                )
            )
            controls.append(
                _allocation_total_table(
                    rows,
                    show_invested=self.selected_financial_year is None,
                )
            )
            if self.selected_financial_year is None and any(
                not row["invested_complete"] for row in rows
            ):
                controls.append(
                    ft.Text(
                        "Net invested is partial for members whose imported history does not include an initial scheme-contribution balance.",
                        color="#8A5A00",
                    )
                )
        self.body.controls = controls
        self.page.update()

    def _render_securities(self) -> None:
        current_rows = self._filter_rows(
            self._rows_for_financial_year(db.list_years(self.connection))
        )
        selected_rows = current_rows
        disclosures = db.latest_security_disclosures(self.connection)
        exposure = build_security_exposure(selected_rows, disclosures)
        exposure_label = "Family exposure" if self.selected_member is None else "Member exposure"
        controls = [
            ft.Text("Underlying stocks and bonds", size=26, weight=ft.FontWeight.W_600),
            self._filter_bar(),
            ft.Text(
                (
                    f"FY {self.selected_financial_year} statement values are estimated "
                    "using the latest stored manager disclosures, which may be from a different date."
                    if self.selected_financial_year
                    else (
                        "Estimates combine the latest imported account value for each NPS scheme "
                        "with security weights from its pension fund's published portfolio. "
                        "Stored latest NAVs are used when available; otherwise the statement NAV is used."
                    )
                )
            ),
            ft.Row(
                wrap=True,
                controls=[
                    ft.FilledButton(
                        f"Refresh {manager} portfolio",
                        icon=ft.Icons.REFRESH,
                        data=manager,
                        on_click=self.refresh_securities,
                    )
                    for manager in ("ICICI", "HDFC", "LIC")
                ],
            ),
        ]
        if not current_rows:
            controls.append(
                ft.Text(
                    "No holdings match the selected filters."
                    if db.current_holding_rows(self.connection)
                    else "Import NPS statements to see security-level exposure."
                )
            )
        elif not disclosures:
            controls.append(
                ft.Container(
                    content=ft.Text(
                        "No official portfolio disclosure has been downloaded yet. "
                        "Refresh to fetch the latest ICICI, HDFC, or LIC Pension Fund report. "
                        "Other pension fund managers and unsupported schemes remain uncovered."
                    ),
                    bgcolor="#FFF8E6",
                    padding=20,
                    border_radius=12,
                )
            )
        else:
            disclosure_dates = {}
            for row in disclosures:
                disclosure_dates[row["fund_manager"]] = row["disclosure_date"]
            date_summary = " · ".join(
                f"{manager}: {disclosure_date}"
                for manager, disclosure_date in sorted(disclosure_dates.items())
            )
            controls.append(
                ft.Text(
                    f"Official disclosure dates: {date_summary or 'unknown'} · "
                    f"Look-through coverage: {_format_inr(exposure['covered_value'], decimals=0)} of "
                    f"{_format_inr(exposure['total_value'], decimals=0)} "
                    f"({exposure['coverage']:.1%})."
                )
            )
            controls.append(
                ft.Text(
                    "Security exposures are estimates, not account-level holdings. "
                    "Official sources: "
                    + "; ".join(sorted({row["source_url"] for row in disclosures})),
                    color="#6B7280",
                )
            )
            if exposure["unsupported"]:
                names = "; ".join(sorted(exposure["unsupported"]))
                controls.append(
                    ft.Text(
                        "Not included in look-through (unsupported manager or scheme): "
                        + names,
                        color="#8A5A00",
                    )
                )
            if exposure["positions"]:
                totals: dict[str, float] = {}
                for row in exposure["positions"]:
                    totals[row["instrument_type"]] = (
                        totals.get(row["instrument_type"], 0.0) + row["exposure"]
                    )
                controls.append(
                    ft.Row(
                        wrap=True,
                        controls=[
                            ft.Container(
                                content=ft.Text(
                                    f"{asset}: {_format_inr(value, decimals=0)}",
                                    weight=ft.FontWeight.W_600,
                                ),
                                bgcolor="#FFFFFF",
                                padding=12,
                                border_radius=10,
                            )
                            for asset, value in sorted(totals.items())
                        ],
                    )
                )
                controls.append(
                    _table_view(
                        ft.DataTable(
                                columns=[
                                    ft.DataColumn("Instrument"),
                                    ft.DataColumn("Security"),
                                    ft.DataColumn("ISIN"),
                                    ft.DataColumn("Industry"),
                                    ft.DataColumn("Exposure", numeric=True),
                                    ft.DataColumn("Portfolio %", numeric=True),
                                ],
                                rows=[
                                    ft.DataRow(
                                        cells=[
                                            ft.DataCell(row["instrument_type"]),
                                            ft.DataCell(_bounded_text(row["security_name"], width=260)),
                                            ft.DataCell(_bounded_text(row["isin"], width=120)),
                                            ft.DataCell(_bounded_text(row["industry"], width=180)),
                                            ft.DataCell(_format_inr(row["exposure"], decimals=0)),
                                            ft.DataCell(f"{row['portfolio_weight']:.2%}"),
                                        ]
                                    )
                                    for row in exposure["positions"]
                                ],
                                tooltip=exposure_label,
                        )
                    )
                )
            elif exposure["covered_value"] == 0:
                controls.append(
                    ft.Text("No imported schemes match the available official disclosures.")
                )
        self.body.controls = controls
        self.page.update()

    async def refresh_securities(self, _event=None) -> None:
        manager = _event.control.data if _event is not None else "ICICI"
        fetchers = {
            "ICICI": fetch_latest_icici_disclosure,
            "HDFC": fetch_latest_hdfc_disclosure,
            "LIC": fetch_latest_lic_disclosure,
        }
        if not self._begin_busy(
            f"Fetching the latest official {manager} portfolio disclosure..."
        ):
            return
        try:
            disclosure = await asyncio.to_thread(fetchers[manager])
            save_security_disclosure(self.connection, disclosure)
        except Exception as error:
            self.status.value = f"Official portfolio refresh failed: {error}"
        else:
            self.status.value = (
                f"Saved {manager} portfolio disclosure dated "
                f"{disclosure.as_of_date.isoformat()}."
            )
            self._render_securities()
        finally:
            self._end_busy()

    def _movement_section(self, import_id: int) -> list:
        rows = db.transactions_for_import(self.connection, import_id)
        cash = sum(float(row["amount"] or 0) for row in rows if row["narration_kind"] in CASH_KINDS)
        table_rows = []
        for row in rows:
            table_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(row["txn_date"]),
                        ft.DataCell(_bounded_text(row["scheme_name"], width=300)),
                        ft.DataCell(row["narration_kind"]),
                        ft.DataCell(_bounded_text(row["description"], width=360)),
                        ft.DataCell("" if row["amount"] is None else _format_inr(row["amount"])),
                        ft.DataCell("" if row["units"] is None else f"{row['units']:,.4f}"),
                    ]
                )
            )
        return [
            ft.Text(
                "Scheme movements. Cash-flow total excludes opening and closing balances: "
                + _format_inr(cash)
            ),
            _table_view(
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
                )
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
        self.selected_tier = None
        self.selected_scheme = None
        self.selected_financial_year = None
        self.status.value = "Showing selected statement."
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
            _bounded_text(
                f"Review {self.pending_path.name}",
                width=760,
                size=22,
                weight=ft.FontWeight.W_600,
            ),
            ft.Row(
                controls=[
                    ft.Text("Account:", size=13, color="#52627A"),
                    _bounded_text(account, width=640, size=13),
                ]
            ),
            ft.Text(
                f"{_tier_label(result.tier)}  {result.financial_year}  "
                f"as on {result.as_of_date.isoformat()}  "
                f"holdings {_format_inr(result.holdings_value)}"
            ),
            ft.Text(
                f"{len(result.holdings)} schemes, {len(result.contributions)} contributions, "
                f"{len(result.transactions)} scheme rows. "
                "Contribution totals are kept separate from scheme movements."
            ),
        ]
        if result.holdings:
            controls.append(
                _table_view(
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
                                    ft.DataCell(_bounded_text(row.scheme_name, width=360)),
                                    ft.DataCell(f"{row.units:,.4f}"),
                                    ft.DataCell(f"{row.statement_nav:,.4f}"),
                                    ft.DataCell(_format_inr(row.market_value)),
                                ]
                            )
                            for row in result.holdings
                        ],
                    )
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
                    dropdown = ft.Dropdown(label=f"{section}.{field_name}", options=options, expand=True)
                    self.header_inputs[(section, field_name)] = dropdown
                    controls.append(ft.Row(controls=[dropdown]))
        if result.unknown_narrations:
            controls.append(ft.Text("New narration text. Choose a kind. It will be reused next time."))
            options = [ft.DropdownOption(key=kind, text=kind) for kind in NARRATION_KINDS]
            for phrase in result.unknown_narrations:
                dropdown = ft.Dropdown(label=phrase, options=options, expand=True)
                self.narration_inputs[_norm(phrase)] = dropdown
                controls.append(ft.Row(controls=[dropdown]))
        for warning in result.warnings:
            controls.append(ft.Text(warning))
        controls.append(
            ft.Row(
                controls=[ft.FilledButton("Save statement", on_click=self.save_review)],
                alignment=ft.MainAxisAlignment.END,
            )
        )
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
        self.action_menu.disabled = True
        self.page.update()
        return True

    def _end_busy(self) -> None:
        self.busy = False
        self.progress.visible = False
        self.action_menu.disabled = False
        self.page.update()

    def _message(self, text: str) -> None:
        self.status.value = text
        self.page.update()


def _tier_label(tier: str) -> str:
    return "Tier 1 pension" if tier == "nps_tier1" else "Tier 2"


def _row_value(row) -> float:
    price = row["latest_nav"] if row["latest_nav"] is not None else row["statement_nav"]
    return float(row["units"]) * float(price)


def _holding_table(
    rows,
    position_metrics: dict | None = None,
    *,
    value_label: str = "Current value",
) -> ft.DataTable:
    columns = [
        ft.DataColumn("Scheme"),
        ft.DataColumn("Units", numeric=True),
        ft.DataColumn("Statement NAV", numeric=True),
        ft.DataColumn("Latest NAV", numeric=True),
        ft.DataColumn(value_label, numeric=True),
    ]
    if position_metrics:
        columns.extend(
            [
                ft.DataColumn("Net invested", numeric=True),
                ft.DataColumn("XIRR (calculated / reported)", numeric=True),
            ]
        )
    table_rows = []
    for row in rows:
        cells = [
            ft.DataCell(_bounded_text(row["scheme_name"], width=340)),
            ft.DataCell(f"{row['units']:,.4f}"),
            ft.DataCell(f"{row['statement_nav']:,.4f}"),
            ft.DataCell("" if row["latest_nav"] is None else f"{row['latest_nav']:,.4f}"),
            ft.DataCell(_format_inr(_row_value(row))),
        ]
        if position_metrics:
            metric = position_metrics.get(
                (row["member_id"], row["tier"], row["scheme_name"])
            )
            if metric is None or metric["net_invested"] is None:
                invested = "—"
            else:
                invested = _format_inr(metric["net_invested"])
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
                    ft.DataCell(f"{calculated} / {reported}"),
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
        padding=16,
        expand=True,
        bgcolor="#FFFFFF",
        border=ft.Border.all(1, "#E4E8EF"),
        border_radius=14,
    )


def _returns_card(
    calculated: str,
    reported: str,
    *,
    statement_only: bool = False,
) -> ft.Container:
    fields = []
    if not statement_only:
        fields.append(
            ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Text("Calculated", size=11, color="#657084"),
                        width=88,
                    ),
                    ft.Text(
                        calculated,
                        size=12,
                        weight=ft.FontWeight.W_600,
                        expand=True,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        )
    fields.append(
        ft.Row(
            controls=[
                ft.Container(
                    content=ft.Text(
                        "Statement XIRR",
                        size=11,
                        color="#657084",
                        tooltip="Reported at account level in the statement",
                    ),
                    width=88,
                ),
                ft.Text(
                    reported,
                    size=12,
                    weight=ft.FontWeight.W_600,
                    expand=True,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
    )
    return ft.Container(
        content=ft.Column(controls=fields, spacing=8),
        padding=16,
        expand=True,
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
        if row["member_id"] == "family":
            label = "Family"
        elif row["tier"]:
            label = _tier_label(row["tier"])
        else:
            label = row["member"]
        percentage = float(value)
        if field == "calculated_xirr":
            percentage *= 100
        formatted.append(f"{label}: {percentage:.2f}%")
    if formatted:
        return " · ".join(formatted)
    return "Not reported" if field == "reported_xirr" else "Not enough history"


def _allocation_bucket_table(
    rows,
    *,
    show_invested: bool = True,
    tiers=("nps_tier1", "nps_tier2"),
) -> ft.Row:
    assets = ("Equity", "Corporate", "Government", "Alternative")
    columns = [
        ft.DataColumn("Account"),
        ft.DataColumn("Tier"),
        *[ft.DataColumn(asset) for asset in assets],
    ]
    table_rows = []
    for row in rows:
        for tier in tiers:
            tier_amounts = [row["categories"][(tier, asset)] for asset in assets]
            if not any(amount["current_value"] for amount in tier_amounts):
                continue
            cells = [
                ft.DataCell(_bounded_text(row["member"], width=220)),
                ft.DataCell("Tier 1" if tier == "nps_tier1" else "Tier 2"),
            ]
            for amounts in tier_amounts:
                cells.append(
                    ft.DataCell(
                        _allocation_amount_cell(
                            amounts,
                            row["invested_complete"],
                            show_invested,
                        )
                    )
                )
            table_rows.append(ft.DataRow(cells=cells))
    return _table_view(
        ft.DataTable(columns=columns, rows=table_rows)
    )


def _allocation_total_table(rows, *, show_invested: bool = True) -> ft.Row:
    assets = ("Equity", "Corporate", "Government", "Alternative")
    columns = [
        ft.DataColumn("Account"),
        *[ft.DataColumn(asset) for asset in assets],
        ft.DataColumn("Portfolio"),
    ]
    table_rows = []
    for row in rows:
        cells = [ft.DataCell(_bounded_text(row["member"], width=220))]
        for asset in assets:
            amounts = row["asset_totals"][asset]
            cells.append(
                ft.DataCell(
                    _allocation_amount_cell(
                        amounts,
                        row["invested_complete"],
                        show_invested,
                    )
                )
            )
        portfolio = {
            "current_value": row["current_value"],
            "net_invested": row["net_invested"],
        }
        cells.append(
            ft.DataCell(
                _allocation_amount_cell(
                    portfolio,
                    row["invested_complete"],
                    show_invested,
                )
            )
        )
        table_rows.append(ft.DataRow(cells=cells))
    return _table_view(ft.DataTable(columns=columns, rows=table_rows))


def _allocation_amount_cell(amounts, complete: bool, show_invested: bool) -> ft.Text:
    text = _format_inr(amounts["current_value"], decimals=0)
    if show_invested:
        invested = _format_invested(amounts["net_invested"], complete)
        text += f"\nInvested {invested}"
    return ft.Text(
        text,
        size=11,
        max_lines=2,
        overflow=ft.TextOverflow.ELLIPSIS,
        tooltip=text,
    )


def _format_invested(value: float, complete: bool) -> str:
    if not complete and value == 0:
        return "—"
    prefix = "" if complete else "~"
    return prefix + _format_inr(value, decimals=0)


def _format_inr(value: float, *, decimals: int = 2) -> str:
    amount = float(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 10_000_000:
        return f"{sign}Rs {amount / 10_000_000:.2f} Crore"
    if amount >= 100_000:
        return f"{sign}Rs {amount / 100_000:.2f} Lakh"

    formatted = f"{amount:.{decimals}f}"
    integer, separator, fraction = formatted.partition(".")
    if len(integer) > 3:
        last_three = integer[-3:]
        leading = integer[:-3]
        groups = []
        while leading:
            groups.insert(0, leading[-2:])
            leading = leading[:-2]
        integer = ",".join([*groups, last_three])
    number = integer + (separator + fraction if separator else "")
    return f"{sign}Rs {number}"


def _bounded_text(
    value: str,
    *,
    width: int,
    size: int = 12,
    weight=None,
) -> ft.Text:
    return ft.Text(
        value,
        width=width,
        size=size,
        weight=weight,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
        tooltip=value,
    )


def _holdings_summary_table(rows, *, include_family: bool) -> ft.DataTable:
    by_member: dict[str, dict] = {}
    for row in rows:
        member_id = row["member_id"]
        member = by_member.setdefault(
            member_id,
            {
                "name": row["subscriber_name"] or "NPS subscriber",
                "nps_tier1": 0.0,
                "nps_tier2": 0.0,
            },
        )
        member[row["tier"]] += _row_value(row)

    summary_rows = []
    if include_family:
        family_tier1 = sum(member["nps_tier1"] for member in by_member.values())
        family_tier2 = sum(member["nps_tier2"] for member in by_member.values())
        summary_rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(_bounded_text("Family", width=260, weight=ft.FontWeight.W_600)),
                    ft.DataCell(_format_inr(family_tier1)),
                    ft.DataCell(_format_inr(family_tier2)),
                    ft.DataCell(_format_inr(family_tier1 + family_tier2)),
                ]
            )
        )
    for member in by_member.values():
        summary_rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(_bounded_text(member["name"], width=260)),
                    ft.DataCell(_format_inr(member["nps_tier1"])),
                    ft.DataCell(_format_inr(member["nps_tier2"])),
                    ft.DataCell(
                        _format_inr(member["nps_tier1"] + member["nps_tier2"])
                    ),
                ]
            )
        )
    return ft.DataTable(
        columns=[
            ft.DataColumn("Account"),
            ft.DataColumn("Tier 1", numeric=True),
            ft.DataColumn("Tier 2", numeric=True),
            ft.DataColumn("Combined", numeric=True),
        ],
        rows=summary_rows,
    )


def _table_view(table: ft.DataTable) -> ft.Row:
    table.column_spacing = 16
    table.horizontal_margin = 12
    table.heading_row_height = 42
    table.data_row_min_height = 40
    table.data_row_max_height = 68
    table.data_text_style = ft.TextStyle(size=12)
    table.heading_text_style = ft.TextStyle(size=12, weight=ft.FontWeight.W_600)
    return ft.Row(
        controls=[table],
        scroll=ft.ScrollMode.ALWAYS,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


def _norm(value: str) -> str:
    return " ".join(value.lower().split())
