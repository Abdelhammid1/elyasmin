"""OPS-1 regression suite — go-live data reset.

Runs only against temp COPIES of the dev database; the real one is
never touched.

  1. Every table is classified exactly once (KEEP / WIPE).
  2. wipe_data empties every WIPE table, keeps the setup tables, zeroes
     treasury balances.
  3. The CLI aborts without RESET — nothing changes, backup still taken.
  4. The CLI with --yes wipes the copy.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select

from app.commands.reset_data import (
    KEEP, WIPE, check_classification, reset_data_command, wipe_data,
)
from app.extensions import db


@pytest.fixture
def db_copy(app, tmp_path, monkeypatch):
    """Temp copy of the dev SQLite DB + backups redirected to tmp."""
    with app.app_context():
        src = Path(db.engine.url.database)
    dest = tmp_path / "farm-copy.db"
    shutil.copy2(src, dest)
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    return dest


def _strict_engine(path):
    """SQLite with foreign keys enforced, like Postgres, so a wrong
    delete order fails here instead of in production."""
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def _count(conn, name):
    t = db.metadata.tables[name]
    return conn.execute(select(func.count()).select_from(t)).scalar()


def test_every_table_classified(app):
    with app.app_context():
        check_classification()
        assert not (KEEP & WIPE)
        assert KEEP | WIPE == set(db.metadata.tables)


def test_wipe_keeps_setup(app, db_copy):
    engine = _strict_engine(db_copy)
    with app.app_context():
        with engine.connect() as conn:
            kept_before = {name: _count(conn, name) for name in KEEP}
            assert _count(conn, "cows") > 0  # dev DB has trial data
        with engine.begin() as conn:
            wipe_data(conn)
        with engine.connect() as conn:
            for name in WIPE:
                assert _count(conn, name) == 0, name
            for name in ("users", "cattle_groups", "breeding_statuses",
                         "breeding_event_types", "coa_accounts"):
                assert _count(conn, name) == kept_before[name] > 0, name
            accounts = db.metadata.tables["accounts"]
            balances = conn.execute(
                select(accounts.c.opening_balance, accounts.c.current_balance)
            ).all()
            assert all(o == 0 and c == 0 for o, c in balances)
    engine.dispose()


def test_cli_aborts_without_reset(app, db_copy):
    runner = app.test_cli_runner()
    result = runner.invoke(reset_data_command,
                           ["--database-url", f"sqlite:///{db_copy}"],
                           input="no\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    assert list((Path(app.instance_path) / "backups").glob("farm-*.db"))
    engine = create_engine(f"sqlite:///{db_copy}")
    with app.app_context(), engine.connect() as conn:
        assert _count(conn, "cows") > 0
    engine.dispose()


def test_cli_yes_wipes(app, db_copy):
    runner = app.test_cli_runner()
    result = runner.invoke(reset_data_command,
                           ["--database-url", f"sqlite:///{db_copy}", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Done." in result.output
    engine = create_engine(f"sqlite:///{db_copy}")
    with app.app_context(), engine.connect() as conn:
        assert _count(conn, "cows") == 0
        assert _count(conn, "users") > 0
    engine.dispose()


def test_pg_dump_password_not_in_argv(app, tmp_path, monkeypatch):
    """The Postgres backup must not put the password on pg_dump's
    command line (visible to every user via ps); it goes in PGPASSWORD."""
    from types import SimpleNamespace
    from sqlalchemy.engine import make_url
    from app.commands import reset_data as rd

    calls = {}

    def fake_run(args, env=None, **kw):
        calls["args"], calls["env"] = args, env
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(rd.shutil, "which", lambda name: "/usr/bin/pg_dump")
    monkeypatch.setattr(rd.subprocess, "run", fake_run)
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    engine = SimpleNamespace(
        url=make_url("postgresql+psycopg2://farm:s3cret-pw@db.local:5432/farm"))
    with app.app_context():
        rd._backup(engine, backup_done=False)

    assert not any("s3cret-pw" in str(a) for a in calls["args"])
    assert calls["env"]["PGPASSWORD"] == "s3cret-pw"
    assert any("farm@db.local:5432/farm" in str(a) for a in calls["args"])
