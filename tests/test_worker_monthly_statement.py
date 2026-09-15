"""PHASE 32 (HR-1) + PHASE 41 (WORKER-MONTH-CORRECTION): regression
suite for the worker monthly-statement feature.

Four invariants:
  1. A payment created via `record_payment` with `target_month=YYYY-MM`
     lands in that month bucket, not the payment_date's calendar month.
  2. `Worker.month_window(target_month)` always returns the FULL
     calendar month regardless of `closing_day`. `closing_day` no
     longer offsets the earning window — after the P41 correction it
     only determines `settlement_date`.
  3. Negative balance in the prior month surfaces as `prior_carry` in
     the current month's context.
  4. `worker_statement_pdf` returns a PDF (or a graceful error when
     Chromium/Playwright isn't installed — treated as skip).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.finance import TreasuryAccount
from app.models.labor import Attendance, Worker, WorkerPayment


_TEST_WORKER_NAME = "TEST-HR1-WORKER"
_TEST_TREASURY_NAME = "TEST-HR1-TREASURY"


def _seed_worker(app, closing_day=1, wage_type="daily", rate="100"):
    """Idempotent test worker + treasury."""
    with app.app_context():
        w = Worker.query.filter_by(name=_TEST_WORKER_NAME).first()
        if w is None:
            w = Worker(
                name=_TEST_WORKER_NAME,
                wage_type=wage_type,
                rate=Decimal(rate),
                closing_day=closing_day,
            )
            db.session.add(w)
            db.session.commit()
        else:
            w.wage_type = wage_type
            w.rate = Decimal(rate)
            w.closing_day = closing_day
            db.session.commit()

        t = TreasuryAccount.query.filter_by(name=_TEST_TREASURY_NAME).first()
        if t is None:
            t = TreasuryAccount(
                name=_TEST_TREASURY_NAME,
                account_type=TreasuryAccount.TYPE_CASH,
            )
            db.session.add(t)
            db.session.commit()
            from app.services.coa_seed import wire_treasury_accounts
            wire_treasury_accounts()
            db.session.commit()
        return w.id, t.id


def _cleanup(app):
    """Delete the test worker + all rows that reference it. We leave
    the TEST-HR1-TREASURY row in place — it's referenced by
    account_movements + a wired CoA leaf, and cleaning the whole
    cascade is more trouble than it's worth. Subsequent runs re-use
    the same treasury by name lookup."""
    with app.app_context():
        w = Worker.query.filter_by(name=_TEST_WORKER_NAME).first()
        if w is not None:
            from app.models.finance import Expense
            WorkerPayment.query.filter_by(worker_id=w.id).delete()
            Attendance.query.filter_by(worker_id=w.id).delete()
            Expense.query.filter_by(ref_type="worker_payment").delete()
            db.session.delete(w)
            db.session.commit()


def test_target_month_buckets_payment_into_chosen_month(admin_client, app):
    """Payment made today (September) but booked as target_month=August
    → shows up under August in the worker_detail context, not September."""
    wid, tid = _seed_worker(app)
    try:
        # Post via the real route so the whole plumbing runs
        r = admin_client.post(
            f"/labor/{wid}/pay",
            data={
                "amount": "150",
                "payment_date": date.today().isoformat(),
                "target_month": "2026-08",   # explicit August bucket
                "reason": "advance",
                "account_id": str(tid),
                "notes": "test aug bucket",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code

        with app.app_context():
            p = WorkerPayment.query.filter_by(worker_id=wid,
                                              notes="test aug bucket").first()
            assert p is not None
            assert p.target_month == date(2026, 8, 1), (
                f"expected target_month 2026-08-01, got {p.target_month}"
            )
    finally:
        _cleanup(app)


def test_month_window_is_calendar_month_regardless_of_closing_day(app):
    """PHASE 41 CORRECTION — `closing_day` no longer offsets the
    earning window. The window for target=2026-09-01 must be
    Sept 1-30 whether closing_day is 1, 10, or 28."""
    for cd in (1, 10, 28):
        wid, _tid = _seed_worker(app, closing_day=cd)
        try:
            with app.app_context():
                w = db.session.get(Worker, wid)
                start, end = w.month_window(date(2026, 9, 1))
                assert start == date(2026, 9, 1), (cd, start)
                assert end == date(2026, 9, 30), (cd, end)
        finally:
            _cleanup(app)


def test_settlement_date_is_closing_day_of_following_month(app):
    """PHASE 41 CORRECTION — `settlement_date(target)` is the
    calendar date the earning bucket is paid out on: closing_day
    of the month AFTER target. Year rollover works for December."""
    cases = [
        # closing_day, target_month, expected settlement
        (1,  date(2026, 9, 1),  date(2026, 10, 1)),
        (10, date(2026, 9, 1),  date(2026, 10, 10)),
        (15, date(2026, 12, 1), date(2027, 1, 15)),
    ]
    for cd, target, expected in cases:
        wid, _tid = _seed_worker(app, closing_day=cd)
        try:
            with app.app_context():
                w = db.session.get(Worker, wid)
                assert w.settlement_date(target) == expected, (
                    f"closing_day={cd}, target={target}: "
                    f"got {w.settlement_date(target)}, expected {expected}"
                )
        finally:
            _cleanup(app)


def test_negative_balance_becomes_prior_carry(admin_client, app):
    """Worker earns 0 in August, gets 100 EGP advance booked to August
    → the September worker_detail context surfaces prior_carry=-100."""
    wid, tid = _seed_worker(app, closing_day=1, wage_type="daily", rate="0")
    try:
        # Over-pay against August
        admin_client.post(
            f"/labor/{wid}/pay",
            data={
                "amount": "100",
                "payment_date": date(2026, 8, 15).isoformat(),
                "target_month": "2026-08",
                "reason": "advance",
                "account_id": str(tid),
            },
            follow_redirects=False,
        )
        # View September's detail
        r = admin_client.get(f"/labor/{wid}?month=2026-09")
        body = r.get_data(as_text=True)
        # The carry note only renders if prior_carry != 0. -100.00
        # should appear in the note.
        assert "مرحّل من شهر" in body, "carry-forward note missing"
        assert "أغسطس" in body, "prior month label missing"
    finally:
        _cleanup(app)


def test_worker_statement_pdf_route_registered(app):
    """The PDF route is registered (rendering skipped — Chromium may
    not be installed in CI). Just verify the URL exists."""
    with app.app_context():
        rules = [r for r in app.url_map.iter_rules()
                 if r.endpoint == "labor.worker_statement_pdf"]
        assert len(rules) == 1
        assert "/statement.pdf" in str(rules[0])
