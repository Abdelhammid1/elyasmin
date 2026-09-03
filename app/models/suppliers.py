from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db


class Supplier(db.Model):
    __tablename__ = "suppliers"

    CAT_FEED = "feed"
    CAT_MEDICINE = "medicine"
    CAT_OTHER = "other"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30), nullable=True)
    supplied_categories = db.Column(db.String(120), nullable=False, default="")  # comma-separated
    # TICKET-1 (Dina): debt already owed to this supplier before the system started
    opening_balance = db.Column(
        db.Numeric(14, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # TICKET-1: link to a customer record when it's the same person
    linked_customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True, index=True)

    invoices = db.relationship("PurchaseInvoice", back_populates="supplier", lazy="dynamic")
    payments = db.relationship("SupplierPayment", back_populates="supplier", lazy="dynamic")
    linked_customer = db.relationship("Customer", foreign_keys=[linked_customer_id])

    @property
    def categories_list(self) -> list[str]:
        return [c.strip() for c in (self.supplied_categories or "").split(",") if c.strip()]

    @property
    def categories_labels(self) -> list[str]:
        mapping = {self.CAT_FEED: "علف", self.CAT_MEDICINE: "دواء", self.CAT_OTHER: "أخرى"}
        return [mapping.get(c, c) for c in self.categories_list]

    @property
    def total_invoiced(self) -> Decimal:
        val = (
            db.session.query(func.coalesce(func.sum(PurchaseInvoice.total), 0))
            .filter(PurchaseInvoice.supplier_id == self.id, PurchaseInvoice.is_archived.is_(False))
            .scalar()
        )
        return Decimal(str(val or 0))

    @property
    def total_paid(self) -> Decimal:
        # Sum of dedicated supplier payments PLUS cash invoices' paid_amount
        payments_sum = (
            db.session.query(func.coalesce(func.sum(SupplierPayment.amount), 0))
            .filter(SupplierPayment.supplier_id == self.id, SupplierPayment.is_archived.is_(False))
            .scalar()
        )
        cash_invoices_sum = (
            db.session.query(func.coalesce(func.sum(PurchaseInvoice.paid_amount), 0))
            .filter(
                PurchaseInvoice.supplier_id == self.id,
                PurchaseInvoice.is_archived.is_(False),
            )
            .scalar()
        )
        return Decimal(str(payments_sum or 0)) + Decimal(str(cash_invoices_sum or 0))

    @property
    def balance_due(self) -> Decimal:
        """Amount still owed to the supplier, including any opening balance."""
        return Decimal(str(self.opening_balance or 0)) + self.total_invoiced - self.total_paid

    @property
    def net_balance(self) -> Decimal:
        """TICKET-1: combined balance when the supplier is also a customer.

        Positive = they owe us (as customer), negative = we owe them (as supplier).
        """
        supplier_balance = -self.balance_due  # we owe them → negative from our POV
        customer_balance = self.linked_customer.balance if self.linked_customer else Decimal("0")
        return customer_balance + supplier_balance


class PurchaseInvoice(db.Model):
    __tablename__ = "purchase_invoices"

    PAY_CASH = "cash"
    PAY_CREDIT = "credit"

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False, index=True)
    invoice_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    payment_type = db.Column(db.String(10), nullable=False, default=PAY_CASH)
    original_invoice_no = db.Column(db.String(80), nullable=True)

    subtotal = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))  # sum of lines before tax/discount
    total = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))  # subtotal - discounts + taxes
    paid_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))

    # PHASE 10 (YAS-UX-3): future scheduling flags. The "كرر" duplicate
    # button works today via a one-off duplicate flow (no schedule);
    # a scheduler that reads these columns lands later.
    is_recurring = db.Column(db.Boolean, nullable=False, default=False,
                              server_default="0")
    recurrence_interval = db.Column(db.String(20), nullable=True)

    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    supplier = db.relationship("Supplier", back_populates="invoices")
    lines = db.relationship("PurchaseLine", back_populates="invoice", cascade="all, delete-orphan")
    charges = db.relationship(
        "PurchaseInvoiceCharge",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="PurchaseInvoiceCharge.display_order, PurchaseInvoiceCharge.id",
    )
    allocations = db.relationship(
        "SupplierPaymentAllocation", back_populates="invoice",
        cascade="all, delete-orphan",
    )

    @property
    def payment_label(self) -> str:
        return "نقدي" if self.payment_type == self.PAY_CASH else "آجل"

    @property
    def outstanding(self) -> Decimal:
        # Preserved for backward compat — callers reading the old shape.
        # For the new payment-status derivation use outstanding_amount below,
        # which also nets allocations from credit-invoice partial payments.
        return self.total - self.paid_amount

    # PHASE 4: allocation-aware payment status. `paid_amount` still holds
    # the cash-invoice's own settlement (set at creation for PAY_CASH,
    # zero for PAY_CREDIT); allocations carry the credit-invoice partial
    # settlements. The two sources coexist because a cash invoice never
    # gets an allocation and a credit invoice's `paid_amount` stays 0.
    @property
    def allocated_amount(self) -> Decimal:
        return sum(
            (Decimal(str(a.amount or 0)) for a in self.allocations),
            Decimal("0"),
        ).quantize(Decimal("0.01"))

    @property
    def returned_amount(self) -> Decimal:
        """PHASE 5: total returns against this invoice."""
        return sum(
            (Decimal(str(r.amount or 0))
             for r in self.returns if not r.is_archived),
            Decimal("0"),
        ).quantize(Decimal("0.01"))

    @property
    def outstanding_amount(self) -> Decimal:
        settled = (Decimal(str(self.paid_amount or 0))
                   + self.allocated_amount + self.returned_amount)
        return (Decimal(str(self.total or 0)) - settled).quantize(Decimal("0.01"))

    @property
    def payment_status(self) -> str:
        """'paid' | 'partial' | 'unpaid'."""
        total = Decimal(str(self.total or 0))
        if total <= 0:
            return "paid"
        settled = (Decimal(str(self.paid_amount or 0))
                   + self.allocated_amount + self.returned_amount)
        if settled >= total - Decimal("0.005"):
            return "paid"
        if settled > 0:
            return "partial"
        return "unpaid"

    @property
    def tax_rows(self) -> list:
        return [c for c in self.charges if c.kind == PurchaseInvoiceCharge.KIND_TAX]

    @property
    def discount_rows(self) -> list:
        return [c for c in self.charges if c.kind == PurchaseInvoiceCharge.KIND_DISCOUNT]

    @property
    def total_tax(self) -> Decimal:
        return sum((c.amount_egp for c in self.tax_rows), Decimal("0"))

    @property
    def total_discount(self) -> Decimal:
        return sum((c.amount_egp for c in self.discount_rows), Decimal("0"))


class PurchaseInvoiceCharge(db.Model):
    """TICKET-3: multiple taxes / discounts per invoice, each optionally as
    a percentage of subtotal or a fixed EGP amount."""

    __tablename__ = "purchase_invoice_charges"

    KIND_TAX = "tax"
    KIND_DISCOUNT = "discount"

    TAX_LABELS = {
        "vat": "ضريبة القيمة المضافة",
        "commercial_industrial": "ضريبة تجارية وصناعية",
    }
    DISCOUNT_LABELS = {
        "cash": "خصم نقدي",
        "quantity": "خصم كمية",
    }

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("purchase_invoices.id", name="fk_charge_invoice"),
        nullable=False, index=True,
    )
    kind = db.Column(db.String(10), nullable=False)  # tax | discount
    type_name = db.Column(db.String(60), nullable=False)  # vat | commercial_industrial | cash | quantity | custom:<name>
    is_percentage = db.Column(db.Boolean, nullable=False, default=False)
    rate_pct = db.Column(db.Numeric(6, 3), nullable=True)
    amount_egp = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    display_order = db.Column(db.Integer, nullable=False, default=0)

    invoice = db.relationship("PurchaseInvoice", back_populates="charges")

    @property
    def type_label(self) -> str:
        if self.type_name and self.type_name.startswith("custom:"):
            return self.type_name[len("custom:"):]
        mapping = self.TAX_LABELS if self.kind == self.KIND_TAX else self.DISCOUNT_LABELS
        return mapping.get(self.type_name, self.type_name)


class PurchaseLine(db.Model):
    __tablename__ = "purchase_lines"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("purchase_invoices.id"), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False)
    # qty is ALWAYS in the ingredient's base unit (after conversion)
    qty = db.Column(db.Numeric(14, 3), nullable=False)
    # unit_price is per BASE unit (already converted from the input unit)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    line_total = db.Column(db.Numeric(14, 2), nullable=False)

    # TICKET-2 audit trail — what the user actually typed
    input_qty = db.Column(db.Numeric(14, 3), nullable=True)
    input_unit_code = db.Column(db.String(40), nullable=True)
    input_unit_price = db.Column(db.Numeric(12, 2), nullable=True)  # per input-unit

    invoice = db.relationship("PurchaseInvoice", back_populates="lines")
    ingredient = db.relationship("Ingredient")


class SupplierPayment(db.Model):
    __tablename__ = "supplier_payments"

    METHOD_CASH = "cash"
    METHOD_TRANSFER = "transfer"

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    method = db.Column(db.String(20), nullable=False, default=METHOD_CASH)
    # TREASURY: which account the money left (nullable for pre-accounts rows)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    supplier = db.relationship("Supplier", back_populates="payments")
    account = db.relationship("TreasuryAccount")
    allocations = db.relationship(
        "SupplierPaymentAllocation", back_populates="payment",
        cascade="all, delete-orphan",
    )

    @property
    def method_label(self) -> str:
        return "كاش" if self.method == self.METHOD_CASH else "تحويل بنكي"

    @property
    def allocated_amount(self) -> Decimal:
        """PHASE 4: how much of this payment has been tied to specific invoices."""
        return sum(
            (Decimal(str(a.amount or 0)) for a in self.allocations),
            Decimal("0"),
        ).quantize(Decimal("0.01"))

    @property
    def unallocated_amount(self) -> Decimal:
        """The "on account" balance — payment.amount − allocated_amount."""
        return (Decimal(str(self.amount or 0)) - self.allocated_amount).quantize(Decimal("0.01"))


class SupplierPaymentAllocation(db.Model):
    """PHASE 4: ties a slice of a SupplierPayment to a specific
    PurchaseInvoice. Mirror of PaymentAllocation for the vendor side.

    The ledger already recorded the payment as a whole-supplier payable
    reduction (phase 1 autopost). This is a DISPLAY/REPORTING layer on
    top: it says which invoices a payment was intended to settle.
    Editing an allocation moves nothing on the ledger.

    Invariants (enforced by the allocation service, not the DB):
      SUM(amount for a payment)  <= payment.amount
      SUM(amount for an invoice) <= invoice.outstanding_amount at write time
    """

    __tablename__ = "supplier_payment_allocations"

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(
        db.Integer, db.ForeignKey("supplier_payments.id"),
        nullable=False, index=True,
    )
    invoice_id = db.Column(
        db.Integer, db.ForeignKey("purchase_invoices.id"),
        nullable=False, index=True,
    )
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    payment = db.relationship("SupplierPayment", back_populates="allocations")
    invoice = db.relationship("PurchaseInvoice", back_populates="allocations")

    __table_args__ = (
        db.UniqueConstraint("payment_id", "invoice_id", name="uq_supplier_payment_invoice"),
    )


class PurchaseReturn(db.Model):
    """PHASE 5 — a purchase return (كنوت مدين / مرتجع نقدي).

    Mirror of SalesReturn on the vendor side. Two modes:
      credit — reduces the supplier's payable, no cash moves
      cash   — money comes back into a treasury account
    """

    __tablename__ = "purchase_returns"

    MODE_CREDIT = "credit"
    MODE_CASH = "cash"
    MODE_LABELS = {MODE_CREDIT: "كنوت مدين (خصم من الرصيد)", MODE_CASH: "مرتجع نقدي"}

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"),
                            nullable=False, index=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("purchase_invoices.id"),
                           nullable=True, index=True)
    return_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    mode = db.Column(db.String(10), nullable=False, default=MODE_CREDIT)
    treasury_account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"),
                                    nullable=True, index=True)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    supplier = db.relationship("Supplier",
        backref=db.backref("returns", lazy="dynamic"))
    invoice = db.relationship("PurchaseInvoice",
        backref=db.backref("returns", lazy="dynamic"))
    treasury_account = db.relationship("TreasuryAccount")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def mode_label(self) -> str:
        return self.MODE_LABELS.get(self.mode, self.mode)
