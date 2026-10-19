"""OPS-1: go-live data reset — `flask reset-data`.

Wipes every transaction table (trial data) and keeps the setup the
client configured: users, settings, company profile, chart of accounts,
treasury accounts (balances zeroed), groups, the status list, event
types, and the inventory / expense categories.

Command-line only. Takes a backup first and won't run until the
operator types RESET. Every table must be listed in KEEP or WIPE — a
table that's in neither stops the command, so a future table is never
wiped (or kept) by accident.

Run book:
    flask reset-data            # shows counts, backs up, asks to type RESET
    flask reset-data --help
"""
from __future__ import annotations

import os
import shutil
import subprocess
import warnings
from datetime import datetime
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import create_engine, func, select

from app.extensions import db

KEEP = frozenset({
    "users", "settings", "company_profile", "coa_accounts", "accounts",
    "cattle_groups", "breeding_statuses", "breeding_event_types",
    "expense_categories", "ingredient_categories",
})

WIPE = frozenset({
    # herd
    "cows", "cow_movements", "births", "calves", "deaths", "animal_sales",
    "breeding_events", "breeding_status_changes", "cow_valuations", "cow_milk_records",
    # inventory / feed / medicine
    "ingredients", "ingredient_units", "stock_movements", "medicine_lots",
    "medicine_dispenses", "feed_recipes", "feed_recipe_lines", "feed_runs",
    "feed_run_lines", "feed_tanks", "feed_tank_movements",
    "feeding_sessions", "feeding_additions", "group_feed_allowances",
    # purchases / suppliers
    "suppliers", "purchase_invoices", "purchase_lines",
    "purchase_invoice_charges", "purchase_returns", "supplier_payments",
    "supplier_payment_allocations",
    # sales / customers / milk
    "customers", "milk_deliveries", "milk_invoices", "customer_payments",
    "payment_allocations", "sales_returns", "daily_productions",
    "sales_invoices", "sales_invoice_lines",
    "sales_invoice_payment_allocations",
    # money
    "expenses", "account_movements", "account_transfers", "checks",
    "fixed_assets", "depreciation_postings",
    "journal_entries", "journal_lines",
    # labour
    "workers", "attendances", "worker_payments", "leave_requests",
    # logs
    "audit_logs", "ai_usage_logs", "login_attempts",
})


class ResetError(click.ClickException):
    pass


def check_classification() -> None:
    """Every table must be in exactly one of KEEP / WIPE."""
    tables = set(db.metadata.tables)
    both = KEEP & WIPE
    missing = tables - KEEP - WIPE
    unknown = (KEEP | WIPE) - tables
    if both or missing or unknown:
        raise ResetError(
            "Table lists are out of date — classify every table in "
            f"app/commands/reset_data.py. In both: {sorted(both)}; "
            f"unclassified: {sorted(missing)}; no longer exist: {sorted(unknown)}"
        )


def _wipe_tables():
    """WIPE tables, children first. SQLAlchemy warns about the
    customers <-> suppliers cycle; wipe_data nulls those links before
    deleting, so the order doesn't matter for them."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tables = db.metadata.sorted_tables
    return [t for t in reversed(tables) if t.name in WIPE]


def row_counts(conn) -> dict[str, int]:
    return {
        t.name: conn.execute(select(func.count()).select_from(t)).scalar() or 0
        for t in _wipe_tables()
    }


def wipe_data(conn) -> dict[str, int]:
    """Delete every WIPE table and zero treasury balances on `conn`.
    The caller owns the transaction."""
    check_classification()
    wipe_tables = _wipe_tables()

    # 1. Break the links the delete order can't handle: a table pointing
    #    at itself (cows.mother_id, journal reversals, …) and the
    #    customers <-> suppliers pair. Only those — other FK columns may
    #    sit under CHECK constraints (e.g. a check needs exactly one
    #    party), and the children-first order already covers them.
    cycle = {"customers", "suppliers"}
    for t in wipe_tables:
        for col in t.columns:
            if not col.nullable:
                continue
            targets = {fk.column.table.name for fk in col.foreign_keys}
            if t.name in targets or (t.name in cycle and targets & cycle):
                conn.execute(t.update().values({col.name: None}))

    # 2. Delete, children first.
    deleted = {}
    for t in wipe_tables:
        deleted[t.name] = conn.execute(t.delete()).rowcount or 0

    # 3. Treasury accounts stay (CoA leaves are wired to them) but start
    #    from zero.
    accounts = db.metadata.tables["accounts"]
    conn.execute(accounts.update().values(opening_balance=0, current_balance=0))
    return deleted


def _masked(url) -> str:
    return url.render_as_string(hide_password=True)


def _backup(engine, backup_done: bool) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = Path(current_app.instance_path) / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    url = engine.url
    if url.get_backend_name() == "sqlite":
        src = Path(url.database)
        dest = folder / f"farm-{stamp}.db"
        shutil.copy2(src, dest)
        return str(dest)
    if url.get_backend_name().startswith("postgresql"):
        dest = folder / f"farm-{stamp}.sql"
        pg_dump = shutil.which("pg_dump")
        if pg_dump is None:
            if backup_done:
                return "(skipped — operator confirmed a backup exists)"
            raise ResetError(
                "pg_dump not found, so no automatic backup. Take a backup "
                "yourself and re-run with --backup-done."
            )
        # Password goes through PGPASSWORD, never argv — command-line
        # arguments are visible to every user on the server (ps).
        # (URL.set(password=None) would KEEP the password — None means
        # "unchanged" there; _replace really drops it.)
        conn_url = url.set(drivername="postgresql")._replace(password=None)
        env = {**os.environ, "PGPASSWORD": url.password} if url.password else None
        result = subprocess.run(
            [pg_dump, "--no-owner", "-f", str(dest),
             "--dbname", conn_url.render_as_string(hide_password=False)],
            env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise ResetError(f"pg_dump failed: {result.stderr.strip()}")
        return str(dest)
    if backup_done:
        return "(skipped — operator confirmed a backup exists)"
    raise ResetError(f"Don't know how to back up {url.get_backend_name()}; "
                     "take a backup and re-run with --backup-done.")


@click.command("reset-data")
@with_appcontext
@click.option("--yes", is_flag=True, help="Skip the RESET prompt (scripted runs).")
@click.option("--backup-done", is_flag=True,
              help="You already took a backup (needed when pg_dump isn't available).")
@click.option("--database-url", default=None,
              help="Run against this database instead of the app's (ops / tests).")
def reset_data_command(yes, backup_done, database_url):
    """Wipe all trial data before go-live; keep users and setup.

    Kept: users, settings, company profile, chart of accounts, treasury
    accounts (balances set to 0), groups, statuses, event types,
    inventory and expense categories. Everything else is deleted.
    A backup is taken first.
    """
    check_classification()
    engine = (create_engine(database_url) if database_url
              else db.engine)

    with engine.connect() as conn:
        counts = row_counts(conn)
    click.echo(f"Database: {_masked(engine.url)}")
    click.echo("Will DELETE:")
    for name, n in counts.items():
        if n:
            click.echo(f"  {name}: {n}")
    click.echo(f"  ({sum(counts.values())} rows in total)")
    click.echo("Will KEEP: " + ", ".join(sorted(KEEP)))
    click.echo("Treasury account balances will be set to 0.")

    backup = _backup(engine, backup_done)
    click.echo(f"Backup: {backup}")

    if not yes:
        answer = click.prompt("Type RESET to delete the data", default="",
                              show_default=False)
        if answer.strip() != "RESET":
            click.echo("Aborted — nothing was changed.")
            return

    with engine.begin() as conn:
        deleted = wipe_data(conn)
    click.echo(f"Done. Deleted {sum(deleted.values())} rows.")
