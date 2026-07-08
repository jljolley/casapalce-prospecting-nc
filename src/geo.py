"""Address -> county -> hub resolution (Section 5 of the build spec).

Static lookup tables only -- no live geocoding API. Covers NC fully, offline
and free: county comes from an explicit field if present, else the bundled
zip->county table, else a city->county fallback; hub comes from counties.yaml.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
OTHER_HUB = "Other"


def _load_zip_county() -> dict[str, str]:
    m = {}
    with open(CONFIG_DIR / "nc_zip_county.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m[row["zip"].strip()] = row["county"].strip()
    return m


def _load_city_county() -> dict[str, str]:
    # Fallback only: city (lowercased) -> first county seen for that city.
    m = {}
    with open(CONFIG_DIR / "nc_zip_county.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = row["city"].strip().lower()
            m.setdefault(key, row["county"].strip())
    return m


def _load_county_hub() -> dict[str, str]:
    with open(CONFIG_DIR / "counties.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    county_to_hub = {}
    for hub, counties in raw.items():
        for county in counties:
            county_to_hub[county] = hub
    return county_to_hub


_ZIP_COUNTY = _load_zip_county()
_CITY_COUNTY = _load_city_county()
_COUNTY_HUB = _load_county_hub()


def resolve_county(
    *,
    county: Optional[str] = None,
    zip_code: Optional[str] = None,
    city: Optional[str] = None,
) -> Optional[str]:
    if county:
        return county.strip()
    if zip_code:
        z = zip_code.strip()[:5]
        if z in _ZIP_COUNTY:
            return _ZIP_COUNTY[z]
    if city:
        c = _CITY_COUNTY.get(city.strip().lower())
        if c:
            return c
    return None


def resolve_hub(county: Optional[str]) -> str:
    if not county:
        return OTHER_HUB
    return _COUNTY_HUB.get(county, OTHER_HUB)


def resolve(
    *,
    county: Optional[str] = None,
    zip_code: Optional[str] = None,
    city: Optional[str] = None,
) -> tuple[Optional[str], str]:
    resolved_county = resolve_county(county=county, zip_code=zip_code, city=city)
    return resolved_county, resolve_hub(resolved_county)
