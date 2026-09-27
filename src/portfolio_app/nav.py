from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

LATEST_URL = "https://npsnav.in/api/latest"

_FAMILIES = (
    "ICICI",
    "HDFC",
    "KOTAK",
    "ADITYA BIRLA",
    "BIRLA",
    "TATA",
    "AXIS",
    "DSP",
    "MAX LIFE",
    "UTI",
    "LIC",
    "SBI",
)


@dataclass
class FundNav:
    scheme_code: str
    scheme_name: str
    nav: float
    nav_date: date


@dataclass
class SchemeMatch:
    statement_name: str
    matches: list[FundNav]

    @property
    def unique(self) -> Optional[FundNav]:
        if len(self.matches) == 1:
            return self.matches[0]
        return None


def fetch_latest(url: str = LATEST_URL) -> list[FundNav]:
    request = urllib.request.Request(url, headers={"User-Agent": "nps-portfolio"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    funds = []
    for row in payload.get("data", []):
        funds.append(
            FundNav(
                scheme_code=str(row["Scheme Code"]),
                scheme_name=str(row["Scheme Name"]),
                nav=float(row["NAV"]),
                nav_date=_parse_nav_date(str(row["Last Updated"])),
            )
        )
    return funds


def match_scheme(statement_name: str, funds: list[FundNav]) -> SchemeMatch:
    wanted = _identity(statement_name)
    matches = [fund for fund in funds if _identity(fund.scheme_name) == wanted and wanted[1] and wanted[2]]
    return SchemeMatch(statement_name, matches)


def _identity(name: str) -> tuple[str, str, str]:
    upper = re.sub(r"\s+", " ", name).upper()
    if "TIER II" in upper or "TIER 2" in upper:
        tier = "II"
    elif re.search(r"TIER I\b", upper) or "TIER 1" in upper:
        tier = "I"
    else:
        tier = ""
    letter_match = re.search(r"SCHEME\s+([ECGA])\b", upper)
    letter = letter_match.group(1) if letter_match else ""
    family = ""
    for candidate in _FAMILIES:
        if candidate in upper:
            family = "BIRLA" if "BIRLA" in candidate else candidate.split()[0]
            break
    return family, letter, tier


def _parse_nav_date(value: str) -> date:
    text = value.strip()
    for fmt in ("%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised NAV date: {value!r}")
