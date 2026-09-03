from datetime import date, datetime
from decimal import Decimal

from app.extensions import db


class Ingredient(db.Model):
    __tablename__ = "ingredients"

    CATEGORY_FEED = "feed"
    CATEGORY_MEDICINE = "medicine"

    UNIT_KG = "kg"
    UNIT_LITRE = "litre"
    UNIT_ML = "ml"
    UNIT_PIECE = "piece"
    UNIT_BOX = "box"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    category = db.Column(db.String(60), nullable=False, index=True)
    # TICKET-2: `unit` is the DISPLAY/BASE unit — everything stored/computed in this
    unit = db.Column(db.String(20), nullable=False, default=UNIT_KG)

    current_qty = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0"))
    min_qty = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0"))
    # `last_price` is the latest single purchase price — used to prefill
    # the price input on a new-purchase form (so the user sees the most
    # recent number) and shown on the ingredient detail as a reference.
    # The ledger, stock valuation, and feed-run cost projections all
    # read `avg_cost` — the weighted-average blended in
    # `app/utils/inventory_cost.py`.
    last_price = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0"))
    avg_cost = db.Column(db.Numeric(12, 4), nullable=False, default=Decimal("0"))

    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    __table_args__ = (db.UniqueConstraint("name", "category", name="uq_ingredient_name_category"),)

    # TICKET-2: alternate purchase/dispense units with conversion factors
    alt_units = db.relationship(
        "IngredientUnit",
        back_populates="ingredient",
        cascade="all, delete-orphan",
        order_by="IngredientUnit.id",
    )

    @property
    def base_unit(self) -> str:
        """Alias — `unit` is always the base storage/computation unit."""
        return self.unit

    @property
    def base_unit_label(self) -> str:
        return self.unit_label

    def all_units(self) -> list["IngredientUnit"]:
        """Base unit (factor=1) followed by any registered alt units."""
        base = IngredientUnit(
            ingredient_id=self.id,
            unit_code=self.unit,
            unit_label=self.unit_label,
            factor_to_base=Decimal("1"),
            is_base=True,
        )
        return [base] + list(self.alt_units or [])

    def factor_for(self, unit_code: str) -> Decimal | None:
        """How many base units = 1 of `unit_code`? None if unknown."""
        if unit_code == self.unit:
            return Decimal("1")
        for u in (self.alt_units or []):
            if u.unit_code == unit_code:
                return u.factor_to_base
        return None

    @property
    def category_label(self) -> str:
        # Custom categories are stored with a "custom:" prefix (e.g. "custom:قطع غيار")
        if self.category and self.category.startswith("custom:"):
            return self.category[len("custom:"):]
        return {
            self.CATEGORY_FEED: "علف / مادة خام",
            self.CATEGORY_MEDICINE: "دواء بيطري",
        }.get(self.category, self.category)

    @property
    def is_custom_category(self) -> bool:
        return bool(self.category and self.category.startswith("custom:"))

    @property
    def unit_label(self) -> str:
        return {
            self.UNIT_KG: "كيلو",
            self.UNIT_LITRE: "لتر",
            self.UNIT_ML: "مل",
            self.UNIT_PIECE: "قطعة",
            self.UNIT_BOX: "علبة",
        }.get(self.unit, self.unit)

    @property
    def is_low_stock(self) -> bool:
        return self.min_qty > 0 and self.current_qty <= self.min_qty

    @property
    def stock_value(self) -> Decimal:
        # PHASE 6: valued at weighted-average cost, not last purchase price
        return (self.current_qty or Decimal("0")) * (self.avg_cost or Decimal("0"))


class IngredientUnit(db.Model):
    """TICKET-2: alternate purchase/dispense units for an ingredient with the
    conversion factor to the base unit.

    Example: ingredient "ذرة" with base_unit=kg
      alt_units = [
        (unit_code='ton',  unit_label='طن',       factor=1000),
        (unit_code='sack50', unit_label='شكارة 50', factor=50),
      ]
    """

    __tablename__ = "ingredient_units"

    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(
        db.Integer,
        db.ForeignKey("ingredients.id", name="fk_ingunit_ingredient"),
        nullable=False, index=True,
    )
    unit_code = db.Column(db.String(40), nullable=False)
    unit_label = db.Column(db.String(60), nullable=False)
    factor_to_base = db.Column(db.Numeric(14, 6), nullable=False, default=Decimal("1"))
    is_default_purchase = db.Column(db.Boolean, nullable=False, default=False)
    is_base = False  # set to True only for the transient "virtual" base row from all_units()

    ingredient = db.relationship("Ingredient", back_populates="alt_units")

    __table_args__ = (
        db.UniqueConstraint("ingredient_id", "unit_code", name="uq_ingredient_unit"),
    )


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    REASON_PURCHASE = "purchase"
    REASON_FEED_RUN = "feed_run"
    REASON_MEDICINE = "medicine"
    REASON_ADJUST = "adjust"

    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False, index=True)
    delta = db.Column(db.Numeric(14, 3), nullable=False)  # ALWAYS in ingredient's base unit
    reason = db.Column(db.String(20), nullable=False)
    ref_id = db.Column(db.Integer, nullable=True)
    unit_price_at_move = db.Column(db.Numeric(12, 2), nullable=True)

    # TICKET-2: audit trail — what did the user actually type?
    input_qty = db.Column(db.Numeric(14, 3), nullable=True)
    input_unit_code = db.Column(db.String(40), nullable=True)

    moved_on = db.Column(db.Date, nullable=False, default=date.today, index=True)
    notes = db.Column(db.String(255), nullable=True)
    # PHASE 6: nullable link to a medicine lot so a dispense that pulled
    # from multiple lots reads back cleanly (one StockMovement per lot).
    lot_id = db.Column(db.Integer, db.ForeignKey("medicine_lots.id"),
                       nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    ingredient = db.relationship("Ingredient")
    lot = db.relationship("MedicineLot")

    @property
    def reason_label(self) -> str:
        return {
            self.REASON_PURCHASE: "شراء",
            self.REASON_FEED_RUN: "تشغيل علف",
            self.REASON_MEDICINE: "صرف دواء",
            self.REASON_ADJUST: "تعديل جرد",
        }.get(self.reason, self.reason)


class MedicineLot(db.Model):
    """PHASE 6 — one batch of medicine received from a supplier.

    Only medicine ingredients carry lots. Feed pools into the tank's
    weighted average and doesn't need lot-level tracking. Every dispense
    picks lots FIFO by expiry_date; the picking service lives in
    `app/utils/inventory_cost.py`.

    A brand-new purchase creates one lot per invoice line. A backfill row
    lives on with `source_type='OpeningInventory'` and no expiry so the
    dispense picker can still find something to draw from for medicines
    that were on the shelf before phase 6 shipped.
    """

    __tablename__ = "medicine_lots"

    SOURCE_PURCHASE = "PurchaseInvoice"
    SOURCE_OPENING = "OpeningInventory"

    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(
        db.Integer, db.ForeignKey("ingredients.id"),
        nullable=False, index=True,
    )
    lot_number = db.Column(db.String(60), nullable=True)
    expires_on = db.Column(db.Date, nullable=True, index=True)
    qty_received = db.Column(db.Numeric(14, 3), nullable=False)
    qty_remaining = db.Column(db.Numeric(14, 3), nullable=False)
    unit_cost = db.Column(db.Numeric(12, 4), nullable=False, default=Decimal("0"))
    # Where this lot came from — usually a PurchaseInvoice row
    source_type = db.Column(db.String(40), nullable=False, default=SOURCE_PURCHASE)
    source_id = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    ingredient = db.relationship("Ingredient", backref="medicine_lots")

    __table_args__ = (
        db.UniqueConstraint(
            "ingredient_id", "source_type", "source_id", "lot_number",
            name="uq_med_lot_source",
        ),
    )

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_on and self.expires_on < date.today())

    def days_until_expiry(self) -> int | None:
        if self.expires_on is None:
            return None
        return (self.expires_on - date.today()).days
