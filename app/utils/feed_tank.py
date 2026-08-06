"""FEED-TANK: all balance arithmetic for a feed tank lives here.

Three callers move a tank — creating a feed run, editing one, and withdrawing
for feeding — so the weighted-average maths and the movement bookkeeping are
kept in one place rather than repeated in each route.

Every function records a FeedTankMovement. Nothing here commits; the calling
route owns the transaction.

Sign convention (see FeedTankMovement docstring): qty and total_cost are signed.
Production positive, withdrawal negative, adjustment either way.
"""
from decimal import Decimal

from app.extensions import db
from app.models.feed import FeedTank, FeedTankMovement

QTY = Decimal("0.001")
COST = Decimal("0.001")
MONEY = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def get_or_create_tank(group_id: int) -> FeedTank:
    """The tank for a group, created empty on first use."""
    tank = FeedTank.query.filter_by(group_id=group_id).first()
    if tank is None:
        tank = FeedTank(
            group_id=group_id, current_qty=Decimal("0"), avg_cost_per_kg=Decimal("0")
        )
        db.session.add(tank)
        db.session.flush()
    return tank


def _movement(tank, movement_type, qty, unit_cost, moved_on, *, run_id=None,
              notes=None, user_id=None) -> FeedTankMovement:
    mv = FeedTankMovement(
        tank_id=tank.id,
        movement_type=movement_type,
        qty=qty.quantize(QTY),
        unit_cost=unit_cost.quantize(COST),
        total_cost=(qty * unit_cost).quantize(MONEY),
        ref_feed_run_id=run_id,
        moved_on=moved_on,
        notes=notes,
        created_by_id=user_id,
    )
    db.session.add(mv)
    return mv


def add_production(tank, qty, cost_per_kg, moved_on, *, run_id=None,
                   notes=None, user_id=None) -> FeedTankMovement:
    """Credit a produced batch, blending its cost into the tank average.

        new_avg = (current_qty * avg_cost + qty * cost_per_kg)
                  / (current_qty + qty)
    """
    qty = _d(qty)
    cost_per_kg = _d(cost_per_kg)
    cur_qty = _d(tank.current_qty)
    cur_avg = _d(tank.avg_cost_per_kg)

    new_qty = cur_qty + qty
    if new_qty > 0:
        tank.avg_cost_per_kg = (
            (cur_qty * cur_avg + qty * cost_per_kg) / new_qty
        ).quantize(COST)
    tank.current_qty = new_qty.quantize(QTY)

    return _movement(tank, FeedTankMovement.TYPE_PRODUCTION, qty, cost_per_kg,
                     moved_on, run_id=run_id, notes=notes, user_id=user_id)


def withdraw(tank, qty, moved_on, *, notes=None, user_id=None) -> FeedTankMovement:
    """Debit a feeding withdrawal at the tank's current average cost.

    The average is deliberately left untouched — taking feed out does not change
    what the remaining feed cost. Raises ValueError when the tank cannot cover
    the request; the caller turns that into a message naming the balance.
    """
    qty = _d(qty)
    if qty <= 0:
        raise ValueError("الكمية المسحوبة لازم تكون أكبر من صفر.")

    available = _d(tank.current_qty)
    if qty > available:
        raise ValueError(
            f"الرصيد المتاح في الخزان {available} كيلو بس — مش كفاية لسحب {qty} كيلو."
        )

    unit_cost = _d(tank.avg_cost_per_kg)
    tank.current_qty = (available - qty).quantize(QTY)

    return _movement(tank, FeedTankMovement.TYPE_WITHDRAWAL, -qty, unit_cost,
                     moved_on, notes=notes, user_id=user_id)


def adjust(tank, qty_delta, cost_per_kg, moved_on, *, run_id=None,
           notes=None, user_id=None) -> FeedTankMovement:
    """Correct a tank up or down — used when a feed run's size is edited.

    Going up blends the extra at `cost_per_kg`, exactly like production. Going
    down reverses that blend, and raises ValueError if the tank no longer holds
    that much (it has already been fed out).
    """
    qty_delta = _d(qty_delta)
    cost_per_kg = _d(cost_per_kg)
    cur_qty = _d(tank.current_qty)
    cur_avg = _d(tank.avg_cost_per_kg)

    if qty_delta == 0:
        raise ValueError("مفيش تغيير في الكمية.")

    if qty_delta < 0 and abs(qty_delta) > cur_qty:
        raise ValueError(
            f"الرصيد المتاح في الخزان {cur_qty} كيلو بس — مش ممكن تشيل "
            f"{abs(qty_delta)} كيلو، الكمية دي اتسحبت للتغذية خلاص."
        )

    new_qty = cur_qty + qty_delta
    if new_qty > 0:
        tank.avg_cost_per_kg = (
            (cur_qty * cur_avg + qty_delta * cost_per_kg) / new_qty
        ).quantize(COST)
    else:
        # Tank emptied exactly — no feed left to carry an average
        tank.avg_cost_per_kg = Decimal("0")
    tank.current_qty = new_qty.quantize(QTY)

    return _movement(tank, FeedTankMovement.TYPE_ADJUSTMENT, qty_delta, cost_per_kg,
                     moved_on, run_id=run_id, notes=notes, user_id=user_id)
