from calendar import monthrange
from datetime import date, datetime, timedelta
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
    # HR-1 (PHASE 32): the day-of-month where this worker's pay cycle
    # closes. 1 = calendar month (1st → last day). 10 = 11th of prior
    # month → 10th of current month. Capped at 28 so every month has
    # the day.
    closing_day = db.Column(
        db.Integer, nullable=False, default=1, server_default="1",
    )
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

    # ---------- HR-1 (PHASE 32): custom monthly cycles ----------

    def month_window(self, target_month: date) -> tuple[date, date]:
        """Earning window for a month bucket, respecting `closing_day`.

        `target_month` is the 1st of the month the pay is booked
        against (e.g. `2026-08-01` means "شهر أغسطس"). Returns
        (start, end) inclusive.

          closing_day = 1  → [target_month, last day of target_month]
          closing_day = 10 → [prior_month.day(11), target_month.day(10)]

        The prior-month math is safe even for January (goes back to
        December of the previous year).
        """
        cd = int(self.closing_day or 1)
        cd = max(1, min(28, cd))   # defensive clamp
        y, m = target_month.year, target_month.month
        if cd == 1:
            last_day = monthrange(y, m)[1]
            return date(y, m, 1), date(y, m, last_day)
        # Non-default closing_day: [prior_month.day(cd+1), target.day(cd)]
        end = date(y, m, cd)
        prior = end - timedelta(days=cd)   # somewhere in the prior month
        start = date(prior.year, prior.month, cd + 1)
        return start, end

    def earned_for_month(self, target_month: date) -> Decimal:
        """Sum of earnings across the target month's payroll window."""
        s, e = self.month_window(target_month)
        return self.earned_between(s, e)


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
    # HR-1 (PHASE 32): the month this payment belongs to. Stored as
    # the 1st of the target month (e.g. `2026-08-01` means شهر أغسطس).
    # Nullable: legacy rows fall back to `payment_date`'s calendar
    # month, and the worker_detail query has an `IS NULL` branch.
    target_month = db.Column(db.Date, nullable=True, index=True)
    # TREASURY: which account the money left (nullable for pre-accounts rows)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    worker = db.relationship("Worker", back_populates="payments")
    account = db.relationship("TreasuryAccount")

    @property
    def reason_label(self) -> str:
        return "سلفة" if self.reason == self.REASON_ADVANCE else "دفعة من الراتب"


class LeaveRequest(db.Model):
    """PHASE 15 (YAS-HR-1). A worker asking to take days off.

    - Status is `pending` when submitted, then flipped by an admin to
      `approved` or `rejected` (with an optional decision_note).
    - Not a money event, so autoposting doesn't touch it. If a paid
      leave later has to cut a WorkerPayment on approval, create the
      payment from the approval handler — existing autoposting picks
      it up automatically.
    - Follows the same Check-style status convention (STRING + CHECK
      constraint + STATUS_LABELS dict + status_label property).
    """
    __tablename__ = "leave_requests"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    STATUS_LABELS = {
        STATUS_PENDING: "معلّق",
        STATUS_APPROVED: "معتمد",
        STATUS_REJECTED: "مرفوض",
    }

    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"),
                          nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text, nullable=True)          # what the worker gave as reason

    status = db.Column(db.String(10), nullable=False,
                       default=STATUS_PENDING, server_default="pending",
                       index=True)

    submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    decision_note = db.Column(db.Text, nullable=True)    # required-ish on reject

    worker = db.relationship(
        "Worker",
        backref=db.backref("leave_requests", lazy="dynamic",
                           order_by="LeaveRequest.start_date.desc()"),
    )
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_leave_status",
        ),
        db.CheckConstraint(
            "end_date >= start_date",
            name="ck_leave_dates",
        ),
    )

    @property
    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def days_count(self) -> int:
        return (self.end_date - self.start_date).days + 1
