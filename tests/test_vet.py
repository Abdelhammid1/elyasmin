"""VET-1 regression suite — vet records baseline.

  1. Vaccination for one cow: next_due = date + repeat_days; with a
     medicine → a normal dispense, linked, stock down.
  2. Group vaccination: one record per active cow in the group, shared
     batch, sold cows skipped; per-head medicine → one group dispense.
  3. Due list: due / overdue shown; a newer dose clears it.
  4. Catalog admin: add / disable; disabled items not offered.
  5. Delete: plain record deleted; record with a dispense refused.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.accounting import JournalEntry, JournalLine
from app.models.feed import MedicineDispense
from app.models.herd import CattleGroup, Cow
from app.models.inventory import Ingredient, MedicineLot, StockMovement
from app.models.vet import VetCatalogItem, VetRecord
from app.services.vet_due import build_due

_P = "TEST-VET-"
_VACCINE = _P + "VACCINE"
_DISEASE = _P + "DISEASE"
_MED = _P + "DRUG"
_GROUP = _P + "GROUP"


def _seed(app):
    with app.app_context():
        g = CattleGroup(name=_GROUP, type="milk")
        db.session.add(g)
        db.session.flush()
        cows = []
        for i, status in enumerate((Cow.STATUS_ACTIVE, Cow.STATUS_ACTIVE, Cow.STATUS_SOLD)):
            c = Cow(ear_tag=f"{_P}{i}", gender=Cow.GENDER_FEMALE, group_id=g.id, status=status)
            db.session.add(c)
            cows.append(c)
        vac = VetCatalogItem(kind="vaccine", name=_VACCINE, repeat_days=180, is_active=True)
        dis = VetCatalogItem(kind="disease", name=_DISEASE, is_active=True)
        ing = Ingredient(name=_MED, category=Ingredient.CATEGORY_MEDICINE,
                         unit=Ingredient.UNIT_PIECE, current_qty=Decimal("20"),
                         avg_cost=Decimal("3"))
        db.session.add_all([vac, dis, ing])
        db.session.flush()
        db.session.add(MedicineLot(ingredient_id=ing.id, lot_number="V1", qty_received=20,
                                   qty_remaining=20, unit_cost=3,
                                   source_type=MedicineLot.SOURCE_OPENING, source_id=1))
        db.session.commit()
        return {"group": g.id, "cows": [c.id for c in cows], "vac": vac.id,
                "dis": dis.id, "ing": ing.id}


def _cleanup(app):
    with app.app_context():
        cow_ids = [c.id for c in Cow.query.filter(Cow.ear_tag.like(_P + "%"))]
        if cow_ids:
            VetRecord.query.filter(VetRecord.cow_id.in_(cow_ids)).delete(synchronize_session=False)
        ing = Ingredient.query.filter_by(name=_MED).first()
        if ing is not None:
            for d in MedicineDispense.query.filter_by(ingredient_id=ing.id).all():
                for je in JournalEntry.query.filter_by(source_type="MedicineDispense",
                                                       source_id=d.id).all():
                    JournalLine.query.filter_by(entry_id=je.id).delete()
                    db.session.delete(je)
                db.session.delete(d)
            StockMovement.query.filter_by(ingredient_id=ing.id).delete()
            MedicineLot.query.filter_by(ingredient_id=ing.id).delete()
            db.session.delete(ing)
        if cow_ids:
            Cow.query.filter(Cow.id.in_(cow_ids)).delete(synchronize_session=False)
        VetCatalogItem.query.filter(VetCatalogItem.name.like(_P + "%")).delete(
            synchronize_session=False)
        CattleGroup.query.filter_by(name=_GROUP).delete()
        db.session.commit()


def test_single_vaccination_with_medicine(admin_client, app):
    _cleanup(app)
    s = _seed(app)
    try:
        on = date.today() - timedelta(days=2)
        r = admin_client.post(f"/vet/cow/{s['cows'][0]}/new", data={
            "kind": "vaccination", "item_id": str(s["vac"]), "record_date": on.isoformat(),
            "next_due": "", "ingredient_id": str(s["ing"]), "qty": "2", "notes": "x",
        })
        assert r.status_code in (302, 303), r.data[:300]
        with app.app_context():
            rec = VetRecord.query.filter_by(cow_id=s["cows"][0]).one()
            assert rec.next_due == on + timedelta(days=180)
            assert rec.dispense is not None and rec.dispense.cow_id == s["cows"][0]
            assert rec.dispense.qty == Decimal("2")
            assert db.session.get(Ingredient, s["ing"]).current_qty == Decimal("18")
        # a treatment without a disease is fine; a disease without one isn't
        r = admin_client.post(f"/vet/cow/{s['cows'][0]}/new", data={
            "kind": "treatment", "item_id": "", "record_date": on.isoformat()})
        assert r.status_code in (302, 303)
        r = admin_client.post(f"/vet/cow/{s['cows'][0]}/new", data={
            "kind": "disease", "item_id": "", "record_date": on.isoformat()})
        assert r.status_code == 200
    finally:
        _cleanup(app)


def test_group_vaccination(admin_client, app):
    _cleanup(app)
    s = _seed(app)
    try:
        r = admin_client.post("/vet/group-vaccination", data={
            "item_id": str(s["vac"]), "record_date": date.today().isoformat(),
            "group": str(s["group"]), "ingredient_id": str(s["ing"]), "qty": "1.5",
        })
        assert r.status_code in (302, 303), r.data[:300]
        with app.app_context():
            recs = VetRecord.query.filter(VetRecord.cow_id.in_(s["cows"])).all()
            assert sorted(r.cow_id for r in recs) == s["cows"][:2]   # sold cow skipped
            assert len({r.batch for r in recs}) == 1 and recs[0].batch
            d = MedicineDispense.query.filter_by(ingredient_id=s["ing"]).one()
            assert d.group_id == s["group"] and d.qty == Decimal("3")
            assert all(r.dispense_id == d.id for r in recs)
    finally:
        _cleanup(app)


def test_due_list(admin_client, app):
    _cleanup(app)
    s = _seed(app)
    try:
        today = date.today()
        with app.app_context():
            db.session.add_all([
                VetRecord(cow_id=s["cows"][0], kind="vaccination", item_id=s["vac"],
                          record_date=today - timedelta(days=200), next_due=today - timedelta(days=3)),
                VetRecord(cow_id=s["cows"][1], kind="vaccination", item_id=s["vac"],
                          record_date=today - timedelta(days=10), next_due=today + timedelta(days=170)),
            ])
            db.session.commit()
            rows = [r for r in build_due(today, 7) if r["tag"].startswith(_P)]
            assert [(r["tag"], r["days"]) for r in rows] == [(_P + "0", -3)]
        body = admin_client.get("/vet/due").get_data(as_text=True)
        assert _P + "0" in body and "متأخر" in body
        # a new dose replaces the old due date
        admin_client.post(f"/vet/cow/{s['cows'][0]}/new", data={
            "kind": "vaccination", "item_id": str(s["vac"]), "record_date": today.isoformat()})
        with app.app_context():
            assert not [r for r in build_due(today, 7) if r["tag"].startswith(_P)]
    finally:
        _cleanup(app)


def test_catalog_admin(admin_client, app):
    _cleanup(app)
    s = _seed(app)
    try:
        r = admin_client.post("/vet/catalog", data={
            "kind": "vaccine", "name": _P + "NEW", "repeat_days": "90"})
        assert r.status_code in (302, 303)
        with app.app_context():
            new = VetCatalogItem.query.filter_by(name=_P + "NEW").one()
            assert new.repeat_days == 90 and new.is_active
            new_id = new.id
        admin_client.post(f"/vet/catalog/{new_id}", data={"action": "toggle"})
        form = admin_client.get(f"/vet/cow/{s['cows'][0]}/new").get_data(as_text=True)
        assert f'value="{s["vac"]}"' in form and f'value="{new_id}"' not in form
        # a disabled vaccine is refused on save too
        r = admin_client.post(f"/vet/cow/{s['cows'][0]}/new", data={
            "kind": "vaccination", "item_id": str(new_id), "record_date": date.today().isoformat()})
        assert r.status_code == 200
    finally:
        _cleanup(app)


def test_delete(admin_client, app):
    _cleanup(app)
    s = _seed(app)
    try:
        with app.app_context():
            plain = VetRecord(cow_id=s["cows"][0], kind="disease", item_id=s["dis"],
                              record_date=date.today())
            db.session.add(plain)
            db.session.commit()
            plain_id = plain.id
        admin_client.post(f"/vet/records/{plain_id}/delete")
        with app.app_context():
            assert db.session.get(VetRecord, plain_id) is None
        admin_client.post(f"/vet/cow/{s['cows'][0]}/new", data={
            "kind": "treatment", "item_id": str(s["dis"]), "record_date": date.today().isoformat(),
            "ingredient_id": str(s["ing"]), "qty": "1"})
        with app.app_context():
            linked = VetRecord.query.filter_by(cow_id=s["cows"][0]).one()
            linked_id = linked.id
        r = admin_client.post(f"/vet/records/{linked_id}/delete", follow_redirects=True)
        assert "مينفعش" in r.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(VetRecord, linked_id) is not None
    finally:
        _cleanup(app)
