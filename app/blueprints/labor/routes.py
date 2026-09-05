from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, or_

from app.extensions import db
from app.forms.labor import (
    LeaveRejectForm,
    LeaveRequestForm,
    WorkerForm,
    WorkerPaymentForm,
)
from app.models.finance import TreasuryAccount, Expense
from app.models.labor import Attendance, LeaveRequest, Worker, WorkerPayment
from app.utils import accounts as acc
from app.utils.audit import log_action
from app.utils.decorators import admin_required, write_required
from app.utils.reports import excel_response, pdf_from_current_page


# ---------- HR-1 (PHASE 32) helpers ----------

def _first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _prev_month(d: date) -> date:
    if d.month == 1:
        return date(d.year - 1, 12, 1)
    return date(d.year, d.month - 1, 1)


def _parse_target_month(raw: str | None) -> date:
    """Parse ?month=YYYY-MM (from the dropdown or the payment form) into
    the 1st of that month. Falls back to the current month."""
    if raw:
        try:
            y, m = raw.split("-")
            return date(int(y), int(m), 1)
        except (ValueError, TypeError):
            pass
    return _first_of_month(date.today())


_AR_MONTHS = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]


def _month_label(d: date) -> str:
    return f"{_AR_MONTHS[d.month - 1]} {d.year}"


def _month_options(back: int = 24) -> list[dict]:
    """The last `back` calendar months + this one, newest first."""
    today = _first_of_month(date.today())
    out = []
    for i in range(back + 1):
        m = today
        for _ in range(i):
            m = _prev_month(m)
        out.append({"value": m.strftime("%Y-%m"), "label": _month_label(m)})
    return out

bp = Blueprint("labor", __name__, template_folder="../../templates/labor")


@bp.route("/")
@login_required
def list_workers():
    workers = Worker.query.filter_by(is_archived=False).order_by(Worker.name).all()
    return render_template("labor/list.html", workers=workers)


@bp.route("/new", methods=["GET", "POST"])
@login_required
@write_required
def create_worker():
    form = WorkerForm()
    if form.validate_on_submit():
        w = Worker(
            name=form.name.data.strip(),
            phone=(form.phone.data or "").strip() or None,
            wage_type=form.wage_type.data,
            rate=Decimal(str(form.rate.data)),
            closing_day=int(form.closing_day.data or 1),
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(w)
        db.session.flush()
        log_action("worker_created", "Worker", w.id)
        db.session.commit()
        flash(f"تم إضافة العامل {w.name}.", "success")
        return redirect(url_for("labor.worker_detail", worker_id=w.id))
    return render_template("labor/form.html", form=form, mode="create")


@bp.route("/<int:worker_id>")
@login_required
def worker_detail(worker_id: int):
    worker = db.session.get(Worker, worker_id)
    if not worker or worker.is_archived:
        abort(404)

    today = date.today()
    month_start = today.replace(day=1)
    attendances = (
        Attendance.query.filter(
            Attendance.worker_id == worker.id,
            Attendance.attendance_date >= month_start,
        )
        .order_by(Attendance.attendance_date.desc())
        .all()
    )
    # PHASE 15 (YAS-HR-1): full payment history with running total, plus
    # advances/salaries splits for the two new headline cards.
    history_asc = (
        WorkerPayment.query.filter_by(worker_id=worker.id, is_archived=False)
        .order_by(WorkerPayment.payment_date, WorkerPayment.id)
        .all()
    )
    running = Decimal("0")
    history = []
    for p in history_asc:
        running += Decimal(str(p.amount))
        history.append({"p": p, "running": running.quantize(Decimal("0.01"))})
    history.reverse()  # newest first for display

    total_advances = sum(
        (Decimal(str(p.amount)) for p in history_asc
         if p.reason == WorkerPayment.REASON_ADVANCE),
        Decimal("0"),
    )
    total_salaries = sum(
        (Decimal(str(p.amount)) for p in history_asc
         if p.reason == WorkerPayment.REASON_SALARY),
        Decimal("0"),
    )

    # ---------- HR-1 (PHASE 32): monthly statement ----------
    selected_month = _parse_target_month(request.args.get("month"))
    period_start, period_end = worker.month_window(selected_month)
    # Payments in this month bucket. `target_month == selected_month`
    # covers explicitly-tagged rows; the OR-NULL branch is a belt for
    # legacy rows without a backfill (also see the migration).
    period_payments = (
        WorkerPayment.query
        .filter(WorkerPayment.worker_id == worker.id,
                WorkerPayment.is_archived.is_(False))
        .filter(or_(
            WorkerPayment.target_month == selected_month,
            and_(WorkerPayment.target_month.is_(None),
                 WorkerPayment.payment_date >= selected_month,
                 WorkerPayment.payment_date < _next_month(selected_month)),
        ))
        .order_by(WorkerPayment.payment_date, WorkerPayment.id)
        .all()
    )
    period_earned = worker.earned_for_month(selected_month)
    period_paid = sum(
        (Decimal(str(p.amount)) for p in period_payments), Decimal("0"),
    )
    period_balance = (period_earned - period_paid).quantize(Decimal("0.01"))

    # Carry-forward: if the prior month's balance is negative
    # (over-paid), surface that as a note on this month.
    prior_month = _prev_month(selected_month)
    prior_earned = worker.earned_for_month(prior_month)
    prior_start, prior_end = worker.month_window(prior_month)
    prior_paid = sum(
        (Decimal(str(p.amount)) for p in
            WorkerPayment.query
            .filter(WorkerPayment.worker_id == worker.id,
                    WorkerPayment.is_archived.is_(False))
            .filter(or_(
                WorkerPayment.target_month == prior_month,
                and_(WorkerPayment.target_month.is_(None),
                     WorkerPayment.payment_date >= prior_month,
                     WorkerPayment.payment_date < _next_month(prior_month)),
            )).all()
        ),
        Decimal("0"),
    )
    prior_balance = prior_earned - prior_paid
    prior_carry = prior_balance if prior_balance < 0 else Decimal("0")

    payment_form = WorkerPaymentForm()
    payment_form.account_id.choices = acc.active_choices()

    # Leave requests for this worker (newest start_date first via backref order)
    leaves = worker.leave_requests.all()
    leave_form = LeaveRequestForm()
    return render_template(
        "labor/detail.html",
        worker=worker,
        attendances=attendances,
        history=history,
        total_advances=total_advances,
        total_salaries=total_salaries,
        payment_form=payment_form,
        leaves=leaves,
        leave_form=leave_form,
        # HR-1 monthly-statement context
        selected_month=selected_month.strftime("%Y-%m"),
        selected_month_label=_month_label(selected_month),
        month_options=_month_options(),
        period_start=period_start, period_end=period_end,
        period_payments=period_payments,
        period_earned=period_earned,
        period_paid=period_paid,
        period_balance=period_balance,
        prior_carry=prior_carry,
        prior_month_label=_month_label(prior_month),
        print_mode=bool(request.args.get("print")),
    )


@bp.route("/<int:worker_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
def edit_worker(worker_id: int):
    worker = db.session.get(Worker, worker_id)
    if not worker or worker.is_archived:
        abort(404)
    form = WorkerForm(obj=worker)
    if form.validate_on_submit():
        worker.name = form.name.data.strip()
        worker.phone = (form.phone.data or "").strip() or None
        worker.wage_type = form.wage_type.data
        worker.rate = Decimal(str(form.rate.data))
        worker.closing_day = int(form.closing_day.data or 1)
        worker.notes = form.notes.data
        log_action("worker_updated", "Worker", worker.id)
        db.session.commit()
        flash("تم تحديث بيانات العامل.", "success")
        return redirect(url_for("labor.worker_detail", worker_id=worker.id))
    return render_template("labor/form.html", form=form, mode="edit", worker=worker)


# ---------- Daily attendance grid ----------
@bp.route("/attendance", methods=["GET", "POST"])
@login_required
@write_required
def daily_attendance():
    day_str = request.args.get("day")
    day = date.fromisoformat(day_str) if day_str else date.today()
    workers = Worker.query.filter_by(is_archived=False).order_by(Worker.name).all()

    if request.method == "POST":
        for w in workers:
            batches_raw = request.form.get(f"batches_{w.id}", "").strip()
            absent = request.form.get(f"absent_{w.id}") == "1"
            existing = Attendance.query.filter_by(worker_id=w.id, attendance_date=day).first()

            batches = 0
            if not absent and batches_raw:
                try:
                    batches = int(batches_raw)
                except ValueError:
                    batches = 0

            if existing:
                existing.is_absent = absent
                existing.batches_worked = 0 if absent else batches
            elif absent or batches > 0:
                db.session.add(
                    Attendance(
                        worker_id=w.id,
                        attendance_date=day,
                        batches_worked=0 if absent else batches,
                        is_absent=absent,
                        created_by_id=current_user.id,
                    )
                )
        log_action("attendance_saved", "Attendance", 0, details=str(day))
        db.session.commit()
        flash(f"تم حفظ حضور يوم {day}.", "success")
        return redirect(url_for("labor.daily_attendance", day=day.isoformat()))

    # Build the grid
    grid = []
    for w in workers:
        att = Attendance.query.filter_by(worker_id=w.id, attendance_date=day).first()
        grid.append({"worker": w, "att": att})

    return render_template("labor/attendance.html", grid=grid, day=day)


# ---------- Payment ----------
# ---------- TC-8.4: consolidated labor report ----------
def _labor_report_rows(date_from, date_to):
    workers = Worker.query.filter_by(is_archived=False).order_by(Worker.name).all()
    rows = []
    for w in workers:
        atts = (
            Attendance.query
            .filter(
                Attendance.worker_id == w.id,
                Attendance.attendance_date >= date_from,
                Attendance.attendance_date <= date_to,
            )
            .all()
        )
        present_days = sum(1 for a in atts if not a.is_absent)
        absent_days = sum(1 for a in atts if a.is_absent)
        total_batches = sum((a.batches_worked or 0) for a in atts if not a.is_absent)
        earned = w.earned_between(date_from, date_to)
        paid = w.paid_between(date_from, date_to)
        balance = earned - paid
        rows.append({
            "worker": w,
            "present_days": present_days,
            "absent_days": absent_days,
            "total_batches": total_batches,
            "earned": earned,
            "paid": paid,
            "balance": balance,
        })
    return rows


@bp.route("/report")
@login_required
def report():
    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = date.fromisoformat(to) if to else today
    rows = _labor_report_rows(d_from, d_to)
    totals = {
        "present_days": sum(r["present_days"] for r in rows),
        "absent_days": sum(r["absent_days"] for r in rows),
        "total_batches": sum(r["total_batches"] for r in rows),
        "earned": sum((r["earned"] for r in rows), Decimal("0")),
        "paid": sum((r["paid"] for r in rows), Decimal("0")),
        "balance": sum((r["balance"] for r in rows), Decimal("0")),
    }
    return render_template("labor/report.html", rows=rows, totals=totals,
                           date_from=d_from, date_to=d_to)


@bp.route("/report/excel")
@login_required
def report_excel():
    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = date.fromisoformat(to) if to else today
    rows = _labor_report_rows(d_from, d_to)
    data = [
        [
            r["worker"].name, r["worker"].wage_label, float(r["worker"].rate),
            r["present_days"], r["absent_days"], r["total_batches"],
            float(r["earned"]), float(r["paid"]), float(r["balance"]),
        ] for r in rows
    ]
    return excel_response(
        "تقرير العمالة",
        ["العامل", "نوع الأجر", "السعر", "أيام حضور", "أيام غياب", "إجمالي الحلبات",
         "المستحق", "المدفوع", "المتبقي"],
        data,
        f"labor_report_{d_from}_{d_to}.xlsx",
    )


@bp.route("/<int:worker_id>/pay", methods=["POST"])
@login_required
@write_required
def record_payment(worker_id: int):
    worker = db.session.get(Worker, worker_id)
    if not worker or worker.is_archived:
        abort(404)
    form = WorkerPaymentForm()
    form.account_id.choices = acc.active_choices()
    if not form.validate_on_submit():
        for _, errors in form.errors.items():
            for e in errors:
                flash(e, "error")
        return redirect(url_for("labor.worker_detail", worker_id=worker.id))

    account = db.session.get(TreasuryAccount, form.account_id.data)
    if not account or account.is_archived:
        flash("الحساب غير صالح.", "error")
        return redirect(url_for("labor.worker_detail", worker_id=worker.id))

    # HR-1 (PHASE 32): the month bucket. Form ships 'YYYY-MM' string
    # (default = current month); parse to a first-of-month date. On
    # a bad/blank string the parser falls back to today's month.
    target_month = _parse_target_month(form.target_month.data)

    payment = WorkerPayment(
        worker_id=worker.id,
        amount=Decimal(str(form.amount.data)),
        payment_date=form.payment_date.data,
        target_month=target_month,
        reason=form.reason.data,
        account_id=account.id,
        notes=form.notes.data,
        created_by_id=current_user.id,
    )
    db.session.add(payment)
    db.session.flush()

    # TREASURY: this is the cash event — the mirror Expense below posts nothing,
    # or the account would be debited twice for one wage payment.
    acc.money_out(
        account, payment.amount, payment.payment_date,
        ref_type="worker_payment", ref_id=payment.id, user_id=current_user.id,
        notes=f"دفعة للعامل {worker.name}",
    )
    # ACCOUNTING: post the wage as an expense hit
    from app.services import autoposting
    autoposting.on_worker_payment(payment, account, created_by=current_user.id)

    # US-6.2 BR: auto-record as expense
    db.session.add(
        Expense(
            category=Expense.CAT_WORKER_WAGE,
            amount=payment.amount,
            expense_date=payment.payment_date,
            description=f"دفعة للعامل {worker.name} ({payment.reason_label})",
            ref_type="worker_payment",
            ref_id=payment.id,
            account_id=account.id,
            created_by_id=current_user.id,
        )
    )

    log_action("worker_payment", "WorkerPayment", payment.id,
               details=f"worker={worker.id} amount={payment.amount}")
    db.session.commit()
    flash(f"تم تسجيل دفعة {payment.amount} للعامل {worker.name}.", "success")
    return redirect(url_for("labor.worker_detail", worker_id=worker.id))


# ==================== PHASE 15 (YAS-HR-1) — leave requests ====================

def _leave_or_404(leave_id: int) -> LeaveRequest:
    req = db.session.get(LeaveRequest, leave_id)
    if req is None:
        abort(404)
    return req


@bp.route("/<int:worker_id>/leaves/new", methods=["POST"])
@login_required
@write_required
def submit_leave(worker_id: int):
    """Any logged-in user can submit a leave request on behalf of a worker.
    Approvals are admin-only (see below)."""
    worker = db.session.get(Worker, worker_id)
    if not worker or worker.is_archived:
        abort(404)
    form = LeaveRequestForm()
    if not form.validate_on_submit():
        for _, errors in form.errors.items():
            for e in errors:
                flash(e, "error")
        return redirect(url_for("labor.worker_detail", worker_id=worker.id))
    if form.end_date.data < form.start_date.data:
        flash("تاريخ النهاية قبل تاريخ البداية.", "error")
        return redirect(url_for("labor.worker_detail", worker_id=worker.id))

    req = LeaveRequest(
        worker_id=worker.id,
        start_date=form.start_date.data,
        end_date=form.end_date.data,
        reason=(form.reason.data or "").strip() or None,
        submitted_by_id=current_user.id,
    )
    db.session.add(req)
    db.session.flush()
    log_action("leave_submitted", "LeaveRequest", req.id,
               details=f"worker={worker.id} {req.start_date}..{req.end_date}")
    db.session.commit()
    flash("اتقدّم طلب الإجازة — منتظر اعتماد الإدارة.", "success")
    return redirect(url_for("labor.worker_detail", worker_id=worker.id))


@bp.route("/leaves/<int:leave_id>/approve", methods=["POST"])
@admin_required
def approve_leave(leave_id: int):
    req = _leave_or_404(leave_id)
    if req.status != LeaveRequest.STATUS_PENDING:
        flash("الطلب اتحوّل خلاص — مش ممكن تعتمده تاني.", "error")
    else:
        req.status = LeaveRequest.STATUS_APPROVED
        req.decided_by_id = current_user.id
        req.decided_at = datetime.utcnow()
        log_action("leave_approved", "LeaveRequest", req.id)
        db.session.commit()
        flash(f"اتعتمد طلب إجازة {req.worker.name}.", "success")
    return redirect(request.referrer or
                    url_for("labor.worker_detail", worker_id=req.worker_id))


@bp.route("/leaves/<int:leave_id>/reject", methods=["POST"])
@admin_required
def reject_leave(leave_id: int):
    req = _leave_or_404(leave_id)
    if req.status != LeaveRequest.STATUS_PENDING:
        flash("الطلب اتحوّل خلاص — مش ممكن ترفضه تاني.", "error")
        return redirect(request.referrer or
                        url_for("labor.worker_detail", worker_id=req.worker_id))
    # decision_note is optional; capture whatever came in as free text
    note = (request.form.get("decision_note") or "").strip() or None
    req.status = LeaveRequest.STATUS_REJECTED
    req.decided_by_id = current_user.id
    req.decided_at = datetime.utcnow()
    req.decision_note = note
    log_action("leave_rejected", "LeaveRequest", req.id,
               details=f"note={note or ''}")
    db.session.commit()
    flash(f"اترفض طلب إجازة {req.worker.name}.", "warning")
    return redirect(request.referrer or
                    url_for("labor.worker_detail", worker_id=req.worker_id))


@bp.route("/leaves")
@admin_required
def leaves_queue():
    """Admin queue: everything still pending across every worker, plus the
    latest 30 already-decided rows so admins can audit."""
    pending = (LeaveRequest.query
               .filter_by(status=LeaveRequest.STATUS_PENDING)
               .order_by(LeaveRequest.submitted_at.desc()).all())
    decided = (LeaveRequest.query
               .filter(LeaveRequest.status != LeaveRequest.STATUS_PENDING)
               .order_by(LeaveRequest.decided_at.desc())
               .limit(30).all())
    return render_template("labor/leaves_queue.html",
                           pending=pending, decided=decided,
                           reject_form=LeaveRejectForm())


# ---------- HR-1 (PHASE 32): monthly-statement PDF ----------
@bp.route("/<int:worker_id>/statement.pdf")
@login_required
def worker_statement_pdf(worker_id: int):
    """Render the same worker_detail page in print mode and hand it
    to the shared Chromium PDF pipeline."""
    worker = db.session.get(Worker, worker_id)
    if not worker or worker.is_archived:
        abort(404)
    month = request.args.get("month") or date.today().strftime("%Y-%m")
    target_url = url_for(
        "labor.worker_detail",
        worker_id=worker.id, month=month, **{"print": 1},
        _external=True,
    )
    return pdf_from_current_page(
        target_url, f"worker_{worker.id}_{month}.pdf",
    )
