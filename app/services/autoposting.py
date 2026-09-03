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

from app.extensions import db
from app.models.accounting import LedgerAccount
from app.models.finance import Expense
from app.services.ledger import get_account_by_code, post_journal, LedgerError

# The COA codes every event routes to. Keeping them in one place means the
# chart can be renumbered by editing DEFAULT_COA + this constant, not every
# route.
# PHASE 8c — codes match Ibrahim's spec: 1010/2010/4010/5010 numbering.
CODE_TRADE_PAYABLE   = "2010"    # ذمم الموردين — owed to suppliers
CODE_TRADE_RECEIVABLE = "1100"   # ذمم العملاء  — owed by customers
CODE_MILK_REVENUE    = "4010"
CODE_LIVESTOCK_REV   = "4020"
CODE_WAGES_PAYABLE   = "2030"
CODE_LABOUR_EXPENSE  = "5030"
CODE_OPENING_EQUITY  = "3090"    # equity offset for backfilled openings
CODE_MEDICINE_INVENTORY = "1220" # مخزون الأدوية (matched spec already)
CODE_MEDICINE_EXPENSE   = "5020" # أدوية بيطرية

# Expense-category → COA-code mapping, one place instead of a switch spread
# across every autoposting callsite.
EXPENSE_CODE_BY_CATEGORY = {
    Expense.CAT_ELECTRICITY:      "5040",
    Expense.CAT_MAINTENANCE:      "5050",
    Expense.CAT_RENT:             "5060",
    Expense.CAT_FEED_PURCHASE:    "5010",
    Expense.CAT_MEDICINE_PURCHASE: "5020",
    Expense.CAT_SUPPLIER_PAYMENT: None,   # mirror of a payment — no JE here
    Expense.CAT_WORKER_WAGE:      None,   # mirror of a payment — no JE here
    Expense.CAT_OTHER:            "5080",
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


def _treasury_leaf(treasury_account) -> LedgerAccount:
    """The COA leaf that represents this real cash/bank drawer. Every
    TreasuryAccount row is wired to a leaf by wire_treasury_accounts();
    if this lookup fails, the chart is out of sync and the JE cannot
    be posted."""
    leaf = LedgerAccount.query.filter_by(treasury_account_id=treasury_account.id).first()
    if leaf is None:
        raise LedgerError(
            f"الحساب البنكي '{treasury_account.name}' مش مربوط بحساب في دليل الحسابات — "
            f"شغّل seed_coa بعد أي حساب جديد."
        )
    return leaf


def _code(code: str) -> LedgerAccount:
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


def on_feeding_session(session, *, created_by=None):
    """A feeding session was recorded. Records the feed cost + additions
    against the group being fed — the ledger-native answer to "what did
    this group cost to feed?".

    DR تكلفة الأعلاف   (5100)      total_cost          [tagged: session.group_id]
    CR مخزون العلف     (1210)      feed_cost
    CR مخزون المواد الخام (1200)   additions_cost      (if any)

    Additions are booked against the generic raw-materials account
    because they cover several buckets (silage, hay, straw) that don't
    have their own COA leaf yet. That's fine — later phases can split
    inventory by ingredient category if needed.

    Idempotent by session id — an existing JE for this session is
    deleted before the fresh one is posted, so an edit-and-recompute
    stays a clean swap.
    """
    from app.models.accounting import JournalEntry
    prior = JournalEntry.query.filter_by(
        source_type="FeedingSession", source_id=session.id
    ).all()
    for je in prior:
        db.session.delete(je)

    total = _d(session.total_cost)
    if total <= 0:
        return None

    feed_cost = _d(session.feed_cost)
    additions_cost = _d(session.additions_cost)

    feed_expense = _code(EXPENSE_CODE_BY_CATEGORY[Expense.CAT_FEED_PURCHASE])
    feed_inventory = _code(INVENTORY_CODE_BY_CATEGORY["feed"])
    raw_inventory = _code(INVENTORY_DEFAULT_CODE)

    lines = [
        # single expense line, tagged with the herd group
        {"account_id": feed_expense.id, "debit": total,
         "cost_center_id": session.group_id,
         "memo": f"وجبة {session.meal} — {session.group.name}"},
    ]
    if feed_cost > 0:
        lines.append({
            "account_id": feed_inventory.id, "credit": feed_cost,
            "memo": "من خزان الوصفة",
        })
    if additions_cost > 0:
        lines.append({
            "account_id": raw_inventory.id, "credit": additions_cost,
            "memo": "إضافات من المخزن العام",
        })

    return post_journal(
        description=f"تغذية — {session.group.name} ({session.meal})",
        lines=lines,
        entry_date=session.session_date,
        source_type="FeedingSession",
        source_id=session.id,
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
    # .get() with no default can't tell "known mirror category, skip on
    # purpose" (CAT_SUPPLIER_PAYMENT / CAT_WORKER_WAGE, mapped to None)
    # apart from "unknown category, e.g. a custom:<label> row the user
    # typed in" — both silently returned None here, so every custom
    # expense category was dropped from the ledger with no JE at all.
    # Known mirrors still skip; anything else — custom categories
    # included — falls back to the generic "other expenses" account.
    _MIRROR_CATEGORIES = {Expense.CAT_SUPPLIER_PAYMENT, Expense.CAT_WORKER_WAGE}
    if expense.category in _MIRROR_CATEGORIES:
        return None   # mirror rows aren't a JE source
    code = EXPENSE_CODE_BY_CATEGORY.get(expense.category, EXPENSE_CODE_BY_CATEGORY[Expense.CAT_OTHER])

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
        description=f"دفعة للعامل {payment.worker.name}"
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


# ==================== PHASE 6 — medicine dispense ====================

def on_medicine_dispense(dispense, *, created_by=None):
    """Ledger effect of a vet-medicine dispense.

        DR 5400 أدوية بيطرية   (dispense.total_cost)   [cost_center = group_id]
        CR 1220 مخزون الأدوية

    Idempotent by (source_type='MedicineDispense', source_id=dispense.id) —
    editing or re-posting a dispense wipes and re-lays the JE. When a
    dispense is archived (soft-delete), passing an `is_archived=True`
    dispense here removes the JE without re-posting, matching the returns
    autoposter pattern.

    When the dispense has a `group_id`, the debit line carries a
    cost-centre tag so the milk-cost-by-group report attributes it.
    Cow-level dispenses have no cost centre — this is a real-world
    limitation, not a bug: a per-cow cost centre would explode the COA
    for no reporting benefit.
    """
    _delete_prior_je("MedicineDispense", dispense.id)
    if getattr(dispense, "is_archived", False):
        return None

    amount = _d(dispense.total_cost)
    if amount <= 0:
        return None

    expense = _code(CODE_MEDICINE_EXPENSE)
    inventory = _code(CODE_MEDICINE_INVENTORY)

    dr_line = {
        "account_id": expense.id, "debit": amount,
        "memo": f"صرف دواء — {dispense.ingredient.name}",
    }
    if getattr(dispense, "group_id", None):
        dr_line["cost_center_id"] = dispense.group_id

    cr_line = {
        "account_id": inventory.id, "credit": amount,
        "memo": f"صرف #{dispense.id} — {dispense.ingredient.name}",
    }

    return post_journal(
        description=f"صرف دواء — {dispense.ingredient.name} ({dispense.target_label})",
        lines=[dr_line, cr_line],
        entry_date=dispense.dispensed_on,
        source_type="MedicineDispense",
        source_id=dispense.id,
        created_by=created_by,
    )


# ==================== PHASE 5 — returns ====================

def _delete_prior_je(source_type: str, source_id: int):
    """Idempotence helper for returns — same pattern the milk-delivery and
    feeding autoposters use."""
    from app.models.accounting import JournalEntry
    for je in JournalEntry.query.filter_by(
        source_type=source_type, source_id=source_id,
    ).all():
        db.session.delete(je)


def on_sales_return(ret, *, created_by=None):
    """A customer return. Reverses revenue; releases either the receivable
    (credit mode) or the treasury (cash mode).

    Credit: DR 4100 إيرادات اللبن / CR 1300 ذمم العملاء
    Cash:   DR 4100 إيرادات اللبن / CR treasury_leaf

    The credit-side line is tagged with the customer so the party ledger
    shows the reversal next to the original sale.

    Skipped for archived returns; caller deletes the JE via the archive
    route by calling this after flipping is_archived=True (idempotent
    prior-JE cleanup means it just removes the JE and posts nothing).
    """
    _delete_prior_je("SalesReturn", ret.id)
    if ret.is_archived:
        return None

    amount = _d(ret.amount)
    if amount <= 0:
        return None

    revenue = _code(CODE_MILK_REVENUE)

    dr_line = {
        "account_id": revenue.id, "debit": amount,
        "memo": f"مرتجع #{ret.id} — {ret.reason or ''}",
    }
    if ret.mode == "cash":
        if ret.treasury_account is None:
            raise LedgerError(
                "المرتجع النقدي محتاج حساب خزنة/بنك — اختر واحد من الإعدادات."
            )
        treasury = _treasury_leaf(ret.treasury_account)
        cr_line = {"account_id": treasury.id, "credit": amount,
                   "memo": f"مرتجع نقدي — {ret.customer.name}"}
    else:
        receivable = _code(CODE_TRADE_RECEIVABLE)
        cr_line = {
            "account_id": receivable.id, "credit": amount,
            "party_type": "customer", "party_id": ret.customer_id,
            "memo": f"مرتجع #{ret.id} — {ret.customer.name}",
        }

    return post_journal(
        description=f"مرتجع مبيعات — {ret.customer.name} ({ret.mode_label})",
        lines=[dr_line, cr_line],
        entry_date=ret.return_date,
        source_type="SalesReturn",
        source_id=ret.id,
        created_by=created_by,
    )


def on_purchase_return(ret, *, created_by=None):
    """A return TO a supplier. Reduces inventory; releases either the
    payable (credit mode) or receives cash back (cash mode).

    Credit: DR 2100 ذمم الموردين / CR inventory (by category)
    Cash:   DR treasury_leaf     / CR inventory (by category)

    If the return is tied to a purchase invoice, credit follows that
    invoice's inventory-code split (feed / medicine / raw); otherwise
    everything goes to generic raw stock (1200).
    """
    _delete_prior_je("PurchaseReturn", ret.id)
    if ret.is_archived:
        return None

    amount = _d(ret.amount)
    if amount <= 0:
        return None

    # Inventory credit split — mirror the linked invoice's shape if any.
    inv_split: dict[str, Decimal] = {}
    if ret.invoice and ret.invoice.lines:
        total_lines = sum(
            (_d(l.line_total) for l in ret.invoice.lines), Decimal("0")
        )
        if total_lines > 0:
            for line in ret.invoice.lines:
                code = INVENTORY_CODE_BY_CATEGORY.get(
                    (line.ingredient.category or "").split(":", 1)[0]
                    if line.ingredient else "",
                    INVENTORY_DEFAULT_CODE,
                )
                share = (_d(line.line_total) / total_lines) * amount
                inv_split[code] = inv_split.get(code, Decimal("0")) + share

    if not inv_split:
        inv_split[INVENTORY_DEFAULT_CODE] = amount

    # rounding residual: any diff goes to the default bucket
    posted = sum(inv_split.values(), Decimal("0"))
    diff = amount - posted
    if abs(diff) > Decimal("0.005"):
        inv_split[INVENTORY_DEFAULT_CODE] = (
            inv_split.get(INVENTORY_DEFAULT_CODE, Decimal("0")) + diff
        )

    credit_lines = [
        {"account_id": _code(code).id, "credit": amt,
         "memo": f"إخراج مرتجع #{ret.id}"}
        for code, amt in inv_split.items() if amt != 0
    ]

    if ret.mode == "cash":
        if ret.treasury_account is None:
            raise LedgerError(
                "المرتجع النقدي محتاج حساب خزنة/بنك."
            )
        treasury = _treasury_leaf(ret.treasury_account)
        dr_line = {"account_id": treasury.id, "debit": amount,
                   "memo": f"مرتجع نقدي من {ret.supplier.name}"}
    else:
        payable = _code(CODE_TRADE_PAYABLE)
        dr_line = {
            "account_id": payable.id, "debit": amount,
            "party_type": "supplier", "party_id": ret.supplier_id,
            "memo": f"مرتجع #{ret.id} — {ret.supplier.name}",
        }

    return post_journal(
        description=f"مرتجع مشتريات — {ret.supplier.name} ({ret.mode_label})",
        lines=[dr_line, *credit_lines],
        entry_date=ret.return_date,
        source_type="PurchaseReturn",
        source_id=ret.id,
        created_by=created_by,
    )
