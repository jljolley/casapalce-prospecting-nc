"""Phase-1 proof harness -- NOT a production source.

Hand-written fake signals that exercise the same fetch -> normalize contract
every real adapter uses, so geo -> db -> score -> export can be proven
end-to-end before any network call exists (Phase 1 of the build spec).

Covers, on purpose:
  FX-001  commercial, Wake (Triangle focus hub), in ICP value band -> high score
  FX-002  residential keyword, Guilford (Triad focus hub) -> residential penalty
  FX-003  commercial but below small_value_threshold, Mecklenburg (non-focus hub)
  FX-004  commercial, Dare (Northeast hub) -> non-focus hub, no bonus

`source="fixture"` has no entry in config/scoring.yaml base_points, so these
score on modifiers alone (base 0) -- that's expected here, not a bug; real
base points apply once Phase 2/3 wire up actual sources.
"""
from __future__ import annotations

import json
from datetime import date

from src.adapters.base import Adapter
from src.schema import Signal, normalize_company_name


class FixtureAdapter(Adapter):
    source_name = "fixture"

    _RAW = [
        {
            "id": "FX-001", "company": "Acme Builders LLC", "date": "2026-06-10",
            "address": "123 Main St", "city": "Raleigh", "zip": "27601",
            "value": 500000, "permit_type": "Commercial - New Construction",
        },
        {
            "id": "FX-002", "company": "Smith Home Builders Inc", "date": "2026-06-12",
            "address": "45 Oak Ave", "city": "Greensboro", "zip": "27401",
            "value": 300000, "permit_type": "Single Family Dwelling",
        },
        {
            "id": "FX-003", "company": "Small Jobs Co", "date": "2026-06-05",
            "address": "9 Elm St", "city": "Charlotte", "zip": "28202",
            "value": 40000, "permit_type": "Commercial Tenant Upfit",
        },
        {
            "id": "FX-004", "company": "Mystery Corp", "date": "2026-06-01",
            "address": "1 Nowhere Rd", "city": "Nags Head", "zip": "27959",
            "value": 2000000, "permit_type": "Commercial Office",
        },
    ]

    def fetch(self, since: date) -> list[dict]:
        return [r for r in self._RAW if date.fromisoformat(r["date"]) >= since]

    def normalize(self, raw: list[dict]) -> list[Signal]:
        out = []
        for r in raw:
            out.append(Signal(
                source=self.source_name,
                source_record_id=r["id"],
                signal_date=date.fromisoformat(r["date"]),
                company_name=r["company"],
                company_name_norm=normalize_company_name(r["company"]),
                address=r["address"],
                zip_code=r["zip"],
                city=r["city"],
                value_amount=r["value"],
                signal_detail=r["permit_type"],
                raw_json=json.dumps(r),
            ))
        return out
