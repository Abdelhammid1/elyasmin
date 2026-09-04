from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from app.extensions import db
from app.forms.suppliers import SupplierForm, SupplierPaymentForm
from app.models.finance import TreasuryAccount, Expense
from app.models.sales import Customer
from app.models.suppliers import PurchaseInvoice, Supplier, SupplierPayment
from app.utils import accounts as acc
from app.utils.audit import log_action
from app.utils.reports import excel_response
from app.utils.decorators import write_required

bp = Blueprint("suppliers", __name__, template_folder="../../templates/suppliers")


@bp.route("/")
@login_required
def list_suppliers():
    """PHASE 22: KPI-strip + filter-row + rich-chip list (mirror of customers)."""
    q_text = (request.args.get("q") or "").strip()
    f_balance = (request.args.get("balance") or "all").lower()

    query = Supplier.query.filter_by(is_archived=False)
    if q_text:
        like = f"%{q_text}%"
        query = query.filter(or_(Supplier.name.ilike(like), Supplier.phone.ilike(like)))
    suppliers = query.order_by(Supplier.name).all()

    if f_balance == "owes":
        suppliers = [s for s in suppliers if s.balance_due > 0]
    elif f_balance == "paid":
        suppliers = [s for s in suppliers if s.balance_due <= 0]

    total_owed = sum((s.balance_due for s in suppliers if s.balance_due > 0), Decimal("0"))

    today = date.today()
    overdue_ids = set()
    for inv in PurchaseInvoice.query.filter(
        PurchaseInvoice.is_archived.is_(False),
    ).all():
        if inv.outstanding_amount > 0 and inv.invoice_date and (today - inv.invoice_date).days > 15:
            overdue_ids.add(inv.supplier_id)
    overdue_count = len(overdue_ids)

    since = today - timedelta(days=30)
    paid_30d = db.session.query(
        func.coalesce(func.sum(SupplierPayment.amount), 0)
    ).filter(
        SupplierPayment.is_archived.is_(False),
        SupplierPayment.payment_date >= since,
    ).scalar()

    return render_template(
        "suppliers/list.html",
        suppliers=suppliers,
        total_owed=total_owed,
        active_count=len(suppliers),
        overdue_count=overdue_count,
        overdue_ids=overdue_ids,
        paid_30d=Decimal(str(paid_30d or 0)),
        f_q=q_text, f_balance=f_balance,
    )


def _customer_link_choices():
    """Available customer records to link a supplier to (unlinked + own current)."""
    active = Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    return [(0, "— بدون ربط —")] + [(c.id, c.name) for c in active]


@bp.route("/new", methods=["GET", "POST"])
@login_required
@write_required
def create_supplier():
    form = SupplierForm()
    form.linked_customer_id.choices = _customer_link_choices()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if Supplier.query.filter(func.lower(Supplier.name) == name.lower()).first():
            flash("مورد بنفس الاسم مسجّل قبل كده.", "error")
        else:
            linked_cid = form.linked_customer_id.data or None
            if linked_cid == 0:
                linked_cid = None
            supplier = Supplier(
                name=name,
                phone=(form.phone.data or "").strip() or None,
                supplied_categories=",".join(form.supplied_categories.data),
                opening_balance=Decimal(str(form.opening_balance.data or 0)),
                linked_customer_id=linked_cid,
                notes=form.notes.data,
                created_by_id=current_user.id,
            )
            db.session.add(supplier)
            db.session.flush()
            # Reciprocal link on the customer side
            if linked_cid:
                cust = db.session.get(Customer, linked_cid)
                if cust:
                    cust.linked_supplier_id = supplier.id
            log_action("supplier_created", "Supplier", supplier.id)
            db.session.commit()
            flash(f"تم إضافة المورد {supplier.name}.", "success")
            return redirect(url_for("suppliers.supplier_detail", supplier_id=supplier.id))
    return render_template("suppliers/form.html", form=form, mode="create")


@bp.route("/<int:supplier_id>")
@login_required
def supplier_detail(supplier_id: int):
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier or supplier.is_archived:
        abort(404)
    invoices = (
        PurchaseInvoice.query.filter_by(supplier_id=supplier.id, is_archived=False)
        .order_by(PurchaseInvoice.invoice_date.desc())
        .all()
    )
    payments = (
        SupplierPayment.query.filter_by(supplier_id=supplier.id, is_archived=False)
        .order_by(SupplierPayment.payment_date.desc())
        .all()
    )
    payment_form = SupplierPaymentForm()
    payment_form.account_id.choices = acc.active_choices()

    # TICKET-4: embed linked customer's transactions in the same page
    linked_deliveries = []
    linked_customer_payments = []
    if supplier.linked_customer:
        from app.models.sales import CustomerPayment, MilkDelivery

        linked_deliveries = (
            MilkDelivery.query
            .filter_by(customer_id=supplier.linked_customer.id, is_archived=False)
            .order_by(MilkDelivery.delivery_date.desc())
            .limit(20)
            .all()
        )
        linked_customer_payments = (
            CustomerPayment.query
            .filter_by(customer_id=supplier.linked_customer.id, is_archived=False)
            .order_by(CustomerPayment.payment_date.desc())
            .limit(20)
            .all()
        )

    # PHASE 4: open credit invoices for the payment form's allocation grid
    from app.services.allocations import open_supplier_invoices_for
    open_invoices = open_supplier_invoices_for(supplier.id)

    # PHASE 5: recent purchase returns for this supplier
    from app.models.suppliers import PurchaseReturn
    supplier_returns = (
        PurchaseReturn.query
        .filter_by(supplier_id=supplier.id)
        .order_by(PurchaseReturn.return_date.desc(), PurchaseReturn.id.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "suppliers/detail.html",
        supplier=supplier,
        invoices=invoices,
        payments=payments,
        payment_form=payment_form,
        linked_deliveries=linked_deliveries,
        linked_customer_payments=linked_customer_payments,
        open_invoices=open_invoices,
        supplier_returns=supplier_returns,
    )


@bp.route("/<int:supplier_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
def edit_supplier(supplier_id: int):
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier or supplier.is_archived:
        abort(404)

    form = SupplierForm(obj=supplier)
    form.linked_customer_id.choices = _customer_link_choices()
    if request.method == "GET":
        form.supplied_categories.data = supplier.categories_list
        form.linked_customer_id.data = supplier.linked_customer_id or 0

    if form.validate_on_submit():
        supplier.name = form.name.data.strip()
        supplier.phone = (form.phone.data or "").strip() or None
        supplier.supplied_categories = ",".join(form.supplied_categories.data)
        supplier.notes = form.notes.data

        # TICKET-1: opening balance moves the supplier's whole balance — audit it separately
        old_opening = Decimal(str(supplier.opening_balance or 0))
        new_opening = Decimal(str(form.opening_balance.data or 0))
        if new_opening != old_opening:
            supplier.opening_balance = new_opening
            log_action(
                "supplier_opening_balance_changed", "Supplier", supplier.id,
                details=f"{old_opening} -> {new_opening}",
            )

        # Handle link change: reset old customer + set new one
        new_linked = form.linked_customer_id.data or None
        if new_linked == 0:
            new_linked = None
        if supplier.linked_customer_id != new_linked:
            if supplier.linked_customer_id:
                old = db.session.get(Customer, supplier.linked_customer_id)
                if old and old.linked_supplier_id == supplier.id:
                    old.linked_supplier_id = None
            supplier.linked_customer_id = new_linked
            if new_linked:
                cust = db.session.get(Customer, new_linked)
                if cust:
                    cust.linked_supplier_id = supplier.id

        log_action("supplier_updated", "Supplier", supplier.id)
        db.session.commit()
        flash("تم تحديث بيانات المورد.", "success")
        return redirect(url_for("suppliers.supplier_detail", supplier_id=supplier.id))

    return render_template("suppliers/form.html", form=form, mode="edit", supplier=supplier)


# ---------- US-3.3 Supplier payment ----------
@bp.route("/<int:supplier_id>/pay", methods=["POST"])
@login_required
@write_required
def record_payment(supplier_id: int):
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier or supplier.is_archived:
        abort(404)

    form = SupplierPaymentForm()
    form.account_id.choices = acc.active_choices()
    if not form.account_id.choices:
        flash("لازم تضيف حساب (خزنة أو بنك) الأول عشان تسجّل دفعة.", "error")
        return redirect(url_for("accounts.create_account"))
    if not form.validate_on_submit():
        for field, errors in form.errors.items():
            for e in errors:
                flash(e, "error")
        return redirect(url_for("suppliers.supplier_detail", supplier_id=supplier.id))

    amount = Decimal(str(form.amount.data))
    balance = supplier.balance_due
    overpay_confirmed = form.confirm_overpay.data == "1"

    if amount > balance and not overpay_confirmed:
        flash(
            f"المبلغ المدفوع ({amount}) أكبر من رصيد المورد ({balance}). "
            "فعّل خانة التأكيد لو متأكد.",
            "warning",
        )
        return redirect(url_for("suppliers.supplier_detail", supplier_id=supplier.id))

    account = db.session.get(TreasuryAccount, form.account_id.data)
    if not account or account.is_archived:
        flash("الحساب غير صالح.", "error")
        return redirect(url_for("suppliers.supplier_detail", supplier_id=supplier.id))

    payment = SupplierPayment(
        supplier_id=supplier.id,
        amount=amount,
        payment_date=form.payment_date.data,
        method=form.method.data,
        account_id=account.id,
        notes=form.notes.data,
        created_by_id=current_user.id,
    )
    db.session.add(payment)
    db.session.flush()

    # TREASURY: this is the cash event — it posts the movement. The mirror
    # Expense below carries the same account for reporting but posts NOTHING,
    # or the account would be debited twice for one payment.
    acc.money_out(
        account, amount, payment.payment_date,
        ref_type="supplier_payment", ref_id=payment.id, user_id=current_user.id,
        notes=f"دفعة للمورد {supplier.name}",
    )
    # ACCOUNTING: post the double-entry alongside the treasury movement.
    # Both writes land in the same commit below — one atomic event.
    from app.services import autoposting
    autoposting.on_supplier_payment(payment, account, created_by=current_user.id)

    # US-3.3 AC3: record as expense (cash outflow)
    db.session.add(
        Expense(
            category=Expense.CAT_SUPPLIER_PAYMENT,
            amount=amount,
            expense_date=payment.payment_date,
            description=f"دفعة للمورد {supplier.name}",
            ref_type="supplier_payment",
            ref_id=payment.id,
            account_id=account.id,
            created_by_id=current_user.id,
        )
    )

    # PHASE 4: parse allocation rows (alloc_inv_<id>) if the form carried
    # any. Same shape as the customer route, other direction. If any
    # single allocation is invalid the whole transaction rolls back and
    # the payment is discarded — better than saving a payment that
    # doesn't match what the user intended.
    from decimal import InvalidOperation
    allocations = []
    for key, val in request.form.items():
        if not key.startswith("alloc_inv_"):
            continue
        try:
            inv_id = int(key[len("alloc_inv_"):])
            amt = Decimal(str(val or "0"))
        except (ValueError, InvalidOperation):
            continue
        if amt > 0:
            allocations.append((inv_id, amt))
    if allocations:
        from app.services.allocations import allocate_supplier_payment, AllocationError
        try:
            allocate_supplier_payment(payment, allocations, created_by=current_user.id)
        except AllocationError as ae:
            db.session.rollback()
            flash(str(ae), "error")
            return redirect(url_for("suppliers.supplier_detail", supplier_id=supplier.id))

    log_action(
        "supplier_payment", "SupplierPayment", payment.id,
        details=f"supplier={supplier.id} amount={amount} allocations={len(allocations)}"
    )
    db.session.commit()
    flash(
        f"تم تسجيل دفعة {amount} للمورد {supplier.name} من {account.name}. "
        f"رصيد {account.name} بقى {account.current_balance} جنيه.",
        "success",
    )
    return redirect(url_for("suppliers.supplier_detail", supplier_id=supplier.id))


# ---------- US-3.4 Suppliers report ----------
@bp.route("/report")
@login_required
def suppliers_report():
    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = date.fromisoformat(to) if to else today
    supplier_id = request.args.get("supplier_id", type=int)

    invoice_q = PurchaseInvoice.query.filter(
        PurchaseInvoice.is_archived.is_(False),
        PurchaseInvoice.invoice_date >= d_from,
        PurchaseInvoice.invoice_date <= d_to,
    )
    if supplier_id:
        invoice_q = invoice_q.filter_by(supplier_id=supplier_id)
    invoices = invoice_q.order_by(PurchaseInvoice.invoice_date.desc()).all()

    total_period_invoiced = sum((i.total for i in invoices), Decimal("0"))

    # The dropdown and the statement table used to share one list, so picking a
    # supplier filtered the invoices but left the statement — and the المستحق
    # total — showing every supplier. They are two different questions now.
    all_suppliers = Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    statement_suppliers = (
        [s for s in all_suppliers if s.id == supplier_id] if supplier_id else all_suppliers
    )
    total_owed_now = sum((s.balance_due for s in statement_suppliers), Decimal("0"))

    return render_template(
        "suppliers/report.html",
        invoices=invoices,
        suppliers=all_suppliers,
        statement_suppliers=statement_suppliers,
        selected_supplier_id=supplier_id,
        date_from=d_from, date_to=d_to,
        total_period_invoiced=total_period_invoiced,
        total_owed_now=total_owed_now,
    )


@bp.route("/report/excel")
@login_required
def suppliers_report_excel():
    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = date.fromisoformat(to) if to else today
    # the export has to answer the same question the screen is showing, so it
    # takes the supplier filter too
    supplier_id = request.args.get("supplier_id", type=int)
    invoice_q = PurchaseInvoice.query.filter(
        PurchaseInvoice.is_archived.is_(False),
        PurchaseInvoice.invoice_date >= d_from,
        PurchaseInvoice.invoice_date <= d_to,
    )
    if supplier_id:
        invoice_q = invoice_q.filter_by(supplier_id=supplier_id)
    invoices = invoice_q.order_by(PurchaseInvoice.invoice_date.desc()).all()
    rows = [
        [
            i.invoice_date.isoformat(),
            i.supplier.name,
            i.original_invoice_no or "",
            i.payment_label,
            float(i.total),
            float(i.paid_amount),
            float(i.outstanding),
        ]
        for i in invoices
    ]
    return excel_response(
        "Suppliers",
        ["التاريخ", "المورد", "رقم أصلي", "النوع", "الإجمالي", "المدفوع", "المتبقي"],
        rows,
        f"suppliers_report_{d_from}_{d_to}.xlsx",
    )


# ==================== PHASE 4: supplier dues aging ====================

@bp.route("/dues")
@login_required
def dues():
    """المستحقات على المزرعة — every PurchaseInvoice with outstanding_amount>0
    (credit invoices with partial payments), grouped per supplier and aged
    by days since invoice_date into 0-14 / 15-30 / 31-60 / >60 buckets.
    Mirror of /customers/dues."""
    today = date.today()
    invs = (
        PurchaseInvoice.query
        .filter_by(is_archived=False)
        .order_by(PurchaseInvoice.supplier_id, PurchaseInvoice.invoice_date)
        .all()
    )

    def bucket(days):
        if days <= 14: return "b_0_14"
        if days <= 30: return "b_15_30"
        if days <= 60: return "b_31_60"
        return "b_60_plus"

    per_supplier: dict[int, dict] = {}
    totals = {"b_0_14": Decimal("0"), "b_15_30": Decimal("0"),
              "b_31_60": Decimal("0"), "b_60_plus": Decimal("0"),
              "grand": Decimal("0")}

    for i in invs:
        out = i.outstanding_amount
        if out <= 0:
            continue
        days = (today - i.invoice_date).days
        b = bucket(days)
        c = per_supplier.setdefault(i.supplier_id, {
            "supplier": i.supplier, "invoices": [],
            "b_0_14": Decimal("0"), "b_15_30": Decimal("0"),
            "b_31_60": Decimal("0"), "b_60_plus": Decimal("0"),
            "total": Decimal("0"),
        })
        c["invoices"].append({"inv": i, "days": days, "bucket": b, "outstanding": out})
        c[b] += out
        c["total"] += out
        totals[b] += out
        totals["grand"] += out

    rows = sorted(per_supplier.values(), key=lambda r: r["supplier"].name)
    return render_template("suppliers/dues.html", rows=rows, totals=totals, today=today)
