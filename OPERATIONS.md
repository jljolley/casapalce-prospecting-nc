# CasaPlace Signals Engine — Operations Guide

This is the day-to-day guide for *running* the tool. For how it's built and
why (architecture, per-source data-quality notes, design decisions), see
[README.md](README.md). This doc assumes it's already set up and working.

---

## 1. What runs automatically vs. what you do by hand

| | Automatic | Manual |
|---|---|---|
| OSHA + 5 permit feeds | ✅ every Monday 6:00 AM | -- |
| Scoring / compounding / export | ✅ same run | -- |
| CRM refresh (HubSpot) | ❌ no live API | you run `crm-load` |
| LiensNC / lien / license imports | ❌ no safe API (Section 0 guardrail) | you run `import` |

The scheduled task (`CasaPlace Signals Weekly`, runs as `SYSTEM`) only does
the left column. Nothing in this tool ever writes back to HubSpot, scrapes a
county Clerk site, or logs into LiensNC — those three inputs are
intentionally manual.

---

## 2. Weekly automated cycle — what to expect

Every Monday at 6:00 AM, whether or not you're logged in:

1. Pulls OSHA + Mecklenburg/Raleigh/Durham/Greensboro/Winston-Salem permits
   for the last 10 days (a few days of overlap buffer past the 7-day gap —
   re-fetching the same record just updates it, never duplicates).
2. Matches against whatever CRM export is currently loaded (stale is fine,
   see below).
3. Re-clusters compounding signals and rescoring everything.
4. Writes `output/signals_<date>.xlsx`.

**Check it ran:**
```powershell
schtasks /query /tn "CasaPlace Signals Weekly" /fo LIST /v | Select-String "Last Run Time|Last Result"
Get-Content "C:\Users\Jonathan Jolley\casaplace-signals\output\run_weekly.log" -Tail 20
```
`Last Result` should be `0`. The log ends in `Completed successfully` on a
good run, or `FAILED: <error>` on a bad one — see [Section 5](#5-troubleshooting).

**If it didn't run:** the task runs as `SYSTEM` specifically so logon state
doesn't matter, but if the machine itself was off/asleep at 6:00 AM Monday,
Windows does not retroactively fire a missed task. Trigger it manually:
```powershell
schtasks /run /tn "CasaPlace Signals Weekly"
```

---

## 3. Your recurring manual steps

### Refreshing the CRM export (whenever you have a new HubSpot export)

```
python -m src.main crm-load --file path\to\new_hubspot_export.xlsx
```

This is a full truncate + reload of `crm_accounts` — the export is always
the source of truth, nothing is merged from the old load. Do this *before*
the next scheduled run picks it up, or just run `score` again immediately
afterward to refresh matches against the new data:

```
python -m src.main score
python -m src.main export
```

There's no fixed cadence required by the tool — the weekly job just uses
whatever's currently loaded, so a stale CRM export only means stale
`in_crm`/`crm_status` flags, not a broken run. Refresh it as often as your
HubSpot pipeline actually changes (weekly-ish is reasonable).

### Importing LiensNC / county lien / license-board exports

Whenever you manually pull one of these (see Section 0 of the build spec
for why they're not automated):

```
python -m src.main import --source liensnc --file path\to\export.csv
python -m src.main import --source lien --file path\to\export.csv
python -m src.main import --source license --file path\to\export.csv
python -m src.main score
python -m src.main export
```

**First time using one of these:** `config/sources.yaml`'s `liensnc`/`lien`/
`license` blocks ship with placeholder column headers (no real sample was
available at build time). Open the real export, check its actual column
headers, and edit `column_map` in the relevant block to match — no code
change needed, same as the CRM column mapping.

---

## 4. Reading the output

`output/signals_<date>.xlsx` has four tabs:

1. **Hot - multi-signal** — companies appearing in 2+ sources (e.g. an OSHA
   inspection *and* a building permit for the same company). This is the
   single highest-value list — handle these with a personal touch, not the
   standard sequence. CRM matches excluded by default.
2. **All signals ranked** — everything else, sorted by score descending.
3. **By hub** — same as Tab 2, narrowed to Triangle + Triad (the focus
   markets) unless you exported with an explicit `--hub`.
4. **Already in CRM** — signals matched to an existing HubSpot account
   whose stage counts as "already handled" (see `statuses_to_exclude` in
   `crm.yaml`). Kept separate so nobody works them as cold, but still shown
   — a fresh permit on an account you already own is useful intel for
   whoever's working that relationship.

**Score** is 0–100, additive: base points per source + focus-hub bonus +
ICP-fit bonus + compounding bonus − residential/small-value penalty −
already-in-CRM penalty. **Compounding** is the count of distinct sources
that matched to that company.

**Useful export filters:**
```
python -m src.main export --hub Triangle,Triad --min-score 60
python -m src.main export --include-crm          # merge CRM matches back in
python -m src.main export --format csv           # flat file for bulk CRM import
```

---

## 5. Troubleshooting

**Weekly run shows `FAILED:` in the log.** Read the error message in
`output/run_weekly.log` — it's the actual exception, not a generic message.
Common causes:

- **DOL API key rejected (401):** the OSHA adapter needs `DOL_API_KEY` in
  `.env`. Keys don't normally expire, but if yours was regenerated on the
  DOL portal, update `.env` and re-run.
- **An ArcGIS endpoint moved or a field was renamed:** government GIS
  endpoints occasionally get migrated (this happened once already during
  the build — see README's Phase 3 notes on Durham/Winston-Salem). If one
  permit feed starts failing, the whole `run-all` chain stops at that
  source — run sources individually to isolate:
  ```
  python -m src.main pull --source permit_meck --since 2026-07-01
  python -m src.main pull --source permit_raleigh --since 2026-07-01
  # ... etc, one at a time
  ```
  Whichever one throws is the one whose `layer_url`/`field_map` in
  `config/sources.yaml` needs re-discovering against the live service.
- **Network blip / transient timeout:** just re-run
  `schtasks /run /tn "CasaPlace Signals Weekly"` — every stage is
  idempotent, safe to re-run.

**A signal looks wrong (wrong hub, wrong score, missing company name).**
Every signal keeps its original `raw_json` in `data/signals.db` for
auditing:
```
python -c "import sqlite3; c=sqlite3.connect('data/signals.db'); c.row_factory=sqlite3.Row; print(dict(c.execute('SELECT * FROM signals WHERE source_record_id=?', ('<id>',)).fetchone()))"
```

**Something in `config/*.yaml` doesn't parse.** All configs are read fresh
on every command — a YAML syntax error will surface immediately as a
traceback the next time you run anything, not silently.

---

## 6. Tuning knobs (all config, zero code changes)

| Want to change... | Edit |
|---|---|
| Scoring weights, ICP value band, residential penalty | `config/scoring.yaml` |
| Which hubs get the focus-hub bonus | `config/scoring.yaml` -> `focus_hubs` |
| County -> hub grouping | `config/counties.yaml` |
| CRM match sensitivity (fuzzy threshold, review band) | `config/crm.yaml` -> `match` |
| Which CRM stages count as "already handled" | `config/crm.yaml` -> `statuses_to_exclude` |
| Enable/disable an automated source | `config/sources.yaml` -> `<source>.enabled` |
| Add a 6th ArcGIS permit jurisdiction | new `permit_*` block in `config/sources.yaml` |
| Map a different CRM's export columns | `config/crm.yaml` -> `column_map` |
| Map a real LiensNC/lien/license export's columns | `config/sources.yaml` -> `liensnc`/`lien`/`license` -> `column_map` |

After any config edit, just re-run `score` + `export` (no need to re-pull) —
scores and clusters are recomputed from what's already in `data/signals.db`.

---

## 7. Files that matter

- **`data/signals.db`** — all ingested state (SQLite). Gitignored, never
  committed, never manually edited. Delete it only if you want to start
  completely fresh (loses `first_seen` history and forces a full re-pull).
- **`output/*.xlsx` / `*.csv`** — dated exports, safe to delete old ones
  any time; the tool only ever reads from the database, never from a prior
  export.
- **`output/run_weekly.log`** — capped at ~2000 lines (auto-trims), safe to
  delete or read any time.
- **`.env`** — `DOL_API_KEY`. Never commit this (it's gitignored).
- **`config/*.yaml` + `config/nc_zip_county.csv`** — all tunable behavior.
  These *are* committed to git, so config changes are version-controlled.

## 8. Quick command reference

```
python -m src.main run-all --since 2026-06-01 --crm-file path\to\export.xlsx  # everything
python -m src.main pull --source osha --since 2026-06-01                      # one automated source
python -m src.main import --source liensnc --file path\to\export.csv         # one manual source
python -m src.main crm-load --file path\to\export.xlsx                        # refresh CRM only
python -m src.main score                                                      # recompute from existing data
python -m src.main export --hub Triangle,Triad --min-score 60 --exclude-crm   # filtered export
```
