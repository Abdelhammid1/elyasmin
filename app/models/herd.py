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

    # HERD-2 Part 1: reproductive-cycle statuses. Order chosen so the
    # UI dropdown reads roughly cow-then-heifer.
    BS_INSEMINATED   = "inseminated"    # ملقحة
    BS_PREGNANT      = "pregnant"       # عشار
    BS_DRY           = "dry"            # جفاف
    BS_WAITING       = "waiting"        # انتظار
    BS_NURSING       = "nursing"        # رضيع
    BS_HEIFER_SMALL  = "heifer_small"   # عجلة صغيرة
    BS_HEIFER_GROWN  = "heifer_grown"   # عجلة نامية
    BS_HEIFER_INSEM  = "heifer_insem"   # عجلة ملقحة
    BS_HEIFER_PREG   = "heifer_preg"    # عجلة عشار

    BREEDING_STATUS_LABELS = {
        BS_INSEMINATED:  "ملقحة",
        BS_PREGNANT:     "عشار",
        BS_DRY:          "جفاف",
        BS_WAITING:      "انتظار",
        BS_NURSING:      "رضيع",
        BS_HEIFER_SMALL: "عجلة صغيرة",
        BS_HEIFER_GROWN: "عجلة نامية",
        BS_HEIFER_INSEM: "عجلة ملقحة",
        BS_HEIFER_PREG:  "عجلة عشار",
    }

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
        """HERD-2 Part 1: Arabic label for the reproductive status,
        or None when unset (males, calves, unrecorded cows)."""
        if not self.breeding_status:
            return None
        return self.BREEDING_STATUS_LABELS.get(
            self.breeding_status, self.breeding_status
        )

    @property
    def season_count(self) -> int:
        """HERD-2 Part 2: total calvings for this cow. Cheap COUNT on
        the births table — Birth is authoritative for calving events
        (breeding_events also has calving rows for each Birth, but
        Birth is the older, indexed source and doesn't require any
        join). Auto-updates immediately after `create_birth`
        commits a new row."""
        from sqlalchemy import func as _func
        return (
            db.session.query(_func.count(Birth.id))
            .filter(Birth.mother_id == self.id)
            .scalar() or 0
        )


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

    EVENT_INSEMINATION    = "insemination"     # تلقيح
    EVENT_PREGNANCY_CHECK = "pregnancy_check"  # جس
    EVENT_DRYING          = "drying"           # تجفيف
    EVENT_WAITING         = "waiting"          # انتظار
    EVENT_CALVING         = "calving"          # ولادة
    EVENT_WEANING         = "weaning"          # فطام

    EVENT_LABELS = {
        EVENT_INSEMINATION:    "تلقيح",
        EVENT_PREGNANCY_CHECK: "جس",
        EVENT_DRYING:          "تجفيف",
        EVENT_WAITING:         "انتظار",
        EVENT_CALVING:         "ولادة",
        EVENT_WEANING:         "فطام",
    }

    RESULT_PREGNANT     = "pregnant"      # عشار
    RESULT_NOT_PREGNANT = "not_pregnant"  # مش عشار

    RESULT_LABELS = {
        RESULT_PREGNANT:     "عشار",
        RESULT_NOT_PREGNANT: "مش عشار",
    }

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

    @property
    def event_label(self) -> str:
        return self.EVENT_LABELS.get(self.event_type, self.event_type)

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


class EventStatusSuggestion(db.Model):
    """The editable config mapping the suggest-and-confirm flow
    reads on every event. Row per (event_type, event_result?,
    suggested_status) triple; one row per `event_type` (+
    optional discriminator on the result for `pregnancy_check`)
    is flagged `is_default=True` so the UI preselects it.

    Ships seeded from the migration; a proper admin CRUD is
    deferred to a follow-up ticket. Until then, edits are a
    manual UPDATE on the table.
    """
    __tablename__ = "event_status_suggestions"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(30), nullable=False, index=True)
    # NULL when the event has no discriminator (every non-preg-check
    # event). For pregnancy_check, holds 'pregnant' or 'not_pregnant'.
    event_result = db.Column(db.String(20), nullable=True, index=True)
    suggested_status = db.Column(db.String(30), nullable=False)
    is_default = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0",
    )
    sort_order = db.Column(
        db.Integer, nullable=False, default=0, server_default="0",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "event_type", "event_result", "suggested_status",
            name="uq_esm_event_status",
        ),
    )


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
