from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db


class Worker(db.Model):
    __tablename__ = "workers"

    WAGE_PER_BATCH = "per_batch"
    WAGE_DAILY = "daily"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    phone = db.Column(db.String(30), nullable=True)
    wage_type = db.Column(db.String(20), nullable=False, default=WAGE_PER_BATCH)
    rate = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0"))
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    attendances = db.relationship("Attendance", back_populates="worker", lazy="dynamic")
    payments = db.relationship("WorkerPayment", back_populates="worker", lazy="dynamic")

    @property
    def wage_label(self) -> str:
        return "بالحلبة" if self.wage_type == self.WAGE_PER_BATCH else "يومي"

    def earned_between(self, start: date, end: date) -> Decimal:
        atts = (
            db.session.query(Attendance)
            .filter(
                Attendance.worker_id == self.id,
                Attendance.attendance_date >= start,
                Attendance.attendance_date <= end,
                Attendance.is_absent.is_(False),
            )
            .all()
        )
        total = Decimal("0")
        for a in atts:
            if self.wage_type == self.WAGE_PER_BATCH:
                total += (a.batches_worked or 0) * self.rate
            else:
                total += self.rate  # daily
        return total.quantize(Decimal("0.01"))

    def paid_between(self, start: date, end: date) -> Decimal:
        val = (
            db.session.query(func.coalesce(func.sum(WorkerPayment.amount), 0))
            .filter(
                WorkerPayment.worker_id == self.id,
                WorkerPayment.payment_date >= start,
                WorkerPayment.payment_date <= end,
                WorkerPayment.is_archived.is_(False),
            )
            .scalar()
        )
        return Decimal(str(val or 0))

    @property
    def month_earned(self) -> Decimal:
        today = date.today()
        start = today.replace(day=1)
        return self.earned_between(start, today)

    @property
    def month_paid(self) -> Decimal:
        today = date.today()
        start = today.replace(day=1)
        return self.paid_between(start, today)

    @property
    def month_balance(self) -> Decimal:
        return self.month_earned - self.month_paid


class Attendance(db.Model):
    __tablename__ = "attendances"

    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"), nullable=False, index=True)
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    batches_worked = db.Column(db.Integer, nullable=False, default=0)
    is_absent = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    worker = db.relationship("Worker", back_populates="attendances")

    __table_args__ = (
        db.UniqueConstraint("worker_id", "attendance_date", name="uq_attendance_worker_date"),
    )


class WorkerPayment(db.Model):
    __tablename__ = "worker_payments"

    REASON_ADVANCE = "advance"
    REASON_SALARY = "salary"

    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    reason = db.Column(db.String(20), nullable=False, default=REASON_ADVANCE)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    worker = db.relationship("Worker", back_populates="payments")

    @property
    def reason_label(self) -> str:
        return "سلفة" if self.reason == self.REASON_ADVANCE else "دفعة من الراتب"
