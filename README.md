# CasaPlace Signals Engine

Local tool that pulls NC construction compliance-pain signals, normalizes them
into one schema, scores them, detects compounding signals, and outputs a
ranked outreach list.

Build status: **Phase 7 of 7 -- feature-complete.** All acceptance criteria
(Section 10 of the build spec) verified against real data. See the build
spec for the full phase plan.

## Setup

```
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env       # then fill in DOL_API_KEY -- see "OSHA source" below
```

## Running it

Everything runs from the repo root with the venv active:

```
python -m src.main crm-load --file path\to\hubspot_export.xlsx
python -m src.main pull --source osha --since 2026-06-01
python -m src.main score
python -m src.main export
```

or all enabled sources at once (CRM file optional -- omit it to just refresh
against whatever's already loaded):

```
python -m src.main run-all --since 2026-06-01 --crm-file path\to\hubspot_export.xlsx
```

Output lands in `output/signals_<date>.xlsx`. State lives in `data/signals.db`
(SQLite, gitignored).

`fixture` is a hand-written dev-only source (`src/adapters/fixture.py`) used to
prove the pipeline before any real API is wired up -- it is not a government
data source and scores on scoring modifiers alone (no configured base points).

## OSHA source (Phase 2)

`osha` pulls NC construction-NAICS (23xxxx) inspections from the DOL
Developer Portal API (`apiprod.dol.gov`, `OSHA_inspection` table). You need a
free API key from https://dataportal.dol.gov/registration, saved in `.env` as
`DOL_API_KEY`. If the JSON endpoint fails, the adapter automatically retries
the same query with `format=csv` (the modern replacement for the spec's
original bulk-CSV catalog, whose old URL now redirects into a JS app with no
flat-file catalog left at that address).

## Permit sources (Phase 3)

One adapter (`src/adapters/arcgis_permits.py`), five `permit_*` config blocks
in `sources.yaml` -- each jurisdiction publishes through ArcGIS but with
different field names, so all differentiation lives in config, not code:

| source | layer type | company_name | address/geo | value_amount |
|---|---|---|---|---|
| `permit_meck` | county FeatureServer | owner only (no contractor field) | zip-based | yes |
| `permit_raleigh` | city FeatureServer | real contractor field | zip-based | yes |
| `permit_durham` | city+county MapServer | none (only free-text comments) | `static_county: Durham` | yes |
| `permit_greensboro` | city MapServer | real contractor field | `static_county: Guilford` | yes |
| `permit_winstonsalem` | county-wide FeatureServer (EnerGov) | none | `static_county: Forsyth` | **none -- not exposed by this feed** |

`static_county` skips geo.py's zip/city lookup for feeds that have no
address field but are scoped to one fixed jurisdiction. `permit_type_labels`
translates raw categorical values (e.g. Raleigh's `"Non-Residential"`) into
labels that are safe for scoring.py's keyword match -- `"residential"` is a
literal substring of `"non-residential"`, so passing it through unmodified
would trigger a false residential penalty.

To add a sixth jurisdiction: add a new `permit_*` block with its discovered
`layer_url` + `field_map` -- no code change. If a feed needs a code change,
the `field_map` abstraction is wrong; fix that instead.

## CRM dedupe (Phase 4)

`crm-load` reads a CRM export (CSV/XLSX, column mapping in `config/crm.yaml`)
into `crm_accounts`, then `score` matches every signal against it before
computing scores. Read-only -- never writes back to the CRM.

Your real HubSpot export is a **per-contact** export (multiple contacts can
share one company), so `crm.py` folds rows to one `crm_accounts` row per
normalized company name at load time. Domain is derived from `Email` (a
Contacts export has no separate "Company Domain Name" field).

**Suppression is gated by status**, not by "any match": `statuses_to_exclude`
in `crm.yaml` lists which CRM stages count as "already handled" enough to set
`in_crm=1` (the -15 penalty + routes to the "Already in CRM" tab in Phase 5).
A match against a merely cold "Lead" still gets `crm_match_name`/`crm_status`
stamped for visibility but is **not** suppressed -- surfacing a fresh signal
on a company you're tracking but haven't closed is exactly the point of
compounding-signal detection. Right now every account in your real export is
`Lead` except one (`Opportunity`) -- none are `Customer` yet, so the penalty
doesn't fire on anything today. That's an honest reflection of your current
pipeline stage, not a bug; it'll activate the moment HubSpot stages move.

**Tuning note:** matching against your real 127-company export produced ~50
review-band (score 80-87) hits, many of them false positives from generic
shared words ("Construction", "Group", "Contracting" are common across
unrelated NC builders). That's expected -- review-band matches are meant to
be eyeballed, not auto-trusted -- but if it's too noisy, raise
`match.threshold` / narrow `match.review_band` in `config/crm.yaml`; no code
change needed.

Domain-exact matching and the city tie-breaker are implemented per spec but
are currently no-ops: the master schema has no `domain`/`city` column on
signals (none of the 5 permit feeds + OSHA reliably expose a company email),
so there's nothing on the signal side to compare against yet. Both activate
automatically the moment a future source populates those fields.

## Compounding detection + export (Phase 5)

`score` now runs, in order: `crm.match_signals()` -> `matching.find_compounding()`
-> `scoring.score_all()`. That order deviates from the spec's run-all flow
diagram (which lists compounding matching *after* scoring) deliberately: the
scoring formula reads `compounding` as an input, so it has to be current
*before* scoring runs for that bonus to be correct in one pass rather than a
run behind.

**Fuzzy company matching on raw names is unreliable and I had to fix it
before shipping.** Against this project's own real ingested data, plain
rapidfuzz `token_sort_ratio` merged `"ADI CONSTRUCTION"` with `"AIA
CONSTRUCTION"` (93.8%) and `"MAURER GENERAL CONTRACTORS"` with `"PARKER
GENERAL CONTRACTORS"` (92.3%) -- different companies that just share generic
industry words, and there's no single threshold that separates those from
genuine variants like `"SMITH BROTHERS CONSTRUCTION"` / `"SMITH BROS
CONSTRUCTION"` (92.0%) -- the score ranges overlap. Fix: strip generic
industry boilerplate (construction, contractors, group, the, ...) before
comparing, which collapses false positives to ~67% while real variants stay
80%+. That in turn created a second failure mode -- short single-word
residuals like `"PARKER"` vs `"BARKER"` (83.3%) are indistinguishable from
genuine matches at the same score -- so single-token stripped cores require a
stricter 90% bar than multi-token cores (80%). See `src/matching.py`'s
docstring for the full reasoning. Verified against real data: all 3 false
positives gone, the one genuine cross-source match (Lennar Carolinas,
appearing in both an OSHA inspection and a Raleigh permit) survives.

`export.py` now writes the full 4-tab workbook: **Hot - multi-signal**
(compounding >= 2, CRM-excluded by default), **All signals ranked**,
**By hub** (defaults to `scoring.yaml`'s `focus_hubs` unless `--hub`
already narrowed things), and **Already in CRM** (`in_crm=1`, extra
`crm_status`/`crm_match_score` columns). CLI: `--hub`, `--min-score`,
`--exclude-crm`/`--include-crm`, `--format xlsx|csv` (csv writes a flat
"All signals ranked"-equivalent file for direct CRM import).

## Manual-source CSV import (Phase 6)

LiensNC requires an authenticated session, and county Clerk of Superior
Court sites / license-board lookups are UI-only portals -- none of these are
safe to automate (Section 0's guardrails). `src/adapters/csv_import.py` is
one adapter, driven by three config blocks (`liensnc`, `lien`, `license` in
`sources.yaml`), same "one adapter, N configs" pattern as the permit feeds.
Drop a manual export in by hand:

```
python -m src.main import --source liensnc --file path\to\liensnc_export.csv
python -m src.main import --source lien --file path\to\county_lien_export.csv
python -m src.main import --source license --file path\to\license_board_export.csv
python -m src.main score
python -m src.main export
```

**No real sample export was available for any of these three**, so
`column_map` in each block uses placeholder header names -- edit them to
match your actual export's real headers the same way you'd edit
`crm.yaml`'s `column_map`, no code change needed.

Proved this end-to-end with a synthetic fixture rather than guessed data,
and deliberately included a row for "Lennar Carolinas" (already in the real
ingested data with `compounding=2` from an OSHA inspection + a Raleigh
permit) to test the exact 3-way scenario the spec calls out: *"a Mecklenburg
permit AND an OSHA inspection AND -- once manual sources are in -- an
expiring license."* Confirmed `compounding` correctly became 3 and the score
bonus updated on all three rows, then reverted the DB to real data only by
deleting the test rows and re-scoring (back to compounding=2, exactly the
prior state) -- the test never left synthetic data in your real database.

## Scheduling (Phase 7, optional)

The spec calls for a cron/launchd entry; this is Windows, so the equivalent
is Task Scheduler. `scripts/run_weekly.ps1` runs OSHA + the five permit
feeds, scores, and exports a fresh dated xlsx to `output/`, logging each run
to `output/run_weekly.log`. Deliberately **no CRM refresh** in the scheduled
job -- HubSpot has no live API in v1, so `crm-load` stays a manual step you
run yourself whenever you have a fresh export; the scheduled run just uses
whatever's already loaded (a no-op if nothing is).

Uses a 10-day lookback on a weekly cadence -- a few days of overlap buffer
past the strict 7-day gap, since `upsert` is idempotent (re-fetching
overlapping records just updates them, never duplicates) and the permit
feeds occasionally lag a day or two before a record appears.

Tested directly: a clean run produces a correctly-formatted log and dated
xlsx; a broken environment (venv python missing) logs `FAILED: <error>` and
exits non-zero, so Task Scheduler will correctly show the run as failed
rather than silently succeeding.

**Not registered on this machine** -- that's an ongoing, persistent system
change (it'll keep running weekly in the background indefinitely) worth
triggering deliberately rather than as a side effect of a build step. To
register it yourself (adjust day/time as you like):

```
schtasks /create /tn "CasaPlace Signals Weekly" /tr "powershell.exe -ExecutionPolicy Bypass -File \"C:\Users\Jonathan Jolley\casaplace-signals\scripts\run_weekly.ps1\"" /sc weekly /d MON /st 06:00
```

To remove it later: `schtasks /delete /tn "CasaPlace Signals Weekly" /f`

## Config

- `config/sources.yaml` -- per-source endpoint URLs, field mappings, enable flags, and the manual-import column maps.
- `config/counties.yaml` -- NC county -> hub mapping. Triangle + Triad are focus markets.
- `config/scoring.yaml` -- scoring weights and modifiers. Editable without touching code.
- `config/nc_zip_county.csv` -- bundled NC zip -> county lookup (offline, no geocoding API).
- `config/crm.yaml` -- CRM export column mapping + match thresholds.
