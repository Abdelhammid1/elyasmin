"""INVENTORY: weighted-average cost bookkeeping for a single ingredient.

Every ingredient carries `current_qty` and `avg_cost`. Purchases blend
into the average; consumption withdraws at the current average and does
not touch it; a purchase-return reverses qty at the current average
(standard textbook — we do not try to un-blend a past purchase).

Nothing here commits. The caller owns the transaction and the JE
autoposts around it.

Modelled on `app/utils/feed_tank.py`, but for `Ingredient` instead of
`FeedTank`. The two intentionally share the same shape and vocabulary so
someone who has read one has read both.
"""
from decimal import Decimal

QTY = Decimal("0.001")
COST = Decimal("0.0001")


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def blend_purchase(ing, qty_added, unit_cost) -> Decimal:
    """Fold a purchase into the ingredient's weighted-average cost.

        new_avg = (cur_qty * cur_avg + qty_added * unit_cost)
                  / (cur_qty + qty_added)

    `last_price` is kept for the "آخر سعر شراء" reference display only —
    the autoposter and stock valuation both read `avg_cost` now.

    Returns the new avg_cost after blending.
    """
    qty_added = _d(qty_added)
    unit_cost = _d(unit_cost)
    cur_qty = _d(ing.current_qty)
    cur_avg = _d(ing.avg_cost)

    new_qty = cur_qty + qty_added
    if new_qty > 0:
        ing.avg_cost = (
            (cur_qty * cur_avg + qty_added * unit_cost) / new_qty
        ).quantize(COST)
    ing.current_qty = new_qty.quantize(QTY)
    ing.last_price = unit_cost  # reference only from phase 6 on
    return _d(ing.avg_cost)


def withdraw(ing, qty) -> Decimal:
    """Decrement `qty` from the ingredient at the current avg_cost.

    Returns the money value withdrawn (`qty * avg_cost`) so the caller
    can slot it into the consuming JE. Raises ValueError if the request
    exceeds what's on hand.
    """
    qty = _d(qty)
    if qty <= 0:
        raise ValueError("الكمية المسحوبة لازم تكون أكبر من صفر.")

    available = _d(ing.current_qty)
    if qty > available:
        raise ValueError(
            f"الرصيد المتاح من {ing.name} هو {available} {ing.unit_label} — "
            f"مش كفاية لسحب {qty}."
        )

    value = (qty * _d(ing.avg_cost)).quantize(Decimal("0.01"))
    new_qty = (available - qty).quantize(QTY)
    ing.current_qty = new_qty
    if new_qty == 0:
        # Nothing left to carry a cost — reset so the next purchase seeds fresh
        ing.avg_cost = Decimal("0")
    return value


def reverse_purchase(ing, qty_removed) -> Decimal:
    """Pull `qty_removed` off the shelf at the current avg_cost.

    Used by purchase-return: we don't try to work out what the original
    purchase cost was, we just credit inventory at today's WA. The
    difference (if any) is a valuation gain/loss the ledger already
    captures because the return JE credits inventory at the RETURN amount
    while this call reduces the stock ledger — the two-sided diff is
    exactly the WA-drift entry the accountant expects.

    Returns the money value removed. Raises ValueError if qty_removed
    exceeds what's on hand (over-return is a data error, not something
    we quietly clamp).
    """
    qty_removed = _d(qty_removed)
    if qty_removed <= 0:
        raise ValueError("كمية المرتجع لازم تكون أكبر من صفر.")
    available = _d(ing.current_qty)
    if qty_removed > available:
        raise ValueError(
            f"مش ممكن ترجع {qty_removed} من {ing.name} — "
            f"الرصيد الحالي {available} بس."
        )
    value = (qty_removed * _d(ing.avg_cost)).quantize(Decimal("0.01"))
    new_qty = (available - qty_removed).quantize(QTY)
    ing.current_qty = new_qty
    if new_qty == 0:
        ing.avg_cost = Decimal("0")
    return value
