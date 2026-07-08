"""Compounding-signal detection (Section 7 of the build spec) -- the money feature.

The single highest-value output: one company appearing in >=2 sources (e.g.
a Mecklenburg permit AND an OSHA inspection for the same company). Groups
signals by company_name_norm (exact match, the primary and most reliable
signal), then fuzzy-merges near-duplicate names across groups to catch
spelling variants -- OSHA's naming especially is not standardized.

Fuzzy matching on the RAW normalized name is unreliable for company names:
against this project's real ingested data, plain rapidfuzz token_sort_ratio
merged "ADI CONSTRUCTION" with "AIA CONSTRUCTION" (93.8%) and "MAURER GENERAL
CONTRACTORS" with "PARKER GENERAL CONTRACTORS" (92.3%) -- clearly different
companies that just share generic industry words. There is no single
threshold that separates these from genuine variants like "SMITH BROTHERS
CONSTRUCTION" / "SMITH BROS CONSTRUCTION" (92.0%) -- the false positives and
the real matches overlap in the same score range. So before fuzzy-comparing,
strip generic industry boilerplate (construction, contractors, group, the,
...) from both names -- this collapses the false positives to ~67% (no
longer confusable) while genuine variants still land at 80%+, since the
comparison now focuses on the part of the name that actually identifies the
company.

That stripping introduces its own failure mode, though: once boilerplate is
gone, some stripped cores are just one short word, and single-character
surname collisions ("PARKER" vs "BARKER", 83.3%) score identically to
genuine variants ("SMITH BROTHERS" vs "SMITH BROS", also 83.3%) -- no
threshold separates them. So single-token stripped cores require a much
higher bar (90%, separates "PARKER"/"BARKER" at 83.3 from plausible real
variants like "MYRICK"/"MYRICKS" at 92.3) than multi-token cores (80%,
which is where multi-word variants like "SMITH BROTHERS"/"SMITH BROS" and
"THE CHRISTMAN"/"CHRISTMAN" actually land).

Deliberately run BEFORE scoring.score_all() in the pipeline (main.py's
`score` command), not after as the spec's flow diagram lists it: the scoring
formula (Section 6) reads `compounding` as an input
(`compounding * (compounding_count - 1)`), so compounding has to be current
*before* scoring runs for that bonus to be correct on a single pass, not one
run behind.
"""
from __future__ import annotations

import sqlite3

from rapidfuzz import fuzz

FUZZY_THRESHOLD = 80          # multi-token stripped cores
SINGLE_TOKEN_THRESHOLD = 90   # single-token stripped cores -- see module docstring

_GENERIC_WORDS = {
    "THE", "CONSTRUCTION", "CONTRACTORS", "CONTRACTING", "BUILDERS", "BUILDING",
    "GENERAL", "GROUP", "HOMES", "HOME", "COMPANY", "ENTERPRISES", "SERVICES",
    "DEVELOPMENT", "DEVELOPERS", "PARTNERS", "PROPERTIES", "PROPERTY",
}


def _strip_generic(name: str) -> str:
    tokens = [t for t in name.split() if t not in _GENERIC_WORDS]
    return " ".join(tokens) if tokens else name  # don't collapse an all-generic name to ""


def find_compounding(conn: sqlite3.Connection) -> int:
    """Cluster signals by (near-duplicate) company name; set compounding =
    count of distinct sources per cluster. Idempotent -- only touches the
    compounding column, and a signal with no company name is left at its
    default (1), un-clustered."""
    rows = conn.execute(
        "SELECT id, company_name_norm, source FROM signals "
        "WHERE company_name_norm IS NOT NULL AND company_name_norm != ''"
    ).fetchall()

    by_norm: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_norm.setdefault(r["company_name_norm"], []).append(r)

    names = list(by_norm.keys())
    stripped = {n: _strip_generic(n) for n in names}

    # Union-find over normalized name strings, merging near-duplicates.
    parent = {n: n for n in names}

    def find(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = stripped[names[i]], stripped[names[j]]
            is_single_token = " " not in a and " " not in b
            threshold = SINGLE_TOKEN_THRESHOLD if is_single_token else FUZZY_THRESHOLD
            if fuzz.token_sort_ratio(a, b) >= threshold:
                union(names[i], names[j])

    clusters: dict[str, list[sqlite3.Row]] = {}
    for n in names:
        clusters.setdefault(find(n), []).extend(by_norm[n])

    updated = 0
    for cluster_rows in clusters.values():
        distinct_sources = len(set(r["source"] for r in cluster_rows))
        for r in cluster_rows:
            conn.execute("UPDATE signals SET compounding = ? WHERE id = ?", (distinct_sources, r["id"]))
            updated += 1

    conn.commit()
    return updated
