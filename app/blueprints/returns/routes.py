"""PHASE 5 — sales / purchase return endpoints.

Every create goes through the autoposter alongside the DB write; archive
posts a mirror JE with is_reversal=True. Nothing is deleted from the
ledger — reversal is the audit-safe way.
"""
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms.returns import PurchaseReturnForm, SalesReturnForm
from app.models.finance import Account
from app.models.sales import Customer, MilkInvoice, SalesReturn
from app.models.suppliers import PurchaseInvoice, PurchaseReturn, Supplier
from app.services import autoposting
from app.services.ledger import LedgerError, post_journal
from app.utils import accounts as acc
from app.utils.audit import log_action

bp = Blueprint("returns", __name__, template_folder="../../templates/returns")


# ---------- shared helpers ----------
def _load_treasury_choices(form):
    form.treasury_account_id.choices = [("", "— اختياري (للنقدي فقط) —")] + [
        (str(id_), label) for id_, label in acc.active_choices()
    ]


def _apply_common(ret, form):
    ret.return_date = form.return_date.data
    ret.amount = Decimal(str(form.amount.data))
    ret.mode = form.mode.data
    if form.mode.data == "cash":
        if not form.treasury_account_id.data:
            form.treasury_account_id.errors.append(
                "لازم تختار حساب لما يكون المرتجع نقدي."
            )
            return False
        ret.treasury_account_id = form.treasury_account_id.data
    else:
        ret.treasury_account_id = None
    ret.reason = (form.reason.data or "").strip() or None
    ret.notes = (form.notes.data or "").strip() or None
    return True


# ---------- index ----------
@bp.route("/")
@login_required
def index():
    return render_template("returns/index.html")


# ================== SALES RETURNS ==================

@bp.route("/sales")
@login_required
def sales_list():
    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today - timedelta(days=90)
    d_to = date.fromisoformat(to) if to else today
    cid = request.args.get("customer_id", type=int)

    q = SalesReturn.query.filter(
        SalesReturn.return_date >= d_from,
        SalesReturn.return_date <= d_to,
    )
    if cid:
        q = q.filter_by(customer_id=cid)
    returns = q.order_by(SalesReturn.return_date.desc(), SalesReturn.id.desc()).all()
    customers = Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    total = sum((Decimal(str(r.amount)) for r in returns if not r.is_archived), Decimal("0"))
    return render_template(
        "returns/sales_list.html",
        returns=returns, customers=customers,
        date_from=d_from, date_to=d_to, selected_customer_id=cid,
        total=total,
    )


@bp.route("/sales/new", methods=["GET", "POST"])
@login_required
def sales_new():
    form = SalesReturnForm()
    form.customer_id.choices = [
        (c.id, c.name) for c in
        Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    ]
    preset_customer = request.args.get("customer_id", type=int)
    if request.method == "GET" and preset_customer:
        form.customer_id.data = preset_customer
    _load_treasury_choices(form)
    # invoices dropdown: this customer's issued milk invoices (any customer if none picked yet)
    cid = form.customer_id.data or preset_customer
    inv_q = MilkInvoice.query.filter_by(is_archived=False)
    if cid:
        inv_q = inv_q.filter_by(customer_id=cid)
    form.invoice_id.choices = [("", "— بدون فاتورة محددة —")] + [
        (str(i.id), f"#{i.id} — {i.issue_date} — {i.grand_total}")
        for i in inv_q.order_by(MilkInvoice.issue_date.desc()).limit(50).all()
    ]

    if form.validate_on_submit():
        ret = SalesReturn(
            customer_id=form.customer_id.data,
            invoice_id=form.invoice_id.data,
            created_by_id=current_user.id,
        )
        if not _apply_common(ret, form):
            return render_template("returns/form.html", form=form, side="sales")
        db.session.add(ret); db.session.flush()
        try:
            autoposting.on_sales_return(ret, created_by=current_user.id)
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
            return render_template("returns/form.html", form=form, side="sales")
        log_action("sales_return", "SalesReturn", ret.id,
                   details=f"customer={ret.customer_id} amount={ret.amount} mode={ret.mode}")
        db.session.commit()
        flash(f"تم تسجيل المرتجع #{ret.id} بمبلغ {ret.amount} جنيه.", "success")
        return redirect(url_for("returns.sales_detail", ret_id=ret.id))

    return render_template("returns/form.html", form=form, side="sales")


@bp.route("/sales/<int:ret_id>")
@login_required
def sales_detail(ret_id):
    ret = db.session.get(SalesReturn, ret_id)
    if ret is None:
        abort(404)
    from app.models.accounting import JournalEntry
    je = JournalEntry.query.filter_by(
        source_type="SalesReturn", source_id=ret_id, is_active=True,
    ).first()
    return render_template("returns/sales_detail.html", ret=ret, je=je)


@bp.route("/sales/<int:ret_id>/archive", methods=["POST"])
@login_required
def sales_archive(ret_id):
    if not current_user.is_admin:
        abort(403)
    ret = db.session.get(SalesReturn, ret_id)
    if ret is None:
        abort(404)
    if ret.is_archived:
        flash("المرتجع ملغي بالفعل.", "info")
        return redirect(url_for("returns.sales_detail", ret_id=ret_id))
    ret.is_archived = True
    # deletes the prior JE — releasing revenue/receivable back to what they were
    autoposting.on_sales_return(ret)
    log_action("sales_return_archived", "SalesReturn", ret.id)
    db.session.commit()
    flash("تم إلغاء المرتجع — الأرصدة رجعت لما قبله.", "success")
    return redirect(url_for("returns.sales_detail", ret_id=ret_id))


# ================== PURCHASE RETURNS ==================

@bp.route("/purchases")
@login_required
def purchases_list():
    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today - timedelta(days=90)
    d_to = date.fromisoformat(to) if to else today
    sid = request.args.get("supplier_id", type=int)

    q = PurchaseReturn.query.filter(
        PurchaseReturn.return_date >= d_from,
        PurchaseReturn.return_date <= d_to,
    )
    if sid:
        q = q.filter_by(supplier_id=sid)
    returns = q.order_by(PurchaseReturn.return_date.desc(), PurchaseReturn.id.desc()).all()
    suppliers = Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    total = sum((Decimal(str(r.amount)) for r in returns if not r.is_archived), Decimal("0"))
    return render_template(
        "returns/purchases_list.html",
        returns=returns, suppliers=suppliers,
        date_from=d_from, date_to=d_to, selected_supplier_id=sid,
        total=total,
    )


@bp.route("/purchases/new", methods=["GET", "POST"])
@login_required
def purchases_new():
    form = PurchaseReturnForm()
    form.supplier_id.choices = [
        (s.id, s.name) for s in
        Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    ]
    preset = request.args.get("supplier_id", type=int)
    if request.method == "GET" and preset:
        form.supplier_id.data = preset
    _load_treasury_choices(form)
    sid = form.supplier_id.data or preset
    inv_q = PurchaseInvoice.query.filter_by(is_archived=False)
    if sid:
        inv_q = inv_q.filter_by(supplier_id=sid)
    form.invoice_id.choices = [("", "— بدون فاتورة محددة —")] + [
        (str(i.id), f"#{i.id} — {i.invoice_date} — {i.total}")
        for i in inv_q.order_by(PurchaseInvoice.invoice_date.desc()).limit(50).all()
    ]

    if form.validate_on_submit():
        ret = PurchaseReturn(
            supplier_id=form.supplier_id.data,
            invoice_id=form.invoice_id.data,
            created_by_id=current_user.id,
        )
        if not _apply_common(ret, form):
            return render_template("returns/form.html", form=form, side="purchases")
        db.session.add(ret); db.session.flush()
        try:
            autoposting.on_purchase_return(ret, created_by=current_user.id)
            # PHASE 6: if the return is tied to a specific invoice, pull the
            # returned goods back off the shelf proportionally to the
            # invoice's lines. Without this the ledger's inventory balance
            # falls but the operational current_qty stays flat — the exact
            # mismatch the /inventory/valuation view is built to surface.
            if ret.invoice and ret.invoice.lines:
                from app.utils import inventory_cost
                total_lines = sum(
                    (Decimal(str(l.line_total)) for l in ret.invoice.lines),
                    Decimal("0"),
                )
                if total_lines > 0:
                    for line in ret.invoice.lines:
                        share_money = (
                            Decimal(str(line.line_total)) / total_lines
                        ) * Decimal(str(ret.amount))
                        qty_share = (
                            share_money / Decimal(str(line.unit_price))
                        ).quantize(Decimal("0.001"))
                        if qty_share > 0:
                            try:
                                inventory_cost.reverse_purchase(
                                    line.ingredient, qty_share
                                )
                            except ValueError:
                                # Stock was already consumed after purchase —
                                # ledger reversal still holds, but operational
                                # counter can't go negative. Accountant reads
                                # the diff on /inventory/valuation.
                                pass
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
            return render_template("returns/form.html", form=form, side="purchases")
        log_action("purchase_return", "PurchaseReturn", ret.id,
                   details=f"supplier={ret.supplier_id} amount={ret.amount} mode={ret.mode}")
        db.session.commit()
        flash(f"تم تسجيل المرتجع #{ret.id} بمبلغ {ret.amount} جنيه.", "success")
        return redirect(url_for("returns.purchases_detail", ret_id=ret.id))

    return render_template("returns/form.html", form=form, side="purchases")


@bp.route("/purchases/<int:ret_id>")
@login_required
def purchases_detail(ret_id):
    ret = db.session.get(PurchaseReturn, ret_id)
    if ret is None:
        abort(404)
    from app.models.accounting import JournalEntry
    je = JournalEntry.query.filter_by(
        source_type="PurchaseReturn", source_id=ret_id, is_active=True,
    ).first()
    return render_template("returns/purchases_detail.html", ret=ret, je=je)


@bp.route("/purchases/<int:ret_id>/archive", methods=["POST"])
@login_required
def purchases_archive(ret_id):
    if not current_user.is_admin:
        abort(403)
    ret = db.session.get(PurchaseReturn, ret_id)
    if ret is None:
        abort(404)
    if ret.is_archived:
        flash("المرتجع ملغي بالفعل.", "info")
        return redirect(url_for("returns.purchases_detail", ret_id=ret_id))
    ret.is_archived = True
    autoposting.on_purchase_return(ret)
    log_action("purchase_return_archived", "PurchaseReturn", ret.id)
    db.session.commit()
    flash("تم إلغاء المرتجع — الأرصدة رجعت لما قبله.", "success")
    return redirect(url_for("returns.purchases_detail", ret_id=ret_id))
