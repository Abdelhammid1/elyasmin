"""PHASE 8a — routes for the checks module.

Two mirror sides (received / issued), each with list + create + detail
+ two admin transitions (clear/settle and bounce). Every create + every
transition posts a JE via `app.services.checks`.
"""
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms.checks import (
    BounceCheckForm,
    ClearCheckForm,
    IssuedCheckForm,
    ReceivedCheckForm,
)
from app.models.checks import Check
from app.models.sales import Customer
from app.models.suppliers import Supplier
from app.services import checks as checks_service
from app.services.ledger import LedgerError
from app.utils import accounts as acc
from app.utils.audit import log_action

bp = Blueprint("checks", __name__, template_folder="../../templates/checks")


# ---------- shared helpers ----------
def _load_treasury_choices(form):
    form.treasury_account_id.choices = acc.active_choices()


def _list_scope(direction: str, form_args):
    """Common filter machinery for both received/issued list pages."""
    today = date.today()
    fm = form_args.get("date_from")
    to = form_args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today - timedelta(days=90)
    d_to = date.fromisoformat(to) if to else today + timedelta(days=60)
    status = form_args.get("status") or ""
    q = Check.query.filter(
        Check.direction == direction,
        Check.due_date >= d_from,
        Check.due_date <= d_to,
    )
    if status:
        q = q.filter_by(status=status)
    return q.order_by(Check.due_date.asc(), Check.id.asc()).all(), d_from, d_to, status


# ---------- index ----------
@bp.route("/")
@login_required
def index():
    today = date.today()
    upcoming = (
        Check.query.filter(
            Check.status == Check.STATUS_PENDING,
            Check.due_date <= today + timedelta(days=7),
        )
        .order_by(Check.due_date.asc())
        .limit(20)
        .all()
    )
    return render_template("checks/index.html", upcoming=upcoming, today=today)


# ================== RECEIVED ==================

@bp.route("/received")
@login_required
def received_list():
    rows, d_from, d_to, status = _list_scope(Check.DIRECTION_RECEIVED, request.args)
    return render_template(
        "checks/received_list.html",
        rows=rows, date_from=d_from, date_to=d_to, selected_status=status,
    )


@bp.route("/received/new", methods=["GET", "POST"])
@login_required
def received_new():
    form = ReceivedCheckForm()
    form.customer_id.choices = [
        (c.id, c.name) for c in
        Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    ]
    if request.method == "GET":
        preset = request.args.get("customer_id", type=int)
        if preset:
            form.customer_id.data = preset

    if form.validate_on_submit():
        chk = Check(
            direction=Check.DIRECTION_RECEIVED,
            customer_id=form.customer_id.data,
            check_number=form.check_number.data.strip(),
            bank_name=form.bank_name.data.strip(),
            drawer_name=(form.drawer_name.data or "").strip() or None,
            amount=Decimal(str(form.amount.data)),
            issue_date=form.issue_date.data,
            due_date=form.due_date.data,
            status=Check.STATUS_PENDING,
            related_ref=(form.related_ref.data or "").strip() or None,
            notes=(form.notes.data or "").strip() or None,
            created_by_id=current_user.id,
        )
        db.session.add(chk); db.session.flush()
        try:
            checks_service.on_check_received(chk, created_by=current_user.id)
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
            return render_template("checks/form.html", form=form, side="received")
        log_action("check_received", "Check", chk.id,
                   details=f"customer={chk.customer_id} amount={chk.amount}")
        db.session.commit()
        flash(f"تم استلام الشيك #{chk.check_number} بمبلغ {chk.amount} جنيه.", "success")
        return redirect(url_for("checks.received_detail", check_id=chk.id))

    return render_template("checks/form.html", form=form, side="received")


@bp.route("/received/<int:check_id>")
@login_required
def received_detail(check_id):
    chk = db.session.get(Check, check_id)
    if chk is None or chk.direction != Check.DIRECTION_RECEIVED:
        abort(404)
    clear_form = ClearCheckForm()
    _load_treasury_choices(clear_form)
    bounce_form = BounceCheckForm()
    from app.models.accounting import JournalEntry
    jes = JournalEntry.query.filter(
        JournalEntry.source_type.in_(
            ["Check:receive", "Check:clear", "Check:bounce"]),
        JournalEntry.source_id == chk.id,
        JournalEntry.is_active.is_(True),
    ).order_by(JournalEntry.date, JournalEntry.id).all()
    return render_template(
        "checks/received_detail.html",
        chk=chk, jes=jes, clear_form=clear_form, bounce_form=bounce_form,
    )


@bp.route("/received/<int:check_id>/clear", methods=["POST"])
@login_required
def received_clear(check_id):
    chk = db.session.get(Check, check_id)
    if chk is None or chk.direction != Check.DIRECTION_RECEIVED:
        abort(404)
    if chk.status != Check.STATUS_PENDING:
        flash("الشيك اتحوّل خلاص — مش ممكن تصرفه.", "error")
        return redirect(url_for("checks.received_detail", check_id=chk.id))

    form = ClearCheckForm()
    _load_treasury_choices(form)
    if not form.validate_on_submit():
        for _, errs in form.errors.items():
            for e in errs:
                flash(e, "error")
        return redirect(url_for("checks.received_detail", check_id=chk.id))

    chk.treasury_account_id = form.treasury_account_id.data
    chk.cleared_on = form.cleared_on.data
    chk.status = Check.STATUS_CLEARED
    try:
        checks_service.on_check_cleared_received(chk, created_by=current_user.id)
    except LedgerError as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("checks.received_detail", check_id=chk.id))
    log_action("check_cleared", "Check", chk.id,
               details=f"treasury={chk.treasury_account_id}")
    db.session.commit()
    flash("تم تحصيل الشيك.", "success")
    return redirect(url_for("checks.received_detail", check_id=chk.id))


@bp.route("/received/<int:check_id>/bounce", methods=["POST"])
@login_required
def received_bounce(check_id):
    chk = db.session.get(Check, check_id)
    if chk is None or chk.direction != Check.DIRECTION_RECEIVED:
        abort(404)
    if chk.status != Check.STATUS_PENDING:
        flash("الشيك اتحوّل خلاص.", "error")
        return redirect(url_for("checks.received_detail", check_id=chk.id))

    form = BounceCheckForm()
    if not form.validate_on_submit():
        for _, errs in form.errors.items():
            for e in errs:
                flash(e, "error")
        return redirect(url_for("checks.received_detail", check_id=chk.id))

    chk.bounced_on = form.bounced_on.data
    if form.notes.data:
        chk.notes = ((chk.notes or "") + "\n" + form.notes.data).strip()
    chk.status = Check.STATUS_BOUNCED
    checks_service.on_check_bounced_received(chk, created_by=current_user.id)
    log_action("check_bounced", "Check", chk.id, details="received")
    db.session.commit()
    flash("تم تسجيل ارتداد الشيك — الرصيد رجع للعميل.", "warning")
    return redirect(url_for("checks.received_detail", check_id=chk.id))


# ================== ISSUED ==================

@bp.route("/issued")
@login_required
def issued_list():
    rows, d_from, d_to, status = _list_scope(Check.DIRECTION_ISSUED, request.args)
    return render_template(
        "checks/issued_list.html",
        rows=rows, date_from=d_from, date_to=d_to, selected_status=status,
    )


@bp.route("/issued/new", methods=["GET", "POST"])
@login_required
def issued_new():
    form = IssuedCheckForm()
    form.supplier_id.choices = [
        (s.id, s.name) for s in
        Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    ]
    if request.method == "GET":
        preset = request.args.get("supplier_id", type=int)
        if preset:
            form.supplier_id.data = preset

    if form.validate_on_submit():
        chk = Check(
            direction=Check.DIRECTION_ISSUED,
            supplier_id=form.supplier_id.data,
            check_number=form.check_number.data.strip(),
            bank_name=form.bank_name.data.strip(),
            amount=Decimal(str(form.amount.data)),
            issue_date=form.issue_date.data,
            due_date=form.due_date.data,
            status=Check.STATUS_PENDING,
            related_ref=(form.related_ref.data or "").strip() or None,
            notes=(form.notes.data or "").strip() or None,
            created_by_id=current_user.id,
        )
        db.session.add(chk); db.session.flush()
        try:
            checks_service.on_check_issued(chk, created_by=current_user.id)
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
            return render_template("checks/form.html", form=form, side="issued")
        log_action("check_issued", "Check", chk.id,
                   details=f"supplier={chk.supplier_id} amount={chk.amount}")
        db.session.commit()
        flash(f"تم إصدار الشيك #{chk.check_number} بمبلغ {chk.amount} جنيه.", "success")
        return redirect(url_for("checks.issued_detail", check_id=chk.id))

    return render_template("checks/form.html", form=form, side="issued")


@bp.route("/issued/<int:check_id>")
@login_required
def issued_detail(check_id):
    chk = db.session.get(Check, check_id)
    if chk is None or chk.direction != Check.DIRECTION_ISSUED:
        abort(404)
    settle_form = ClearCheckForm()
    _load_treasury_choices(settle_form)
    bounce_form = BounceCheckForm()
    from app.models.accounting import JournalEntry
    jes = JournalEntry.query.filter(
        JournalEntry.source_type.in_(
            ["Check:receive", "Check:clear", "Check:bounce"]),
        JournalEntry.source_id == chk.id,
        JournalEntry.is_active.is_(True),
    ).order_by(JournalEntry.date, JournalEntry.id).all()
    return render_template(
        "checks/issued_detail.html",
        chk=chk, jes=jes, settle_form=settle_form, bounce_form=bounce_form,
    )


@bp.route("/issued/<int:check_id>/settle", methods=["POST"])
@login_required
def issued_settle(check_id):
    chk = db.session.get(Check, check_id)
    if chk is None or chk.direction != Check.DIRECTION_ISSUED:
        abort(404)
    if chk.status != Check.STATUS_PENDING:
        flash("الشيك اتصرف خلاص.", "error")
        return redirect(url_for("checks.issued_detail", check_id=chk.id))

    form = ClearCheckForm()
    _load_treasury_choices(form)
    if not form.validate_on_submit():
        for _, errs in form.errors.items():
            for e in errs:
                flash(e, "error")
        return redirect(url_for("checks.issued_detail", check_id=chk.id))

    chk.treasury_account_id = form.treasury_account_id.data
    chk.cleared_on = form.cleared_on.data
    chk.status = Check.STATUS_CLEARED
    try:
        checks_service.on_check_settled_issued(chk, created_by=current_user.id)
    except LedgerError as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("checks.issued_detail", check_id=chk.id))
    log_action("check_settled", "Check", chk.id,
               details=f"treasury={chk.treasury_account_id}")
    db.session.commit()
    flash("تم صرف الشيك — الرصيد اتخصم من الحساب.", "success")
    return redirect(url_for("checks.issued_detail", check_id=chk.id))


@bp.route("/issued/<int:check_id>/bounce", methods=["POST"])
@login_required
def issued_bounce(check_id):
    chk = db.session.get(Check, check_id)
    if chk is None or chk.direction != Check.DIRECTION_ISSUED:
        abort(404)
    if chk.status != Check.STATUS_PENDING:
        flash("الشيك اتحوّل خلاص.", "error")
        return redirect(url_for("checks.issued_detail", check_id=chk.id))

    form = BounceCheckForm()
    if not form.validate_on_submit():
        for _, errs in form.errors.items():
            for e in errs:
                flash(e, "error")
        return redirect(url_for("checks.issued_detail", check_id=chk.id))

    chk.bounced_on = form.bounced_on.data
    if form.notes.data:
        chk.notes = ((chk.notes or "") + "\n" + form.notes.data).strip()
    chk.status = Check.STATUS_BOUNCED
    checks_service.on_check_bounced_issued(chk, created_by=current_user.id)
    log_action("check_bounced", "Check", chk.id, details="issued")
    db.session.commit()
    flash("تم تسجيل ارتداد الشيك — الرصيد رجع للمورد.", "warning")
    return redirect(url_for("checks.issued_detail", check_id=chk.id))


# ================== VOID (admin) ==================

@bp.route("/<int:check_id>/void", methods=["POST"])
@login_required
def void(check_id):
    if not current_user.is_admin:
        abort(403)
    chk = db.session.get(Check, check_id)
    if chk is None:
        abort(404)
    checks_service.void_check(chk)
    log_action("check_voided", "Check", chk.id)
    db.session.commit()
    flash("تم إلغاء الشيك بالكامل — كل القيود اتشالت.", "warning")
    if chk.direction == Check.DIRECTION_RECEIVED:
        return redirect(url_for("checks.received_list"))
    return redirect(url_for("checks.issued_list"))
