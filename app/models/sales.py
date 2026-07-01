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

    deliveries = db.relationship("MilkDelivery", back_populates="customer", lazy="dynamic")
    payments = db.relationship("CustomerPayment", back_populates="customer", lazy="dynamic")

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


class MilkDelivery(db.Model):
    __tablename__ = "milk_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    delivery_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    qty_kg = db.Column(db.Numeric(14, 3), nullable=False)

    protein_pct = db.Column(db.Numeric(5, 2), nullable=True)  # only for quality-based
    bacteria_count = db.Column(db.Integer, nullable=True)  # only for quality-based

    unit_price = db.Column(db.Numeric(10, 3), nullable=False)
    total_value = db.Column(db.Numeric(14, 2), nullable=False)

    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    customer = db.relationship("Customer", back_populates="deliveries")


class CustomerPayment(db.Model):
    __tablename__ = "customer_payments"

    METHOD_CASH = "cash"
    METHOD_TRANSFER = "transfer"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    method = db.Column(db.String(20), nullable=False, default=METHOD_CASH)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    customer = db.relationship("Customer", back_populates="payments")

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
