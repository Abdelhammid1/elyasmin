"""PHASE 36 (SALES-1) regression suite.

Locks the general sales-invoice contract:

  1. Cash cow sale, gain (price > book): JE posts
     DR treasury + CR 4020 + CR 1400 + DR 4096 (book) +
     CR 4096 (proceeds) → net CR 4096 = gain.
  2. Cash cow sale, loss (price < book): JE posts
     DR treasury + CR 4020 + CR 1400 + DR 4096 book +
     CR 4096 proceeds → net DR 4096 = loss.
  3. Credit inventory sale: JE has AR party tag; StockMovement
     REASON_SALE with negative delta is written; avg_cost
     snapshot preserved.
  4. Mixed payment on free-text line: DR treasury + DR AR + CR
     4090.
  5. Duplicate cow guard: a second sale of the same cow is
     refused by the UniqueConstraint on sales_invoice_lines.cow_id.
  6. Delete invoice restores state: cow un-marked, current_value
     restored, stock topped back up, JE reversed.
  7. party_balance("customer", cid) reflects the credit portion.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.accounting import JournalEntry, JournalLine, LedgerAccount
from app.models.finance import TreasuryAccount, AccountMovement
from app.models.herd import AnimalSale, CattleGroup, Cow
from app.models.inventory import (
    Ingredient, IngredientCategory, StockMovement,
)
from app.models.sales import Customer, CustomerPayment
from app.models.sales_general import (
    SalesInvoice, SalesInvoiceLine, SalesInvoicePaymentAllocation,
)
from app.services.ledger import party_balance


_COW_TAG = "SALES1-TEST-COW"
_ING_TAG = "SALES1-TEST-ING"
_CAT_TAG = "SALES1-TEST-CAT"
_CUST_TAG = "SALES1-TEST-CUSTOMER"


# ------------ helpers ------------

def _seed_cow(app, book_value=Decimal("100")):
    with app.app_context():
        c = Cow.query.filter_by(ear_tag=_COW_TAG).first()
        if c is None:
            group = CattleGroup.query.first()
            c = Cow(
                ear_tag=_COW_TAG,
                gender=Cow.GENDER_FEMALE,
                group_id=group.id,
                status=Cow.STATUS_ACTIVE,
                current_value=book_value,
            )
            db.session.add(c)
        else:
            c.status = Cow.STATUS_ACTIVE
            c.current_value = book_value
        db.session.commit()
        return c.id


def _seed_sellable_ingredient(app, qty=Decimal("100"),
                              avg_cost=Decimal("3")):
    with app.app_context():
        cat = IngredientCategory.query.filter_by(name=_CAT_TAG).first()
        if cat is None:
            cat = IngredientCategory(name=_CAT_TAG, is_active=True,
                                     is_sellable=True)
            db.session.add(cat)
            db.session.flush()
        else:
            cat.is_sellable = True
        ing = Ingredient.query.filter_by(name=_ING_TAG).first()
        if ing is None:
            ing = Ingredient(
                name=_ING_TAG, category=cat.name,
                unit=Ingredient.UNIT_KG,
                current_qty=qty, avg_cost=avg_cost,
                last_price=avg_cost,
            )
            db.session.add(ing)
        else:
            ing.category = cat.name
            ing.current_qty = qty
            ing.avg_cost = avg_cost
        db.session.commit()
        return ing.id


def _seed_customer(app):
    with app.app_context():
        c = Customer.query.filter_by(name=_CUST_TAG).first()
        if c is None:
            c = Customer(name=_CUST_TAG,
                         contract_type=Customer.CONTRACT_DAILY,
                         pricing_type=Customer.PRICING_FIXED)
            db.session.add(c)
            db.session.commit()
        return c.id


def _first_treasury(app):
    with app.app_context():
        t = TreasuryAccount.query.filter_by(is_archived=False).first()
        return t.id


def _cleanup(app):
    with app.app_context():
        # Kill anything test-tagged we created.
        # Sales invoices first (they own the JE + allocations +
        # movements).
        invs = SalesInvoice.query.all()
        for inv in invs:
            # Only the ones on our test cow / ingredient / customer.
            relevant = False
            for line in inv.lines:
                if line.cow and line.cow.ear_tag == _COW_TAG:
                    relevant = True
                if line.ingredient and line.ingredient.name == _ING_TAG:
                    relevant = True
            if inv.customer and inv.customer.name == _CUST_TAG:
                relevant = True
            if not relevant:
                continue
            # Drop the JE
            for je in JournalEntry.query.filter_by(
                source_type="SalesInvoice", source_id=inv.id
            ).all():
                JournalLine.query.filter_by(entry_id=je.id).delete(
                    synchronize_session=False)
                db.session.delete(je)
            # Clean up cash-portion payment + allocations
            for a in list(inv.allocations):
                p = a.payment
                db.session.delete(a)
                if p:
                    AccountMovement.query.filter_by(
                        ref_type="sales_invoice_cash",
                        ref_id=inv.id,
                    ).delete(synchronize_session=False)
                    db.session.delete(p)
            # Wipe stock movements this invoice caused
            StockMovement.query.filter(
                StockMovement.reason == StockMovement.REASON_SALE,
                StockMovement.ref_id == inv.id,
            ).delete(synchronize_session=False)
            # Wipe mirror AnimalSale rows
            AnimalSale.query.filter(
                AnimalSale.notes.like(f"SALES-1 invoice #{inv.id}%")
            ).delete(synchronize_session=False)
            # Drop the invoice + lines (cascade)
            db.session.delete(inv)
        # Reset cow if it was sold
        cow = Cow.query.filter_by(ear_tag=_COW_TAG).first()
        if cow:
            cow.status = Cow.STATUS_ACTIVE
            cow.current_value = Decimal("100")
            AnimalSale.query.filter_by(cow_id=cow.id).delete()
        db.session.commit()


def _post_invoice(admin_client, payload_lines, *,
                  customer_id=None, walkin_name=None,
                  payment_type="cash", treasury_id=None,
                  cash_amount=None, credit_amount=None,
                  today=None):
    """Convenience shim: build form-data for POST /sales/invoices."""
    data = {
        "invoice_date": (today or date.today().isoformat()),
        "payment_type": payment_type,
        "cash_amount": str(cash_amount or "0"),
        "credit_amount": str(credit_amount or "0"),
    }
    if customer_id:
        data["customer_id"] = str(customer_id)
    if walkin_name:
        data["walkin_name"] = walkin_name
    if treasury_id:
        data["treasury_account_id"] = str(treasury_id)
    for i, line in enumerate(payload_lines):
        for k, v in line.items():
            data[f"line-{i}-{k}"] = str(v)
    return admin_client.post("/sales/invoices", data=data,
                             follow_redirects=False)


def _je_for(app, invoice_id):
    """Return (je, lines) with account codes materialised inside
    the app context so lazy loads don't blow up in the test body."""
    with app.app_context():
        je = JournalEntry.query.filter_by(
            source_type="SalesInvoice", source_id=invoice_id
        ).first()
        lines = list(je.lines) if je else []
        for l in lines:
            _ = l.account.code
        return je, lines


# ------------ tests ------------

def test_cash_cow_sale_gain_posts_je(admin_client, app):
    _cleanup(app)
    cow_id = _seed_cow(app, book_value=Decimal("100"))
    treasury_id = _first_treasury(app)
    try:
        r = _post_invoice(
            admin_client,
            [{"kind": "cow", "cow_id": cow_id,
              "qty": "1", "unit_price": "150"}],
            walkin_name="زبون كاش",
            payment_type="cash",
            treasury_id=treasury_id,
            cash_amount="150",
        )
        assert r.status_code in (302, 303), r.data[:400]
        with app.app_context():
            inv = SalesInvoice.query.filter_by(
                walkin_name="زبون كاش"
            ).order_by(SalesInvoice.id.desc()).first()
            assert inv is not None
            assert inv.grand_total == Decimal("150.00")
            c = db.session.get(Cow, cow_id)
            assert c.status == Cow.STATUS_SOLD
            assert c.current_value == Decimal("0")
            # Mirror AnimalSale created
            mirror = AnimalSale.query.filter_by(
                cow_id=cow_id
            ).order_by(AnimalSale.id.desc()).first()
            assert mirror is not None
            assert mirror.price == Decimal("150.00")

        je, lines = _je_for(app, inv.id)
        assert je is not None
        by_code = {}
        for l in lines:
            by_code.setdefault(l.account.code, []).append(l)

        # 3-leg shape: DR treasury 150 / CR 1400 100 / CR 4096 50
        # DR treasury 150
        tr_lines = [l for group in by_code.values() for l in group
                    if l.debit and l.credit == Decimal("0")]
        assert any(l.debit == Decimal("150") for l in tr_lines)
        # CR 1400 = 100 (book close-out)
        assert sum(l.credit for l in by_code.get("1400", [])) == Decimal("100")
        # CR 4096 = 50 (gain), no DR on 4096
        gl_lines = by_code.get("4096", [])
        dr = sum(l.debit for l in gl_lines)
        cr = sum(l.credit for l in gl_lines)
        assert dr == Decimal("0")
        assert cr == Decimal("50")
        # 4020 NOT touched — 4096 holds the whole revenue-vs-book picture
        assert sum(l.credit for l in by_code.get("4020", [])) == Decimal("0")
    finally:
        _cleanup(app)


def test_cash_cow_sale_loss_posts_je(admin_client, app):
    _cleanup(app)
    cow_id = _seed_cow(app, book_value=Decimal("200"))
    treasury_id = _first_treasury(app)
    try:
        r = _post_invoice(
            admin_client,
            [{"kind": "cow", "cow_id": cow_id,
              "qty": "1", "unit_price": "150"}],
            walkin_name="زبون كاش",
            payment_type="cash",
            treasury_id=treasury_id,
            cash_amount="150",
        )
        assert r.status_code in (302, 303)
        with app.app_context():
            inv = SalesInvoice.query.filter_by(
                walkin_name="زبون كاش"
            ).order_by(SalesInvoice.id.desc()).first()

        _, lines = _je_for(app, inv.id)
        by_code = {}
        for l in lines:
            by_code.setdefault(l.account.code, []).append(l)

        # Loss: DR treasury 150 / CR 1400 200 / DR 4096 50 (loss)
        assert sum(l.credit for l in by_code.get("1400", [])) == Decimal("200")
        gl = by_code.get("4096", [])
        dr = sum(l.debit for l in gl)
        cr = sum(l.credit for l in gl)
        assert dr == Decimal("50")
        assert cr == Decimal("0")
    finally:
        _cleanup(app)


def test_credit_inventory_sale_writes_stock_movement(admin_client, app):
    _cleanup(app)
    cust_id = _seed_customer(app)
    ing_id = _seed_sellable_ingredient(
        app, qty=Decimal("100"), avg_cost=Decimal("3"))
    try:
        r = _post_invoice(
            admin_client,
            [{"kind": "inventory", "ingredient_id": ing_id,
              "qty": "10", "unit_price": "5"}],
            customer_id=cust_id,
            payment_type="credit",
            credit_amount="50",
        )
        assert r.status_code in (302, 303), r.data[:400]
        with app.app_context():
            inv = SalesInvoice.query.filter_by(
                customer_id=cust_id
            ).order_by(SalesInvoice.id.desc()).first()
            assert inv is not None
            assert inv.grand_total == Decimal("50.00")
            # StockMovement written
            mv = StockMovement.query.filter_by(
                ingredient_id=ing_id, ref_id=inv.id,
                reason=StockMovement.REASON_SALE,
            ).first()
            assert mv is not None
            assert mv.delta == Decimal("-10.000")
            # Ingredient qty went down 10
            ing = db.session.get(Ingredient, ing_id)
            assert ing.current_qty == Decimal("90.000")
            # Party balance reflects the credit
            bal = party_balance("customer", cust_id)
        assert bal == Decimal("50.00")

        _, lines = _je_for(app, inv.id)
        by_code = {}
        for l in lines:
            by_code.setdefault(l.account.code, []).append(l)
        # DR 1100 = 50, party-tagged
        ar = by_code.get("1100", [])
        assert sum(l.debit for l in ar) == Decimal("50")
        assert all(
            (l.party_type == "customer" and l.party_id == cust_id)
            for l in ar
        )
        # 4030 revenue: CR 50 proceeds + DR 30 cost = net CR 20
        rev = by_code.get("4030", [])
        assert sum(l.credit for l in rev) == Decimal("50")
        assert sum(l.debit for l in rev) == Decimal("30")
        # Inventory leaf 1200 CR 30 (test category → default 1200)
        inv_leaf = by_code.get("1200", [])
        assert sum(l.credit for l in inv_leaf) == Decimal("30")
    finally:
        _cleanup(app)


def test_mixed_payment_free_line(admin_client, app):
    _cleanup(app)
    cust_id = _seed_customer(app)
    treasury_id = _first_treasury(app)
    try:
        r = _post_invoice(
            admin_client,
            [{"kind": "free",
              "description": "خدمة استشارة",
              "qty": "1", "unit_price": "500"}],
            customer_id=cust_id,
            payment_type="mixed",
            treasury_id=treasury_id,
            cash_amount="200",
            credit_amount="300",
        )
        assert r.status_code in (302, 303), r.data[:400]
        with app.app_context():
            inv = SalesInvoice.query.filter_by(
                customer_id=cust_id
            ).order_by(SalesInvoice.id.desc()).first()
            assert inv.grand_total == Decimal("500.00")
            assert inv.cash_amount == Decimal("200")
            assert inv.credit_amount == Decimal("300")
            # Allocation for the cash portion
            allocs = SalesInvoicePaymentAllocation.query.filter_by(
                invoice_id=inv.id
            ).all()
            assert len(allocs) == 1
            assert allocs[0].amount == Decimal("200.00")

        _, lines = _je_for(app, inv.id)
        by_code = {}
        for l in lines:
            by_code.setdefault(l.account.code, []).append(l)
        # DR AR = 300 (credit portion, party tag)
        ar = by_code.get("1100", [])
        assert sum(l.debit for l in ar) == Decimal("300")
        # 4090 revenue CR 500
        rev = by_code.get("4090", [])
        assert sum(l.credit for l in rev) == Decimal("500")
        # DR treasury total = 200 (cash portion)
        total_dr_cash = sum(
            l.debit for group in by_code.values() for l in group
            if l.debit > 0 and l.account.code not in ("1100", "4096", "4030")
        )
        assert total_dr_cash == Decimal("200")
    finally:
        _cleanup(app)


def test_mixed_payment_paid_amount_does_not_double_count_cash(admin_client, app):
    """SALES-2 (PHASE 42) regression: pre-fix `paid_amount`
    summed `cash_amount + SUM(allocations)`, but for a real-
    customer invoice the cash portion was ALREADY inside
    `allocations` (create_invoice writes it there for the
    "الجزء المدفوع" bar). Result: paid was doubled.

    Ticket example: cash 40,000 + credit 55,000 = 95,000. The
    bug reported paid=80,000 and outstanding=15,000. Correct
    values are paid=40,000 and outstanding=55,000.
    """
    _cleanup(app)
    cust_id = _seed_customer(app)
    treasury_id = _first_treasury(app)
    try:
        r = _post_invoice(
            admin_client,
            [{"kind": "free",
              "description": "بيع عام مركّب",
              "qty": "1", "unit_price": "95000"}],
            customer_id=cust_id,
            payment_type="mixed",
            treasury_id=treasury_id,
            cash_amount="40000",
            credit_amount="55000",
        )
        assert r.status_code in (302, 303), r.data[:400]
        with app.app_context():
            inv = SalesInvoice.query.filter_by(
                customer_id=cust_id
            ).order_by(SalesInvoice.id.desc()).first()
            assert inv.grand_total == Decimal("95000.00")
            assert inv.cash_amount == Decimal("40000.00")
            assert inv.credit_amount == Decimal("55000.00")
            # Exactly ONE allocation for the cash portion at issue.
            allocs = SalesInvoicePaymentAllocation.query.filter_by(
                invoice_id=inv.id
            ).all()
            assert len(allocs) == 1
            assert allocs[0].amount == Decimal("40000.00")
            # THE FIX: paid = allocation total (40000), not
            # 40000 + 40000 = 80000.
            assert inv.paid_amount == Decimal("40000.00")
            assert inv.outstanding_amount == Decimal("55000.00")
            assert inv.payment_status == "partial"
    finally:
        _cleanup(app)


def test_walkin_cash_paid_amount_uses_cash_amount(admin_client, app):
    """SALES-2 (PHASE 42): for a walk-in cash invoice there's no
    customer to link a CustomerPayment to, so create_invoice
    does NOT write an allocation. The paid formula must fall
    back to `cash_amount` in that case so a walk-in cash sale
    reads as fully paid, not as fully outstanding."""
    _cleanup(app)
    treasury_id = _first_treasury(app)
    try:
        r = _post_invoice(
            admin_client,
            [{"kind": "free",
              "description": "بيع walk-in",
              "qty": "1", "unit_price": "300"}],
            walkin_name="زبون كاش",
            payment_type="cash",
            treasury_id=treasury_id,
            cash_amount="300",
        )
        assert r.status_code in (302, 303), r.data[:400]
        with app.app_context():
            inv = SalesInvoice.query.filter_by(
                walkin_name="زبون كاش"
            ).order_by(SalesInvoice.id.desc()).first()
            assert inv.customer_id is None
            # No allocation was created (no Customer to link)
            allocs = SalesInvoicePaymentAllocation.query.filter_by(
                invoice_id=inv.id
            ).all()
            assert len(allocs) == 0
            # …but paid still reads as the full 300 via the
            # walk-in branch on paid_amount.
            assert inv.paid_amount == Decimal("300.00")
            assert inv.outstanding_amount == Decimal("0.00")
            assert inv.payment_status == "paid"
    finally:
        _cleanup(app)


def test_duplicate_cow_sale_refused(admin_client, app):
    _cleanup(app)
    cow_id = _seed_cow(app, book_value=Decimal("100"))
    treasury_id = _first_treasury(app)
    try:
        r1 = _post_invoice(
            admin_client,
            [{"kind": "cow", "cow_id": cow_id,
              "qty": "1", "unit_price": "150"}],
            walkin_name="زبون-1", payment_type="cash",
            treasury_id=treasury_id, cash_amount="150",
        )
        assert r1.status_code in (302, 303)
        # Second attempt — cow is now STATUS_SOLD, so the ValueError
        # guard in create_invoice fires with an Arabic flash message
        # BEFORE the UniqueConstraint would ever bite. Either way,
        # the invoice is refused.
        r2 = _post_invoice(
            admin_client,
            [{"kind": "cow", "cow_id": cow_id,
              "qty": "1", "unit_price": "150"}],
            walkin_name="زبون-2", payment_type="cash",
            treasury_id=treasury_id, cash_amount="150",
        )
        assert r2.status_code == 400
        # And no second SalesInvoice row got persisted on this cow.
        with app.app_context():
            n = (
                SalesInvoiceLine.query.filter_by(cow_id=cow_id).count()
            )
        assert n == 1
    finally:
        _cleanup(app)


def test_delete_invoice_restores_state(admin_client, app):
    _cleanup(app)
    cow_id = _seed_cow(app, book_value=Decimal("100"))
    ing_id = _seed_sellable_ingredient(
        app, qty=Decimal("100"), avg_cost=Decimal("3"))
    treasury_id = _first_treasury(app)
    try:
        r = _post_invoice(
            admin_client,
            [{"kind": "cow", "cow_id": cow_id,
              "qty": "1", "unit_price": "150"},
             {"kind": "inventory", "ingredient_id": ing_id,
              "qty": "5", "unit_price": "4"}],
            walkin_name="زبون",
            payment_type="cash", treasury_id=treasury_id,
            cash_amount="170",
        )
        assert r.status_code in (302, 303), r.data[:400]
        with app.app_context():
            inv = SalesInvoice.query.filter_by(
                walkin_name="زبون"
            ).order_by(SalesInvoice.id.desc()).first()
            inv_id = inv.id

        r = admin_client.post(
            f"/sales/invoices/{inv_id}/delete", follow_redirects=False,
        )
        assert r.status_code in (302, 303)

        with app.app_context():
            inv = db.session.get(SalesInvoice, inv_id)
            assert inv.is_archived is True
            # Cow un-marked
            cow = db.session.get(Cow, cow_id)
            assert cow.status == Cow.STATUS_ACTIVE
            assert cow.current_value == Decimal("100.00")
            # Stock topped back up
            ing = db.session.get(Ingredient, ing_id)
            assert ing.current_qty == Decimal("100.000")
            # JE gone
            je = JournalEntry.query.filter_by(
                source_type="SalesInvoice", source_id=inv_id,
            ).first()
            assert je is None
    finally:
        _cleanup(app)
