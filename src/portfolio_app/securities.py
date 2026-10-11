from __future__ import annotations

import calendar
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from io import BytesIO
from typing import BinaryIO
from urllib.parse import urljoin

import pandas as pd

ICICI_DISCLOSURE_PAGE = "https://www.icicipension.in/public_disclosure/portfolio_details"
ICICI_PORTFOLIO_API = "https://api.icicipension.in/get_check/portfolio"
LIC_DISCLOSURE_PAGE = "https://www.licpensionfund.in/portfolio-disclosures"
HDFC_DISCLOSURE_PAGE = "https://www.hdfcpension.com/public-disclosures/"

SUPPORTED_SCHEMES = {
    (asset, tier)
    for asset in ("E", "C", "G")
    for tier in ("nps_tier1", "nps_tier2")
}


@dataclass(frozen=True)
class SecurityPosition:
    scheme_asset: str
    tier: str
    security_name: str
    isin: str
    industry: str
    instrument_type: str
    quantity: float | None
    market_value: float
    portfolio_weight: float
    rating: str


@dataclass(frozen=True)
class PortfolioDisclosure:
    fund_manager: str
    as_of_date: date
    source_url: str
    positions: list[SecurityPosition]


def fetch_latest_icici_disclosure(today: date | None = None) -> PortfolioDisclosure:
    current = today or date.today()
    request_headers = {"User-Agent": "NPS-Family-Portfolio/1.0"}
    failures: list[str] = []

    for month_offset in range(7):
        month_index = current.month - 1 - month_offset
        year = current.year + month_index // 12
        month = month_index % 12 + 1
        month_name = calendar.month_abbr[month].lower()
        api_url = f"{ICICI_PORTFOLIO_API}/{month_name}/{year}"
        request = urllib.request.Request(api_url, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            failures.append(f"{month_name} {year}: HTTP {error.code}")
            continue

        file_url = _disclosure_file_url(payload)
        if file_url is None:
            continue
        file_request = urllib.request.Request(file_url, headers=request_headers)
        try:
            with urllib.request.urlopen(file_request, timeout=60) as response:
                workbook = response.read()
        except urllib.error.HTTPError as error:
            failures.append(f"{month_name} {year} workbook: HTTP {error.code}")
            continue
        filename = _disclosure_filename(payload)
        return _parse_manager_workbook(
            workbook,
            "ICICI",
            ICICI_DISCLOSURE_PAGE,
            filename,
        )

    detail = "; ".join(failures)
    suffix = f" Details: {detail}" if detail else ""
    raise RuntimeError(
        "No official ICICI portfolio disclosure was available for the latest "
        f"seven months.{suffix}"
    )


def fetch_latest_hdfc_disclosure() -> PortfolioDisclosure:
    html = _fetch_url(HDFC_DISCLOSURE_PAGE, user_agent=None).decode("utf-8", "replace")
    links = _DisclosureLinkParser.parse(html, HDFC_DISCLOSURE_PAGE)
    workbook_url = next(
        (
            link.href
            for link in links
            if "monthly-website-forms" in link.href.lower()
            and link.href.lower().endswith(".xlsx")
        ),
        None,
    )
    if workbook_url is None:
        raise RuntimeError("No monthly HDFC NPS portfolio workbook was found.")
    workbook = _fetch_url(workbook_url, user_agent=None)
    return _parse_manager_workbook(
        workbook,
        "HDFC",
        HDFC_DISCLOSURE_PAGE,
        workbook_url.rsplit("/", 1)[-1],
    )


def fetch_latest_lic_disclosure() -> PortfolioDisclosure:
    html = _fetch_url(LIC_DISCLOSURE_PAGE).decode("utf-8", "replace")
    links = _DisclosureLinkParser.parse(html, LIC_DISCLOSURE_PAGE)
    workbooks: dict[tuple[str, str], tuple[str, bytes]] = {}
    for link in links:
        if not link.href.lower().endswith(".xlsx"):
            continue
        match = re.search(
            r"\b([ECG])\s*TIER\s*([12])\s+SCHEME\b",
            link.label,
            re.IGNORECASE,
        )
        if match is None:
            continue
        scheme = (
            match.group(1).upper(),
            "nps_tier1" if match.group(2) == "1" else "nps_tier2",
        )
        if scheme not in workbooks:
            workbooks[scheme] = (link.href, _fetch_url(link.href))

    missing = SUPPORTED_SCHEMES - set(workbooks)
    if missing:
        names = ", ".join(f"{asset} {tier}" for asset, tier in sorted(missing))
        raise RuntimeError(
            "The LIC disclosure page did not provide all six supported scheme "
            f"workbooks; missing: {names}."
        )

    positions: list[SecurityPosition] = []
    disclosure_date: date | None = None
    for scheme, (workbook_url, workbook) in workbooks.items():
        parsed = _parse_manager_workbook(
            workbook,
            "LIC",
            LIC_DISCLOSURE_PAGE,
            workbook_url.rsplit("/", 1)[-1],
            scheme_override=scheme,
        )
        if disclosure_date is not None and parsed.as_of_date != disclosure_date:
            raise ValueError("LIC scheme workbooks contain inconsistent disclosure dates.")
        disclosure_date = parsed.as_of_date
        positions.extend(parsed.positions)
    return PortfolioDisclosure("LIC", disclosure_date, LIC_DISCLOSURE_PAGE, positions)


def parse_icici_disclosure(
    workbook: bytes | BinaryIO,
    filename: str = "portfolio.xlsx",
) -> PortfolioDisclosure:
    return _parse_manager_workbook(
        workbook,
        "ICICI",
        ICICI_DISCLOSURE_PAGE,
        filename,
    )


def parse_hdfc_disclosure(
    workbook: bytes | BinaryIO,
    filename: str = "portfolio.xlsx",
) -> PortfolioDisclosure:
    return _parse_manager_workbook(
        workbook,
        "HDFC",
        HDFC_DISCLOSURE_PAGE,
        filename,
    )


def parse_lic_disclosure(
    workbook: bytes | BinaryIO,
    scheme: tuple[str, str],
    filename: str = "portfolio.xlsx",
) -> PortfolioDisclosure:
    return _parse_manager_workbook(
        workbook,
        "LIC",
        LIC_DISCLOSURE_PAGE,
        filename,
        scheme_override=scheme,
    )


@dataclass(frozen=True)
class _DisclosureLink:
    href: str
    label: str


class _DisclosureLinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[_DisclosureLink] = []
        self._href: str | None = None
        self._label = ""

    @classmethod
    def parse(cls, html: str, base_url: str) -> list[_DisclosureLink]:
        parser = cls(base_url)
        parser.feed(html)
        return parser.links

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        href = attributes.get("href")
        if href:
            self._href = urljoin(self.base_url, href.strip())
            self._label = attributes.get("aria-label") or ""

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._label += " " + data.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append(_DisclosureLink(self._href, self._label.strip()))
            self._href = None
            self._label = ""


def _fetch_url(url: str, user_agent: str | None = "NPS-Family-Portfolio/1.0") -> bytes:
    request = (
        url
        if user_agent is None
        else urllib.request.Request(url, headers={"User-Agent": user_agent})
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _parse_manager_workbook(
    workbook: bytes | BinaryIO,
    fund_manager: str,
    source_url: str,
    filename: str = "portfolio.xlsx",
    scheme_override: tuple[str, str] | None = None,
) -> PortfolioDisclosure:
    engine = (
        "xlrd"
        if filename.lower().endswith(".xls") and not filename.lower().endswith(".xlsx")
        else "openpyxl"
    )
    try:
        source = BytesIO(workbook) if isinstance(workbook, bytes) else workbook
        excel = pd.ExcelFile(source, engine=engine)
    except Exception as error:
        raise ValueError(
            f"Could not read the {fund_manager} portfolio workbook: {error}"
        ) from error

    positions: list[SecurityPosition] = []
    disclosure_date: date | None = None
    for sheet_name in excel.sheet_names:
        scheme = scheme_override or _parse_scheme_sheet(sheet_name)
        if scheme is None:
            continue
        frame = pd.read_excel(excel, sheet_name=sheet_name, header=None)
        sheet_date, sheet_positions = _parse_scheme_sheet_rows(frame, scheme)
        if sheet_positions:
            if disclosure_date is not None and sheet_date != disclosure_date:
                raise ValueError(
                    f"{fund_manager} portfolio sheets contain inconsistent disclosure dates."
                )
            disclosure_date = sheet_date
            positions.extend(sheet_positions)
        if scheme_override is not None:
            break

    if not positions or disclosure_date is None:
        raise ValueError(
            f"The {fund_manager} workbook did not contain supported Tier 1/Tier 2 "
            "E, C, or G holdings."
        )
    return PortfolioDisclosure(
        fund_manager=fund_manager,
        as_of_date=disclosure_date,
        source_url=source_url,
        positions=positions,
    )


def build_security_exposure(
    holdings: list,
    disclosure_rows: list,
    member_id: str | None = None,
) -> dict:
    disclosures: dict[tuple[str, str, str], list] = {}
    for row in disclosure_rows:
        key = (row["fund_manager"], row["scheme_asset"], row["tier"])
        disclosures.setdefault(key, []).append(row)

    positions: dict[tuple[str, str], dict] = {}
    covered_value = 0.0
    unsupported: dict[str, float] = {}
    total_value = 0.0
    selected_holdings = [
        row
        for row in holdings
        if member_id is None or row["member_id"] == member_id
    ]
    for holding in selected_holdings:
        name = str(holding["scheme_name"])
        value = float(holding["units"]) * float(
            holding["latest_nav"]
            if holding["latest_nav"] is not None
            else holding["statement_nav"]
        )
        total_value += value
        scheme_asset = _asset_from_scheme_name(name)
        fund_manager = _fund_manager_from_scheme_name(name)
        scheme_key = (
            (fund_manager, scheme_asset, str(holding["tier"]))
            if fund_manager and scheme_asset
            else None
        )
        data = disclosures.get(scheme_key, []) if scheme_key else []
        if not data:
            unsupported[name] = unsupported.get(name, 0.0) + value
            continue
        covered_value += value
        for security in data:
            isin = str(security["isin"] or "").strip()
            security_name = str(security["security_name"] or "").strip()
            security_key = ("isin", isin.upper()) if isin else (
                "name",
                re.sub(r"\W+", "", security_name).upper(),
            )
            item = positions.setdefault(
                security_key,
                {
                    "security_name": security_name,
                    "isin": isin,
                    "industry": security["industry"] or "",
                    "instrument_type": security["instrument_type"],
                    "exposure": 0.0,
                    "schemes": set(),
                },
            )
            item["exposure"] += value * float(security["portfolio_weight"])
            item["schemes"].add(name)

    result_positions = []
    for item in positions.values():
        result_positions.append(
            {
                **item,
                "schemes": sorted(item["schemes"]),
                "portfolio_weight": (
                    0.0 if total_value == 0 else item["exposure"] / total_value
                ),
            }
        )
    result_positions.sort(key=lambda row: row["exposure"], reverse=True)
    dates = sorted(
        {
            str(row["disclosure_date"])
            for row in disclosure_rows
            if row["disclosure_date"] is not None
        }
    )
    return {
        "positions": result_positions,
        "total_value": total_value,
        "covered_value": covered_value,
        "coverage": 0.0 if total_value == 0 else covered_value / total_value,
        "unsupported": unsupported,
        "disclosure_date": dates[-1] if dates else None,
    }


def _disclosure_file_url(payload: dict) -> str | None:
    try:
        return str(payload["data"][0]["attributes"]["Scheme_Details"]["data"]["attributes"]["url"])
    except (KeyError, IndexError, TypeError):
        return None


def _disclosure_filename(payload: dict) -> str:
    try:
        return str(
            payload["data"][0]["attributes"]["Scheme_Details"]["data"]["attributes"]["name"]
        )
    except (KeyError, IndexError, TypeError):
        return "portfolio.xls"


def _parse_scheme_sheet(sheet_name: str) -> tuple[str, str] | None:
    normalized = re.sub(r"\s+", " ", sheet_name.strip().lower()).replace("l", "i")
    match = re.search(
        r"\bscheme\s+([ecg])\b.*\btier[\s-]*(i{1,2}|[12])\b",
        normalized,
    )
    if match is None:
        return None
    scheme_asset = match.group(1).upper()
    tier_token = match.group(2)
    tier = "nps_tier1" if tier_token in {"i", "1"} else "nps_tier2"
    return scheme_asset, tier


def _parse_scheme_sheet_rows(
    frame: pd.DataFrame,
    scheme: tuple[str, str],
) -> tuple[date, list[SecurityPosition]]:
    header_row = None
    columns: dict[str, int] = {}
    for index, row in frame.iterrows():
        values = [_normalize(value) for value in row.tolist()]
        if "particulars" in values and any(
            value.endswith("market value") for value in values
        ):
            header_row = index
            market_value_column = next(
                i for i, value in enumerate(values)
                if value.endswith("market value")
            )
            columns = {
                "name": values.index("particulars"),
                "market_value": market_value_column,
                "weight": next(
                    (
                        i for i, value in enumerate(values)
                        if value in {"% of portfolio", "percentage of portfolio"}
                    ),
                    -1,
                ),
                "isin": next((i for i, value in enumerate(values) if value.startswith("isin")), -1),
                "industry": next((i for i, value in enumerate(values) if value == "industry"), -1),
                "quantity": next(
                    (
                        i for i, value in enumerate(values)
                        if value in {"quantity", "no of units"}
                    ),
                    -1,
                ),
                "rating": next((i for i, value in enumerate(values) if value.startswith("rating")), -1),
            }
            break
    if header_row is None:
        raise ValueError("An ICICI scheme worksheet is missing its portfolio table header.")

    disclosure_date = None
    for _, row in frame.head(header_row).iterrows():
        date_label = _normalize(row.iloc[0])
        if re.match(r"(?:portfolio )?statement as on\b", date_label) and len(row) > 1:
            date_candidates = [row.iloc[1]]
            date_candidates.append(
                re.sub(
                    r"^(?:portfolio )?statement as on\s*:?\s*",
                    "",
                    str(row.iloc[0]),
                    flags=re.IGNORECASE,
                )
            )
            disclosure_date = next(
                (
                    parsed
                    for candidate in date_candidates
                    if (parsed := _as_date(candidate)) is not None
                ),
                None,
            )
            break
    if disclosure_date is None:
        raise ValueError("A pension-fund scheme worksheet is missing its portfolio date.")

    total_value = None
    for _, row in frame.iloc[header_row + 1 :].iterrows():
        labels = [_normalize(value) for value in row.tolist()]
        if any(label.startswith("grand total") for label in labels):
            total_value = _number(_cell(row, columns["market_value"]))
            break

    records: list[SecurityPosition] = []
    instrument_type = "Other"
    for _, row in frame.iloc[header_row + 1 :].iterrows():
        labels = [_normalize(value) for value in row.tolist()]
        if any(label.startswith("grand total") for label in labels):
            break
        if any("subtotal" in label or label == "total" for label in labels):
            continue
        name = _string(_cell(row, columns["name"]))
        normalized_name = _normalize(name)
        if not normalized_name:
            continue
        section_type = _section_instrument_type(normalized_name)
        market_value = _number(_cell(row, columns["market_value"]))
        if section_type is not None and market_value is None:
            instrument_type = section_type
            continue
        if normalized_name in {
            "equity oriented mutual fund schemes",
            "government securities and related investments",
        }:
            continue
        if market_value is None or normalized_name in {
            "particulars",
            "equity instruments",
            "debt instruments",
            "government securities",
            "money market instruments",
        }:
            continue

        rating = _string(_cell(row, columns["rating"]))
        isin = _string(_cell(row, columns["isin"]))
        if (
            normalized_name.startswith("cash/")
            or any(term in normalized_name for term in ("overnight fund", "liquid fund", "treps"))
        ):
            security_type = "Money market"
        elif rating.upper() == "SOV" or _is_government_security(name, isin):
            security_type = "Government bond"
        elif instrument_type == "Equity":
            security_type = "Equity"
        elif instrument_type == "Corporate bond":
            security_type = "Corporate bond"
        elif instrument_type == "Money market":
            security_type = "Money market"
        else:
            security_type = instrument_type

        weight = _number(_cell(row, columns["weight"])) if columns["weight"] >= 0 else None
        if weight is None and total_value:
            weight = market_value / total_value
        if weight is None:
            continue
        if 1 < weight <= 100:
            weight /= 100
        if not -1 <= weight <= 1:
            raise ValueError(f"Invalid portfolio weight for {name!r}: {weight}")
        records.append(
            SecurityPosition(
                scheme_asset=scheme[0],
                tier=scheme[1],
                security_name=name,
                isin=isin,
                industry=_string(_cell(row, columns["industry"])),
                instrument_type=security_type,
                quantity=_number(_cell(row, columns["quantity"])),
                market_value=market_value,
                portfolio_weight=weight,
                rating=rating,
            )
        )
    if not records:
        raise ValueError("A pension-fund scheme worksheet has no readable security rows.")
    weight_total = sum(row.portfolio_weight for row in records)
    if abs(weight_total - 1.0) > 0.02:
        raise ValueError(
            f"Scheme portfolio weights sum to {weight_total:.2%}, not approximately 100%."
        )
    return disclosure_date, records


def _section_instrument_type(label: str) -> str | None:
    if (
        "equity instruments" in label
        or label == "equity"
        or label.startswith("equities and related investments")
    ):
        return "Equity"
    if (
        "government securit" in label
        or "state government loans" in label
        or "central government security" in label
    ):
        return "Government bond"
    if (
        "debt instruments" in label
        or "bond" in label
        or "ncd" in label
        or label == "debt"
    ):
        return "Corporate bond"
    if (
        "money market instruments" in label
        or "treasury bill" in label
        or "commercial paper" in label
    ):
        return "Money market"
    if "overnight funds" in label:
        return "Money market"
    if "real estate investment trust units" in label or "mutual fund units" in label:
        return "Other"
    if "alternative investment" in label or label.startswith("other "):
        return "Other"
    return None


def _is_government_security(name: str, isin: str) -> bool:
    normalized = _normalize(name)
    isin = isin.upper()
    return (
        "government security" in normalized
        or "state government" in normalized
        or " gsec" in f" {normalized}"
        or isin.startswith("IN002")
    )


def _asset_from_scheme_name(name: str) -> str | None:
    match = re.search(r"\bSCHEME\s+([ECG])\b", name.upper())
    if match is None:
        return None
    return match.group(1)


def _fund_manager_from_scheme_name(name: str) -> str | None:
    normalized = name.upper()
    for fund_manager in ("ICICI", "HDFC", "LIC"):
        if fund_manager in normalized:
            return fund_manager
    return None


def _cell(row: pd.Series, index: int) -> object:
    if index < 0 or index >= len(row):
        return None
    return row.iloc[index]


def _normalize(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower().rstrip(".")


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
        if not value or value in {"-", "—"}:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or pd.isna(value):
        return None
    for fmt in (
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %B %Y",
        "%d %b %Y",
    ):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None
