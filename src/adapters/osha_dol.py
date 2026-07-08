"""OSHA / NC DOL inspections adapter (Section 1.1 of the build spec).

Modern API path, DOL Developer Portal open data API. Endpoint confirmed live
against https://apiprod.dol.gov/v4/datasets on 2026-07-08: agency=OSHA,
api_url=inspection, tablename=OSHA_inspection. See config/sources.yaml for the
exact base_url -- don't hard-code a guessed one here.

Auth is the X-API-KEY as a query parameter. Pagination is offset/limit; the
API does not return a total record count, so we page until a page comes back
shorter than page_size.

CSV-catalog fallback: the spec's original bulk-CSV catalog domain
(enforcedata.dol.gov/views/data_catalogs.php) now 301-redirects into a JS
SPA at data.dol.gov -- the standalone flat-file catalog no longer exists at
that URL. Its live replacement is the same apiprod.dol.gov endpoint with
format=csv instead of json (confirmed live 2026-07-08, identical fields).
When the JSON request fails, fetch() retries once via CSV before giving up.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

import requests
import yaml

from src.adapters.base import Adapter
from src.schema import Signal, normalize_company_name

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"

# insp_type / safety_hlth code legends, from the live metadata endpoint
# (GET /v4/get/OSHA/inspection/json/metadata), confirmed 2026-07-08.
INSP_TYPE_LABELS = {
    "A": "Accident", "B": "Complaint", "C": "Referral", "D": "Monitoring",
    "E": "Variance", "F": "FollowUp", "G": "Unprog Rel", "H": "Planned",
    "I": "Prog Related", "J": "Unprog Other", "K": "Prog Other", "L": "Other",
    "M": "Fat/Cat", "N": "Unprog Emph",
}
SAFETY_HLTH_LABELS = {"S": "Safety", "H": "Health"}

# DOL prepends an internal establishment id to estab_name, e.g.
# "161486 - KEBLG LLC" -- strip it so company_name is just the company.
_ESTAB_PREFIX_RE = re.compile(r"^\d+\s*-\s*")


def _clean_company_name(estab_name: str) -> str:
    if not estab_name:
        return ""
    return _ESTAB_PREFIX_RE.sub("", estab_name).strip()


class OshaDolAdapter(Adapter):
    source_name = "osha"

    def __init__(self):
        with open(CONFIG_DIR / "sources.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)["osha"]
        self.base_url = cfg["base_url"]
        self.state = cfg["state"]
        self.naics_prefix = cfg["naics_prefix"]
        self.page_size = cfg.get("page_size", 5000)
        self.date_field = cfg.get("date_field", "open_date")

        api_key_env = cfg["api_key_env"]
        self.api_key = os.environ.get(api_key_env)
        if not self.api_key:
            raise RuntimeError(
                f"{api_key_env} not set -- copy .env.example to .env and add your DOL API key"
            )

    def _filter_object(self, since: date) -> str:
        return json.dumps({
            "and": [
                {"field": "site_state", "operator": "eq", "value": self.state},
                {"field": "naics_code", "operator": "like", "value": f"{self.naics_prefix}%"},
                {"field": self.date_field, "operator": "gt", "value": since.isoformat()},
            ]
        })

    def _fetch_page(self, fmt: str, filter_obj: str, offset: int) -> requests.Response:
        url = self.base_url.rsplit("/", 1)[0] + f"/{fmt}"
        return requests.get(
            url,
            params={
                "X-API-KEY": self.api_key,
                "limit": self.page_size,
                "offset": offset,
                "filter_object": filter_obj,
                "sort": "desc",
                "sort_by": self.date_field,
            },
            headers={"User-Agent": "casaplace-signals/0.1 (contact: jolley.jonathan@gmail.com)"},
            timeout=30,
        )

    def fetch(self, since: date) -> list[dict]:
        filter_obj = self._filter_object(since)
        records: list[dict] = []
        offset = 0
        while True:
            try:
                resp = self._fetch_page("json", filter_obj, offset)
                resp.raise_for_status()
                page = resp.json().get("data", [])
            except (requests.RequestException, ValueError):
                # CSV-catalog fallback -- same endpoint, format=csv (see module docstring)
                resp = self._fetch_page("csv", filter_obj, offset)
                resp.raise_for_status()
                page = list(csv.DictReader(io.StringIO(resp.text)))

            records.extend(page)
            if len(page) < self.page_size:
                break
            offset += self.page_size
            time.sleep(0.5)  # be a good API citizen: <=1-2 req/sec

        return records

    def normalize(self, raw: list[dict]) -> list[Signal]:
        out = []
        for r in raw:
            company = _clean_company_name(r.get("estab_name") or "")

            insp_label = INSP_TYPE_LABELS.get(r.get("insp_type"), r.get("insp_type") or "")
            safety_hlth = SAFETY_HLTH_LABELS.get(r.get("safety_hlth"), "")
            detail = " / ".join(p for p in (insp_label, safety_hlth) if p)

            signal_date = None
            if r.get("open_date"):
                signal_date = datetime.fromisoformat(r["open_date"]).date()

            out.append(Signal(
                source=self.source_name,
                source_record_id=str(r["activity_nr"]),
                signal_date=signal_date,
                company_name=company,
                company_name_norm=normalize_company_name(company),
                address=r.get("site_address") or "",
                zip_code=r.get("site_zip"),
                city=r.get("site_city"),
                signal_detail=detail,
                raw_json=json.dumps(r),
            ))
        return out
