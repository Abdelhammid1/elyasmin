"""PHASE 6 — opening-inventory JE.

Every ingredient the client creates with stock-on-hand should hit the
ledger the same way a treasury account with an opening balance already
does (`app/blueprints/accounts/routes.py:72-87`):

    DR (category inventory leaf)      = current_qty * avg_cost
    CR 3900 أرصدة افتتاحية

Kept in its own module so the create-ingredient route, the
migration-time backfill, and the /inventory/valuation "post missing
openings" button can all share the same code path (and the same
idempotence key).
"""
from decimal import Decimal

from app.extensions import db
from app.models.accounting import JournalEntry
from app.models.inventory import Ingredient
from app.services.autoposting import (
    CODE_OPENING_EQUITY,
    INVENTORY_CODE_BY_CATEGORY,
    INVENTORY_DEFAULT_CODE,
)
from app.services.ledger import get_account_by_code, post_journal, LedgerError


SOURCE_TYPE = "OpeningInventory:Ingredient"


def _leaf_code_for(ing: Ingredient) -> str:
    cat = (ing.category or "").split(":", 1)[0]
    return INVENTORY_CODE_BY_CATEGORY.get(cat, INVENTORY_DEFAULT_CODE)


def has_opening_je(ing: Ingredient) -> bool:
    """True when this ingredient already carries an opening JE — used to
    skip on backfill and on re-post from the valuation button."""
    return db.session.query(
        JournalEntry.query
        .filter_by(source_type=SOURCE_TYPE, source_id=ing.id, is_active=True)
        .exists()
    ).scalar()


def post_opening_je(ing: Ingredient, *, created_by=None):
    """Post the opening JE for this ingredient. No-op if one already
    exists (so the caller doesn't need to guard). Raises LedgerError if
    the amount would be zero or the COA is missing the target leaf.
    """
    if has_opening_je(ing):
        return None

    amount = (
        Decimal(str(ing.current_qty or 0)) * Decimal(str(ing.avg_cost or 0))
    ).quantize(Decimal("0.01"))
    if amount <= 0:
        return None

    leaf = get_account_by_code(_leaf_code_for(ing))
    equity = get_account_by_code(CODE_OPENING_EQUITY)
    if leaf is None or equity is None:
        raise LedgerError(
            "دليل الحسابات ناقص أحد الحسابات المطلوبة (مخزون/أرصدة افتتاحية)."
        )

    return post_journal(
        description=f"رصيد افتتاحي — {ing.name}",
        lines=[
            {"account_id": leaf.id, "debit": amount,
             "memo": f"افتتاحي {ing.name}"},
            {"account_id": equity.id, "credit": amount,
             "memo": f"مقابل افتتاحي {ing.name}"},
        ],
        source_type=SOURCE_TYPE,
        source_id=ing.id,
        created_by=created_by,
    )


def backfill_missing() -> tuple[int, Decimal]:
    """Scan every ingredient with `current_qty > 0` and no opening JE,
    posting one for each. Returns (rows_posted, total_amount)."""
    posted = 0
    total = Decimal("0")
    ings = (
        Ingredient.query
        .filter(Ingredient.current_qty > 0)
        .filter(Ingredient.is_archived.is_(False))
        .all()
    )
    for ing in ings:
        if has_opening_je(ing):
            continue
        je = post_opening_je(ing)
        if je is not None:
            posted += 1
            total += Decimal(str(je.lines[0].debit or 0))
    return posted, total
