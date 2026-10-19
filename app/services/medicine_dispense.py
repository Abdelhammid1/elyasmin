"""Dispense a vet medicine from stock — shared by /medicine/new and the
VET-1 vet records (a vaccination / treatment that uses a medicine).

Lots are picked FIFO by expiry, each lot is decremented with its own
StockMovement, the dispense is valued at the blend of what was pulled,
and it is posted to the ledger. The caller commits.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.feed import MedicineDispense
from app.models.inventory import Ingredient, StockMovement


class DispenseError(ValueError):
    """Arabic message, safe to flash."""


def dispense_medicine(ing: Ingredient, input_qty: Decimal, unit_code: str | None,
                      *, cow_id: int | None, group_id: int | None, on: date,
                      notes: str | None, user_id: int | None) -> MedicineDispense:
    from app.services import autoposting
    from app.utils import inventory_cost
    from app.utils.units import to_base

    if ing is None or ing.category != Ingredient.CATEGORY_MEDICINE or ing.is_archived:
        raise DispenseError("لازم تختار دواء صحيح.")
    if input_qty <= 0:
        raise DispenseError("الكمية لازم تكون أكبر من صفر.")
    if bool(cow_id) == bool(group_id):
        raise DispenseError("الصرف لازم يكون على بقرة واحدة أو مجموعة واحدة.")

    # TICKET-2: convert from user's unit to base unit
    code: str = (unit_code or ing.unit).strip()
    if code != ing.unit and ing.factor_for(code) is None:
        raise DispenseError(f"الوحدة {code} مش معرّفة للدواء ده.")
    qty = to_base(input_qty, code, ing) or input_qty

    if qty > ing.current_qty:
        raise DispenseError(f"مفيش رصيد كافي من {ing.name}. المتاح: {ing.current_qty} {ing.unit_label}.")

    # PHASE 6: pick lots FIFO by expiry, decrement each lot, and value the
    # dispense at the weighted average of what was actually pulled.
    try:
        picks = inventory_cost.pick_lots_fifo(ing, qty)
    except ValueError as ve:
        raise DispenseError(str(ve)) from ve

    money_out = sum(((q * c).quantize(Decimal("0.01")) for _, q, c in picks), Decimal("0"))
    unit_price = (money_out / qty).quantize(Decimal("0.01")) if qty else Decimal("0")

    dispense = MedicineDispense(
        ingredient_id=ing.id,
        qty=qty,
        unit_price_at_dispense=unit_price,
        total_cost=money_out.quantize(Decimal("0.01")),
        input_qty=input_qty,
        input_unit_code=code,
        cow_id=cow_id,
        group_id=group_id,
        lot_id=picks[0][0].id if picks else None,
        dispensed_on=on,
        notes=notes,
        created_by_id=user_id,
    )
    db.session.add(dispense)
    db.session.flush()

    # Deduct the ingredient's operational qty, decrement each lot, and
    # record one StockMovement per lot so the audit trail names which
    # batch each dose came from.
    ing.current_qty = ing.current_qty - qty
    if ing.current_qty == 0:
        # avg_cost carries no meaning when the shelf is empty
        ing.avg_cost = Decimal("0")
    for lot, take, cost in picks:
        lot.qty_remaining = (Decimal(str(lot.qty_remaining)) - take).quantize(Decimal("0.001"))
        db.session.add(StockMovement(
            ingredient_id=ing.id,
            delta=-take,
            reason=StockMovement.REASON_MEDICINE,
            ref_id=dispense.id,
            unit_price_at_move=cost,
            input_qty=input_qty,
            input_unit_code=code,
            lot_id=lot.id,
            moved_on=on,
            notes=f"صرف دواء — {dispense.target_label} — دفعة #{lot.id}",
            created_by_id=user_id,
        ))

    autoposting.on_medicine_dispense(dispense, created_by=user_id)
    return dispense
