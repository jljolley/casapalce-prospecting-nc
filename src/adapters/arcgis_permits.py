"""ArcGIS permit adapter (Section 1.2 of the build spec) -- ONE adapter, N configs.

All five county/city permit feeds publish through ArcGIS (FeatureServer /
MapServer) behind the same REST query interface, so one parameterized
adapter handles all of them: each jurisdiction is just a `permit_*` block in
config/sources.yaml with its layer URL + field map. If a feed needs a code
change here, the field_map abstraction is wrong -- fix that, not add
per-city code.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
import yaml

from src.adapters.base import Adapter
from src.schema import Signal, normalize_company_name

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
PAGE_SIZE = 1000


class ArcGISPermitAdapter(Adapter):
    """Parameterized by `source_name` -- one of the permit_* blocks in sources.yaml."""

    def __init__(self, source_name: str):
        self.source_name = source_name
        with open(CONFIG_DIR / "sources.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)[source_name]
        self.layer_url = cfg["layer_url"]
        self.date_field = cfg["date_field"]
        self.where_extra = cfg.get("where_extra")
        self.field_map = cfg["field_map"]
        # Optional raw-value -> canonical-label translation (e.g. Raleigh's
        # permitclassmapped uses "Non-Residential", which contains "residential"
        # as a literal substring -- scoring.py's keyword match would misread
        # that as the residential penalty. Translate to "Commercial" in config
        # instead of baking a one-off string fix into adapter code.
        self.permit_type_labels = cfg.get("permit_type_labels", {})
        # Some feeds (e.g. Durham's combined city+county layer) have no
        # address/zip field at all -- the whole feed is one fixed jurisdiction,
        # so skip geo.py's zip/city lookup and assign the county directly.
        self.static_county = cfg.get("static_county")

    def _where(self, since: date) -> str:
        clause = f"{self.date_field} >= timestamp '{since.isoformat()} 00:00:00'"
        if self.where_extra:
            clause += f" AND {self.where_extra}"
        return clause

    def fetch(self, since: date) -> list[dict]:
        where = self._where(since)
        records: list[dict] = []
        offset = 0
        while True:
            resp = requests.get(
                f"{self.layer_url}/query",
                params={
                    "where": where,
                    "outFields": "*",
                    "f": "json",
                    "resultOffset": offset,
                    "resultRecordCount": PAGE_SIZE,
                    "orderByFields": f"{self.date_field} DESC",
                },
                headers={"User-Agent": "casaplace-signals/0.1 (contact: jolley.jonathan@gmail.com)"},
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                raise RuntimeError(f"[{self.source_name}] ArcGIS query error: {body['error']}")

            features = body.get("features", [])
            records.extend(f["attributes"] for f in features)
            if len(features) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            time.sleep(0.5)  # be a good API citizen: <=1-2 req/sec

        return records

    def normalize(self, raw: list[dict]) -> list[Signal]:
        fm = self.field_map
        out = []
        for r in raw:
            company = str(r.get(fm["company_name"]) or "").strip() if fm.get("company_name") else ""
            address = str(r.get(fm["address"]) or "").strip() if fm.get("address") else ""
            city = str(r.get(fm["city"]) or "").strip() if fm.get("city") else None

            zip_raw = r.get(fm["zip"]) if fm.get("zip") else None
            zip_code = str(int(zip_raw)) if isinstance(zip_raw, float) else (str(zip_raw) if zip_raw else None)

            value_amount = r.get(fm["value_amount"]) if fm.get("value_amount") else None

            permit_type = r.get(fm["permit_type"]) if fm.get("permit_type") else None
            permit_type = self.permit_type_labels.get(permit_type, permit_type)
            permit_desc = r.get(fm["permit_desc"]) if fm.get("permit_desc") else None
            signal_detail = " - ".join(str(p).strip() for p in (permit_type, permit_desc) if p)

            record_id = r.get(fm["source_record_id"])

            signal_date = None
            raw_date = r.get(self.date_field)
            if raw_date:
                signal_date = datetime.fromtimestamp(raw_date / 1000, tz=timezone.utc).date()

            out.append(Signal(
                source=self.source_name,
                source_record_id=str(record_id),
                signal_date=signal_date,
                company_name=company,
                company_name_norm=normalize_company_name(company),
                address=address,
                county=self.static_county,
                zip_code=zip_code,
                city=city,
                value_amount=float(value_amount) if value_amount is not None else None,
                signal_detail=signal_detail,
                raw_json=json.dumps(r),
            ))
        return out
