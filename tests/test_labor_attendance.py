"""PHASE 38 (DAILY-PRESENT) regression suite.

Locks the daily-wage attendance fix: a daily worker's "حاضر"
signal now lands an Attendance row, which the labor report
reads via `Worker.earned_between`. Pre-fix, a present daily
worker generated zero rows because `batches` was always 0 and
the save-trigger was `absent OR batches > 0`.

Four cases:
  1. Daily worker + present → row created, earnings = rate.
  2. Daily worker + absent → row created with is_absent=True,
     earnings = 0.
  3. Daily worker + neither → existing row gets deleted.
  4. Per-batch worker with batches>0 → unchanged behavior.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.labor import Attendance, Worker


_DAILY_TAG = "DAILY-PRESENT-TEST-D"
_BATCH_TAG = "DAILY-PRESENT-TEST-B"


def _seed_worker(app, name, wage_type, rate):
    with app.app_context():
        w = Worker.query.filter_by(name=name).first()
        if w is None:
            w = Worker(name=name, wage_type=wage_type,
                       rate=Decimal(str(rate)))
            db.session.add(w)
        else:
            w.wage_type = wage_type
            w.rate = Decimal(str(rate))
        db.session.commit()
        return w.id


def _cleanup(app):
    with app.app_context():
        for tag in (_DAILY_TAG, _BATCH_TAG):
            w = Worker.query.filter_by(name=tag).first()
            if w is None:
                continue
            Attendance.query.filter_by(worker_id=w.id).delete()
            db.session.delete(w)
        db.session.commit()


def test_daily_present_creates_row_and_earns_rate(admin_client, app):
    wid = _seed_worker(app, _DAILY_TAG, Worker.WAGE_DAILY, 100)
    today = date.today()
    try:
        r = admin_client.post(
            f"/labor/attendance?day={today.isoformat()}",
            data={f"present_{wid}": "1"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.data[:400]
        with app.app_context():
            att = Attendance.query.filter_by(
                worker_id=wid, attendance_date=today
            ).first()
            assert att is not None
            assert att.is_absent is False
            assert att.batches_worked == 0

            w = db.session.get(Worker, wid)
            assert w.earned_between(today, today) == Decimal("100.00")
    finally:
        _cleanup(app)


def test_daily_absent_still_works(admin_client, app):
    wid = _seed_worker(app, _DAILY_TAG, Worker.WAGE_DAILY, 100)
    today = date.today()
    try:
        r = admin_client.post(
            f"/labor/attendance?day={today.isoformat()}",
            data={f"absent_{wid}": "1"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        with app.app_context():
            att = Attendance.query.filter_by(
                worker_id=wid, attendance_date=today
            ).first()
            assert att is not None
            assert att.is_absent is True
            w = db.session.get(Worker, wid)
            assert w.earned_between(today, today) == Decimal("0.00")
    finally:
        _cleanup(app)


def test_daily_uncheck_removes_row(admin_client, app):
    wid = _seed_worker(app, _DAILY_TAG, Worker.WAGE_DAILY, 100)
    today = date.today()
    try:
        # First: mark present.
        admin_client.post(
            f"/labor/attendance?day={today.isoformat()}",
            data={f"present_{wid}": "1"},
            follow_redirects=False,
        )
        with app.app_context():
            assert Attendance.query.filter_by(
                worker_id=wid, attendance_date=today
            ).count() == 1

        # Then: un-check → post with no toggles.
        r = admin_client.post(
            f"/labor/attendance?day={today.isoformat()}",
            data={},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        with app.app_context():
            assert Attendance.query.filter_by(
                worker_id=wid, attendance_date=today
            ).count() == 0
    finally:
        _cleanup(app)


def test_per_batch_worker_path_unchanged(admin_client, app):
    wid = _seed_worker(app, _BATCH_TAG, Worker.WAGE_PER_BATCH, 20)
    today = date.today()
    try:
        r = admin_client.post(
            f"/labor/attendance?day={today.isoformat()}",
            data={f"batches_{wid}": "5"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        with app.app_context():
            att = Attendance.query.filter_by(
                worker_id=wid, attendance_date=today
            ).first()
            assert att is not None
            assert att.is_absent is False
            assert att.batches_worked == 5
            w = db.session.get(Worker, wid)
            assert w.earned_between(today, today) == Decimal("100.00")  # 5*20
    finally:
        _cleanup(app)
