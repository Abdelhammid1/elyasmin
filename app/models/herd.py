from datetime import datetime, date

from app.extensions import db


class CattleGroup(db.Model):
    __tablename__ = "cattle_groups"

    TYPE_MILK = "milk"
    TYPE_DRY = "dry"
    TYPE_PRE_BIRTH = "pre_birth"
    TYPE_NURSING = "nursing"
    TYPE_FATTENING = "fattening"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    type = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    cows = db.relationship("Cow", back_populates="group", lazy="dynamic")

    @property
    def active_count(self) -> int:
        return self.cows.filter_by(status=Cow.STATUS_ACTIVE, is_archived=False).count()


class Cow(db.Model):
    __tablename__ = "cows"

    GENDER_FEMALE = "female"
    GENDER_MALE = "male"

    STATUS_ACTIVE = "active"
    STATUS_SOLD = "sold"
    STATUS_DEAD = "dead"

    id = db.Column(db.Integer, primary_key=True)
    ear_tag = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=True)
    date_of_birth = db.Column(db.Date, nullable=True)
    gender = db.Column(db.String(10), nullable=False, default=GENDER_FEMALE)

    group_id = db.Column(db.Integer, db.ForeignKey("cattle_groups.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE, index=True)
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
