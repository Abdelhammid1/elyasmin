"""TICKET-2: unit conversion helpers.

Every quantity that touches the DB (`PurchaseLine.qty`, `StockMovement.delta`,
`MedicineDispense.qty`, `Ingredient.current_qty`) is ALWAYS stored in the
ingredient's base unit (`Ingredient.unit`). The user may input quantities in
alternate units defined in `IngredientUnit`; we convert here.
"""
from decimal import Decimal
from typing import Optional


def to_base(qty, unit_code: str, ingredient) -> Optional[Decimal]:
    """Convert a user-typed qty in `unit_code` to the ingredient's base unit.

    Returns None if the unit_code is not registered for this ingredient.
    """
    if qty is None:
        return None
    try:
        q = Decimal(str(qty))
    except Exception:  # noqa: BLE001
        return None
    factor = ingredient.factor_for(unit_code)
    if factor is None:
        return None
    return (q * Decimal(str(factor))).quantize(Decimal("0.001"))


def per_base_price(unit_price, unit_code: str, ingredient) -> Optional[Decimal]:
    """User buys 2 ton at 500 EGP/ton → price per base unit (kg) = 500 / 1000 = 0.5.

    Returns per-base-unit price so `line_total = qty_base × per_base_price` stays consistent.
    """
    if unit_price is None:
        return None
    try:
        p = Decimal(str(unit_price))
    except Exception:  # noqa: BLE001
        return None
    factor = ingredient.factor_for(unit_code)
    if factor is None or factor == 0:
        return None
    return (p / Decimal(str(factor))).quantize(Decimal("0.001"))
