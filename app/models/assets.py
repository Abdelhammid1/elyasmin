"""PHASE 8b — fixed assets + monthly depreciation.

A `FixedAsset` is a piece of equipment the farm owns: milking machine,
feed mixer, water pump. Each row carries its purchase cost, salvage
value (what it's expected to be worth at end-of-life), and useful life
in months. Monthly depreciation = (cost - salvage) / life.

Every posting run creates a `DepreciationPosting` row + the matching
JE. A UNIQUE constraint on (asset_id, period_month) makes double-posts
impossible even under a double-click race.

Livestock deliberately NOT modelled here — see the phase 8 plan;
that's an opt-in the client didn't pick.
"""
from datetime import date, datetime
from decimal import Decimal

from app.extensions import db


class FixedAsset(db.Model):
    __tablename__ = "fixed_assets"

    CATEGORY_EQUIPMENT = "equipment"
    CATEGORY_MACHINERY = "machinery"
    CATEGORY_OTHER = "other"
    CATEGORY_LABELS = {
        CATEGORY_EQUIPMENT: "معدات",
        CATEGORY_MACHINERY: "آلات",
        CATEGORY_OTHER: "أخرى",
    }

    STATUS_ACTIVE = "active"
    STATUS_DISPOSED = "disposed"
    STATUS_ARCHIVED = "archived"
    STATUS_LABELS = {
        STATUS_ACTIVE: "نشط",
        STATUS_DISPOSED: "تم البيع/التخريد",
        STATUS_ARCHIVED: "مؤرشف",
    }

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(20), nullable=False, default=CATEGORY_EQUIPMENT)
    purchase_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    purchase_cost = db.Column(db.Numeric(14, 2), nullable=False)
    salvage_value = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    useful_life_months = db.Column(db.Integer, nullable=False)  # e.g. 60 for 5 years

    # Running total updated by each monthly post — also derivable from JEs
    # but stored so list pages don't fire N queries. Kept honest by the
    # unique constraint on DepreciationPosting.
    accumulated_depreciation = db.Column(
        db.Numeric(14, 2), nullable=False, default=Decimal("0"),
    )

    # Source of the purchase — either a treasury (cash purchase) or a
    # supplier (credit purchase). Exactly one is set at purchase-post time.
    treasury_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=True,
    )
    supplier_id = db.Column(
        db.Integer, db.ForeignKey("suppliers.id"), nullable=True,
    )

    status = db.Column(
        db.String(20), nullable=False, default=STATUS_ACTIVE,
        server_default="active", index=True,
    )
    disposed_on = db.Column(db.Date, nullable=True)
    disposal_notes = db.Column(db.Text, nullable=True)

    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    treasury_account = db.relationship("TreasuryAccount")
    supplier = db.relationship("Supplier")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    depreciation_postings = db.relationship(
        "DepreciationPosting",
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="DepreciationPosting.period_month",
    )

    __table_args__ = (
        db.CheckConstraint(
            "category IN ('equipment', 'machinery', 'other')",
            name="ck_asset_category",
        ),
        db.CheckConstraint(
            "status IN ('active', 'disposed', 'archived')",
            name="ck_asset_status",
        ),
    )

    @property
    def category_label(self) -> str:
        return self.CATEGORY_LABELS.get(self.category, self.category)

    @property
    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def monthly_depreciation(self) -> Decimal:
        life = int(self.useful_life_months or 0)
        if life <= 0:
            return Decimal("0")
        cost = Decimal(str(self.purchase_cost or 0))
        salvage = Decimal(str(self.salvage_value or 0))
        return ((cost - salvage) / life).quantize(Decimal("0.01"))

    @property
    def book_value(self) -> Decimal:
        return (
            Decimal(str(self.purchase_cost or 0))
            - Decimal(str(self.accumulated_depreciation or 0))
        ).quantize(Decimal("0.01"))

    @property
    def is_fully_depreciated(self) -> bool:
        return self.book_value <= Decimal(str(self.salvage_value or 0))


class DepreciationPosting(db.Model):
    __tablename__ = "depreciation_postings"

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(
        db.Integer, db.ForeignKey("fixed_assets.id"),
        nullable=False, index=True,
    )
    # First-of-month date — a period marker, not a real posting date.
    period_month = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    je_id = db.Column(
        db.Integer, db.ForeignKey("journal_entries.id"),
        nullable=True,
    )
    posted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    posted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    asset = db.relationship("FixedAsset", back_populates="depreciation_postings")
    journal_entry = db.relationship("JournalEntry")

    __table_args__ = (
        db.UniqueConstraint(
            "asset_id", "period_month", name="uq_dep_per_asset_month",
        ),
    )
