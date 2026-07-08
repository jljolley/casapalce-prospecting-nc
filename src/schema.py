"""Master signal schema (Section 4 of the build spec).

One row per signal, natural key = (source, source_record_id). Every adapter's
normalize() returns a list of these regardless of source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

SOURCES = [
    "osha",
    "permit_meck",
    "permit_raleigh",
    "permit_durham",
    "permit_greensboro",
    "permit_winstonsalem",
    "liensnc",
    "lien",
    "license",
]

_COMPANY_SUFFIXES = {"LLC", "INC", "CORP", "CO", "LP", "PLLC"}


def normalize_company_name(name: str) -> str:
    """Uppercase, strip common suffixes, collapse whitespace/punctuation.

    Used everywhere company identity matters: signal compounding matching
    (matching.py) AND CRM matching (crm.py). Consistency here is what makes
    both fuzzy-match steps work.
    """
    if not name:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", name.upper())
    tokens = [t for t in cleaned.split() if t not in _COMPANY_SUFFIXES]
    return " ".join(tokens)


@dataclass
class Signal:
    source: str
    source_record_id: str

    signal_date: Optional[date] = None
    company_name: str = ""
    company_name_norm: str = ""
    side: str = "Unknown"          # Partner | Pro | Unknown
    address: str = ""
    county: Optional[str] = None   # derived by geo.py
    hub: Optional[str] = None      # derived by geo.py
    value_amount: Optional[float] = None
    signal_detail: str = ""        # permit type / inspection type / license class
    raw_json: str = ""

    score: Optional[int] = None
    compounding: int = 1           # count of distinct sources matched to this company
    first_seen: Optional[datetime] = None

    in_crm: int = 0
    crm_match_name: Optional[str] = None
    crm_status: Optional[str] = None
    crm_match_score: Optional[int] = None

    # Transient geo inputs -- consumed by geo.resolve(), not persisted to the
    # signals table (county/hub are the derived, persisted result).
    zip_code: Optional[str] = None
    city: Optional[str] = None
