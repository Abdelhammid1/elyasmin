"""HERD-4 regression suite — status on the cow form, no name.

  1. Create with a status → stored + one history row (from None).
  2. Create without a status → NULL, no history row.
  3. Edit preg → dry → stored + history row.
  4. Add form has no name input and lists the statuses.
"""
from __future__ import annotations

from app.extensions import db
from app.models.herd import (
    BreedingStatusChange, CattleGroup, Cow, CowMovement,
)


_TAG = "TEST-HERD4-COW"


def _non_fattening_group_id(app):
    with app.app_context():
        g = CattleGroup.query.filter(
            CattleGroup.type != CattleGroup.TYPE_FATTENING,
            CattleGroup.is_archived.is_(False),
        ).first()
        return g.id


def _cleanup(app):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_TAG).first()
        if c is not None:
            BreedingStatusChange.query.filter_by(cow_id=c.id).delete()
            CowMovement.query.filter_by(cow_id=c.id).delete()
            db.session.delete(c)
            db.session.commit()


def _post_new(admin_client, app, status):
    return admin_client.post("/herd/new", data={
        "ear_tag": _TAG,
        "breeding_status": status,
        "gender": "female",
        "group_id": str(_non_fattening_group_id(app)),
        "season_count_offset": "0",
    }, follow_redirects=False)


def test_create_with_status_writes_history(admin_client, app):
    _cleanup(app)
    try:
        r = _post_new(admin_client, app, "preg")
        assert r.status_code in (302, 303), r.data[:300]
        with app.app_context():
            c = Cow.query.filter_by(ear_tag=_TAG).first()
            assert c.breeding_status == "preg"
            rows = BreedingStatusChange.query.filter_by(cow_id=c.id).all()
            assert len(rows) == 1
            assert rows[0].from_status is None
            assert rows[0].to_status == "preg"
    finally:
        _cleanup(app)


def test_create_without_status(admin_client, app):
    _cleanup(app)
    try:
        r = _post_new(admin_client, app, "")
        assert r.status_code in (302, 303), r.data[:300]
        with app.app_context():
            c = Cow.query.filter_by(ear_tag=_TAG).first()
            assert c.breeding_status is None
            assert BreedingStatusChange.query.filter_by(cow_id=c.id).count() == 0
    finally:
        _cleanup(app)


def test_edit_status_writes_history(admin_client, app):
    _cleanup(app)
    try:
        _post_new(admin_client, app, "preg")
        with app.app_context():
            c = Cow.query.filter_by(ear_tag=_TAG).first()
            cid, gid = c.id, c.group_id
        r = admin_client.post(f"/herd/{cid}/edit", data={
            "ear_tag": _TAG,
            "breeding_status": "dry",
            "gender": "female",
            "group_id": str(gid),
            "season_count_offset": "0",
        }, follow_redirects=False)
        assert r.status_code in (302, 303), r.data[:300]
        with app.app_context():
            assert db.session.get(Cow, cid).breeding_status == "dry"
            last = (BreedingStatusChange.query.filter_by(cow_id=cid)
                    .order_by(BreedingStatusChange.id.desc()).first())
            assert (last.from_status, last.to_status) == ("preg", "dry")
    finally:
        _cleanup(app)


def test_add_form_has_status_and_no_name(admin_client, app):
    body = admin_client.get("/herd/new").get_data(as_text=True)
    assert 'name="name"' not in body
    assert 'name="breeding_status"' in body
    for label in ("عشار مؤكد", "حليب", "فارغة"):
        assert label in body
