"""Deterministic, additive scoring (Section 6 of the build spec).

Fully config-driven -- changing a weight in config/scoring.yaml must never
require a code change. The already_in_crm modifier is a no-op until Phase 4
sets in_crm, and the compounding modifier is a no-op until Phase 5's
matching.py sets compounding > 1; both are safe to leave wired up now.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_scoring_config() -> dict:
    with open(CONFIG_DIR / "scoring.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _is_residential(signal_detail: str, cfg: dict) -> bool:
    if not signal_detail:
        return False
    text = signal_detail.lower()
    if any(kw in text for kw in cfg.get("commercial_keywords", [])):
        return False
    return any(kw in text for kw in cfg.get("residential_keywords", []))


def score_signal(row: dict[str, Any], cfg: dict) -> int:
    base_points = cfg["base_points"]
    modifiers = cfg["modifiers"]
    focus_hubs = set(cfg.get("focus_hubs", []))
    icp_low, icp_high = cfg["icp_value_band"]
    small_threshold = cfg["small_value_threshold"]

    score = base_points.get(row["source"], 0)

    if row.get("hub") in focus_hubs:
        score += modifiers["focus_hub"]

    value = row.get("value_amount")
    if value is not None and icp_low <= value <= icp_high:
        score += modifiers["icp_fit"]

    is_small = value is not None and value < small_threshold
    is_residential = _is_residential(row.get("signal_detail") or "", cfg)
    if is_small or is_residential:
        score += modifiers["residential_or_small"]

    compounding = row.get("compounding") or 1
    score += modifiers["compounding"] * (compounding - 1)

    if row.get("in_crm"):
        score += modifiers["already_in_crm"]

    return max(0, min(100, round(score)))


def score_all(conn: sqlite3.Connection) -> int:
    cfg = load_scoring_config()
    rows = conn.execute("SELECT * FROM signals").fetchall()
    for row in rows:
        s = score_signal(dict(row), cfg)
        conn.execute("UPDATE signals SET score = ? WHERE id = ?", (s, row["id"]))
    conn.commit()
    return len(rows)
