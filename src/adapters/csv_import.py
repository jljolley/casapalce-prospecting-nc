"""Manual-source CSV import adapter (Section 0 guardrails + Phase 6 of the build spec).

LiensNC requires an authenticated session, and county Clerk of Superior
Court sites / license-board lookups are UI-only portals -- Section 0's
guardrails explicitly forbid automating any of them. This adapter is what
lets those manual exports flow through the exact same
schema/geo/CRM/scoring/matching pipeline as the automated sources, without
ever scraping or logging into anything: a human exports a CSV/XLSX by hand,
drops it in, and everything downstream is identical.

One adapter, three config blocks (liensnc/lien/license in sources.yaml) --
same "one adapter, N configs" pattern as arcgis_permits.py. column_map keys
mirror the master schema fields directly, since a manual export can call its
columns whatever it wants.

Unlike the automated adapters, this reads one specific file path (from
`signals import --file`) rather than querying a live endpoint on a
schedule, so fetch() ignores `since` -- there's no incremental "since"
concept for a one-off manual export drop.
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import openpyxl
import yaml

from src.adapters.base import Adapter
from src.schema import Signal, normalize_company_name

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


class CSVImportAdapter(Adapter):
    """Parameterized by `source_name` -- one of liensnc/lien/license in sources.yaml."""

    def __init__(self, source_name: str, file_path: str):
        self.source_name = source_name
        self.file_path = file_path
        with open(CONFIG_DIR / "sources.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)[source_name]
        self.file_format = cfg.get("file_format", "csv")
        self.date_format = cfg.get("date_format", "%Y-%m-%d")
        self.column_map = cfg["column_map"]

    def _read_rows(self) -> list[dict]:
        if self.file_format == "xlsx":
            wb = openpyxl.load_workbook(self.file_path, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                return []
            header = [str(h).strip() if h is not None else "" for h in rows[0]]
            return [dict(zip(header, row)) for row in rows[1:]]
        if self.file_format == "csv":
            with open(self.file_path, newline="", encoding="utf-8-sig") as f:
                return list(csv.DictReader(f))
        raise ValueError(f"unsupported file_format: {self.file_format!r}")

    def fetch(self, since: date) -> list[dict]:
        return self._read_rows()

    def _parse_date(self, raw: object) -> Optional[date]:
        if raw in (None, ""):
            return None
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw
        try:
            return datetime.strptime(str(raw).strip(), self.date_format).date()
        except ValueError:
            return None

    def _parse_amount(self, raw: object) -> Optional[float]:
        if raw in (None, ""):
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        try:
            return float(str(raw).replace(",", "").replace("$", "").strip())
        except ValueError:
            return None

    def normalize(self, raw: list[dict]) -> list[Signal]:
        cm = self.column_map
        out = []
        for i, r in enumerate(raw):
            company = str(r.get(cm["company_name"]) or "").strip() if cm.get("company_name") else ""

            record_id = r.get(cm["source_record_id"]) if cm.get("source_record_id") else None
            record_id = str(record_id).strip() if record_id else f"{self.source_name}-row{i}"

            address = str(r.get(cm["address"]) or "").strip() if cm.get("address") else ""
            county = str(r.get(cm["county"]) or "").strip() or None if cm.get("county") else None
            zip_code = str(r.get(cm["zip"]) or "").strip() or None if cm.get("zip") else None
            city = str(r.get(cm["city"]) or "").strip() or None if cm.get("city") else None

            value_amount = self._parse_amount(r.get(cm["value_amount"])) if cm.get("value_amount") else None
            signal_detail = str(r.get(cm["signal_detail"]) or "").strip() if cm.get("signal_detail") else ""
            signal_date = self._parse_date(r.get(cm["signal_date"])) if cm.get("signal_date") else None

            out.append(Signal(
                source=self.source_name,
                source_record_id=record_id,
                signal_date=signal_date,
                company_name=company,
                company_name_norm=normalize_company_name(company),
                address=address,
                county=county,
                zip_code=zip_code,
                city=city,
                value_amount=value_amount,
                signal_detail=signal_detail,
                raw_json=json.dumps(r, default=str),
            ))
        return out
