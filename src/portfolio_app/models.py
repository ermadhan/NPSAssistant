from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class HoldingRow:
    scheme_name: str
    asset_class: str
    units: float
    statement_nav: float
    market_value: float
    scheme_code: Optional[str] = None


@dataclass
class ContributionRow:
    txn_date: date
    particulars: str
    uploaded_by: str
    employee_amount: float
    employer_amount: float
    total: float


@dataclass
class SchemeTransactionRow:
    txn_date: date
    scheme_name: str
    description: str
    narration_kind: str
    amount: Optional[float]
    nav: Optional[float]
    units: Optional[float]


@dataclass
class HeaderMappingRequest:
    fingerprint: str
    section_headers: dict[str, list[str]]
    missing_fields: dict[str, list[str]]


@dataclass
class ParseResult:
    tier: str
    financial_year: str
    filename_year: Optional[str]
    year_mismatch: bool
    pran: str
    subscriber_name: str
    scheme_choice: str
    generation_date: Optional[datetime]
    as_of_date: date
    nav_date: Optional[date]
    period_start: date
    period_end: date
    holdings_value: float
    contribution_count: int
    total_contribution: float
    total_withdrawal: float
    notional_gain: float
    charges: float
    xirr: Optional[float]
    holdings: list[HoldingRow]
    contributions: list[ContributionRow]
    transactions: list[SchemeTransactionRow]
    fingerprint: str
    unknown_narrations: list[str] = field(default_factory=list)
    header_mapping: Optional[HeaderMappingRequest] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def needs_header_map(self) -> bool:
        return self.header_mapping is not None
