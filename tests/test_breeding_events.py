"""PHASE 35 HERD-2 Part 1 regression suite.

Locks the suggest-and-confirm contract:
  1. Recording an event never changes `breeding_status` on its own.
  2. Confirming a suggestion writes both a BreedingStatusChange row
     AND updates `Cow.breeding_status` (atomic).
  3. Retro-edit writes a status-change row with `event_id=NULL` and
     never touches the breeding_events log.
  4. `create_birth` writes a matching BreedingEvent(event_type=
     'calving', birth_id=…) alongside the Birth row.
  5. `Cow.season_count` (Part 2) increments when a birth is added.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.herd import (
    Birth,
    BreedingEvent,
    BreedingStatusChange,
    CattleGroup,
    Cow,
)


_TEST_TAG = "TEST-BR2-COW"


def _seed_cow(app, breeding_status=None):
    """Idempotent female test cow."""
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_TEST_TAG).first()
        if c is None:
            group = CattleGroup.query.first()
            c = Cow(
                ear_tag=_TEST_TAG,
                name="Test-Br2",
                gender=Cow.GENDER_FEMALE,
                group_id=group.id,
                status=Cow.STATUS_ACTIVE,
            )
            db.session.add(c)
        c.breeding_status = breeding_status
        db.session.commit()
        return c.id


def _cleanup(app):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_TEST_TAG).first()
        if c is None:
            return
        BreedingStatusChange.query.filter_by(cow_id=c.id).delete()
        BreedingEvent.query.filter_by(cow_id=c.id).delete()
        # If any Birth was created during a test, wipe it + its
        # calves so create_birth's cascade doesn't leave orphans.
        for b in Birth.query.filter_by(mother_id=c.id).all():
            from app.models.herd import Calf, CowMovement
            Calf.query.filter_by(birth_id=b.id).delete()
            # The calf cows themselves plus movements
            for calf_row in Calf.query.filter_by(birth_id=b.id).all():
                if calf_row.cow_id:
                    CowMovement.query.filter_by(
                        cow_id=calf_row.cow_id
                    ).delete()
                    Cow.query.filter_by(id=calf_row.cow_id).delete()
            db.session.delete(b)
        db.session.delete(c)
        db.session.commit()


def test_recording_event_leaves_breeding_status_unchanged(admin_client, app):
    cow_id = _seed_cow(app, breeding_status=Cow.BS_INSEMINATED)
    try:
        r = admin_client.post(
            f"/herd/{cow_id}/breeding/new",
            data={
                "event_type": "pregnancy_check",
                "event_date": date.today().isoformat(),
                "result": "pregnant",
                "notes": "test event",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code
        with app.app_context():
            c = db.session.get(Cow, cow_id)
            # Status did NOT auto-change
            assert c.breeding_status == Cow.BS_INSEMINATED
            # But event WAS recorded
            events = BreedingEvent.query.filter_by(cow_id=cow_id).all()
            assert len(events) == 1
            assert events[0].event_type == "pregnancy_check"
            assert events[0].result == "pregnant"
            # And no status-change row yet
            assert BreedingStatusChange.query.filter_by(
                cow_id=cow_id
            ).count() == 0
    finally:
        _cleanup(app)


def test_confirming_suggestion_writes_status_change_and_updates(admin_client, app):
    cow_id = _seed_cow(app, breeding_status=Cow.BS_INSEMINATED)
    try:
        # Create the event first
        admin_client.post(
            f"/herd/{cow_id}/breeding/new",
            data={
                "event_type": "pregnancy_check",
                "event_date": date.today().isoformat(),
                "result": "pregnant",
            },
            follow_redirects=False,
        )
        with app.app_context():
            event_id = BreedingEvent.query.filter_by(
                cow_id=cow_id
            ).first().id

        # Confirm the suggested "pregnant" status
        r = admin_client.post(
            f"/herd/{cow_id}/breeding/{event_id}/confirm-status",
            data={"new_status": "pregnant"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code

        with app.app_context():
            c = db.session.get(Cow, cow_id)
            assert c.breeding_status == Cow.BS_PREGNANT
            changes = BreedingStatusChange.query.filter_by(
                cow_id=cow_id
            ).all()
            assert len(changes) == 1
            assert changes[0].from_status == Cow.BS_INSEMINATED
            assert changes[0].to_status == Cow.BS_PREGNANT
            assert changes[0].event_id == event_id
    finally:
        _cleanup(app)


def test_retro_edit_writes_change_without_touching_events(admin_client, app):
    cow_id = _seed_cow(app, breeding_status=Cow.BS_INSEMINATED)
    try:
        # Record a real event first (untouched by the retro edit)
        admin_client.post(
            f"/herd/{cow_id}/breeding/new",
            data={
                "event_type": "insemination",
                "event_date": date.today().isoformat(),
            },
            follow_redirects=False,
        )
        with app.app_context():
            events_before = BreedingEvent.query.filter_by(cow_id=cow_id).count()
            assert events_before == 1

        # Retro-edit: change status to "dry" with a reason
        r = admin_client.post(
            f"/herd/{cow_id}/breeding/retro-status",
            data={
                "new_status": "dry",
                "reason": "التصحيح: البقرة كانت في مرحلة جفاف",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code

        with app.app_context():
            c = db.session.get(Cow, cow_id)
            assert c.breeding_status == Cow.BS_DRY
            # Events log is EXACTLY the same
            events_after = BreedingEvent.query.filter_by(cow_id=cow_id).count()
            assert events_after == events_before
            # New status-change row with event_id NULL
            retro = BreedingStatusChange.query.filter_by(
                cow_id=cow_id, event_id=None,
            ).all()
            assert len(retro) == 1
            assert retro[0].from_status == Cow.BS_INSEMINATED
            assert retro[0].to_status == Cow.BS_DRY
            assert "التصحيح" in (retro[0].reason or "")
    finally:
        _cleanup(app)


def test_create_birth_mirrors_calving_into_breeding_events(admin_client, app):
    """`create_birth` writes a matching BreedingEvent with
    event_type='calving' and birth_id set — Part 2's season count
    reads Birth directly so this keeps the two logs in sync."""
    cow_id = _seed_cow(app)
    try:
        # Register a birth via the real route
        r = admin_client.post(
            "/herd/births/new",
            data={
                "mother_id": str(cow_id),
                "birth_date": date.today().isoformat(),
                "calves_count": "1",
                "delivery_type": "natural",
                "notes": "test br2 birth",
                "calf_gender_0": "female",
                "calf_alive_0": "1",
                "calf_tag_0": "",
            },
            follow_redirects=False,
        )
        # 302 to assign-groups page (HERD-1 flow)
        assert r.status_code in (302, 303), r.status_code

        with app.app_context():
            births = Birth.query.filter_by(mother_id=cow_id).all()
            assert len(births) == 1

            calving_events = BreedingEvent.query.filter_by(
                cow_id=cow_id,
                event_type=BreedingEvent.EVENT_CALVING,
            ).all()
            assert len(calving_events) == 1
            assert calving_events[0].birth_id == births[0].id

            # HERD-2 Part 2: season_count reflects the birth
            c = db.session.get(Cow, cow_id)
            # Season count property was added in Part 2; skip if
            # not present (this test then only locks Part 1).
            if hasattr(c, "season_count"):
                assert c.season_count == 1
    finally:
        _cleanup(app)


def test_timeline_returns_events_and_changes(admin_client, app):
    cow_id = _seed_cow(app)
    try:
        admin_client.post(
            f"/herd/{cow_id}/breeding/new",
            data={
                "event_type": "waiting",
                "event_date": date.today().isoformat(),
            },
            follow_redirects=False,
        )
        r = admin_client.get(f"/herd/{cow_id}/breeding")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "سجل الإجراءات" in body
        assert "سجل تغيرات الحالة" in body
        assert "انتظار" in body   # the event_type label
    finally:
        _cleanup(app)
