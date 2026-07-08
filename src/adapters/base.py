"""Adapter contract (Section 2/3 of the build spec).

Every source -- automated or manual -- implements this same interface, so the
pipeline downstream of fetch/normalize (geo -> db -> CRM dedupe -> scoring ->
matching -> export) is identical regardless of source. Adapters stay dumb:
fetch + map to schema, nothing else.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from src.schema import Signal


class Adapter(ABC):
    source_name: str

    @abstractmethod
    def fetch(self, since: date) -> list[dict]:
        """Raw records from the source, filtered to `since` where the source supports it."""
        ...

    @abstractmethod
    def normalize(self, raw: list[dict]) -> list[Signal]:
        """Map raw records to the master Signal schema."""
        ...
