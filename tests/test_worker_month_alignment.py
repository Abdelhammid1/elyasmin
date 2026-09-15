"""PHASE 40 (WORKER-MONTH) + PHASE 41 CORRECTION regression suite.

Locks the "الشهر" alignment on /labor/<id> under the corrected
semantics:

  1. Top stat cards + attendance table + statement all read the
     same `period_start / period_end`, and that pair is now
     ALWAYS the full calendar month of `selected_month`
     (regardless of `closing_day`).
  2. The statement header shows `settlement_date` — closing_day
     of the following month — as a display-only label so the
     admin can see when the month is due to be paid out.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.labor import Attendance, Worker


_TAG = "WORKER-MONTH-TEST"


def _seed_worker(app, closing_day=10, rate=100):
    """Idempotent — reuses the row if a prior run left it around
    so the test doesn't collide with itself."""
    with app.app_context():
        w = Worker.query.filter_by(name=_TAG).first()
        if w is None:
            w = Worker(
                name=_TAG,
                wage_type=Worker.WAGE_DAILY,
                rate=Decimal(str(rate)),
                closing_day=closing_day,
            )
            db.session.add(w)
        else:
            w.wage_type = Worker.WAGE_DAILY
            w.rate = Decimal(str(rate))
            w.closing_day = closing_day
        db.session.commit()
        return w.id


def _cleanup(app):
    with app.app_context():
        w = Worker.query.filter_by(name=_TAG).first()
        if w is None:
            return
        Attendance.query.filter_by(worker_id=w.id).delete()
        db.session.delete(w)
        db.session.commit()


def test_top_cards_use_calendar_month_earnings(admin_client, app):
    """A worker with closing_day=10 still earns based on the full
    calendar month (not the pre-correction Aug-11→Sep-10 offset).

    Setup:
      - Worker closing_day=10, rate=100.
      - Attendance on 2026-09-05 (inside the Sept calendar month).
      - Attendance on 2026-08-15 (inside the pre-correction Sept
        window but OUTSIDE the corrected Sept calendar month).

    Expected under the corrected semantics:
      period_earned(Sept) = 100 (just the Sept 5 row) — not 200.
    """
    wid = _seed_worker(app, closing_day=10, rate=100)
    try:
        with app.app_context():
            db.session.add_all([
                Attendance(worker_id=wid,
                           attendance_date=date(2026, 9, 5),
                           is_absent=False, batches_worked=0),
                Attendance(worker_id=wid,
                           attendance_date=date(2026, 8, 15),
                           is_absent=False, batches_worked=0),
            ])
            db.session.commit()

            w = db.session.get(Worker, wid)
            target = date(2026, 9, 1)
            assert w.earned_for_month(target) == Decimal("100.00")

        r = admin_client.get(f"/labor/{wid}?month=2026-09")
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        # The rendered top card carries the calendar-month number.
        assert '<div class="stat-value" dir="ltr">100.00</div>' in body
        assert "مستحق (سبتمبر 2026)" in body
    finally:
        _cleanup(app)


def test_attendance_list_shows_only_calendar_month(admin_client, app):
    """Attendance list on the detail page filters by
    period_start / period_end — the full calendar month.

    Setup:
      - Worker closing_day=10.
      - Attendance on 2026-09-05 (inside Sept calendar month).
      - Attendance on 2026-08-15 (pre-correction inside Sept
        window; corrected NOT).

    Expected: Sept 5 date appears; Aug 15 date does not.
    """
    wid = _seed_worker(app, closing_day=10, rate=100)
    try:
        with app.app_context():
            db.session.add_all([
                Attendance(worker_id=wid,
                           attendance_date=date(2026, 9, 5),
                           is_absent=False, batches_worked=0),
                Attendance(worker_id=wid,
                           attendance_date=date(2026, 8, 15),
                           is_absent=False, batches_worked=0),
            ])
            db.session.commit()

        r = admin_client.get(f"/labor/{wid}?month=2026-09")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "2026-09-05" in body
        assert "2026-08-15" not in body
    finally:
        _cleanup(app)


def test_settlement_date_rendered_on_statement(admin_client, app):
    """The statement header should show the settlement date so
    the admin sees when the month is due to be paid out. For
    closing_day=10 and target=Sept 2026, that's Oct 10, 2026.
    """
    wid = _seed_worker(app, closing_day=10, rate=100)
    try:
        r = admin_client.get(f"/labor/{wid}?month=2026-09")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "تاريخ التسوية" in body
        # ISO date rendered by the template as {{ settlement_date }}.
        assert "2026-10-10" in body
        assert "فترة الاستحقاق" in body
    finally:
        _cleanup(app)
