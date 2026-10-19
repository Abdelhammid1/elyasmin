from datetime import datetime, date
from decimal import Decimal

from app.extensions import db


class CattleGroup(db.Model):
    __tablename__ = "cattle_groups"

    TYPE_MILK = "milk"
    TYPE_DRY = "dry"
    TYPE_PRE_BIRTH = "pre_birth"
    TYPE_NURSING = "nursing"
    TYPE_FATTENING = "fattening"
    TYPE_CUSTOM = "custom"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    type = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    cows = db.relationship("Cow", back_populates="group", lazy="dynamic")

    TYPE_LABELS = {
        TYPE_MILK: "حليب",
        TYPE_DRY: "جفاف",
        TYPE_PRE_BIRTH: "انتظار ولادة",
        TYPE_NURSING: "رضاعة",
        TYPE_FATTENING: "تسمين",
        TYPE_CUSTOM: "مخصصة",
    }

    @property
    def type_label(self) -> str:
        return self.TYPE_LABELS.get(self.type, self.type)

    @property
    def active_count(self) -> int:
        return self.cows.filter_by(status=Cow.STATUS_ACTIVE, is_archived=False).count()

    @property
    def can_archive(self) -> bool:
        """A group can be archived only if it has no active cows."""
        return self.active_count == 0


class Cow(db.Model):
    __tablename__ = "cows"

    GENDER_FEMALE = "female"
    GENDER_MALE = "male"

    STATUS_ACTIVE = "active"
    STATUS_SOLD = "sold"
    STATUS_DEAD = "dead"

    # HERD-3: reproductive / lifecycle status codes from the client's
    # list (STATUS.txt) + "milking" for a cow that has calved. The
    # names live in the `breeding_statuses` table (editable from
    # /herd/statuses); these constants are the seeded codes so
    # routes and tests can reference them.
    BS_CALF      = "calf"       # عجلة رضيعة
    BS_B_CALF    = "b_calf"     # عجل رضيع
    BS_WEANING   = "weaning"    # فطام
    BS_HEIFER    = "heifer"     # عجلة نامية
    BS_FATTENING = "fattening"  # تسمين
    BS_INSEM     = "insem"      # ملقحة
    BS_PREG      = "preg"       # عشار
    BS_PREG2     = "preg2"      # عشار مؤكد
    BS_DRY       = "dry"        # جفاف
    BS_WAITING   = "waiting"    # انتظار
    BS_MILKING   = "milking"    # حليب
    BS_ABORT     = "abort"      # طراحة
    BS_OPEN      = "open"       # فارغة

    id = db.Column(db.Integer, primary_key=True)
    ear_tag = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    gender = db.Column(db.String(10), nullable=False, default=GENDER_FEMALE)

    group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE, index=True)
    # HERD-2 Part 1 (PHASE 35): reproductive status — separate from
    # lifecycle `status` (active/sold/dead). Nullable; meaningful only
    # for females. Values are BS_* constants below; never changes
    # without an explicit user confirmation from the breeding-event
    # flow (or the retro-edit screen).
    breeding_status = db.Column(db.String(30), nullable=True)
    # HERD-2 Part 3 (PHASE 35): the cow's most recent book value
    # (زكاة basis). History lives in `cow_valuations`; this column
    # is the running "current" snapshot that changes only via a
    # `CowValuation` insert (never edited in place).
    current_value = db.Column(
        db.Numeric(12, 2), nullable=False, default=Decimal("0"),
        server_default="0",
    )
    # SEASON-OFFSET (PHASE 37): manual backfill for calvings that
    # happened BEFORE the cow was registered in the system. Zero
    # for cows born inside the system; the "عدد المواسم" widget
    # adds this on top of the COUNT of Birth rows.
    season_count_offset = db.Column(
        db.Integer, nullable=False, default=0, server_default="0",
    )
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    mother_id = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=True)

    group = db.relationship("CattleGroup", back_populates="cows")
    movements = db.relationship(
        "CowMovement", back_populates="cow", order_by="CowMovement.moved_on.desc()"
    )
    calves_born = db.relationship(
        "Calf", foreign_keys="Calf.cow_id", back_populates="cow"
    )

    @property
    def age_months(self) -> int | None:
        if not self.date_of_birth:
            return None
        today = date.today()
        return (today.year - self.date_of_birth.year) * 12 + (
            today.month - self.date_of_birth.month
        )

    @property
    def gender_label(self) -> str:
        return "أنثى" if self.gender == self.GENDER_FEMALE else "ذكر"

    @property
    def status_label(self) -> str:
        return {
            self.STATUS_ACTIVE: "نشط",
            self.STATUS_SOLD: "مباع",
            self.STATUS_DEAD: "نافق",
        }.get(self.status, self.status)

    @property
    def breeding_status_label(self) -> str | None:
        """Arabic name of the status (from `breeding_statuses`), or
        None when unset."""
        if not self.breeding_status:
            return None
        return breeding_status_label(self.breeding_status)

    @property
    def season_count(self) -> int:
        """HERD-2 Part 2 + SEASON-OFFSET (PHASE 37): total calvings
        for this cow — historical offset (manual backfill for
        calvings that happened before the cow was registered in
        the system) plus every Birth recorded here. Auto-updates
        immediately after `create_birth` commits a new row."""
        from sqlalchemy import func as _func
        counted = (
            db.session.query(_func.count(Birth.id))
            .filter(Birth.mother_id == self.id)
            .scalar() or 0
        )
        return counted + (self.season_count_offset or 0)


class CowMovement(db.Model):
    __tablename__ = "cow_movements"

    id = db.Column(db.Integer, primary_key=True)
    cow_id = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=False, index=True)
    from_group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=True)
    to_group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=False)
    moved_on = db.Column(db.Date, nullable=False, default=date.today, index=True)
    reason = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    # HERD-10: the event that made this move (NULL for manual moves), so
    # deleting the event can undo it.
    event_id = db.Column(
        db.Integer, db.ForeignKey("breeding_events.id"), nullable=True, index=True,
    )

    cow = db.relationship("Cow", back_populates="movements")
    from_group = db.relationship("CattleGroup", foreign_keys=[from_group_id])
    to_group = db.relationship("CattleGroup", foreign_keys=[to_group_id])


class Birth(db.Model):
    __tablename__ = "births"

    DELIVERY_NATURAL = "natural"
    DELIVERY_HARD = "hard"
    DELIVERY_DEAD = "dead"

    id = db.Column(db.Integer, primary_key=True)
    mother_id = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=False, index=True)
    birth_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    calves_count = db.Column(db.Integer, nullable=False, default=1)
    delivery_type = db.Column(db.String(20), nullable=False, default=DELIVERY_NATURAL)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    mother = db.relationship("Cow", foreign_keys=[mother_id])
    calves = db.relationship("Calf", back_populates="birth", cascade="all, delete-orphan")

    @property
    def delivery_label(self) -> str:
        return {
            self.DELIVERY_NATURAL: "طبيعية",
            self.DELIVERY_HARD: "صعبة",
            self.DELIVERY_DEAD: "نافق",
        }.get(self.delivery_type, self.delivery_type)


class Calf(db.Model):
    __tablename__ = "calves"

    id = db.Column(db.Integer, primary_key=True)
    birth_id = db.Column(db.Integer, db.ForeignKey("births.id"), nullable=False, index=True)
    cow_id = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=True)
    gender = db.Column(db.String(10), nullable=False)
    is_alive = db.Column(db.Boolean, nullable=False, default=True)

    birth = db.relationship("Birth", back_populates="calves")
    cow = db.relationship("Cow", foreign_keys=[cow_id], back_populates="calves_born")


class Death(db.Model):
    __tablename__ = "deaths"

    REASON_DISEASE = "disease"
    REASON_ACCIDENT = "accident"
    REASON_UNKNOWN = "unknown"

    id = db.Column(db.Integer, primary_key=True)
    cow_id = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=False, unique=True)
    death_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    reason = db.Column(db.String(20), nullable=False, default=REASON_UNKNOWN)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    cow = db.relationship("Cow")

    @property
    def reason_label(self) -> str:
        return {
            self.REASON_DISEASE: "مرض",
            self.REASON_ACCIDENT: "حادثة",
            self.REASON_UNKNOWN: "غير معروف",
        }.get(self.reason, self.reason)


class AnimalSale(db.Model):
    __tablename__ = "animal_sales"

    id = db.Column(db.Integer, primary_key=True)
    cow_id = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=False, unique=True)
    sale_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    buyer_name = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Numeric(12, 2), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    cow = db.relationship("Cow")


# ==================== HERD-2 Part 1 (PHASE 35): breeding events ====================


class BreedingEvent(db.Model):
    """One row per reproductive-cycle event on a cow. Immutable
    history — retro-edits change the derived `Cow.breeding_status`
    via `BreedingStatusChange` but never mutate a row here.

    `calving` rows are written by `create_birth` alongside the
    Birth row, so the two logs stay in sync without a separate
    manual entry.
    """
    __tablename__ = "breeding_events"

    # HERD-5: seeded codes of the `breeding_event_types` table. Names
    # and the group/status each type applies live in that table.
    EVENT_INSEMINATION      = "insemination"       # تلقيح
    EVENT_PREGNANCY_CHECK   = "pregnancy_check"    # جس
    EVENT_PREGNANCY_CONFIRM = "pregnancy_confirm"  # جس تأكيد
    EVENT_DRYING            = "drying"             # تجفيف
    EVENT_WAITING           = "waiting"            # انتظار ولادة
    EVENT_CALVING           = "calving"            # ولادة
    EVENT_ABORTION          = "abortion"           # طراحة
    EVENT_WEANING           = "weaning"            # فطام

    RESULT_PREGNANT     = "pregnant"      # عشار
    RESULT_NOT_PREGNANT = "not_pregnant"  # مش عشار

    RESULT_LABELS = {
        RESULT_PREGNANT:     "عشار",
        RESULT_NOT_PREGNANT: "مش عشار",
    }

    # HERD-8: where an insemination's cost came from.
    COST_STOCK = "stock"   # semen straws drawn from inventory
    COST_CASH = "cash"     # paid from a treasury (e.g. to the vet)
    COST_LABELS = {COST_STOCK: "من المخزون", COST_CASH: "نقدي"}

    id = db.Column(db.Integer, primary_key=True)
    cow_id = db.Column(
        db.Integer, db.ForeignKey("cows.id"),
        nullable=False, index=True,
    )
    event_type = db.Column(db.String(30), nullable=False, index=True)
    event_date = db.Column(
        db.Date, nullable=False, default=date.today, index=True,
    )
    # Meaningful only for `pregnancy_check`; else NULL.
    result = db.Column(db.String(20), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    # Set when the event mirrors a Birth (event_type='calving'); else NULL.
    birth_id = db.Column(
        db.Integer, db.ForeignKey("births.id"),
        nullable=True, index=True,
    )
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False,
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True,
    )

    cow = db.relationship("Cow", backref=db.backref(
        "breeding_events",
        order_by="BreedingEvent.event_date.desc()",
        lazy="dynamic",
    ))
    birth = db.relationship("Birth")

    # HERD-8: insemination cost (NULL source = no cost recorded).
    cost_source = db.Column(db.String(10), nullable=True)
    cost_amount = db.Column(db.Numeric(12, 2), nullable=True)
    cost_ingredient_id = db.Column(
        db.Integer, db.ForeignKey("ingredients.id"), nullable=True,
    )
    cost_qty = db.Column(db.Numeric(10, 3), nullable=True)
    cost_expense_id = db.Column(
        db.Integer, db.ForeignKey("expenses.id"), nullable=True,
    )
    cost_ingredient = db.relationship("Ingredient")
    cost_expense = db.relationship("Expense")

    @property
    def cost_source_label(self) -> str:
        return self.COST_LABELS.get(self.cost_source, "—")

    @property
    def event_label(self) -> str:
        return event_type_label(self.event_type)

    @property
    def result_label(self) -> str | None:
        if not self.result:
            return None
        return self.RESULT_LABELS.get(self.result, self.result)


class BreedingStatusChange(db.Model):
    """Typed audit trail for `Cow.breeding_status`. Mirrors
    `CowMovement`'s shape (from → to, who, when). Every confirm
    from the breeding-event flow — and every retro-edit — writes
    one row here.
    """
    __tablename__ = "breeding_status_changes"

    id = db.Column(db.Integer, primary_key=True)
    cow_id = db.Column(
        db.Integer, db.ForeignKey("cows.id"),
        nullable=False, index=True,
    )
    from_status = db.Column(db.String(30), nullable=True)
    to_status = db.Column(db.String(30), nullable=False)
    changed_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True,
    )
    changed_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True,
    )
    # Set when triggered by confirming a BreedingEvent's suggestion;
    # NULL for retro edits.
    event_id = db.Column(
        db.Integer, db.ForeignKey("breeding_events.id"),
        nullable=True, index=True,
    )
    # Free-text reason (mainly for retro edits — "corrected the
    # confirmation of 2026-08-15 — was inseminated, actually
    # heifer_grown").
    reason = db.Column(db.String(255), nullable=True)

    cow = db.relationship("Cow", backref=db.backref(
        "status_changes",
        order_by="BreedingStatusChange.changed_at.desc()",
        lazy="dynamic",
    ))
    event = db.relationship("BreedingEvent")
    changed_by = db.relationship("User")


# ==================== HERD-3: status list ====================

class BreedingStatus(db.Model):
    """HERD-3: the client's status list (STATUS.txt + "milking").

    Linked to `Cow.breeding_status` by `code` string (no FK), the
    same way IngredientCategory links by name — so the history
    table and the suggestion table keep their plain string columns.
    `code` never changes once created; `name` is editable.
    """
    __tablename__ = "breeding_statuses"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(60), nullable=False)
    sort_order = db.Column(
        db.Integer, nullable=False, default=0, server_default="0",
    )
    is_active = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1",
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<BreedingStatus {self.code}>"

    @property
    def cow_count(self) -> int:
        return Cow.query.filter_by(
            breeding_status=self.code,
            status=Cow.STATUS_ACTIVE,
            is_archived=False,
        ).count()


def breeding_status_map() -> dict[str, str]:
    """{code: name} for every status, inactive included (so old
    history rows still get a label). Cached for the request — the
    herd list renders hundreds of rows."""
    from flask import g, has_app_context
    if has_app_context() and "breeding_status_map" in g:
        return g.breeding_status_map
    mapping = {s.code: s.name for s in BreedingStatus.query.all()}
    if has_app_context():
        g.breeding_status_map = mapping
    return mapping


def breeding_status_choices() -> list[tuple[str, str]]:
    """[(code, name)] of active statuses, in list order — for pickers."""
    rows = (
        BreedingStatus.query.filter_by(is_active=True)
        .order_by(BreedingStatus.sort_order, BreedingStatus.id)
        .all()
    )
    return [(s.code, s.name) for s in rows]


def breeding_status_label(code: str | None) -> str:
    """Arabic name for a status code; the code itself if unknown,
    an em-dash if empty."""
    if not code:
        return "—"
    return breeding_status_map().get(code, code)


# ==================== HERD-5: event types ====================

class BreedingEventType(db.Model):
    """HERD-5: an event type (إجراء) and what it does to the cow.

    Recording an event of this type pre-fills the event form with
    `target_group` and the status (`target_status`, or for types with
    a result — pregnancy checks — `status_if_positive` /
    `status_if_negative`). The user can change both before saving.

    Linked to `BreedingEvent.event_type` by `code` string, like the
    statuses. System types (the seeded ones) keep their code and can't
    be disabled — `calving` in particular is wired to the birth form.
    """
    __tablename__ = "breeding_event_types"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(60), nullable=False)
    sort_order = db.Column(
        db.Integer, nullable=False, default=0, server_default="0",
    )
    is_active = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1",
    )
    is_system = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0",
    )
    target_group_id = db.Column(
        db.Integer, db.ForeignKey("cattle_groups.id"), nullable=True,
    )
    target_status = db.Column(db.String(30), nullable=True)
    has_result = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0",
    )
    status_if_positive = db.Column(db.String(30), nullable=True)
    status_if_negative = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    target_group = db.relationship("CattleGroup")

    def __repr__(self) -> str:
        return f"<BreedingEventType {self.code}>"

    def default_status(self, result: str | None = None) -> str | None:
        """Status this event sets. For result types the result picks
        which one; with no result yet, nothing."""
        if self.has_result:
            if result == BreedingEvent.RESULT_PREGNANT:
                return self.status_if_positive
            if result == BreedingEvent.RESULT_NOT_PREGNANT:
                return self.status_if_negative
            return None
        return self.target_status


def event_type_map() -> dict[str, str]:
    """{code: name} for every event type, inactive included. Cached
    for the request."""
    from flask import g, has_app_context
    if has_app_context() and "event_type_map" in g:
        return g.event_type_map
    mapping = {t.code: t.name for t in BreedingEventType.query.all()}
    if has_app_context():
        g.event_type_map = mapping
    return mapping


def active_event_types() -> list[BreedingEventType]:
    return (
        BreedingEventType.query.filter_by(is_active=True)
        .order_by(BreedingEventType.sort_order, BreedingEventType.id)
        .all()
    )


def event_type_label(code: str | None) -> str:
    if not code:
        return "—"
    return event_type_map().get(code, code)


# ==================== HERD-2 Part 3 (PHASE 35): herd valuation ====================


class CowValuation(db.Model):
    """Historical record of every revaluation of a single cow.
    `Cow.current_value` is the running snapshot of the latest row
    for the same cow; this table is the audit trail.

    On insert, a JE is posted (DR 1400 / CR 4095 for a gain, or
    the reverse for a loss) with `source_type='CowValuation'`,
    `source_id=this.id` — so the journal_detail page's "قيد
    المصدر" link walks straight back to the cow that was
    revalued."""
    __tablename__ = "cow_valuations"

    id = db.Column(db.Integer, primary_key=True)
    cow_id = db.Column(
        db.Integer, db.ForeignKey("cows.id"),
        nullable=False, index=True,
    )
    valuation_date = db.Column(
        db.Date, nullable=False, default=date.today, index=True,
    )
    value = db.Column(db.Numeric(12, 2), nullable=False)
    # Snapshot of the cow's current_value at the moment of this
    # revaluation. Lets the historical report reconstruct
    # "value at date T" without walking backward through JEs.
    prior_value = db.Column(
        db.Numeric(12, 2), nullable=False, default=Decimal("0"),
        server_default="0",
    )
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False,
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True,
    )

    cow = db.relationship("Cow", backref=db.backref(
        "valuations",
        order_by="CowValuation.valuation_date.desc()",
        lazy="dynamic",
    ))
    created_by = db.relationship("User")

    @property
    def delta(self) -> Decimal:
        """Positive = gain, negative = loss."""
        return (Decimal(str(self.value or 0))
                - Decimal(str(self.prior_value or 0)))
