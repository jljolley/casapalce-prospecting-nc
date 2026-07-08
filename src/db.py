"""SQLite state: create/upsert/dedupe (Section 2/4 of the build spec)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.schema import Signal

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "signals.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            signal_date TEXT,
            company_name TEXT,
            company_name_norm TEXT,
            side TEXT,
            address TEXT,
            county TEXT,
            hub TEXT,
            value_amount REAL,
            signal_detail TEXT,
            raw_json TEXT,
            score INTEGER,
            compounding INTEGER DEFAULT 1,
            first_seen TEXT,
            in_crm INTEGER DEFAULT 0,
            crm_match_name TEXT,
            crm_status TEXT,
            crm_match_score INTEGER,
            UNIQUE(source, source_record_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched INTEGER,
            new_count INTEGER,
            dup_count INTEGER
        )
    """)
    conn.commit()


def upsert_signals(conn: sqlite3.Connection, signals: list[Signal]) -> tuple[int, int]:
    """Insert new signals, update existing ones. Natural key = (source, source_record_id)."""
    new_count = 0
    dup_count = 0
    for s in signals:
        existing = conn.execute(
            "SELECT id FROM signals WHERE source = ? AND source_record_id = ?",
            (s.source, s.source_record_id),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE signals SET
                    signal_date = ?, company_name = ?, company_name_norm = ?, side = ?,
                    address = ?, county = ?, hub = ?, value_amount = ?, signal_detail = ?,
                    raw_json = ?, compounding = ?
                WHERE source = ? AND source_record_id = ?
                """,
                (
                    s.signal_date.isoformat() if s.signal_date else None,
                    s.company_name, s.company_name_norm, s.side, s.address,
                    s.county, s.hub, s.value_amount, s.signal_detail, s.raw_json,
                    s.compounding, s.source, s.source_record_id,
                ),
            )
            dup_count += 1
        else:
            first_seen = (
                s.first_seen.isoformat() if s.first_seen
                else datetime.now(timezone.utc).isoformat()
            )
            conn.execute(
                """
                INSERT INTO signals
                    (source, source_record_id, signal_date, company_name, company_name_norm,
                     side, address, county, hub, value_amount, signal_detail, raw_json,
                     compounding, first_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    s.source, s.source_record_id,
                    s.signal_date.isoformat() if s.signal_date else None,
                    s.company_name, s.company_name_norm, s.side, s.address,
                    s.county, s.hub, s.value_amount, s.signal_detail, s.raw_json,
                    s.compounding, first_seen,
                ),
            )
            new_count += 1
    conn.commit()
    return new_count, dup_count


def log_run(conn: sqlite3.Connection, source: str, fetched: int, new_count: int, dup_count: int) -> None:
    conn.execute(
        "INSERT INTO run_log (ts, source, fetched, new_count, dup_count) VALUES (?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), source, fetched, new_count, dup_count),
    )
    conn.commit()
