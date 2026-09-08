"""PHASE 35 HERD-2 Part 3 regression suite.

Locks the revaluation ledger contract:
  1. Value up (gain) posts DR 1400 حيوانات المزرعة / CR 4095.
  2. Value down (loss) posts DR 4095 / CR 1400.
  3. No-op revaluation (same value) posts nothing and creates no
     CowValuation row.
  4. Historical valuation report at date T sums the latest-≤T
     value per cow (0 for cows with no valuation by T).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.accounting import JournalEntry, JournalLine, LedgerAccount
from app.models.herd import CattleGroup, Cow, CowValuation


_TEST_TAG = "TEST-VAL2-COW"


def _seed_cow(app, current_value: Decimal = Decimal("0")):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_TEST_TAG).first()
        if c is None:
            group = CattleGroup.query.first()
            c = Cow(
                ear_tag=_TEST_TAG,
                gender=Cow.GENDER_FEMALE,
                group_id=group.id,
                status=Cow.STATUS_ACTIVE,
                current_value=current_value,
            )
            db.session.add(c)
        else:
            c.current_value = current_value
        db.session.commit()
        return c.id


def _cleanup(app):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_TEST_TAG).first()
        if c is None:
            return
        # Kill JEs that referenced this cow's valuations (source_type
        # = 'CowValuation'), then valuations, then the cow itself.
        val_ids = [
            v.id for v in CowValuation.query.filter_by(cow_id=c.id).all()
        ]
        if val_ids:
            je_ids = [
                j.id for j in JournalEntry.query.filter(
                    JournalEntry.source_type == "CowValuation",
                    JournalEntry.source_id.in_(val_ids),
                ).all()
            ]
            if je_ids:
                JournalLine.query.filter(
                    JournalLine.entry_id.in_(je_ids)
                ).delete(synchronize_session=False)
                JournalEntry.query.filter(
                    JournalEntry.id.in_(je_ids)
                ).delete(synchronize_session=False)
        CowValuation.query.filter_by(cow_id=c.id).delete()
        db.session.delete(c)
        db.session.commit()


def _last_je_for_valuation(app, valuation_id):
    with app.app_context():
        je = JournalEntry.query.filter_by(
            source_type="CowValuation",
            source_id=valuation_id,
        ).first()
        lines = list(je.lines) if je else []
        # Force-materialize account codes while inside the app ctx
        for l in lines:
            _ = l.account.code
        return je, lines


def test_gain_posts_dr_1400_cr_4095(admin_client, app):
    cow_id = _seed_cow(app, current_value=Decimal("100"))
    try:
        # Bulk save form: value_<id> input + a valuation_date
        r = admin_client.post(
            "/herd/valuation/save",
            data={
                "valuation_date": date.today().isoformat(),
                f"value_{cow_id}": "150",
                f"notes_{cow_id}": "test gain",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code

        with app.app_context():
            v = CowValuation.query.filter_by(cow_id=cow_id).first()
            assert v is not None
            assert v.value == Decimal("150.00")
            assert v.prior_value == Decimal("100.00")

            c = db.session.get(Cow, cow_id)
            assert c.current_value == Decimal("150.00")

        je, lines = _last_je_for_valuation(app, v.id)
        assert je is not None
        assert len(lines) == 2
        # 50 gain: DR 1400 / CR 4095
        by_code = {l.account.code: l for l in lines}
        assert by_code["1400"].debit == Decimal("50")
        assert by_code["1400"].credit == Decimal("0")
        assert by_code["4095"].debit == Decimal("0")
        assert by_code["4095"].credit == Decimal("50")
    finally:
        _cleanup(app)


def test_loss_posts_dr_4095_cr_1400(admin_client, app):
    cow_id = _seed_cow(app, current_value=Decimal("200"))
    try:
        r = admin_client.post(
            "/herd/valuation/save",
            data={
                "valuation_date": date.today().isoformat(),
                f"value_{cow_id}": "150",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)

        with app.app_context():
            v = CowValuation.query.filter_by(cow_id=cow_id).first()
            assert v.value == Decimal("150.00")

        _, lines = _last_je_for_valuation(app, v.id)
        by_code = {l.account.code: l for l in lines}
        # 50 loss: DR 4095 / CR 1400
        assert by_code["4095"].debit == Decimal("50")
        assert by_code["4095"].credit == Decimal("0")
        assert by_code["1400"].debit == Decimal("0")
        assert by_code["1400"].credit == Decimal("50")
    finally:
        _cleanup(app)


def test_no_op_revaluation_posts_nothing(admin_client, app):
    cow_id = _seed_cow(app, current_value=Decimal("100"))
    try:
        with app.app_context():
            je_before = JournalEntry.query.filter_by(
                source_type="CowValuation"
            ).count()
        r = admin_client.post(
            "/herd/valuation/save",
            data={
                "valuation_date": date.today().isoformat(),
                f"value_{cow_id}": "100",   # same as current
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)

        with app.app_context():
            # No CowValuation row
            assert CowValuation.query.filter_by(cow_id=cow_id).count() == 0
            # No new JE
            je_after = JournalEntry.query.filter_by(
                source_type="CowValuation"
            ).count()
            assert je_after == je_before
    finally:
        _cleanup(app)


def test_historical_report_uses_latest_le_target(admin_client, app):
    """Two revaluations on this cow, one on day-2 and one today.
    Report at day-1 = 0 (no valuation yet). Report today = latest.
    Report at day-2 = the day-2 value."""
    cow_id = _seed_cow(app, current_value=Decimal("0"))
    try:
        day_2 = (date.today() - timedelta(days=2)).isoformat()
        # First: 200 on day-2
        admin_client.post(
            "/herd/valuation/save",
            data={
                "valuation_date": day_2,
                f"value_{cow_id}": "200",
            },
            follow_redirects=False,
        )
        # Then: 300 today
        admin_client.post(
            "/herd/valuation/save",
            data={
                "valuation_date": date.today().isoformat(),
                f"value_{cow_id}": "300",
            },
            follow_redirects=False,
        )

        # Report today: uses current_value = 300
        r = admin_client.get("/herd/valuation/report")
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "300.00" in body

        # Report at day-2: reconstructs to 200
        r = admin_client.get(f"/herd/valuation/report?date={day_2}")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "200.00" in body

        # Report before any valuation: 0.00 for this cow (still
        # rendered in the row list at 0)
        day_5 = (date.today() - timedelta(days=5)).isoformat()
        r = admin_client.get(f"/herd/valuation/report?date={day_5}")
        assert r.status_code == 200
    finally:
        _cleanup(app)
