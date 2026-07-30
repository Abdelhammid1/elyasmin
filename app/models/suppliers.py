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
        """Amount still owed to the supplier."""
        return self.total_invoiced - self.total_paid

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

    # TICKET-2: tax + discount
    # Types stored as: predefined ('vat', 'commercial', 'industrial', 'cash', 'quantity') OR 'custom:<name>'
    tax_type = db.Column(db.String(60), nullable=True)
    tax_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    discount_type = db.Column(db.String(60), nullable=True)
    discount_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))

    total = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))  # subtotal - discount + tax
    paid_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))

    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    supplier = db.relationship("Supplier", back_populates="invoices")
    lines = db.relationship("PurchaseLine", back_populates="invoice", cascade="all, delete-orphan")

    TAX_LABELS = {
        "vat": "ضريبة القيمة المضافة",
        "commercial": "ضريبة تجارية",
        "industrial": "ضريبة صناعية",
    }
    DISCOUNT_LABELS = {
        "cash": "خصم نقدي",
        "quantity": "خصم كمية",
    }

    @property
    def payment_label(self) -> str:
        return "نقدي" if self.payment_type == self.PAY_CASH else "آجل"

    @property
    def outstanding(self) -> Decimal:
        return self.total - self.paid_amount

    def _label(self, value: str | None, mapping: dict) -> str:
        if not value:
            return "—"
        if value.startswith("custom:"):
            return value[len("custom:"):]
        return mapping.get(value, value)

    @property
    def tax_label(self) -> str:
        return self._label(self.tax_type, self.TAX_LABELS)

    @property
    def discount_label(self) -> str:
        return self._label(self.discount_type, self.DISCOUNT_LABELS)


class PurchaseLine(db.Model):
    __tablename__ = "purchase_lines"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("purchase_invoices.id"), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False)
    qty = db.Column(db.Numeric(14, 3), nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    line_total = db.Column(db.Numeric(14, 2), nullable=False)

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
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    supplier = db.relationship("Supplier", back_populates="payments")

    @property
    def method_label(self) -> str:
        return "كاش" if self.method == self.METHOD_CASH else "تحويل بنكي"
