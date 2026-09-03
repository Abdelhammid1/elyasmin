"""PHASE 8b — fixed assets autoposter.

Three ledger events:

    purchase: DR 1510 معدات وآلات   /  CR treasury  OR  CR 2100 (supplier)
    monthly:  DR 5600 مصروف الإهلاك /  CR 1520 مجمع إهلاك المعدات
    dispose:  DR 1520 (accumulated)  DR treasury (if sale)
              CR 1510 (original cost)
              CR 4900 إيرادات أخرى  OR  DR 5900 مصروفات متنوعة    (whichever balances)

The purchase source is exactly one of `treasury_account_id` (cash) or
`supplier_id` (credit). The dispose path books the writeoff — cost off
the books, accumulated off the books, cash in (or nothing) — and the
diff lands as a gain-on-sale or loss-on-disposal.
"""
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.assets import DepreciationPosting, FixedAsset
from app.services.autoposting import (
    CODE_TRADE_PAYABLE,
    _code,
    _delete_prior_je,
    _treasury_leaf,
)
from app.services.ledger import LedgerError, post_journal

CODE_FIXED_ASSETS       = "1510"   # معدات وآلات
CODE_ACCUM_DEPRECIATION = "1520"   # مجمع إهلاك المعدات
CODE_DEPRECIATION_EXP   = "5600"   # مصروف الإهلاك
CODE_OTHER_REVENUE      = "4900"   # إيرادات أخرى (used for gain-on-sale)
CODE_MISC_EXPENSE       = "5900"   # مصروفات متنوعة (used for loss-on-disposal)


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def record_asset_purchase(asset: FixedAsset, *, created_by=None):
    """Book the asset onto the balance sheet.

    Debit 1510 fixed-assets by the cost; credit either the treasury the
    money left from OR the supplier's payable (credit purchase). Exactly
    one of `treasury_account_id` / `supplier_id` on the asset row must
    be set — raises LedgerError otherwise.
    """
    _delete_prior_je("FixedAsset:purchase", asset.id)
    cost = _d(asset.purchase_cost)
    if cost <= 0:
        return None

    if asset.treasury_account_id and asset.supplier_id:
        raise LedgerError("لا يمكن شراء الأصل نقدي وبالآجل في نفس الوقت.")
    if not asset.treasury_account_id and not asset.supplier_id:
        raise LedgerError("لازم تختار مصدر الشراء: خزنة أو مورد.")

    fixed_asset_leaf = _code(CODE_FIXED_ASSETS)

    dr_line = {"account_id": fixed_asset_leaf.id, "debit": cost,
               "memo": f"شراء {asset.name}"}

    if asset.treasury_account_id:
        treasury = _treasury_leaf(asset.treasury_account)
        cr_line = {"account_id": treasury.id, "credit": cost,
                   "memo": f"شراء أصل — {asset.name}"}
    else:
        payable = _code(CODE_TRADE_PAYABLE)
        cr_line = {"account_id": payable.id, "credit": cost,
                   "party_type": "supplier", "party_id": asset.supplier_id,
                   "memo": f"شراء أصل — {asset.name}"}

    return post_journal(
        description=f"شراء أصل ثابت — {asset.name}",
        lines=[dr_line, cr_line],
        entry_date=asset.purchase_date,
        source_type="FixedAsset:purchase",
        source_id=asset.id,
        created_by=created_by,
    )


def post_monthly_depreciation(asset: FixedAsset, period_month: date,
                              *, created_by=None) -> DepreciationPosting:
    """Post one month of depreciation.

    Enforces "at most one per (asset, month)" via the DB unique
    constraint on `depreciation_postings.uq_dep_per_asset_month`. A
    second call for the same period raises IntegrityError — caller
    (the bulk-run route) catches that and skips.

    Skips (returns None) when the asset is disposed, already fully
    depreciated, or the monthly amount rounds to zero.
    """
    if asset.status != FixedAsset.STATUS_ACTIVE:
        return None
    if asset.is_fully_depreciated:
        return None

    amount = asset.monthly_depreciation
    # Cap the last month's amount at whatever's left above salvage so
    # we don't over-depreciate.
    remaining = _d(asset.book_value) - _d(asset.salvage_value)
    if remaining <= 0:
        return None
    if amount > remaining:
        amount = remaining

    if amount <= 0:
        return None

    dep_exp = _code(CODE_DEPRECIATION_EXP)
    accum = _code(CODE_ACCUM_DEPRECIATION)

    period = _first_of_month(period_month)

    posting = DepreciationPosting(
        asset_id=asset.id, period_month=period,
        amount=amount, posted_by_id=created_by,
    )
    db.session.add(posting)
    db.session.flush()  # trips the unique constraint here, before the JE

    je = post_journal(
        description=f"إهلاك شهر {period.strftime('%Y-%m')} — {asset.name}",
        lines=[
            {"account_id": dep_exp.id, "debit": amount,
             "memo": f"إهلاك {asset.name}"},
            {"account_id": accum.id, "credit": amount,
             "memo": f"إهلاك متراكم — {asset.name}"},
        ],
        entry_date=period,
        source_type="FixedAsset:depreciation",
        source_id=posting.id,
        created_by=created_by,
    )
    posting.je_id = je.id if je else None
    asset.accumulated_depreciation = _d(asset.accumulated_depreciation) + amount
    return posting


def dispose_asset(asset: FixedAsset, disposal_date: date,
                  sale_price: Decimal = Decimal("0"),
                  sale_treasury=None, notes: str = "",
                  *, created_by=None):
    """Write the asset off the books.

    Book value = cost − accumulated depreciation. Sale price vs book
    value determines gain (CR 4900) or loss (DR 5900):

        DR 1520 accumulated_depreciation
        DR treasury          (sale_price, if any)
        [DR 5900 loss  OR  CR 4900 gain]     (whichever balances)
        CR 1510 fixed_asset_cost
    """
    _delete_prior_je("FixedAsset:dispose", asset.id)

    cost = _d(asset.purchase_cost)
    accum = _d(asset.accumulated_depreciation)
    book_value = cost - accum
    sale_price = _d(sale_price)
    gain_or_loss = sale_price - book_value  # positive = gain, negative = loss

    lines = [
        {"account_id": _code(CODE_ACCUM_DEPRECIATION).id, "debit": accum,
         "memo": f"إغلاق مجمع إهلاك — {asset.name}"} if accum > 0 else None,
        {"account_id": _code(CODE_FIXED_ASSETS).id, "credit": cost,
         "memo": f"شطب {asset.name}"},
    ]
    if sale_price > 0:
        if not sale_treasury:
            raise LedgerError("لازم تختار حساب لاستلام ثمن البيع.")
        lines.append({
            "account_id": _treasury_leaf(sale_treasury).id, "debit": sale_price,
            "memo": f"ثمن بيع {asset.name}",
        })
    if gain_or_loss > 0:
        lines.append({
            "account_id": _code(CODE_OTHER_REVENUE).id, "credit": gain_or_loss,
            "memo": f"ربح بيع أصل — {asset.name}",
        })
    elif gain_or_loss < 0:
        lines.append({
            "account_id": _code(CODE_MISC_EXPENSE).id, "debit": -gain_or_loss,
            "memo": f"خسارة بيع أصل — {asset.name}",
        })

    lines = [l for l in lines if l is not None]
    je = post_journal(
        description=f"تخريد أصل ثابت — {asset.name}",
        lines=lines,
        entry_date=disposal_date,
        source_type="FixedAsset:dispose",
        source_id=asset.id,
        created_by=created_by,
    )

    asset.status = FixedAsset.STATUS_DISPOSED
    asset.disposed_on = disposal_date
    asset.disposal_notes = notes or None
    return je
