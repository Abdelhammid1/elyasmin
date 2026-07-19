from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.labor import WorkerForm, WorkerPaymentForm
from app.models.finance import Expense
from app.models.labor import Attendance, Worker, WorkerPayment
from app.utils.audit import log_action
from app.utils.reports import excel_response

bp = Blueprint("labor", __name__, template_folder="../../templates/labor")


@bp.route("/")
@login_required
def list_workers():
    workers = Worker.query.filter_by(is_archived=False).order_by(Worker.name).all()
    return render_template("labor/list.html", workers=workers)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_worker():
    form = WorkerForm()
    if form.validate_on_submit():
        w = Worker(
            name=form.name.data.strip(),
            phone=(form.phone.data or "").strip() or None,
            wage_type=form.wage_type.data,
            rate=Decimal(str(form.rate.data)),
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
    payments = (
        WorkerPayment.query.filter_by(worker_id=worker.id, is_archived=False)
        .order_by(WorkerPayment.payment_date.desc())
        .limit(50)
        .all()
    )
    payment_form = WorkerPaymentForm()
    return render_template(
        "labor/detail.html",
        worker=worker,
        attendances=attendances,
        payments=payments,
        payment_form=payment_form,
    )


@bp.route("/<int:worker_id>/edit", methods=["GET", "POST"])
@login_required
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
        worker.notes = form.notes.data
        log_action("worker_updated", "Worker", worker.id)
        db.session.commit()
        flash("تم تحديث بيانات العامل.", "success")
        return redirect(url_for("labor.worker_detail", worker_id=worker.id))
    return render_template("labor/form.html", form=form, mode="edit", worker=worker)


# ---------- Daily attendance grid ----------
@bp.route("/attendance", methods=["GET", "POST"])
@login_required
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
def record_payment(worker_id: int):
    worker = db.session.get(Worker, worker_id)
    if not worker or worker.is_archived:
        abort(404)
    form = WorkerPaymentForm()
    if not form.validate_on_submit():
        for _, errors in form.errors.items():
            for e in errors:
                flash(e, "error")
        return redirect(url_for("labor.worker_detail", worker_id=worker.id))

    payment = WorkerPayment(
        worker_id=worker.id,
        amount=Decimal(str(form.amount.data)),
        payment_date=form.payment_date.data,
        reason=form.reason.data,
        notes=form.notes.data,
        created_by_id=current_user.id,
    )
    db.session.add(payment)
    db.session.flush()

    # US-6.2 BR: auto-record as expense
    db.session.add(
        Expense(
            category=Expense.CAT_WORKER_WAGE,
            amount=payment.amount,
            expense_date=payment.payment_date,
            description=f"دفعة للعامل {worker.name} ({payment.reason_label})",
            ref_type="worker_payment",
            ref_id=payment.id,
            created_by_id=current_user.id,
        )
    )

    log_action("worker_payment", "WorkerPayment", payment.id,
               details=f"worker={worker.id} amount={payment.amount}")
    db.session.commit()
    flash(f"تم تسجيل دفعة {payment.amount} للعامل {worker.name}.", "success")
    return redirect(url_for("labor.worker_detail", worker_id=worker.id))
