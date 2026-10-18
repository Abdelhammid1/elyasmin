"""HERD-3 regression suite — the client's status list.

  1. The 13 seeded statuses exist, in the client's order, all active.
  2. breeding_status_label resolves a code; unknown → itself.
  3. A status added from the admin screen shows up in retro-edit.
  4. Disabling a status is refused while an active cow carries it.
  5. A calving event's confirm page preselects "milking" (حليب).
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models.herd import (
    BreedingEvent, BreedingStatus, BreedingStatusChange, CattleGroup, Cow,
    breeding_status_choices, breeding_status_label,
)


_COW_TAG = "TEST-HERD3-COW"
_NEW_NAME = "HERD3-TEST-حالة"

_EXPECTED = [
    "calf", "b_calf", "weaning", "heifer", "fattening", "insem", "preg",
    "preg2", "dry", "waiting", "milking", "abort", "open",
]


def _seed_cow(app, status=None):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_COW_TAG).first()
        if c is None:
            c = Cow(ear_tag=_COW_TAG, gender=Cow.GENDER_FEMALE,
                    group_id=CattleGroup.query.first().id,
                    status=Cow.STATUS_ACTIVE)
            db.session.add(c)
        c.breeding_status = status
        db.session.commit()
        return c.id


def _cleanup(app):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_COW_TAG).first()
        if c is not None:
            BreedingStatusChange.query.filter_by(cow_id=c.id).delete()
            BreedingEvent.query.filter_by(cow_id=c.id).delete()
            db.session.delete(c)
        BreedingStatus.query.filter_by(name=_NEW_NAME).delete()
        db.session.commit()


def test_seeded_statuses_in_client_order(app):
    with app.app_context():
        codes = [code for code, _ in breeding_status_choices()]
        # Admin-added rows (if any left over) sort after the seed.
        assert codes[:13] == _EXPECTED
        assert breeding_status_label("milking") == "حليب"
        assert breeding_status_label("preg2") == "عشار مؤكد"


def test_label_falls_back_to_code(app):
    with app.app_context():
        assert breeding_status_label("no_such_code") == "no_such_code"
        assert breeding_status_label(None) == "—"


def test_admin_add_status_appears_in_retro_edit(admin_client, app):
    cow_id = _seed_cow(app)
    try:
        r = admin_client.post("/herd/statuses/new", data={"name": _NEW_NAME})
        assert r.status_code in (302, 303)
        with app.app_context():
            s = BreedingStatus.query.filter_by(name=_NEW_NAME).first()
            assert s is not None
            assert s.code == f"custom_{s.id}"
            assert s.is_active is True

        r = admin_client.get(f"/herd/{cow_id}/breeding/retro-status")
        assert r.status_code == 200
        assert _NEW_NAME in r.get_data(as_text=True)
    finally:
        _cleanup(app)


def test_toggle_refused_while_cows_carry_status(admin_client, app):
    _seed_cow(app, status=Cow.BS_DRY)
    try:
        with app.app_context():
            dry_id = BreedingStatus.query.filter_by(code="dry").first().id

        admin_client.post(f"/herd/statuses/{dry_id}/toggle")
        with app.app_context():
            assert db.session.get(BreedingStatus, dry_id).is_active is True
    finally:
        _cleanup(app)


def test_toggle_allowed_when_unused(admin_client, app):
    try:
        admin_client.post("/herd/statuses/new", data={"name": _NEW_NAME})
        with app.app_context():
            sid = BreedingStatus.query.filter_by(name=_NEW_NAME).first().id
        admin_client.post(f"/herd/statuses/{sid}/toggle")
        with app.app_context():
            assert db.session.get(BreedingStatus, sid).is_active is False
            assert _NEW_NAME not in [n for _, n in breeding_status_choices()]
    finally:
        _cleanup(app)


def test_calving_event_suggests_milking(admin_client, app):
    cow_id = _seed_cow(app, status=Cow.BS_WAITING)
    try:
        r = admin_client.post(
            f"/herd/{cow_id}/breeding/new",
            data={"event_type": "calving",
                  "event_date": date.today().isoformat()},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        r = admin_client.get(r.headers["Location"])
        body = r.get_data(as_text=True)
        assert "حليب" in body
        assert 'value="milking"' in body

        with app.app_context():
            ev = BreedingEvent.query.filter_by(cow_id=cow_id).first()
        r = admin_client.post(
            f"/herd/{cow_id}/breeding/{ev.id}/confirm-status",
            data={"new_status": "milking"},
        )
        assert r.status_code in (302, 303)
        with app.app_context():
            assert db.session.get(Cow, cow_id).breeding_status == Cow.BS_MILKING
    finally:
        _cleanup(app)
