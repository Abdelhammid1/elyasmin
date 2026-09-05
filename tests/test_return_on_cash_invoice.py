"""PHASE 25 + FIN-8 (PHASE 32) regression tests.

PHASE 25: cash purchase invoice with a full cash-refund return was
showing outstanding = -total and chip = 'مسدّدة'. Correct behavior
is outstanding = 0 and status = 'returned'.

FIN-8: same bug shape on MilkInvoice — the milk list showed
`المتبقي: -600` on a fully-collected + fully-returned invoice, and
the chip read 'محصّلة' instead of 'مرتجعة'. This file locks both
sides down.
"""
from datetime import date
from decimal import Decimal

import pytest


def test_cash_purchase_full_return_status_is_returned(app):
    """The user-reported case: cash purchase 14.50, then a cash-refund
    return of 14.50. Was: outstanding -14.50 + مسدّدة. Now: 0 + مرتجعة."""
    with app.app_context():
        from app.extensions import db
        from app.models.suppliers import (
            PurchaseInvoice, PurchaseReturn, Supplier,
        )

        sup = Supplier.query.first()
        assert sup is not None, "dev DB has no supplier — can't test"

        inv = PurchaseInvoice(
            supplier_id=sup.id,
            invoice_date=date.today(),
            payment_type="cash",
            subtotal=Decimal("14.50"),
            total=Decimal("14.50"),
            paid_amount=Decimal("14.50"),   # cash — auto-paid at creation
        )
        db.session.add(inv)
        db.session.commit()

        ret = PurchaseReturn(
            supplier_id=sup.id,
            invoice_id=inv.id,
            return_date=date.today(),
            amount=Decimal("14.50"),
            mode="cash",
        )
        db.session.add(ret)
        db.session.commit()

        try:
            # No more negative outstanding
            assert inv.outstanding_amount == Decimal("0.00"), (
                f"expected outstanding 0.00, got {inv.outstanding_amount}"
            )
            # Status flips to 'returned', not misleading 'paid'
            assert inv.payment_status == "returned", (
                f"expected status 'returned', got '{inv.payment_status}'"
            )
            assert inv.is_fully_returned is True
        finally:
            # Clean up so the smoke test's row count stays stable
            db.session.delete(ret)
            db.session.delete(inv)
            db.session.commit()


def test_credit_purchase_partial_payment_still_correct(app):
    """Guard: the fix didn't regress the normal credit-invoice case
    that was working before (allocations + returns both reduce)."""
    with app.app_context():
        from app.extensions import db
        from app.models.suppliers import (
            PurchaseInvoice, PurchaseReturn, Supplier,
        )
        sup = Supplier.query.first()

        inv = PurchaseInvoice(
            supplier_id=sup.id,
            invoice_date=date.today(),
            payment_type="credit",
            subtotal=Decimal("100"),
            total=Decimal("100"),
            paid_amount=Decimal("0"),
        )
        db.session.add(inv)
        db.session.commit()

        # 30 comes back as a credit note
        ret = PurchaseReturn(
            supplier_id=sup.id, invoice_id=inv.id,
            return_date=date.today(),
            amount=Decimal("30"), mode="credit",
        )
        db.session.add(ret)
        db.session.commit()

        try:
            # 100 owed − 30 credit note = 70 remaining (no allocations yet)
            assert inv.outstanding_amount == Decimal("70.00"), (
                f"expected outstanding 70.00, got {inv.outstanding_amount}"
            )
            assert inv.payment_status == "partial", (
                f"expected 'partial', got '{inv.payment_status}'"
            )
        finally:
            db.session.delete(ret)
            db.session.delete(inv)
            db.session.commit()


# ==================== FIN-8 (PHASE 32) MilkInvoice tests ====================


def test_milk_invoice_full_collect_full_return_status_is_returned(app):
    """FIN-8: production bug — MilkInvoice for شركة بيتي, 600 EGP.
    Collected 600 in full via a CustomerPayment + PaymentAllocation.
    Then a full 600 SalesReturn. Pre-fix: outstanding_amount was
    -600 in the milk list and status was 'paid'. Correct: 0 + returned."""
    from datetime import date as _date
    with app.app_context():
        from app.extensions import db
        from app.models.sales import (
            Customer, CustomerPayment, MilkInvoice,
            PaymentAllocation, SalesReturn,
        )

        cus = Customer.query.first()
        assert cus is not None, "dev DB has no customer — can't test"

        inv = MilkInvoice(
            customer_id=cus.id,
            period_from=_date.today(), period_to=_date.today(),
            issue_date=_date.today(),
            status=MilkInvoice.STATUS_ISSUED,
            grand_total=Decimal("600.00"),
        )
        db.session.add(inv)
        db.session.commit()

        # Full collection: one payment, one allocation.
        pay = CustomerPayment(
            customer_id=cus.id,
            amount=Decimal("600.00"),
            payment_date=_date.today(),
            method=CustomerPayment.METHOD_CASH,
        )
        db.session.add(pay)
        db.session.flush()
        alloc = PaymentAllocation(
            payment_id=pay.id, invoice_id=inv.id,
            amount=Decimal("600.00"),
        )
        db.session.add(alloc)

        # Full return: SalesReturn for the entire invoice amount.
        ret = SalesReturn(
            customer_id=cus.id,
            invoice_id=inv.id,
            return_date=_date.today(),
            amount=Decimal("600.00"),
            mode="cash",
        )
        db.session.add(ret)
        db.session.commit()

        try:
            # No more negative outstanding
            assert inv.outstanding_amount == Decimal("0.00"), (
                f"expected outstanding 0.00, got {inv.outstanding_amount}"
            )
            # Status flips to 'returned', not misleading 'paid'
            assert inv.payment_status == "returned", (
                f"expected 'returned', got '{inv.payment_status}'"
            )
            assert inv.is_fully_returned is True
        finally:
            db.session.delete(ret)
            db.session.delete(alloc)
            db.session.delete(pay)
            db.session.delete(inv)
            db.session.commit()


def test_milk_invoice_partial_return_still_correct(app):
    """FIN-8 guard: the fix didn't regress the normal partial-return
    case (allocations + a smaller return both reduce, no clamp)."""
    from datetime import date as _date
    with app.app_context():
        from app.extensions import db
        from app.models.sales import (
            Customer, MilkInvoice, SalesReturn,
        )

        cus = Customer.query.first()

        inv = MilkInvoice(
            customer_id=cus.id,
            period_from=_date.today(), period_to=_date.today(),
            issue_date=_date.today(),
            status=MilkInvoice.STATUS_ISSUED,
            grand_total=Decimal("500.00"),
        )
        db.session.add(inv)
        db.session.commit()

        # 100 comes back as a credit note — no allocations yet
        ret = SalesReturn(
            customer_id=cus.id, invoice_id=inv.id,
            return_date=_date.today(),
            amount=Decimal("100.00"), mode="credit",
        )
        db.session.add(ret)
        db.session.commit()

        try:
            # 500 grand − 100 return = 400 remaining
            assert inv.outstanding_amount == Decimal("400.00"), (
                f"expected 400.00, got {inv.outstanding_amount}"
            )
            assert inv.payment_status == "partial", (
                f"expected 'partial', got '{inv.payment_status}'"
            )
            assert inv.is_fully_returned is False
        finally:
            db.session.delete(ret)
            db.session.delete(inv)
            db.session.commit()
