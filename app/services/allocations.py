"""PHASE 3/4 — payment allocation service, both directions.

The one place that decides "this payment paid these invoices". Every
allocation write goes through here so the two invariants are enforced in
one place per side:

  Customer side (phase 3):
    SUM(allocations for a payment)  <= payment.amount
    SUM(allocations for an invoice) <= invoice.grand_total

  Supplier side (phase 4): identical shape against
    SupplierPayment / PurchaseInvoice.

Nothing here writes to the ledger — allocations are a display/reporting
layer on top of the phase-1 receivable/payable JE.
"""
from decimal import Decimal
from typing import Iterable, Optional

from app.extensions import db
from app.models.sales import (
    CustomerPayment, MilkInvoice, PaymentAllocation,
)
from app.models.suppliers import (
    PurchaseInvoice, SupplierPayment, SupplierPaymentAllocation,
)


class AllocationError(ValueError):
    """An allocation was rejected — invariant violated. Message is in Arabic
    and safe to flash."""


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def open_customer_invoices_for(customer_id: int) -> list[MilkInvoice]:
    """Every ISSUED milk invoice for this customer with outstanding > 0,
    oldest first — the natural order for a "pay oldest first" allocator.
    Draft invoices are not payable yet, so excluded."""
    invs = (
        MilkInvoice.query
        .filter_by(customer_id=customer_id, is_archived=False,
                   status=MilkInvoice.STATUS_ISSUED)
        .order_by(MilkInvoice.issue_date, MilkInvoice.id)
        .all()
    )
    return [i for i in invs if i.outstanding_amount > 0]


# Legacy alias for phase-3 code that hasn't caught up. Both names point at
# the same function so a stragglring import doesn't crash.
def open_invoices_for(customer_id: int) -> list[MilkInvoice]:
    return open_customer_invoices_for(customer_id)


def allocate_customer_payment(
    payment: CustomerPayment,
    allocations: Iterable[tuple[int, Decimal]],
    *,
    created_by: Optional[int] = None,
    replace: bool = False,
) -> list[PaymentAllocation]:
    """Attach `allocations` = [(invoice_id, amount), ...] to `payment`.

    Refuses if:
      - any invoice belongs to a different customer
      - any amount is <= 0
      - SUM of allocations > payment.amount
      - any single allocation would push an invoice past its grand_total

    When `replace=True`, existing allocations for this payment are wiped
    and re-created — the natural shape for "edit the allocation" later.

    Returns the created PaymentAllocation rows. Does NOT commit.
    """
    if replace:
        for a in list(payment.allocations):
            db.session.delete(a)
        db.session.flush()

    allocations = [(int(iid), _d(amt)) for iid, amt in allocations if amt and _d(amt) > 0]
    if not allocations:
        return []

    # SUM sanity — payment side
    total = sum((a for _, a in allocations), Decimal("0"))
    if total > _d(payment.amount) + Decimal("0.005"):
        raise AllocationError(
            f"مجموع التوزيع ({total}) أكبر من قيمة الدفعة ({_d(payment.amount)})."
        )

    # Load invoices in one query — same guard as the ledger service does
    invoice_ids = {iid for iid, _ in allocations}
    invoices = {
        i.id: i for i in MilkInvoice.query.filter(MilkInvoice.id.in_(invoice_ids)).all()
    }

    rows = []
    for iid, amt in allocations:
        inv = invoices.get(iid)
        if inv is None:
            raise AllocationError(f"الفاتورة رقم {iid} مش موجودة.")
        if inv.customer_id != payment.customer_id:
            raise AllocationError(
                f"الفاتورة {inv.id} لعميل تاني — مش ممكن توزيع دفعة {payment.customer.name} عليها."
            )
        remaining_before = inv.outstanding_amount  # already excludes deletes done above
        if amt > remaining_before + Decimal("0.005"):
            raise AllocationError(
                f"الفاتورة {inv.id}: المتبقّي {remaining_before} أقل من التوزيع {amt}."
            )
        row = PaymentAllocation(
            payment_id=payment.id, invoice_id=inv.id, amount=amt,
            created_by_id=created_by,
        )
        db.session.add(row)
        rows.append(row)

    return rows


# Legacy alias — the customer callers still import `allocate_payment`.
def allocate_payment(payment, allocations, **kw):
    return allocate_customer_payment(payment, allocations, **kw)


# ==================== PHASE 4 — supplier side ====================

def open_supplier_invoices_for(supplier_id: int) -> list[PurchaseInvoice]:
    """Every CREDIT purchase invoice for this supplier with outstanding > 0,
    oldest first. Cash invoices are excluded — they're settled at creation
    (paid_amount == total), so an allocation against one would double-count."""
    invs = (
        PurchaseInvoice.query
        .filter_by(supplier_id=supplier_id, is_archived=False,
                   payment_type=PurchaseInvoice.PAY_CREDIT)
        .order_by(PurchaseInvoice.invoice_date, PurchaseInvoice.id)
        .all()
    )
    return [i for i in invs if i.outstanding_amount > 0]


def allocate_supplier_payment(
    payment: SupplierPayment,
    allocations: Iterable[tuple[int, Decimal]],
    *,
    created_by: Optional[int] = None,
    replace: bool = False,
) -> list[SupplierPaymentAllocation]:
    """Attach `allocations` = [(invoice_id, amount), ...] to a supplier
    payment. Mirror of allocate_customer_payment with the invariants on
    the vendor side.

    Refuses if:
      - any invoice belongs to a different supplier
      - any amount is <= 0
      - SUM of allocations > payment.amount
      - any single allocation would push an invoice past its outstanding
    """
    if replace:
        for a in list(payment.allocations):
            db.session.delete(a)
        db.session.flush()

    allocations = [(int(iid), _d(amt)) for iid, amt in allocations if amt and _d(amt) > 0]
    if not allocations:
        return []

    total = sum((a for _, a in allocations), Decimal("0"))
    if total > _d(payment.amount) + Decimal("0.005"):
        raise AllocationError(
            f"مجموع التوزيع ({total}) أكبر من قيمة الدفعة ({_d(payment.amount)})."
        )

    invoice_ids = {iid for iid, _ in allocations}
    invoices = {
        i.id: i for i in PurchaseInvoice.query.filter(PurchaseInvoice.id.in_(invoice_ids)).all()
    }

    rows = []
    for iid, amt in allocations:
        inv = invoices.get(iid)
        if inv is None:
            raise AllocationError(f"الفاتورة رقم {iid} مش موجودة.")
        if inv.supplier_id != payment.supplier_id:
            raise AllocationError(
                f"الفاتورة {inv.id} لمورد تاني — مش ممكن توزيع دفعة {payment.supplier.name} عليها."
            )
        remaining_before = inv.outstanding_amount
        if amt > remaining_before + Decimal("0.005"):
            raise AllocationError(
                f"الفاتورة {inv.id}: المتبقّي {remaining_before} أقل من التوزيع {amt}."
            )
        row = SupplierPaymentAllocation(
            payment_id=payment.id, invoice_id=inv.id, amount=amt,
            created_by_id=created_by,
        )
        db.session.add(row)
        rows.append(row)

    return rows
