from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db


class Customer(db.Model):
    __tablename__ = "customers"

    CONTRACT_DAILY = "daily"
    CONTRACT_WEEKLY = "weekly"

    PRICING_FIXED = "fixed"
    PRICING_QUALITY = "quality"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30), nullable=True)
    contract_type = db.Column(db.String(20), nullable=False, default=CONTRACT_DAILY)
    pricing_type = db.Column(db.String(20), nullable=False, default=PRICING_FIXED)
    fixed_price = db.Column(db.Numeric(10, 3), nullable=True)  # جنيه/كيلو
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # TICKET-1: link to a supplier record when it's the same person
    linked_supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True, index=True)

    deliveries = db.relationship("MilkDelivery", back_populates="customer", lazy="dynamic")
    payments = db.relationship("CustomerPayment", back_populates="customer", lazy="dynamic")
    linked_supplier = db.relationship("Supplier", foreign_keys=[linked_supplier_id])

    @property
    def contract_label(self) -> str:
        return "يومي" if self.contract_type == self.CONTRACT_DAILY else "أسبوعي"

    @property
    def pricing_label(self) -> str:
        return "سعر ثابت" if self.pricing_type == self.PRICING_FIXED else "على أساس التحليل"

    @property
    def total_delivered_value(self) -> Decimal:
        val = (
            db.session.query(func.coalesce(func.sum(MilkDelivery.total_value), 0))
            .filter(MilkDelivery.customer_id == self.id, MilkDelivery.is_archived.is_(False))
            .scalar()
        )
        return Decimal(str(val or 0))

    @property
    def total_paid(self) -> Decimal:
        val = (
            db.session.query(func.coalesce(func.sum(CustomerPayment.amount), 0))
            .filter(CustomerPayment.customer_id == self.id, CustomerPayment.is_archived.is_(False))
            .scalar()
        )
        return Decimal(str(val or 0))

    @property
    def balance(self) -> Decimal:
        """Amount the customer still owes us (positive = customer owes us)."""
        return self.total_delivered_value - self.total_paid

    @property
    def net_balance(self) -> Decimal:
        """TICKET-1: combined balance when the customer is also a supplier.

        Positive = they owe us net, negative = we owe them net.
        """
        supplier_balance = -self.linked_supplier.balance_due if self.linked_supplier else Decimal("0")
        return self.balance + supplier_balance


class MilkDelivery(db.Model):
    __tablename__ = "milk_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    delivery_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    qty_kg = db.Column(db.Numeric(14, 3), nullable=False)

    protein_pct = db.Column(db.Numeric(5, 2), nullable=True)  # only for quality-based
    bacteria_count = db.Column(db.Integer, nullable=True)  # only for quality-based
    # TICKET-A: fat sits in the analysis the price is derived from. Optional —
    # the lab figure is not always back when the delivery is recorded.
    fat_pct = db.Column(db.Numeric(5, 2), nullable=True)

    # TICKET-4: nullable — the client records a delivery first and prices it
    # later, sometimes days later. NULL total_value means "not priced yet".
    unit_price = db.Column(db.Numeric(10, 3), nullable=True)  # السعر
    base_value = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))  # الثمن = qty × price

    # Client's Excel columns. TICKET-A: these are RATES PER KILO, not amounts —
    # the client enters جنيه/كيلو and the invoice multiplies by the quantity.
    # Wide scale because converting a legacy amount on a 24,450 kg delivery
    # gives 0.0000229…, which rounds to zero at two places and loses the value.
    fat_bonus = db.Column(db.Numeric(18, 10), nullable=False, default=Decimal("0"))       # الدهن
    protein_bonus = db.Column(db.Numeric(18, 10), nullable=False, default=Decimal("0"))   # البروتين
    bacteria_adj = db.Column(db.Numeric(18, 10), nullable=False, default=Decimal("0"))    # البكتيريا
    transport = db.Column(db.Numeric(18, 10), nullable=False, default=Decimal("0"))       # النقل
    other_adj = db.Column(db.Numeric(18, 10), nullable=False, default=Decimal("0"))       # أخرى

    subtotal = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))        # الإجمالي

    qty_deduction = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))   # خ كمية
    cash_deduction = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))  # خ نقدي
    rounding = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0"))        # كسور

    total_value = db.Column(db.Numeric(14, 2), nullable=True)                             # الصافي

    invoice_id = db.Column(db.Integer, db.ForeignKey("milk_invoices.id"), nullable=True)

    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    customer = db.relationship("Customer", back_populates="deliveries")
    invoice = db.relationship("MilkInvoice", back_populates="deliveries")

    # TICKET-A: the adjustment columns hold rates, but the invoice the customer
    # receives is a money document — it prints these amounts, not the rates.
    def _adj_amount(self, rate) -> Decimal:
        qty = Decimal(str(self.qty_kg or 0))
        return (Decimal(str(rate or 0)) * qty).quantize(Decimal("0.01"))

    @property
    def fat_amount(self) -> Decimal:
        return self._adj_amount(self.fat_bonus)

    @property
    def protein_amount(self) -> Decimal:
        return self._adj_amount(self.protein_bonus)

    @property
    def bacteria_amount(self) -> Decimal:
        return self._adj_amount(self.bacteria_adj)

    @property
    def transport_amount(self) -> Decimal:
        return self._adj_amount(self.transport)

    @property
    def other_amount(self) -> Decimal:
        return self._adj_amount(self.other_adj)

    @property
    def additions_total(self) -> Decimal:
        """All التعديلات together, in EGP."""
        rates = (self.fat_bonus, self.protein_bonus, self.bacteria_adj,
                 self.transport, self.other_adj)
        total = sum((Decimal(str(r or 0)) for r in rates), Decimal("0"))
        return (total * Decimal(str(self.qty_kg or 0))).quantize(Decimal("0.01"))

    @property
    def is_priced(self) -> bool:
        """TICKET-4: False while the delivery is still awaiting its price."""
        return self.unit_price is not None and self.total_value is not None

    @property
    def pricing_status_label(self) -> str:
        return "مسعّر" if self.is_priced else "بانتظار التسعير"

    @property
    def is_locked(self) -> bool:
        """TICKET-4: an issued invoice is a document the customer already has.

        Once it is issued the delivery behind it must not change.
        """
        return bool(self.invoice and self.invoice.status == MilkInvoice.STATUS_ISSUED)


class MilkInvoice(db.Model):
    """Groups multiple daily deliveries for a customer over a period into one
    printable/exportable invoice. Matches the client's real Excel format."""

    __tablename__ = "milk_invoices"

    STATUS_DRAFT = "draft"
    STATUS_ISSUED = "issued"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    invoice_number = db.Column(db.String(40), nullable=True, index=True)  # optional external #
    period_from = db.Column(db.Date, nullable=False)
    period_to = db.Column(db.Date, nullable=False)
    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(20), nullable=False, default=STATUS_DRAFT)

    grand_total = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    customer = db.relationship("Customer")
    deliveries = db.relationship(
        "MilkDelivery",
        back_populates="invoice",
        order_by="MilkDelivery.delivery_date",
    )

    @property
    def status_label(self) -> str:
        return "مسوّدة" if self.status == self.STATUS_DRAFT else "صادرة"

    def recompute_total(self) -> None:
        # TICKET-4: unpriced deliveries carry total_value = None; skip them
        self.grand_total = sum(
            (d.total_value for d in self.deliveries if d.total_value is not None),
            Decimal("0"),
        )


class CustomerPayment(db.Model):
    __tablename__ = "customer_payments"

    METHOD_CASH = "cash"
    METHOD_TRANSFER = "transfer"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    method = db.Column(db.String(20), nullable=False, default=METHOD_CASH)
    # TREASURY: which account the money landed in (nullable for pre-accounts rows)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    customer = db.relationship("Customer", back_populates="payments")
    account = db.relationship("Account")

    @property
    def method_label(self) -> str:
        return "كاش" if self.method == self.METHOD_CASH else "تحويل بنكي"


class DailyProduction(db.Model):
    __tablename__ = "daily_productions"

    id = db.Column(db.Integer, primary_key=True)
    production_date = db.Column(db.Date, unique=True, nullable=False, index=True)
    total_kg = db.Column(db.Numeric(14, 3), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    @property
    def total_delivered_kg(self) -> Decimal:
        val = (
            db.session.query(func.coalesce(func.sum(MilkDelivery.qty_kg), 0))
            .filter(
                MilkDelivery.delivery_date == self.production_date,
                MilkDelivery.is_archived.is_(False),
            )
            .scalar()
        )
        return Decimal(str(val or 0))

    @property
    def waste_kg(self) -> Decimal:
        w = self.total_kg - self.total_delivered_kg
        return w if w > 0 else Decimal("0")
