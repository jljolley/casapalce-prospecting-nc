"""CRM export dedupe (Section 4a of the build spec).

The point: don't hand Jonathan a "fresh lead" that's already a customer or a
live opportunity. Every signal is matched against a CRM export before
scoring; matches against a status in statuses_to_exclude get in_crm=1, which
triggers the already_in_crm scoring penalty and routes them to the "Already
in CRM" export tab.

CRM-agnostic: works off a CSV/XLSX export from any CRM. config/crm.yaml maps
that CRM's column names to what we need -- switching CRMs is a config edit,
not a code change. Read-only: we only ever consume the export, never write
back to it.

Domain-exact matching and the city tie-breaker are implemented per spec but
are currently no-ops: the master schema (Section 4) has no domain or city
column on signals (none of the 5 permit feeds + OSHA reliably expose a
company domain/email), so there is nothing on the signal side to compare
against yet. Both activate automatically the moment a future source
populates those fields -- no code change needed then either.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Optional

import openpyxl
import yaml
from rapidfuzz import fuzz

from src.schema import normalize_company_name

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_crm_config() -> dict:
    with open(CONFIG_DIR / "crm.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _read_rows(path: str, file_format: str) -> list[dict]:
    if file_format == "xlsx":
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        return [dict(zip(header, row)) for row in rows[1:]]
    if file_format == "csv":
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    raise ValueError(f"unsupported crm file_format: {file_format!r}")


def _domain_from_email(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    return email.split("@", 1)[1].strip().lower() or None


def init_crm_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crm_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            company_name_norm TEXT,
            domain TEXT,
            status TEXT,
            city TEXT,
            raw_json TEXT
        )
    """)
    conn.commit()


def load_crm(conn: sqlite3.Connection, file_path: str) -> int:
    """Truncate + reload crm_accounts from a CRM export. The export is the
    source of truth -- idempotent, never writes back to the CRM."""
    init_crm_table(conn)
    cfg = load_crm_config()
    col = cfg["column_map"]
    exclude_statuses = set(cfg.get("statuses_to_exclude", []))

    raw_rows = _read_rows(file_path, cfg.get("file_format", "csv"))

    # Per-contact export -- multiple rows can share one company. Fold to one
    # crm_accounts row per company_name_norm.
    grouped: dict[str, dict] = {}
    for row in raw_rows:
        company = (row.get(col["company"]) or "").strip() if col.get("company") else ""
        if not company:
            continue
        norm = normalize_company_name(company)

        domain = None
        if col.get("domain"):
            domain = (row.get(col["domain"]) or "").strip().lower() or None
        elif col.get("email"):
            domain = _domain_from_email(row.get(col["email"]))

        status = (row.get(col["status"]) or "").strip() if col.get("status") else ""
        city = (row.get(col["city"]) or "").strip() if col.get("city") else None

        acct = grouped.get(norm)
        if acct is None:
            grouped[norm] = {
                "company_name": company,
                "company_name_norm": norm,
                "domain": domain,
                "status": status,
                "city": city,
                "contacts": [row],
            }
            continue

        acct["contacts"].append(row)
        if not acct["domain"] and domain:
            acct["domain"] = domain
        if not acct["city"] and city:
            acct["city"] = city
        # Most-advanced status wins: if any contact at this company is
        # already Customer/Opportunity, the company counts as already
        # handled even if another contact row is still a cold Lead.
        if acct["status"] not in exclude_statuses and status in exclude_statuses:
            acct["status"] = status

    conn.execute("DELETE FROM crm_accounts")
    for acct in grouped.values():
        conn.execute(
            """INSERT INTO crm_accounts
               (company_name, company_name_norm, domain, status, city, raw_json)
               VALUES (?,?,?,?,?,?)""",
            (
                acct["company_name"], acct["company_name_norm"], acct["domain"],
                acct["status"], acct["city"], json.dumps(acct["contacts"], default=str),
            ),
        )
    conn.commit()
    return len(grouped)


def match_signals(conn: sqlite3.Connection) -> int:
    """Match every signal against loaded crm_accounts. Idempotent -- only
    refreshes in_crm/crm_match_name/crm_status/crm_match_score on the
    signals table; never touches crm_accounts. No-op if crm_accounts is
    empty (no CRM export loaded yet)."""
    init_crm_table(conn)
    accounts = conn.execute("SELECT * FROM crm_accounts").fetchall()
    if not accounts:
        return 0

    cfg = load_crm_config()
    match_cfg = cfg.get("match", {})
    threshold = match_cfg.get("threshold", 88)
    review_low, review_high = match_cfg.get("review_band", [threshold - 8, threshold])
    use_domain_exact = match_cfg.get("use_domain_exact", True)
    exclude_statuses = set(cfg.get("statuses_to_exclude", []))

    signals = conn.execute("SELECT id, company_name_norm FROM signals").fetchall()
    matched = 0

    for sig in signals:
        sig = dict(sig)
        sig_norm = sig.get("company_name_norm")
        if not sig_norm:
            continue

        best = None  # (score, account_row)

        sig_domain = sig.get("domain")  # always None today -- see module docstring
        if use_domain_exact and sig_domain:
            domain_hits = [a for a in accounts if a["domain"] and a["domain"] == sig_domain]
            if domain_hits:
                best = (100, domain_hits[0])

        if best is None:
            candidates = [
                (fuzz.token_sort_ratio(sig_norm, a["company_name_norm"]), a)
                for a in accounts if a["company_name_norm"]
            ]
            if candidates:
                candidates.sort(key=lambda c: c[0], reverse=True)
                top_score = candidates[0][0]
                tied = [c for c in candidates if c[0] == top_score]
                if len(tied) > 1:
                    # City tie-breaker (spec 4a step 5) -- inert today, see
                    # module docstring: signals have no persisted city to
                    # compare against, so this just takes the first tie.
                    sig_city = sig.get("city")
                    city_matches = [c for c in tied if sig_city and c[1]["city"] == sig_city]
                    best = city_matches[0] if city_matches else tied[0]
                else:
                    best = tied[0]

        if best is None:
            continue
        score, acct = best

        if score >= threshold:
            is_handled = (not exclude_statuses) or (acct["status"] in exclude_statuses)
            conn.execute(
                "UPDATE signals SET in_crm=?, crm_match_name=?, crm_status=?, crm_match_score=? WHERE id=?",
                (1 if is_handled else 0, acct["company_name"], acct["status"], score, sig["id"]),
            )
            matched += 1
        elif review_low <= score < threshold:
            conn.execute(
                "UPDATE signals SET in_crm=0, crm_match_name=?, crm_status=?, crm_match_score=? WHERE id=?",
                (acct["company_name"], acct["status"], score, sig["id"]),
            )
        else:
            conn.execute(
                "UPDATE signals SET in_crm=0, crm_match_name=NULL, crm_status=NULL, crm_match_score=NULL WHERE id=?",
                (sig["id"],),
            )

    conn.commit()
    return matched
