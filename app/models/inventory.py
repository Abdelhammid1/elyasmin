from datetime import date, datetime
from decimal import Decimal

from app.extensions import db


class Ingredient(db.Model):
    __tablename__ = "ingredients"

    CATEGORY_FEED = "feed"
    CATEGORY_MEDICINE = "medicine"

    UNIT_KG = "kg"
    UNIT_LITRE = "litre"
    UNIT_PIECE = "piece"
    UNIT_BOX = "box"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    category = db.Column(db.String(20), nullable=False, index=True)
    unit = db.Column(db.String(20), nullable=False, default=UNIT_KG)

    current_qty = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0"))
    min_qty = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0"))
    last_price = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0"))

    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    __table_args__ = (db.UniqueConstraint("name", "category", name="uq_ingredient_name_category"),)

    @property
    def category_label(self) -> str:
        return {
            self.CATEGORY_FEED: "علف / مادة خام",
            self.CATEGORY_MEDICINE: "دواء بيطري",
        }.get(self.category, self.category)

    @property
    def unit_label(self) -> str:
        return {
            self.UNIT_KG: "كيلو",
            self.UNIT_LITRE: "لتر",
            self.UNIT_PIECE: "قطعة",
            self.UNIT_BOX: "علبة",
        }.get(self.unit, self.unit)

    @property
    def is_low_stock(self) -> bool:
        return self.min_qty > 0 and self.current_qty <= self.min_qty

    @property
    def stock_value(self) -> Decimal:
        return (self.current_qty or Decimal("0")) * (self.last_price or Decimal("0"))


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    REASON_PURCHASE = "purchase"
    REASON_FEED_RUN = "feed_run"
    REASON_MEDICINE = "medicine"
    REASON_ADJUST = "adjust"

    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False, index=True)
    delta = db.Column(db.Numeric(14, 3), nullable=False)  # positive = in, negative = out
    reason = db.Column(db.String(20), nullable=False)
    ref_id = db.Column(db.Integer, nullable=True)  # invoice/feed_run/dispense id
    unit_price_at_move = db.Column(db.Numeric(12, 2), nullable=True)

    moved_on = db.Column(db.Date, nullable=False, default=date.today, index=True)
    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    ingredient = db.relationship("Ingredient")

    @property
    def reason_label(self) -> str:
        return {
            self.REASON_PURCHASE: "شراء",
            self.REASON_FEED_RUN: "تشغيل علف",
            self.REASON_MEDICINE: "صرف دواء",
            self.REASON_ADJUST: "تعديل جرد",
        }.get(self.reason, self.reason)
