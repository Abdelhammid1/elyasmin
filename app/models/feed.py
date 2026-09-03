from datetime import date, datetime
from decimal import Decimal

from app.extensions import db


class FeedRecipe(db.Model):
    """A versioned feed recipe for a cattle group.

    On edit, the current recipe is archived (is_archived=True) and a NEW row is created
    with an updated effective_from date, so historical feed-run cost calculations remain
    reproducible from FeedRunLine snapshots.
    """

    __tablename__ = "feed_recipes"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=False, index=True)
    effective_from = db.Column(db.Date, nullable=False, default=date.today)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    group = db.relationship("CattleGroup")
    lines = db.relationship(
        "FeedRecipeLine",
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="FeedRecipeLine.id",
    )

    @property
    def total_batch_weight(self) -> Decimal:
        return sum((l.kg_per_batch for l in self.lines), Decimal("0"))

    @property
    def batch_cost(self) -> Decimal:
        # PHASE 7: value at weighted-average cost so the projection matches
        # what the feed-run autoposter will actually book (it also reads
        # avg_cost). last_price was a rough proxy that drifted every time
        # a purchase came in at a new price.
        return sum(
            (
                (l.kg_per_batch * (l.ingredient.avg_cost or Decimal("0")))
                for l in self.lines
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"))

    @property
    def cost_per_kg(self) -> Decimal:
        weight = self.total_batch_weight
        if weight == 0:
            return Decimal("0")
        return (self.batch_cost / weight).quantize(Decimal("0.001"))


class FeedRecipeLine(db.Model):
    __tablename__ = "feed_recipe_lines"

    id = db.Column(db.Integer, primary_key=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey("feed_recipes.id"), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False)
    kg_per_batch = db.Column(db.Numeric(12, 3), nullable=False)

    recipe = db.relationship("FeedRecipe", back_populates="lines")
    ingredient = db.relationship("Ingredient")

    @property
    def batch_line_cost(self) -> Decimal:
        # PHASE 7: match FeedRecipe.batch_cost — value at avg_cost, not last_price
        return (self.kg_per_batch * (self.ingredient.avg_cost or Decimal("0"))).quantize(Decimal("0.01"))


class FeedRun(db.Model):
    __tablename__ = "feed_runs"

    id = db.Column(db.Integer, primary_key=True)
    run_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=False, index=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey("feed_recipes.id"), nullable=False)
    batches_count = db.Column(db.Integer, nullable=False)

    total_weight_kg = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0"))
    total_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    cost_per_kg = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0"))

    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    group = db.relationship("CattleGroup")
    recipe = db.relationship("FeedRecipe")
    lines = db.relationship(
        "FeedRunLine", back_populates="run", cascade="all, delete-orphan"
    )


class FeedTank(db.Model):
    """FEED-TANK: the mixed feed sitting in storage for one group.

    Production and consumption are separate events on this farm. A worker runs a
    recipe and produces a large batch that goes into storage; the feeding worker
    draws from it four times a day (فجر/ظهر/عصر/مغرب) over several days until it
    runs out. So a FeedRun now *credits* this tank instead of counting as
    consumption, and the cost reports read the withdrawals.

    One tank per group — the schema keeps a single active recipe per group (see
    feed/routes.py:_current_recipe_for_group), so there is nothing finer to split
    on today.
    """

    __tablename__ = "feed_tanks"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(
        db.Integer, db.ForeignKey("cattle_groups.id"), nullable=False, unique=True, index=True
    )
    current_qty = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0"))
    avg_cost_per_kg = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0"))
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    group = db.relationship("CattleGroup")
    movements = db.relationship(
        "FeedTankMovement",
        back_populates="tank",
        order_by="FeedTankMovement.moved_on, FeedTankMovement.id",
    )

    @property
    def current_value(self) -> Decimal:
        """What the feed sitting in the tank is worth right now."""
        return (
            Decimal(str(self.current_qty or 0)) * Decimal(str(self.avg_cost_per_kg or 0))
        ).quantize(Decimal("0.01"))

    @property
    def is_empty(self) -> bool:
        return Decimal(str(self.current_qty or 0)) <= 0


class FeedTankMovement(db.Model):
    """FEED-TANK: every credit and debit on a feed tank.

    `unit_cost` is the cost per kg at the moment of the movement — for a
    withdrawal that is the tank's weighted-average cost, which is what makes the
    milk-cost report reproducible after the fact.

    SIGN CONVENTION: `qty` and `total_cost` are both signed. Production is
    positive, withdrawal negative, adjustment either way. That keeps a statement's
    running balance a plain cumulative sum, and lets one adjustment type cover a
    correction in either direction.
    """

    __tablename__ = "feed_tank_movements"

    TYPE_PRODUCTION = "production"
    TYPE_WITHDRAWAL = "withdrawal"
    TYPE_ADJUSTMENT = "adjustment"

    TYPE_LABELS = {
        TYPE_PRODUCTION: "إنتاج (تشغيل وصفة)",
        TYPE_WITHDRAWAL: "سحب (تغذية)",
        TYPE_ADJUSTMENT: "تسوية",
    }

    id = db.Column(db.Integer, primary_key=True)
    tank_id = db.Column(db.Integer, db.ForeignKey("feed_tanks.id"), nullable=False, index=True)
    movement_type = db.Column(db.String(20), nullable=False, index=True)
    qty = db.Column(db.Numeric(14, 3), nullable=False)
    unit_cost = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0"))
    total_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    ref_feed_run_id = db.Column(
        db.Integer, db.ForeignKey("feed_runs.id"), nullable=True, index=True
    )
    moved_on = db.Column(db.Date, nullable=False, default=date.today, index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    tank = db.relationship("FeedTank", back_populates="movements")
    feed_run = db.relationship("FeedRun")

    @property
    def type_label(self) -> str:
        return self.TYPE_LABELS.get(self.movement_type, self.movement_type)

    @property
    def abs_qty(self) -> Decimal:
        return abs(Decimal(str(self.qty or 0)))

    @property
    def abs_cost(self) -> Decimal:
        return abs(Decimal(str(self.total_cost or 0)))


class FeedRunLine(db.Model):
    """Snapshot of what was actually consumed at run time, at the price at that moment.

    This makes the milk cost calculation in Sprint 6 fully reproducible even if ingredient
    prices change later.
    """

    __tablename__ = "feed_run_lines"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("feed_runs.id"), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False)
    qty_used = db.Column(db.Numeric(14, 3), nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    line_cost = db.Column(db.Numeric(14, 2), nullable=False)

    run = db.relationship("FeedRun", back_populates="lines")
    ingredient = db.relationship("Ingredient")


class MedicineDispense(db.Model):
    """A dispense of vet medicine to a specific cow OR a whole group.

    Exactly one of cow_id / group_id is set.
    """

    __tablename__ = "medicine_dispenses"

    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False)
    # qty stored ALWAYS in ingredient's base unit
    qty = db.Column(db.Numeric(14, 3), nullable=False)
    unit_price_at_dispense = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0"))
    total_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))

    # TICKET-2 audit trail
    input_qty = db.Column(db.Numeric(14, 3), nullable=True)
    input_unit_code = db.Column(db.String(40), nullable=True)

    cow_id = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=True)

    # PHASE 6: the primary lot the dispense drew from (the first, if it
    # spanned multiple). Full per-lot breakdown lives on the StockMovement
    # rows the dispense produced.
    lot_id = db.Column(db.Integer, db.ForeignKey("medicine_lots.id"),
                       nullable=True, index=True)

    dispensed_on = db.Column(db.Date, nullable=False, default=date.today, index=True)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    ingredient = db.relationship("Ingredient")
    cow = db.relationship("Cow")
    group = db.relationship("CattleGroup")
    lot = db.relationship("MedicineLot")

    @property
    def target_label(self) -> str:
        if self.cow_id:
            return f"بقرة {self.cow.ear_tag}"
        if self.group_id:
            return f"مجموعة {self.group.name}"
        return "—"


class GroupFeedAllowance(db.Model):
    """TICKET-3: which raw materials may be added to a group at feeding time.

    The client wants each group pre-set with what is allowed (e.g. مجموعة الحليب
    → دريس حجازى، سيلاج، قش، تبن قمح). It stops a worker adding the wrong thing
    at 5am and saves him scrolling the whole inventory.
    """

    __tablename__ = "group_feed_allowances"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=False, index=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False, index=True)

    group = db.relationship("CattleGroup")
    ingredient = db.relationship("Ingredient")

    __table_args__ = (
        db.UniqueConstraint("group_id", "ingredient_id", name="uq_group_allowance"),
    )


class FeedingSession(db.Model):
    """TICKET-3: one actual feeding of one group — the event that costs money.

    The distinction the whole ticket turns on:

      * `feed_qty` comes out of the group's FeedTank — the recipe the vet set,
        already mixed and stored. Nothing else may be drawn from there.
      * `additions` come straight out of general inventory (سيلاج، تبن، دريس،
        قش). They are mixed in at the trough, are NOT part of the recipe's
        composition, and must never be written back into it.

    They meet in one place only: the cost of this meal.
    """

    __tablename__ = "feeding_sessions"

    MEAL_FAJR = "fajr"
    MEAL_DHUHR = "dhuhr"
    MEAL_ASR = "asr"
    MEAL_MAGHRIB = "maghrib"

    MEAL_LABELS = {
        MEAL_FAJR: "الفجر",
        MEAL_DHUHR: "الظهر",
        MEAL_ASR: "العصر",
        MEAL_MAGHRIB: "المغرب",
    }
    # The client: milk group eats 3 times a day, the rest twice.
    MEALS_MILK = [MEAL_FAJR, MEAL_DHUHR, MEAL_MAGHRIB]
    MEALS_OTHER = [MEAL_FAJR, MEAL_MAGHRIB]

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=False, index=True)
    session_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    meal = db.Column(db.String(20), nullable=False)

    feed_qty = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0"))
    feed_unit_cost = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0"))
    feed_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    additions_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    total_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))

    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    group = db.relationship("CattleGroup")
    additions = db.relationship(
        "FeedingAddition", back_populates="session", cascade="all, delete-orphan"
    )

    @staticmethod
    def meals_for(group) -> list[str]:
        from app.models.herd import CattleGroup
        return (FeedingSession.MEALS_MILK if group.type == CattleGroup.TYPE_MILK
                else FeedingSession.MEALS_OTHER)

    @property
    def meal_label(self) -> str:
        return self.MEAL_LABELS.get(self.meal, self.meal)


class FeedingAddition(db.Model):
    """TICKET-3: one raw material added at the trough, priced at that moment.

    Deducted from general inventory. Never touches recipe composition.
    """

    __tablename__ = "feeding_additions"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("feeding_sessions.id"), nullable=False, index=True
    )
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredients.id"), nullable=False)
    qty = db.Column(db.Numeric(14, 3), nullable=False)
    unit_cost = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0"))
    total_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))

    session = db.relationship("FeedingSession", back_populates="additions")
    ingredient = db.relationship("Ingredient")
