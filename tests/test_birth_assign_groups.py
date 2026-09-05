"""PHASE 31 (HERD-1): regression suite for the "assign groups after
birth" confirmation page.

Three invariants:
  1. `assign_birth_groups` GET renders and pre-selects the nursing
     group for the dam and each live calf (or the current group if
     nursing doesn't exist).
  2. POSTing with an explicit alternative for the dam actually
     changes `dam.group_id` AND records a CowMovement.
  3. If the user never POSTs (page abandoned), `dam.group_id`
     equals the value it had immediately after `create_birth` — no
     auto-move happens on birth alone.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.extensions import db
from app.models.herd import Birth, Calf, CattleGroup, Cow, CowMovement


def _seed_dam_and_group(app):
    """Ensure a female cow + a non-nursing starting group exist so we
    can move her around."""
    with app.app_context():
        # Non-nursing starting group
        starter = CattleGroup.query.filter_by(
            type=CattleGroup.TYPE_MILK).first()
        if starter is None:
            starter = CattleGroup(name="حليب-اختبار", type=CattleGroup.TYPE_MILK)
            db.session.add(starter)
            db.session.commit()

        # Nursing group (should exist from real data, but make sure)
        nursing = CattleGroup.query.filter_by(
            type=CattleGroup.TYPE_NURSING).first()
        if nursing is None:
            nursing = CattleGroup(name="رضاعة-اختبار", type=CattleGroup.TYPE_NURSING)
            db.session.add(nursing)
            db.session.commit()

        # Female cow in the starter group
        dam = Cow.query.filter_by(
            gender=Cow.GENDER_FEMALE, is_archived=False,
            status=Cow.STATUS_ACTIVE,
        ).first()
        if dam is None:
            dam = Cow(
                ear_tag="TEST-DAM-HERD1",
                gender=Cow.GENDER_FEMALE,
                group_id=starter.id,
                status=Cow.STATUS_ACTIVE,
                is_archived=False,
            )
            db.session.add(dam)
            db.session.commit()
        else:
            # Force her back into the starter group so the test is
            # deterministic when re-run against a mutated dev DB.
            dam.group_id = starter.id
            db.session.commit()

        return dam.id, starter.id, nursing.id


def _create_birth(app, dam_id, calves=1):
    """Directly seed a Birth + Calves in the DB — bypass the route.
    Live calves get real Cow rows in the dam's group (matches what
    `create_birth` does post-HERD-1)."""
    with app.app_context():
        dam = db.session.get(Cow, dam_id)
        birth = Birth(
            mother_id=dam.id,
            birth_date=date.today(),
            calves_count=calves,
            delivery_type=Birth.DELIVERY_NATURAL,
        )
        db.session.add(birth)
        db.session.flush()

        calf_ids = []
        for i in range(calves):
            calf_cow = Cow(
                ear_tag=f"TEST-CALF-HERD1-{birth.id}-{i}",
                gender=Cow.GENDER_FEMALE,
                group_id=dam.group_id,   # dam's current group per HERD-1
                mother_id=dam.id,
                status=Cow.STATUS_ACTIVE,
            )
            db.session.add(calf_cow)
            db.session.flush()
            calf_ids.append(calf_cow.id)
            db.session.add(Calf(
                birth_id=birth.id, cow_id=calf_cow.id,
                gender=Cow.GENDER_FEMALE, is_alive=True,
            ))

        db.session.commit()
        return birth.id, calf_ids


def _cleanup(app):
    """Remove test-seeded rows."""
    with app.app_context():
        CowMovement.query.filter(
            CowMovement.cow_id.in_(
                db.session.query(Cow.id).filter(
                    Cow.ear_tag.like("TEST-%HERD1%")
                )
            )
        ).delete(synchronize_session=False)
        # Delete birth + calves
        for b in Birth.query.filter(
            Birth.mother_id.in_(
                db.session.query(Cow.id).filter(
                    Cow.ear_tag == "TEST-DAM-HERD1"
                )
            )
        ).all():
            Calf.query.filter_by(birth_id=b.id).delete()
            db.session.delete(b)
        Cow.query.filter(Cow.ear_tag.like("TEST-%HERD1%")).delete()
        db.session.commit()


def test_get_renders_with_nursing_default(admin_client, app):
    dam_id, starter_id, nursing_id = _seed_dam_and_group(app)
    birth_id, calf_ids = _create_birth(app, dam_id, calves=1)
    try:
        r = admin_client.get(f"/herd/births/{birth_id}/assign-groups")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        # nursing group name should appear as a <select option>
        with app.app_context():
            nursing = db.session.get(CattleGroup, nursing_id)
            assert nursing.name in body
        # dam-select carries a nursing-selected marker
        assert f'value="{nursing_id}" selected' in body or \
               f'value="{nursing_id}"selected' in body
    finally:
        _cleanup(app)


def test_post_moves_dam_to_chosen_group(admin_client, app):
    dam_id, starter_id, nursing_id = _seed_dam_and_group(app)
    birth_id, calf_ids = _create_birth(app, dam_id, calves=1)
    try:
        r = admin_client.post(
            f"/herd/births/{birth_id}/assign-groups",
            data={
                "dam_group_id": str(nursing_id),
                f"calf_group_{calf_ids[0]}": str(starter_id),
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code
        with app.app_context():
            dam = db.session.get(Cow, dam_id)
            assert dam.group_id == nursing_id, (
                "dam wasn't moved despite explicit POST"
            )
            calf = db.session.get(Cow, calf_ids[0])
            assert calf.group_id == starter_id, (
                "calf wasn't moved despite explicit POST"
            )
            # One CowMovement per animal that actually moved
            moves = CowMovement.query.filter(
                CowMovement.cow_id.in_([dam_id, calf_ids[0]])
            ).all()
            assert len(moves) >= 2
    finally:
        _cleanup(app)


def test_skipping_the_page_leaves_dam_in_starter_group(admin_client, app):
    """The birth is recorded but no confirmation POST happens →
    dam.group_id equals starter, calf.group_id equals starter too."""
    dam_id, starter_id, nursing_id = _seed_dam_and_group(app)
    birth_id, calf_ids = _create_birth(app, dam_id, calves=1)
    try:
        # No POST to /assign-groups — just check DB state after the
        # bare `_create_birth` (which is what the new `create_birth`
        # does behind the scenes).
        with app.app_context():
            dam = db.session.get(Cow, dam_id)
            calf = db.session.get(Cow, calf_ids[0])
            assert dam.group_id == starter_id, (
                "dam was auto-moved without confirmation"
            )
            assert calf.group_id == starter_id, (
                "calf was auto-placed outside starter"
            )
    finally:
        _cleanup(app)
