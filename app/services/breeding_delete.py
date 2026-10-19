"""HERD-10: delete a breeding event and undo what it did.

An event can have:
  - set the cow's status (BreedingStatusChange.event_id)   — HERD-5
  - moved her to another group (CowMovement.event_id)       — HERD-5
  - an insemination cost from stock or cash                 — HERD-8

Status and group are only rolled back when this event made the cow's
latest change of that kind. If something newer happened since, the
cow keeps her current status/group and the old history row is kept
but unlinked — rolling back under a newer change would put her in a
state that never existed.
"""
from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.models.herd import BreedingEvent, BreedingStatusChange, CowMovement


class DeleteRefused(ValueError):
    """Arabic message, safe to flash."""


def _undo_status(event, cow, undone: list[str]) -> None:
    from app.models.herd import breeding_status_label
    for change in BreedingStatusChange.query.filter_by(event_id=event.id).all():
        newer = (BreedingStatusChange.query
                 .filter(BreedingStatusChange.cow_id == cow.id,
                         BreedingStatusChange.id > change.id)
                 .count())
        if newer == 0 and cow.breeding_status == change.to_status:
            cow.breeding_status = change.from_status or None
            db.session.delete(change)
            undone.append(f"الحالة رجعت {breeding_status_label(change.from_status)}")
        else:
            change.event_id = None
            change.reason = "الإجراء اللي عمل التغيير ده اتحذف"
            undone.append("الحالة الحالية فضلت زي ما هي (فيه تغيير أحدث)")


def _undo_group(event, cow, undone: list[str]) -> None:
    for move in CowMovement.query.filter_by(event_id=event.id).all():
        newer = (CowMovement.query
                 .filter(CowMovement.cow_id == cow.id, CowMovement.id > move.id)
                 .count())
        if newer == 0 and cow.group_id == move.to_group_id and move.from_group_id:
            cow.group_id = move.from_group_id
            db.session.delete(move)
            undone.append(f"المجموعة رجعت {move.from_group.name}")
        else:
            move.event_id = None
            undone.append("المجموعة الحالية فضلت زي ما هي (فيه نقل أحدث)")


def _undo_stock_cost(event, undone: list[str]) -> None:
    from app.models.inventory import StockMovement
    from app.services.autoposting import _delete_prior_je
    ing = event.cost_ingredient
    qty = Decimal(str(event.cost_qty or 0))
    cost = Decimal(str(event.cost_amount or 0))
    if ing is not None and qty > 0:
        cur_qty = Decimal(str(ing.current_qty or 0))
        cur_avg = Decimal(str(ing.avg_cost or 0))
        new_qty = cur_qty + qty
        ing.avg_cost = ((cur_qty * cur_avg + cost) / new_qty).quantize(Decimal("0.0001"))
        ing.current_qty = new_qty.quantize(Decimal("0.001"))
        undone.append(f"رجع {qty:g} {ing.unit_label} {ing.name} للمخزون")
    StockMovement.query.filter_by(reason=StockMovement.REASON_INSEMINATION,
                                  ref_id=event.id).delete()
    _delete_prior_je("BreedingEvent", event.id)


def _undo_cash_cost(event, undone: list[str]) -> None:
    from app.models.finance import AccountMovement, Expense
    from app.services.autoposting import _delete_prior_je
    expense = db.session.get(Expense, event.cost_expense_id) if event.cost_expense_id else None
    event.cost_expense_id = None
    if expense is None:
        return
    for mv in AccountMovement.query.filter_by(ref_type="expense", ref_id=expense.id).all():
        account = mv.account
        account.current_balance = (Decimal(str(account.current_balance or 0))
                                   - Decimal(str(mv.amount))).quantize(Decimal("0.01"))
        db.session.delete(mv)
    _delete_prior_je("Expense", expense.id)
    db.session.flush()
    db.session.delete(expense)
    undone.append(f"اترجع مبلغ {expense.amount} للخزنة")


def delete_breeding_event(event: BreedingEvent) -> list[str]:
    """Undo and delete. Raises DeleteRefused. Caller commits."""
    if event.birth_id:
        raise DeleteRefused(
            "الولادة دي مربوطة بتسجيل ولادة (مواليد وعدد مواسم) — مينفعش تتحذف من هنا."
        )
    cow = event.cow
    undone: list[str] = []
    _undo_status(event, cow, undone)
    _undo_group(event, cow, undone)
    if event.cost_source == BreedingEvent.COST_STOCK:
        _undo_stock_cost(event, undone)
    elif event.cost_source == BreedingEvent.COST_CASH:
        _undo_cash_cost(event, undone)
    db.session.flush()
    db.session.delete(event)
    return undone
