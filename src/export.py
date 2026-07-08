"""Ranked xlsx/csv output (Section 8 of the build spec).

4-tab xlsx layout:
  Tab 1 "Hot - multi-signal": compounding >= 2 companies first, highest score
    at top. Excludes CRM matches by default -- these are net-new, multi-source
    prospects, the ones you most want clean.
  Tab 2 "All signals ranked": every non-CRM signal (when --exclude-crm),
    sorted by score desc.
  Tab 3 "By hub": same rows as Tab 2, further narrowed to the focus hubs
    (config/scoring.yaml -> focus_hubs) unless --hub already narrowed things.
  Tab 4 "Already in CRM": signals with in_crm=1, kept separate so nobody
    works them as cold -- but visible, since a fresh signal on an existing
    account is still useful intel for whoever owns that relationship.

--format csv writes a single flat file (the Tab-2 equivalent) for direct
CRM import; xlsx is the default full 4-tab workbook.
"""
from __future__ import annotations

import csv as csv_module
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

import openpyxl
import yaml

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

COLUMNS = [
    "score", "compounding", "company_name", "side", "source", "signal_date",
    "signal_detail", "address", "county", "hub", "value_amount",
    "in_crm", "crm_match_name",
]
CRM_TAB_COLUMNS = COLUMNS + ["crm_status", "crm_match_score"]


def _focus_hubs() -> list[str]:
    with open(CONFIG_DIR / "scoring.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("focus_hubs", [])


def _write_sheet(wb: openpyxl.Workbook, title: str, rows: list[sqlite3.Row], columns: list[str]) -> None:
    ws = wb.create_sheet(title=title)
    ws.append(columns)
    for row in rows:
        ws.append([row[c] for c in columns])


def ranked_export(
    conn: sqlite3.Connection,
    path: Optional[Path] = None,
    *,
    hubs: Optional[list[str]] = None,
    min_score: int = 0,
    exclude_crm: bool = True,
    fmt: str = "xlsx",
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ext = "csv" if fmt == "csv" else "xlsx"
    if path is None:
        path = OUTPUT_DIR / f"signals_{date.today().isoformat()}.{ext}"

    all_rows = conn.execute(
        "SELECT * FROM signals ORDER BY score DESC, signal_date DESC"
    ).fetchall()

    def passes(row: sqlite3.Row) -> bool:
        if hubs and row["hub"] not in hubs:
            return False
        if (row["score"] or 0) < min_score:
            return False
        return True

    filtered = [r for r in all_rows if passes(r)]
    crm_rows = [r for r in filtered if r["in_crm"]]
    non_crm_rows = [r for r in filtered if not r["in_crm"]] if exclude_crm else filtered

    if fmt == "csv":
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv_module.writer(f)
            writer.writerow(COLUMNS)
            for row in non_crm_rows:
                writer.writerow([row[c] for c in COLUMNS])
        return path

    hot_rows = [r for r in non_crm_rows if (r["compounding"] or 1) >= 2]
    hot_rows = sorted(hot_rows, key=lambda r: (-(r["compounding"] or 1), -(r["score"] or 0)))

    # "By hub" defaults to the configured focus hubs, but an explicit --hub
    # already narrowed non_crm_rows -- don't double up in that case.
    by_hub_filter = hubs if hubs else _focus_hubs()
    by_hub_rows = [r for r in non_crm_rows if not by_hub_filter or r["hub"] in by_hub_filter]

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "Hot - multi-signal", hot_rows, COLUMNS)
    _write_sheet(wb, "All signals ranked", non_crm_rows, COLUMNS)
    _write_sheet(wb, "By hub", by_hub_rows, COLUMNS)
    _write_sheet(wb, "Already in CRM", crm_rows, CRM_TAB_COLUMNS)
    wb.save(path)
    return path
