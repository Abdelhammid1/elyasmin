from datetime import date, datetime
from decimal import Decimal

from app.extensions import db


class Setting(db.Model):
    __tablename__ = "settings"

    KEY_COST_SPLIT_MILK_PCT = "cost_split_milk_pct"
    KEY_COST_SPLIT_OTHERS_PCT = "cost_split_others_pct"
    KEY_QUALITY_PRICE_BASE = "quality_price_base"           # base price EGP/kg
    KEY_QUALITY_PROTEIN_ADJ = "quality_protein_adj"         # + EGP per +1% protein above 3
    KEY_QUALITY_BACTERIA_PENALTY = "quality_bacteria_penalty"  # − EGP per 100k CFU above 100k

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        row = db.session.get(cls, key)
        return row.value if row else default

    @classmethod
    def get_decimal(cls, key: str, default: Decimal = Decimal("0")) -> Decimal:
        raw = cls.get(key, "")
        try:
            return Decimal(raw) if raw else default
        except Exception:  # noqa: BLE001
            return default

    @classmethod
    def set(cls, key: str, value: str, description: str | None = None) -> None:
        row = db.session.get(cls, key)
        if row:
            row.value = value
            if description is not None:
                row.description = description
        else:
            db.session.add(cls(key=key, value=value, description=description))


class Expense(db.Model):
    """Cash-outflow / expense record.

    Populated by:
      - supplier payments  (ref_type='supplier_payment')
      - worker payments    (ref_type='worker_payment')
      - manual entries     (ref_type=None)
    Purchase invoices themselves are NOT expenses at invoice time; they become
    expenses via supplier payments (or immediately for cash invoices).
    """

    __tablename__ = "expenses"

    CAT_ELECTRICITY = "electricity"
    CAT_MAINTENANCE = "maintenance"
    CAT_RENT = "rent"
    CAT_FEED_PURCHASE = "feed_purchase"
    CAT_MEDICINE_PURCHASE = "medicine_purchase"
    CAT_SUPPLIER_PAYMENT = "supplier_payment"
    CAT_WORKER_WAGE = "worker_wage"
    CAT_OTHER = "other"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(40), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    expense_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    description = db.Column(db.String(255), nullable=True)

    ref_type = db.Column(db.String(40), nullable=True)  # supplier_payment / worker_payment / manual
    ref_id = db.Column(db.Integer, nullable=True)

    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    LABELS = {
        CAT_ELECTRICITY: "كهرباء",
        CAT_MAINTENANCE: "صيانة",
        CAT_RENT: "إيجار",
        CAT_FEED_PURCHASE: "شراء علف",
        CAT_MEDICINE_PURCHASE: "شراء أدوية",
        CAT_SUPPLIER_PAYMENT: "دفعة مورد",
        CAT_WORKER_WAGE: "أجور عمالة",
        CAT_OTHER: "أخرى",
    }

    @property
    def category_label(self) -> str:
        return self.LABELS.get(self.category, self.category)
