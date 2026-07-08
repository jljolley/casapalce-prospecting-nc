"""CLI entrypoint (Section 3 of the build spec).

Phase 6: `import` lands manual-source CSV/XLSX exports (liensnc/lien/license)
through the same fetch -> normalize -> geo -> upsert sequence as `pull`,
via the shared _ingest() helper -- the Adapter contract (Section 2) is what
makes that reuse possible regardless of source.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import click
import yaml

from src import crm, db, export, matching, scoring
from src.adapters.arcgis_permits import ArcGISPermitAdapter
from src.adapters.csv_import import CSVImportAdapter
from src.adapters.fixture import FixtureAdapter
from src.adapters.osha_dol import OshaDolAdapter
from src.geo import resolve

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

PERMIT_JURISDICTIONS = [
    "permit_meck", "permit_raleigh", "permit_durham",
    "permit_greensboro", "permit_winstonsalem",
]
MANUAL_SOURCES = ["liensnc", "lien", "license"]

ADAPTER_REGISTRY = {
    "fixture": FixtureAdapter,
    "osha": OshaDolAdapter,
    **{name: (lambda name=name: ArcGISPermitAdapter(name)) for name in PERMIT_JURISDICTIONS},
}


def _load_env() -> None:
    """Minimal .env loader -- avoids adding python-dotenv as a dependency."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _is_enabled(source: str) -> bool:
    with open(CONFIG_DIR / "sources.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return bool(cfg.get(source, {}).get("enabled"))


def _enabled_sources() -> list[str]:
    return [name for name in ADAPTER_REGISTRY if _is_enabled(name)]


@click.group()
def cli():
    _load_env()


def _ingest(source: str, adapter, since_date: date) -> tuple[int, int, int]:
    """Shared fetch -> normalize -> geo-resolve -> upsert -> log sequence.
    Used by both `pull` (automated adapters) and `import` (manual CSV
    adapters) -- the Adapter contract is what makes this reuse possible."""
    conn = db.get_conn()
    db.init_db(conn)

    raw = adapter.fetch(since_date)
    signals = adapter.normalize(raw)
    for s in signals:
        s.county, s.hub = resolve(county=s.county, zip_code=s.zip_code, city=s.city)

    new_count, dup_count = db.upsert_signals(conn, signals)
    db.log_run(conn, source, len(raw), new_count, dup_count)
    return len(raw), new_count, dup_count


@cli.command()
@click.option("--source", required=True, type=click.Choice(sorted(ADAPTER_REGISTRY)))
@click.option("--since", default="2020-01-01", show_default=True)
def pull(source: str, since: str):
    """Fetch + normalize + geo-resolve + upsert one automated source."""
    if not _is_enabled(source):
        raise click.ClickException(
            f"source '{source}' is disabled in config/sources.yaml -- enable it once its endpoint is configured"
        )

    since_date = datetime.strptime(since, "%Y-%m-%d").date()
    adapter = ADAPTER_REGISTRY[source]()
    fetched, new_count, dup_count = _ingest(source, adapter, since_date)
    click.echo(f"[{source}] fetched={fetched} new={new_count} updated={dup_count}")


@cli.command(name="import")
@click.option("--source", "source_name", required=True, type=click.Choice(MANUAL_SOURCES))
@click.option("--file", "file_path", required=True, type=click.Path(exists=True))
def import_cmd(source_name: str, file_path: str):
    """Import a manual-source CSV/XLSX export (liensnc/lien/license) through the same pipeline."""
    adapter = CSVImportAdapter(source_name, file_path)
    fetched, new_count, dup_count = _ingest(source_name, adapter, date.min)
    click.echo(f"[{source_name}] imported={fetched} new={new_count} updated={dup_count}")


@cli.command(name="crm-load")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True))
def crm_load(file_path: str):
    """Load/refresh crm_accounts from a CRM export (CSV/XLSX). Read-only -- never writes back."""
    conn = db.get_conn()
    db.init_db(conn)
    count = crm.load_crm(conn, file_path)
    click.echo(f"loaded {count} CRM accounts from {file_path}")


@cli.command()
def score():
    """Refresh CRM matches + compounding clusters, then recompute scores."""
    conn = db.get_conn()
    db.init_db(conn)
    crm_matched = crm.match_signals(conn)
    compounding_updated = matching.find_compounding(conn)
    n = scoring.score_all(conn)
    click.echo(f"crm-matched {crm_matched}, compounding-updated {compounding_updated}, scored {n} signals")


@cli.command(name="export")
@click.option("--format", "fmt", default="xlsx", type=click.Choice(["xlsx", "csv"]), show_default=True)
@click.option("--hub", "hub_arg", default=None, help="Comma-separated hub names to filter to (all tabs).")
@click.option("--min-score", "min_score", default=0, type=int, show_default=True)
@click.option("--exclude-crm/--include-crm", "exclude_crm", default=True,
              help="Keep CRM matches out of Tabs 1-3 (default) or merge them back in.")
def export_cmd(fmt: str, hub_arg: str | None, min_score: int, exclude_crm: bool):
    """Write the ranked signals workbook (or flat CSV) to output/."""
    conn = db.get_conn()
    db.init_db(conn)
    hubs = [h.strip() for h in hub_arg.split(",")] if hub_arg else None
    path = export.ranked_export(conn, hubs=hubs, min_score=min_score, exclude_crm=exclude_crm, fmt=fmt)
    click.echo(f"wrote {path}")


@cli.command(name="run-all")
@click.option("--since", default="2020-01-01", show_default=True)
@click.option("--crm-file", "crm_file", default=None, type=click.Path(exists=True))
@click.pass_context
def run_all(ctx: click.Context, since: str, crm_file: str | None):
    """Optionally refresh the CRM export, pull every enabled source, score, and export."""
    if crm_file:
        ctx.invoke(crm_load, file_path=crm_file)
    for source in _enabled_sources():
        ctx.invoke(pull, source=source, since=since)
    ctx.invoke(score)
    ctx.invoke(export_cmd)


if __name__ == "__main__":
    cli()
