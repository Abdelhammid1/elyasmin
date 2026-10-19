"""Medicine dispense regression — locks the /medicine/new behaviour
before VET-1 moved its body into app/services/medicine_dispense.py.

  1. Dispense to a cow: stock and lot down, StockMovement, JE 5020/1220.
  2. Two lots: the earlier expiry is drawn first, cost blended.
  3. Not enough stock → refused, nothing saved.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.accounting import JournalEntry, JournalLine
from app.models.feed import MedicineDispense
from app.models.herd import CattleGroup, Cow
from app.models.inventory import Ingredient, MedicineLot, StockMovement

_TAG = "TEST-MED-COW"
_MED = "TEST-MED-DRUG"


def _seed(app, lots=((10, 5, None),)):
    """lots: (qty, unit_cost, days_to_expiry)."""
    with app.app_context():
        cow = Cow(ear_tag=_TAG, gender=Cow.GENDER_FEMALE,
                  group_id=CattleGroup.query.first().id, status=Cow.STATUS_ACTIVE)
        ing = Ingredient(name=_MED, category=Ingredient.CATEGORY_MEDICINE,
                         unit=Ingredient.UNIT_PIECE)
        db.session.add_all([cow, ing])
        db.session.flush()
        total_q = total_v = Decimal("0")
        for i, (q, c, days) in enumerate(lots):
            db.session.add(MedicineLot(
                ingredient_id=ing.id, lot_number=f"L{i}", qty_received=q, qty_remaining=q,
                unit_cost=c, source_type=MedicineLot.SOURCE_OPENING, source_id=i,
                expires_on=date.today() + timedelta(days=days) if days else None,
            ))
            total_q += Decimal(q)
            total_v += Decimal(q) * Decimal(c)
        ing.current_qty = total_q
        ing.avg_cost = (total_v / total_q).quantize(Decimal("0.0001"))
        db.session.commit()
        return cow.id, ing.id


def _cleanup(app):
    with app.app_context():
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
        Cow.query.filter_by(ear_tag=_TAG).delete()
        db.session.commit()


def _post(admin_client, cow_id, ing_id, qty):
    return admin_client.post("/medicine/new", data={
        "ingredient_id": str(ing_id), "qty": str(qty), "dispense_target": "cow",
        "cow_id": str(cow_id), "group_id": "0",
        "dispensed_on": date.today().isoformat(), "notes": "",
    })


def test_dispense_to_cow(admin_client, app):
    _cleanup(app)
    cow_id, ing_id = _seed(app)
    try:
        r = _post(admin_client, cow_id, ing_id, 2)
        assert r.status_code in (302, 303), r.data[:300]
        with app.app_context():
            d = MedicineDispense.query.filter_by(ingredient_id=ing_id).one()
            assert d.cow_id == cow_id and d.qty == Decimal("2")
            assert d.total_cost == Decimal("10.00")
            assert db.session.get(Ingredient, ing_id).current_qty == Decimal("8")
            assert MedicineLot.query.filter_by(ingredient_id=ing_id).one().qty_remaining \
                == Decimal("8")
            mv = StockMovement.query.filter_by(ref_id=d.id,
                                               reason=StockMovement.REASON_MEDICINE).one()
            assert mv.delta == Decimal("-2")
            je = (JournalEntry.query.filter_by(source_type="MedicineDispense", source_id=d.id)
                  .order_by(JournalEntry.id.desc()).first())
            codes = {l.account.code: (l.debit, l.credit) for l in je.lines}
            assert codes["5020"] == (Decimal("10.00"), Decimal("0"))
            assert codes["1220"] == (Decimal("0"), Decimal("10.00"))
    finally:
        _cleanup(app)


def test_fifo_by_expiry(admin_client, app):
    _cleanup(app)
    cow_id, ing_id = _seed(app, lots=((5, 10, 300), (5, 4, 30)))
    try:
        _post(admin_client, cow_id, ing_id, 6)
        with app.app_context():
            lots = {l.lot_number: l.qty_remaining
                    for l in MedicineLot.query.filter_by(ingredient_id=ing_id)}
            assert lots == {"L1": Decimal("0"), "L0": Decimal("4")}
            d = MedicineDispense.query.filter_by(ingredient_id=ing_id).one()
            assert d.total_cost == Decimal("30.00")   # 5×4 + 1×10
    finally:
        _cleanup(app)


def test_not_enough_stock(admin_client, app):
    _cleanup(app)
    cow_id, ing_id = _seed(app)
    try:
        r = _post(admin_client, cow_id, ing_id, 50)
        assert r.status_code == 200
        with app.app_context():
            assert MedicineDispense.query.filter_by(ingredient_id=ing_id).count() == 0
            assert db.session.get(Ingredient, ing_id).current_qty == Decimal("10")
    finally:
        _cleanup(app)
