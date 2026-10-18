"""HERD-5 regression suite — event types that move group + set status.

  1. The 8 seeded types carry the meeting's defaults.
  2. Drying with defaults → moved to the dry group, status جفاف,
     both tied to the event.
  3. Pregnancy check: pregnant → عشار, not pregnant → فارغة.
  4. Calving from the event form → redirect to the birth form; the
     birth sets the dam to حليب and the after-birth page preselects
     the milk group for her.
  5. An added type shows on the event form; system types can't be
     disabled.
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models.herd import (
    Birth, BreedingEvent, BreedingEventType, BreedingStatusChange, Calf,
    CattleGroup, Cow, CowMovement,
)


_TAG = "TEST-HERD5-COW"
_NEW_TYPE = "HERD5-TEST-إجراء"


def _group_id(app, gtype):
    with app.app_context():
        g = CattleGroup.query.filter_by(type=gtype, is_archived=False).first()
        return g.id if g else None


def _seed_cow(app, group_type="milk", status=None):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_TAG).first()
        gid = CattleGroup.query.filter_by(type=group_type, is_archived=False).first().id
        if c is None:
            c = Cow(ear_tag=_TAG, gender=Cow.GENDER_FEMALE, group_id=gid,
                    status=Cow.STATUS_ACTIVE)
            db.session.add(c)
        c.group_id = gid
        c.breeding_status = status
        db.session.commit()
        return c.id


def _cleanup(app):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_TAG).first()
        if c is not None:
            BreedingStatusChange.query.filter_by(cow_id=c.id).delete()
            BreedingEvent.query.filter_by(cow_id=c.id).delete()
            for b in Birth.query.filter_by(mother_id=c.id).all():
                for calf in Calf.query.filter_by(birth_id=b.id).all():
                    if calf.cow_id:
                        CowMovement.query.filter_by(cow_id=calf.cow_id).delete()
                        Cow.query.filter_by(id=calf.cow_id).delete()
                Calf.query.filter_by(birth_id=b.id).delete()
                db.session.delete(b)
            CowMovement.query.filter_by(cow_id=c.id).delete()
            db.session.delete(c)
        BreedingEventType.query.filter_by(name=_NEW_TYPE).delete()
        db.session.commit()


def test_seeded_types(app):
    with app.app_context():
        by_code = {t.code: t for t in BreedingEventType.query.all()}
        for code in ("insemination", "pregnancy_check", "pregnancy_confirm",
                     "drying", "waiting", "calving", "abortion", "weaning"):
            assert code in by_code and by_code[code].is_system
        dry = by_code["drying"]
        assert dry.target_status == "dry"
        assert dry.target_group.type == CattleGroup.TYPE_DRY
        assert by_code["calving"].target_status == "milking"
        assert by_code["pregnancy_confirm"].status_if_positive == "preg2"


def test_drying_moves_group_and_sets_status(admin_client, app):
    cow_id = _seed_cow(app, "milk", Cow.BS_PREG2)
    dry_gid = _group_id(app, "dry")
    try:
        r = admin_client.post(f"/herd/{cow_id}/breeding/new", data={
            "event_type": "drying",
            "event_date": date.today().isoformat(),
        }, follow_redirects=False)
        assert r.status_code in (302, 303)
        with app.app_context():
            c = db.session.get(Cow, cow_id)
            ev = BreedingEvent.query.filter_by(cow_id=cow_id).first()
            assert c.group_id == dry_gid
            assert c.breeding_status == Cow.BS_DRY
            mv = CowMovement.query.filter_by(cow_id=cow_id).order_by(
                CowMovement.id.desc()).first()
            assert mv.to_group_id == dry_gid and "تجفيف" in mv.reason
            ch = BreedingStatusChange.query.filter_by(cow_id=cow_id).first()
            assert ch.event_id == ev.id and ch.to_status == Cow.BS_DRY
    finally:
        _cleanup(app)


def test_user_override_of_group(admin_client, app):
    """User keeps her in the milk group while recording drying."""
    cow_id = _seed_cow(app, "milk", Cow.BS_PREG2)
    milk_gid = _group_id(app, "milk")
    try:
        admin_client.post(f"/herd/{cow_id}/breeding/new", data={
            "event_type": "drying",
            "event_date": date.today().isoformat(),
            "new_group_id": "",
            "new_status": "dry",
        })
        with app.app_context():
            c = db.session.get(Cow, cow_id)
            assert c.group_id == milk_gid
            assert c.breeding_status == Cow.BS_DRY
    finally:
        _cleanup(app)


def test_pregnancy_check_results(admin_client, app):
    cow_id = _seed_cow(app, "milk", Cow.BS_INSEM)
    try:
        admin_client.post(f"/herd/{cow_id}/breeding/new", data={
            "event_type": "pregnancy_check",
            "event_date": date.today().isoformat(),
            "result": "not_pregnant",
        })
        with app.app_context():
            assert db.session.get(Cow, cow_id).breeding_status == Cow.BS_OPEN
        admin_client.post(f"/herd/{cow_id}/breeding/new", data={
            "event_type": "pregnancy_check",
            "event_date": date.today().isoformat(),
            "result": "pregnant",
        })
        with app.app_context():
            assert db.session.get(Cow, cow_id).breeding_status == Cow.BS_PREG
    finally:
        _cleanup(app)


def test_calving_goes_through_birth_form(admin_client, app):
    cow_id = _seed_cow(app, "pre_birth", Cow.BS_WAITING)
    milk_gid = _group_id(app, "milk")
    try:
        r = admin_client.post(f"/herd/{cow_id}/breeding/new", data={
            "event_type": "calving",
            "event_date": date.today().isoformat(),
        }, follow_redirects=False)
        assert r.status_code in (302, 303)
        assert f"mother_id={cow_id}" in r.headers["Location"]
        with app.app_context():
            assert BreedingEvent.query.filter_by(cow_id=cow_id).count() == 0

        r = admin_client.post("/herd/births/new", data={
            "mother_id": str(cow_id),
            "birth_date": date.today().isoformat(),
            "calves_count": "1",
            "delivery_type": "natural",
            "calf_gender_0": "female",
            "calf_alive_0": "1",
            "calf_tag_0": "",
        }, follow_redirects=False)
        assert r.status_code in (302, 303)
        with app.app_context():
            assert db.session.get(Cow, cow_id).breeding_status == Cow.BS_MILKING
            birth_id = Birth.query.filter_by(mother_id=cow_id).first().id

        body = admin_client.get(f"/herd/births/{birth_id}/assign-groups").get_data(as_text=True)
        dam_select = body.split('name="dam_group_id"')[1].split("</select>")[0]
        assert f'value="{milk_gid}" selected' in dam_select
    finally:
        _cleanup(app)


def test_added_type_on_form_and_system_toggle_refused(admin_client, app):
    cow_id = _seed_cow(app, "milk", None)
    try:
        r = admin_client.post("/herd/event-types/new", data={
            "name": _NEW_TYPE, "target_group_id": "", "target_status": "open",
            "status_if_positive": "", "status_if_negative": "", "sort_order": "",
        })
        assert r.status_code in (302, 303), r.data[:300]
        with app.app_context():
            t = BreedingEventType.query.filter_by(name=_NEW_TYPE).first()
            assert t.code == f"custom_{t.id}" and t.target_status == "open"
            drying_id = BreedingEventType.query.filter_by(code="drying").first().id
        assert _NEW_TYPE in admin_client.get(
            f"/herd/{cow_id}/breeding/new").get_data(as_text=True)

        admin_client.post(f"/herd/event-types/{drying_id}/toggle")
        with app.app_context():
            assert db.session.get(BreedingEventType, drying_id).is_active is True
    finally:
        _cleanup(app)
