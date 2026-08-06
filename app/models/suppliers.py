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

    @property
    def payment_label(self) -> str:
        return "نقدي" if self.payment_type == self.PAY_CASH else "آجل"

    @property
    def outstanding(self) -> Decimal:
        return self.total - self.paid_amount

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
    account = db.relationship("Account")

    @property
    def method_label(self) -> str:
        return "كاش" if self.method == self.METHOD_CASH else "تحويل بنكي"
