"""ACCOUNTING FOUNDATION — one function per farm event that produces a JE.

Every existing money event in the app calls one of these next to its own
side effects, in the same transaction. The old rows keep working; the JE
is the second effect that makes the ledger complete.

Sign convention: DR increases assets/expenses, CR increases liabilities/equity/revenue.
Every JE posted here is balanced by construction — one debit line, one
credit line, both for the same amount.

If any of these functions raises LedgerError, it means the underlying event
would leave the ledger broken (missing account, mis-configured chart, etc.).
The caller lets that propagate — better to abort the whole transaction than
to record a money event with no ledger effect.
"""
from decimal import Decimal
from typing import Optional

from app.extensions import db
from app.models.accounting import Account
from app.models.finance import Expense
from app.services.ledger import get_account_by_code, post_journal, LedgerError

# The COA codes every event routes to. Keeping them in one place means the
# chart can be renumbered by editing DEFAULT_COA + this constant, not every
# route.
CODE_TRADE_PAYABLE   = "2100"    # ذمم الموردين — owed to suppliers
CODE_TRADE_RECEIVABLE = "1300"   # ذمم العملاء  — owed by customers
CODE_MILK_REVENUE    = "4100"
CODE_LIVESTOCK_REV   = "4200"
CODE_WAGES_PAYABLE   = "2200"
CODE_LABOUR_EXPENSE  = "5200"
CODE_OPENING_EQUITY  = "3900"    # equity offset for backfilled openings

# Expense-category → COA-code mapping, one place instead of a switch spread
# across every autoposting callsite.
EXPENSE_CODE_BY_CATEGORY = {
    Expense.CAT_ELECTRICITY:      "5300",
    Expense.CAT_MAINTENANCE:      "5310",
    Expense.CAT_RENT:             "5320",
    Expense.CAT_FEED_PURCHASE:    "5100",
    Expense.CAT_MEDICINE_PURCHASE: "5400",
    Expense.CAT_SUPPLIER_PAYMENT: None,   # mirror of a payment — no JE here
    Expense.CAT_WORKER_WAGE:      None,   # mirror of a payment — no JE here
    Expense.CAT_OTHER:            "5900",
}

# The COA code for a raw-material inventory account, by ingredient category.
# A purchase invoice on the "feed" side lands in feed inventory; a medicine
# invoice lands in medicine inventory; anything else in generic raw stock.
INVENTORY_CODE_BY_CATEGORY = {
    "feed":     "1210",
    "medicine": "1220",
}
INVENTORY_DEFAULT_CODE = "1200"


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _treasury_leaf(treasury_account) -> Account:
    """The COA leaf that represents this real cash/bank drawer. Every
    treasury Account is wired to a leaf by wire_treasury_accounts(); if this
    lookup fails, the chart is out of sync and the JE cannot be posted."""
    leaf = Account.query.filter_by(treasury_account_id=treasury_account.id).first()
    if leaf is None:
        raise LedgerError(
            f"الحساب البنكي '{treasury_account.name}' مش مربوط بحساب في دليل الحسابات — "
            f"شغّل seed_coa بعد أي حساب جديد."
        )
    return leaf


def _code(code: str) -> Account:
    acc = get_account_by_code(code)
    if acc is None:
        raise LedgerError(f"الحساب {code} مش موجود في دليل الحسابات.")
    return acc


# ==================== event handlers ====================

def on_supplier_payment(payment, treasury_account, *, created_by=None):
    """A payment out to a supplier. Reduces the payable, reduces the treasury.
    DR ذمم الموردين  /  CR treasury
    """
    payable = _code(CODE_TRADE_PAYABLE)
    treasury = _treasury_leaf(treasury_account)
    amount = _d(payment.amount)

    return post_journal(
        description=f"دفعة للمورد {payment.supplier.name}",
        lines=[
            {"account_id": payable.id, "debit": amount,
             "party_type": "supplier", "party_id": payment.supplier_id,
             "memo": f"دفعة #{payment.id}"},
            {"account_id": treasury.id, "credit": amount,
             "memo": f"من {treasury_account.name}"},
        ],
        entry_date=payment.payment_date,
        source_type="SupplierPayment",
        source_id=payment.id,
        created_by=created_by,
    )


def on_customer_payment(payment, treasury_account, *, created_by=None):
    """A collection from a customer. Reduces the receivable, grows the treasury.
    DR treasury  /  CR ذمم العملاء
    """
    receivable = _code(CODE_TRADE_RECEIVABLE)
    treasury = _treasury_leaf(treasury_account)
    amount = _d(payment.amount)

    return post_journal(
        description=f"دفعة من العميل {payment.customer.name}",
        lines=[
            {"account_id": treasury.id, "debit": amount,
             "memo": f"إلى {treasury_account.name}"},
            {"account_id": receivable.id, "credit": amount,
             "party_type": "customer", "party_id": payment.customer_id,
             "memo": f"دفعة #{payment.id}"},
        ],
        entry_date=payment.payment_date,
        source_type="CustomerPayment",
        source_id=payment.id,
        created_by=created_by,
    )


def on_milk_delivery_priced(delivery, *, created_by=None):
    """A milk delivery gained (or had recomputed) its net value. Records the
    sale on the ledger.
    DR ذمم العملاء  /  CR إيرادات اللبن

    Idempotent by delivery id — an existing JE for this delivery is deleted
    before the fresh one is posted, so a re-price (edit flow) updates the
    ledger correctly. An unpriced delivery (`total_value is None`) removes
    any prior JE and does not post a new one — the "record now, price later"
    contract means an un-pricing takes the row back off the ledger.
    """
    # Delete any prior JE for this delivery so a re-price is a clean swap.
    from app.models.accounting import JournalEntry
    prior = JournalEntry.query.filter_by(
        source_type="MilkDelivery", source_id=delivery.id
    ).all()
    for je in prior:
        db.session.delete(je)

    if delivery.total_value is None:
        return None

    receivable = _code(CODE_TRADE_RECEIVABLE)
    revenue = _code(CODE_MILK_REVENUE)
    amount = _d(delivery.total_value)
    if amount <= 0:
        return None   # a zero-net delivery has nothing to post

    return post_journal(
        description=f"توريد لبن — {delivery.customer.name} ({delivery.qty_kg} كيلو)",
        lines=[
            {"account_id": receivable.id, "debit": amount,
             "party_type": "customer", "party_id": delivery.customer_id,
             "memo": f"توريد #{delivery.id}"},
            {"account_id": revenue.id, "credit": amount,
             "memo": f"صافي بيع {amount} جنيه"},
        ],
        entry_date=delivery.delivery_date,
        source_type="MilkDelivery",
        source_id=delivery.id,
        created_by=created_by,
    )


def on_expense(expense, treasury_account=None, *, created_by=None):
    """A real cash expense. A mirror expense (supplier_payment / worker_payment
    ref_type) is intentionally skipped — the underlying payment already posted
    its own JE, so mirroring here would double-count.

    Cash purchase invoices are a special case: the invoice's own JE already
    debited inventory and credited the payable (via on_purchase_invoice), so
    the accompanying "expense" is just the payable-settlement side.
    DR <category expense>  /  CR treasury     (normal case)
    DR ذمم الموردين        /  CR treasury     (cash purchase invoice case)
    """
    code = EXPENSE_CODE_BY_CATEGORY.get(expense.category)
    if code is None:
        return None   # mirror rows aren't a JE source

    if treasury_account is None:
        return None   # nothing to credit — malformed row, leave to caller check

    treasury = _treasury_leaf(treasury_account)
    amount = _d(expense.amount)

    # Cash purchase invoice: pay off the payable that on_purchase_invoice
    # already created. The DR side is the payable, tagged with the supplier
    # so it lands on the party ledger.
    if expense.ref_type == "purchase_invoice_cash":
        from app.models.suppliers import PurchaseInvoice
        invoice = db.session.get(PurchaseInvoice, expense.ref_id)
        if invoice is None:
            return None  # invoice vanished — nothing sensible to post
        payable = _code(CODE_TRADE_PAYABLE)
        return post_journal(
            description=f"سداد نقدي لفاتورة #{invoice.id} — {invoice.supplier.name}",
            lines=[
                {"account_id": payable.id, "debit": amount,
                 "party_type": "supplier", "party_id": invoice.supplier_id,
                 "memo": f"سداد فاتورة #{invoice.id}"},
                {"account_id": treasury.id, "credit": amount,
                 "memo": f"من {treasury_account.name}"},
            ],
            entry_date=expense.expense_date,
            source_type="Expense",
            source_id=expense.id,
            created_by=created_by,
        )

    expense_acc = _code(code)
    return post_journal(
        description=expense.description or expense.category_label,
        lines=[
            {"account_id": expense_acc.id, "debit": amount},
            {"account_id": treasury.id, "credit": amount,
             "memo": f"من {treasury_account.name}"},
        ],
        entry_date=expense.expense_date,
        source_type="Expense",
        source_id=expense.id,
        created_by=created_by,
    )


def on_treasury_transfer(from_account, to_account, transfer, *, created_by=None):
    """A treasury-to-treasury move. Both sides are asset leaves, so the JE
    is DR destination / CR source of the same amount."""
    src = _treasury_leaf(from_account)
    dst = _treasury_leaf(to_account)
    amount = _d(transfer.amount)

    return post_journal(
        description=f"تحويل من {from_account.name} إلى {to_account.name}",
        lines=[
            {"account_id": dst.id, "debit": amount, "memo": "تحويل وارد"},
            {"account_id": src.id, "credit": amount, "memo": "تحويل صادر"},
        ],
        entry_date=transfer.transfer_date,
        source_type="AccountTransfer",
        source_id=transfer.id,
        created_by=created_by,
    )


def on_worker_payment(payment, treasury_account, *, created_by=None):
    """A wage payment. Direct expense hit — elyasmin's payroll doesn't run a
    monthly accrual, so no wages-payable step: pay the worker → cash out,
    expense in, in one entry.
    DR أجور العمالة  /  CR treasury
    """
    labour = _code(CODE_LABOUR_EXPENSE)
    treasury = _treasury_leaf(treasury_account)
    amount = _d(payment.amount)

    return post_journal(
        description=f"دفعة للعامل {payment.worker.full_name}"
                    + (f" — {payment.notes}" if payment.notes else ""),
        lines=[
            {"account_id": labour.id, "debit": amount},
            {"account_id": treasury.id, "credit": amount,
             "memo": f"من {treasury_account.name}"},
        ],
        entry_date=payment.payment_date,
        source_type="WorkerPayment",
        source_id=payment.id,
        created_by=created_by,
    )


def on_purchase_invoice(invoice, *, created_by=None):
    """A purchase invoice was issued (credit or cash). Records the payable
    and puts the goods into inventory by category. If the invoice was cash,
    the accompanying supplier payment is a SEPARATE event (on_supplier_payment
    posts that side) — no double posting here.
    DR مخزون <category>  /  CR ذمم الموردين
    """
    payable = _code(CODE_TRADE_PAYABLE)
    amount = _d(invoice.total)
    if amount <= 0:
        return None

    # Split by line so a mixed feed+medicine invoice lands on both inventory
    # leaves. If the invoice has no lines (legacy row?), fall back to generic
    # raw materials.
    lines_by_code: dict[str, Decimal] = {}
    if invoice.lines:
        for line in invoice.lines:
            code = INVENTORY_CODE_BY_CATEGORY.get(
                (line.ingredient.category or "").split(":", 1)[0]
                if line.ingredient else "",
                INVENTORY_DEFAULT_CODE,
            )
            line_total = _d(line.line_total)
            lines_by_code[code] = lines_by_code.get(code, Decimal("0")) + line_total
    else:
        lines_by_code[INVENTORY_DEFAULT_CODE] = amount

    # Reconcile — item totals might not sum EXACTLY to invoice.total due to
    # rounding; put any residual in the default inventory bucket.
    posted = sum(lines_by_code.values(), Decimal("0"))
    diff = amount - posted
    if abs(diff) > Decimal("0.005"):
        lines_by_code[INVENTORY_DEFAULT_CODE] = (
            lines_by_code.get(INVENTORY_DEFAULT_CODE, Decimal("0")) + diff
        )

    debit_lines = [
        {"account_id": _code(code).id, "debit": amt,
         "memo": f"مخزون فاتورة #{invoice.id}"}
        for code, amt in lines_by_code.items() if amt != 0
    ]
    return post_journal(
        description=f"فاتورة مشتريات — {invoice.supplier.name} (#{invoice.id})",
        lines=[
            *debit_lines,
            {"account_id": payable.id, "credit": amount,
             "party_type": "supplier", "party_id": invoice.supplier_id,
             "memo": f"فاتورة #{invoice.id}"},
        ],
        entry_date=invoice.invoice_date,
        reference=invoice.original_invoice_no or None,
        source_type="PurchaseInvoice",
        source_id=invoice.id,
        created_by=created_by,
    )
