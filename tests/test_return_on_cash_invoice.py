"""PHASE 25 regression test.

Reproduces the exact bug the user reported: a cash purchase invoice
with a full cash-refund return was showing outstanding = -total and
chip = 'مسدّدة'. The correct behavior is outstanding = 0 and status
= 'returned'. This file ensures the bug can't come back silently.
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
