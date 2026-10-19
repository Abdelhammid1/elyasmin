"""HERD-10 regression suite — delete a breeding event and undo it.

  1. Drying (moved group + set status), latest change → cow back in her
     old group with her old status; movement + change rows gone.
  2. Same, but a newer event changed the status since → current status
     kept, the old change row detached.
  3. Insemination with stock cost → straws back, stock movement + JE gone.
  4. Insemination with cash cost → expense, JE, treasury movement gone,
     treasury balance restored.
  5. Calving linked to a Birth → refused.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.accounting import JournalEntry, JournalLine
from app.models.finance import AccountMovement, Expense, TreasuryAccount
from app.models.herd import (
    Birth, BreedingEvent, BreedingStatusChange, CattleGroup, Cow, CowMovement,
)
from app.models.inventory import Ingredient, StockMovement

_TAG = "TEST-HERD10-COW"
_ITEM = "TEST-HERD10-SEMEN"


def _groups(app):
    with app.app_context():
        milk = CattleGroup.query.filter_by(type="milk", is_archived=False).first().id
        other = (CattleGroup.query.filter(CattleGroup.id != milk,
                                          CattleGroup.is_archived.is_(False))
                 .first().id)
        return milk, other


def _seed(app, qty=10, avg=50):
    milk, _ = _groups(app)
    with app.app_context():
        c = Cow(ear_tag=_TAG, gender=Cow.GENDER_FEMALE, group_id=milk,
                status=Cow.STATUS_ACTIVE, breeding_status=Cow.BS_MILKING)
        db.session.add(c)
        ing = Ingredient.query.filter_by(name=_ITEM).first()
        if ing is None:
            ing = Ingredient(name=_ITEM, category="سائل منوي", unit=Ingredient.UNIT_PIECE)
            db.session.add(ing)
        ing.current_qty = Decimal(qty)
        ing.avg_cost = Decimal(avg)
        db.session.commit()
        return c.id, ing.id


def _cleanup(app):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_TAG).first()
        if c is not None:
            for e in BreedingEvent.query.filter_by(cow_id=c.id).all():
                for je in JournalEntry.query.filter_by(source_type="BreedingEvent", source_id=e.id).all():
                    JournalLine.query.filter_by(entry_id=je.id).delete()
                    db.session.delete(je)
                StockMovement.query.filter_by(reason=StockMovement.REASON_INSEMINATION,
                                              ref_id=e.id).delete()
                if e.cost_expense_id:
                    exp = db.session.get(Expense, e.cost_expense_id)
                    for je in JournalEntry.query.filter_by(source_type="Expense", source_id=exp.id).all():
                        JournalLine.query.filter_by(entry_id=je.id).delete()
                        db.session.delete(je)
                    for mv in AccountMovement.query.filter_by(ref_type="expense", ref_id=exp.id).all():
                        mv.account.current_balance = (mv.account.current_balance or 0) - mv.amount
                        db.session.delete(mv)
                    e.cost_expense_id = None
                    db.session.flush()
                    db.session.delete(exp)
            BreedingStatusChange.query.filter_by(cow_id=c.id).delete()
            CowMovement.query.filter_by(cow_id=c.id).delete()
            BreedingEvent.query.filter_by(cow_id=c.id).delete()
            Birth.query.filter_by(mother_id=c.id).delete()
            db.session.delete(c)
        Ingredient.query.filter_by(name=_ITEM).delete()
        db.session.commit()


def _post_event(admin_client, cow_id, event_type, **extra):
    data = {"event_type": event_type, "event_date": date.today().isoformat()}
    data.update({k: str(v) for k, v in extra.items()})
    r = admin_client.post(f"/herd/{cow_id}/breeding/new", data=data)
    assert r.status_code in (302, 303), r.data[:300]


def _last_event(app, cow_id):
    with app.app_context():
        return (BreedingEvent.query.filter_by(cow_id=cow_id)
                .order_by(BreedingEvent.id.desc()).first().id)


def _delete(admin_client, cow_id, event_id):
    return admin_client.post(f"/herd/{cow_id}/breeding/{event_id}/delete",
                             follow_redirects=True)


def test_delete_latest_undoes_group_and_status(admin_client, app):
    _cleanup(app)
    milk, other = _groups(app)
    cow_id, _ = _seed(app)
    try:
        _post_event(admin_client, cow_id, "drying", new_group_id=other, new_status="dry")
        eid = _last_event(app, cow_id)
        with app.app_context():
            c = db.session.get(Cow, cow_id)
            assert c.group_id == other and c.breeding_status == "dry"
            assert CowMovement.query.filter_by(event_id=eid).count() == 1

        r = _delete(admin_client, cow_id, eid)
        assert r.status_code == 200 and "تم حذف" in r.get_data(as_text=True)
        with app.app_context():
            c = db.session.get(Cow, cow_id)
            assert c.group_id == milk and c.breeding_status == Cow.BS_MILKING
            assert db.session.get(BreedingEvent, eid) is None
            assert CowMovement.query.filter_by(cow_id=cow_id).count() == 0
            assert BreedingStatusChange.query.filter_by(cow_id=cow_id).count() == 0
    finally:
        _cleanup(app)


def test_delete_with_newer_change_keeps_current(admin_client, app):
    _cleanup(app)
    _, other = _groups(app)
    cow_id, _ = _seed(app)
    try:
        _post_event(admin_client, cow_id, "drying", new_group_id=other, new_status="dry")
        first = _last_event(app, cow_id)
        _post_event(admin_client, cow_id, "pregnancy_check", result="pregnant",
                    new_group_id="", new_status="preg")
        _delete(admin_client, cow_id, first)
        with app.app_context():
            c = db.session.get(Cow, cow_id)
            assert c.breeding_status == "preg"
            # group move was still the latest move → rolled back
            old = BreedingStatusChange.query.filter_by(cow_id=cow_id, to_status="dry").one()
            assert old.event_id is None and "اتحذف" in old.reason
    finally:
        _cleanup(app)


def test_delete_stock_cost_returns_straws(admin_client, app):
    _cleanup(app)
    cow_id, ing_id = _seed(app)
    try:
        _post_event(admin_client, cow_id, "insemination", cost_source="stock",
                    cost_ingredient_id=ing_id, cost_qty=2)
        eid = _last_event(app, cow_id)
        with app.app_context():
            assert db.session.get(Ingredient, ing_id).current_qty == Decimal("8")
        _delete(admin_client, cow_id, eid)
        with app.app_context():
            ing = db.session.get(Ingredient, ing_id)
            assert ing.current_qty == Decimal("10")
            assert Decimal(ing.avg_cost) == Decimal("50")
            assert StockMovement.query.filter_by(reason=StockMovement.REASON_INSEMINATION,
                                                 ref_id=eid).count() == 0
            assert JournalEntry.query.filter_by(source_type="BreedingEvent",
                                                source_id=eid).count() == 0
    finally:
        _cleanup(app)


def test_delete_cash_cost_restores_treasury(admin_client, app):
    _cleanup(app)
    cow_id, _ = _seed(app)
    with app.app_context():
        t = TreasuryAccount.query.filter_by(is_archived=False).first()
        tid, before = t.id, Decimal(t.current_balance or 0)
    try:
        _post_event(admin_client, cow_id, "insemination", cost_source="cash",
                    cost_amount="300", cost_account_id=tid)
        eid = _last_event(app, cow_id)
        with app.app_context():
            exp_id = db.session.get(BreedingEvent, eid).cost_expense_id
            assert Decimal(db.session.get(TreasuryAccount, tid).current_balance) == before - 300
        _delete(admin_client, cow_id, eid)
        with app.app_context():
            assert Decimal(db.session.get(TreasuryAccount, tid).current_balance) == before
            assert db.session.get(Expense, exp_id) is None
            assert AccountMovement.query.filter_by(ref_type="expense", ref_id=exp_id).count() == 0
            assert JournalEntry.query.filter_by(source_type="Expense",
                                                source_id=exp_id).count() == 0
    finally:
        _cleanup(app)


def test_birth_linked_calving_refused(admin_client, app):
    _cleanup(app)
    cow_id, _ = _seed(app)
    try:
        with app.app_context():
            b = Birth(mother_id=cow_id, birth_date=date.today())
            db.session.add(b)
            db.session.flush()
            e = BreedingEvent(cow_id=cow_id, event_type=BreedingEvent.EVENT_CALVING,
                              event_date=date.today(), birth_id=b.id)
            db.session.add(e)
            db.session.commit()
            eid = e.id
        r = _delete(admin_client, cow_id, eid)
        assert "مينفعش" in r.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(BreedingEvent, eid) is not None
        # wrong cow in the URL → 404
        assert admin_client.post(f"/herd/{cow_id + 99999}/breeding/{eid}/delete").status_code == 404
    finally:
        _cleanup(app)
