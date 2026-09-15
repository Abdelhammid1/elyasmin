"""PHASE 40 (WORKER-MONTH) regression suite.

Locks the "الشهر" alignment fix on /labor/<id>:

  1. Top stat cards read `period_*` (the worker's payroll
     window for the selected month), not the calendar-month
     properties. This test seeds a scenario where the two
     numbers DIFFER by construction — an Attendance row whose
     date sits inside the payroll window but OUTSIDE the
     calendar month — and asserts the rendered top-card value
     matches `worker.earned_for_month(selected_month)`.
  2. The attendance table on the same page is filtered by the
     selected month's payroll window, not by the calendar
     month. This test seeds two Attendance rows on either side
     of the window boundary and asserts only the in-window one
     renders.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.labor import Attendance, Worker


_TAG = "WORKER-MONTH-TEST"


def _seed_worker(app, closing_day=15, rate=100):
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


def test_top_cards_read_from_worker_month_window(admin_client, app):
    """Scenario constructed so calendar-month vs payroll-window
    disagree.

    Worker closing_day=15, rate=100. Target month = 2026-08-01,
    which for this worker maps to the window Jul 16 → Aug 15.
    One Attendance on 2026-07-25 sits inside the window (earned
    = 100) but OUTSIDE calendar August (`month_earned` = 0).
    Pre-fix, the top card would show 0 while the statement
    below showed 100. Post-fix both show 100.
    """
    wid = _seed_worker(app, closing_day=15, rate=100)
    try:
        with app.app_context():
            db.session.add(Attendance(
                worker_id=wid,
                attendance_date=date(2026, 7, 25),  # inside window
                is_absent=False,
                batches_worked=0,
            ))
            db.session.commit()

            w = db.session.get(Worker, wid)
            target = date(2026, 8, 1)
            window_earned = w.earned_for_month(target)
            assert window_earned == Decimal("100.00")

        r = admin_client.get(f"/labor/{wid}?month=2026-08")
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        # The rendered top card should carry the window's number.
        # `<div class="stat-value" dir="ltr">100.00</div>` is the
        # exact shape of the top "مستحق (…)" card body.
        assert '<div class="stat-value" dir="ltr">100.00</div>' in body
        # The Arabic month label in the card title proves the
        # card is tagged to the selected month (post-fix), not the
        # generic "هذا الشهر" (pre-fix).
        assert "مستحق (أغسطس 2026)" in body
    finally:
        _cleanup(app)


def test_attendance_list_respects_selected_month(admin_client, app):
    """Two attendances on either side of the payroll window.

    Same worker (closing_day=15). Target month 2026-08-01 →
    window Jul 16 → Aug 15. Attendance A on 2026-07-25 is in
    the window; Attendance B on 2026-06-20 is not (it belongs
    to the July 2026 window Jun 16 → Jul 15).

    The attendance table on the detail page should render A
    and not B.
    """
    wid = _seed_worker(app, closing_day=15, rate=100)
    try:
        with app.app_context():
            db.session.add_all([
                Attendance(worker_id=wid,
                           attendance_date=date(2026, 7, 25),
                           is_absent=False, batches_worked=0),
                Attendance(worker_id=wid,
                           attendance_date=date(2026, 6, 20),
                           is_absent=False, batches_worked=0),
            ])
            db.session.commit()

        r = admin_client.get(f"/labor/{wid}?month=2026-08")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        # In-window date rendered by the attendance table.
        assert "2026-07-25" in body
        # Out-of-window date must NOT appear anywhere on the page.
        assert "2026-06-20" not in body
    finally:
        _cleanup(app)
