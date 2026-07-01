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
        return sum(
            (
                (l.kg_per_batch * (l.ingredient.last_price or Decimal("0")))
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
        return (self.kg_per_batch * (self.ingredient.last_price or Decimal("0"))).quantize(Decimal("0.01"))


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
    qty = db.Column(db.Numeric(14, 3), nullable=False)
    unit_price_at_dispense = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0"))
    total_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))

    cow_id = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=True)

    dispensed_on = db.Column(db.Date, nullable=False, default=date.today, index=True)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    ingredient = db.relationship("Ingredient")
    cow = db.relationship("Cow")
    group = db.relationship("CattleGroup")

    @property
    def target_label(self) -> str:
        if self.cow_id:
            return f"بقرة {self.cow.ear_tag}"
        if self.group_id:
            return f"مجموعة {self.group.name}"
        return "—"
