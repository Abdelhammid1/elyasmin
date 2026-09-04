from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from app.extensions import db
from app.forms.sales import CustomerForm, CustomerPaymentForm
from app.models.finance import TreasuryAccount
from app.models.sales import Customer, CustomerPayment, MilkDelivery, MilkInvoice
from app.models.suppliers import Supplier
from app.utils import accounts as acc
from app.utils.audit import log_action
from app.utils.reports import excel_response, pdf_from_current_page

bp = Blueprint("customers", __name__, template_folder="../../templates/customers")


@bp.route("/")
@login_required
def list_customers():
    """PHASE 22: KPI-strip + filter-row + rich-chip list."""
    from datetime import date as _date, timedelta

    q_text = (request.args.get("q") or "").strip()
    f_contract = (request.args.get("contract") or "all").lower()
    f_balance = (request.args.get("balance") or "all").lower()

    query = Customer.query.filter_by(is_archived=False)
    if q_text:
        like = f"%{q_text}%"
        query = query.filter(or_(Customer.name.ilike(like), Customer.phone.ilike(like)))
    if f_contract in ("weekly", "cash"):
        query = query.filter(Customer.contract_type == f_contract)
    customers = query.order_by(Customer.name).all()

    # Balance filter applied in Python (balance is a hybrid property)
    if f_balance == "owes":
        customers = [c for c in customers if c.balance > 0]
    elif f_balance == "paid":
        customers = [c for c in customers if c.balance <= 0]

    total_owed_to_us = sum((c.balance for c in customers if c.balance > 0), Decimal("0"))

    # Overdue: any MilkInvoice for the customer > 15 days outstanding
    today = _date.today()
    overdue_ids = set()
    for inv in MilkInvoice.query.filter(
        MilkInvoice.is_archived.is_(False),
        MilkInvoice.status != "draft",
    ).all():
        if inv.outstanding_amount > 0 and inv.issue_date and (today - inv.issue_date).days > 15:
            overdue_ids.add(inv.customer_id)
    overdue_count = len(overdue_ids)

    # Payments last 30 days
    since = today - timedelta(days=30)
    collected_30d = db.session.query(
        func.coalesce(func.sum(CustomerPayment.amount), 0)
    ).filter(
        CustomerPayment.is_archived.is_(False),
        CustomerPayment.payment_date >= since,
    ).scalar()

    return render_template(
        "customers/list.html",
        customers=customers,
        total_owed_to_us=total_owed_to_us,
        active_count=len(customers),
        overdue_count=overdue_count,
        overdue_ids=overdue_ids,
        collected_30d=Decimal(str(collected_30d or 0)),
        f_q=q_text, f_contract=f_contract, f_balance=f_balance,
    )


def _supplier_link_choices():
    active = Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    return [(0, "— بدون ربط —")] + [(s.id, s.name) for s in active]


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_customer():
    form = CustomerForm()
    form.linked_supplier_id.choices = _supplier_link_choices()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if Customer.query.filter(func.lower(Customer.name) == name.lower()).first():
            flash("عميل بنفس الاسم مسجّل قبل كده.", "error")
        else:
            linked_sid = form.linked_supplier_id.data or None
            if linked_sid == 0:
                linked_sid = None
            c = Customer(
                name=name,
                phone=(form.phone.data or "").strip() or None,
                contract_type=form.contract_type.data,
                pricing_type=form.pricing_type.data,
                fixed_price=form.fixed_price.data
                if form.pricing_type.data == Customer.PRICING_FIXED
                else None,
                linked_supplier_id=linked_sid,
                notes=form.notes.data,
                created_by_id=current_user.id,
            )
            db.session.add(c)
            db.session.flush()
            if linked_sid:
                sup = db.session.get(Supplier, linked_sid)
                if sup:
                    sup.linked_customer_id = c.id
            log_action("customer_created", "Customer", c.id)
            db.session.commit()
            flash(f"تم إضافة العميل {c.name}.", "success")
            return redirect(url_for("customers.customer_detail", customer_id=c.id))
    return render_template("customers/form.html", form=form, mode="create")


@bp.route("/<int:customer_id>")
@login_required
def customer_detail(customer_id: int):
    customer = db.session.get(Customer, customer_id)
    if not customer or customer.is_archived:
        abort(404)

    # US-4.3 AC4: statement from any date to any date
    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = date.fromisoformat(to) if to else today

    deliveries = (
        MilkDelivery.query
        .filter_by(customer_id=customer.id, is_archived=False)
        .filter(MilkDelivery.delivery_date >= d_from, MilkDelivery.delivery_date <= d_to)
        .order_by(MilkDelivery.delivery_date.desc())
        .all()
    )
    payments = (
        CustomerPayment.query
        .filter_by(customer_id=customer.id, is_archived=False)
        .filter(CustomerPayment.payment_date >= d_from, CustomerPayment.payment_date <= d_to)
        .order_by(CustomerPayment.payment_date.desc())
        .all()
    )
    # TICKET-4: unpriced deliveries carry total_value = None
    period_delivered = sum(
        (d.total_value for d in deliveries if d.total_value is not None), Decimal("0")
    )
    period_paid = sum((p.amount for p in payments), Decimal("0"))
    payment_form = CustomerPaymentForm()
    payment_form.account_id.choices = acc.active_choices()

    # TICKET-4: embed linked supplier's transactions in the same page
    linked_invoices = []
    linked_supplier_payments = []
    if customer.linked_supplier:
        from app.models.suppliers import PurchaseInvoice, SupplierPayment

        linked_invoices = (
            PurchaseInvoice.query
            .filter_by(supplier_id=customer.linked_supplier.id, is_archived=False)
            .order_by(PurchaseInvoice.invoice_date.desc())
            .limit(20)
            .all()
        )
        linked_supplier_payments = (
            SupplierPayment.query
            .filter_by(supplier_id=customer.linked_supplier.id, is_archived=False)
            .order_by(SupplierPayment.payment_date.desc())
            .limit(20)
            .all()
        )

    # PHASE 3: open invoices for the payment form's allocation grid
    from app.services.allocations import open_invoices_for
    open_invoices = open_invoices_for(customer.id)

    # PHASE 5: recent sales returns for this customer
    from app.models.sales import SalesReturn
    customer_returns = (
        SalesReturn.query
        .filter_by(customer_id=customer.id)
        .order_by(SalesReturn.return_date.desc(), SalesReturn.id.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "customers/detail.html",
        customer=customer,
        deliveries=deliveries,
        payments=payments,
        payment_form=payment_form,
        date_from=d_from,
        date_to=d_to,
        period_delivered=period_delivered,
        period_paid=period_paid,
        linked_invoices=linked_invoices,
        linked_supplier_payments=linked_supplier_payments,
        open_invoices=open_invoices,
        customer_returns=customer_returns,
    )


@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id: int):
    customer = db.session.get(Customer, customer_id)
    if not customer or customer.is_archived:
        abort(404)
    form = CustomerForm(obj=customer)
    form.linked_supplier_id.choices = _supplier_link_choices()
    if request.method == "GET":
        form.linked_supplier_id.data = customer.linked_supplier_id or 0

    if form.validate_on_submit():
        customer.name = form.name.data.strip()
        customer.phone = (form.phone.data or "").strip() or None
        customer.contract_type = form.contract_type.data
        customer.pricing_type = form.pricing_type.data
        customer.fixed_price = (
            form.fixed_price.data if form.pricing_type.data == Customer.PRICING_FIXED else None
        )
        customer.notes = form.notes.data

        # Handle link change
        new_linked = form.linked_supplier_id.data or None
        if new_linked == 0:
            new_linked = None
        if customer.linked_supplier_id != new_linked:
            if customer.linked_supplier_id:
                old = db.session.get(Supplier, customer.linked_supplier_id)
                if old and old.linked_customer_id == customer.id:
                    old.linked_customer_id = None
            customer.linked_supplier_id = new_linked
            if new_linked:
                sup = db.session.get(Supplier, new_linked)
                if sup:
                    sup.linked_customer_id = customer.id

        log_action("customer_updated", "Customer", customer.id)
        db.session.commit()
        flash("تم تحديث بيانات العميل.", "success")
        return redirect(url_for("customers.customer_detail", customer_id=customer.id))
    return render_template("customers/form.html", form=form, mode="edit", customer=customer)


# ---------- US-4.3 Weekly settlement / payments ----------
@bp.route("/<int:customer_id>/pay", methods=["POST"])
@login_required
def record_payment(customer_id: int):
    customer = db.session.get(Customer, customer_id)
    if not customer or customer.is_archived:
        abort(404)
    form = CustomerPaymentForm()
    form.account_id.choices = acc.active_choices()
    if not form.account_id.choices:
        flash("لازم تضيف حساب (خزنة أو بنك) الأول عشان تسجّل تحصيل.", "error")
        return redirect(url_for("accounts.create_account"))
    if not form.validate_on_submit():
        for _, errors in form.errors.items():
            for e in errors:
                flash(e, "error")
        return redirect(url_for("customers.customer_detail", customer_id=customer.id))

    account = db.session.get(TreasuryAccount, form.account_id.data)
    if not account or account.is_archived:
        flash("الحساب غير صالح.", "error")
        return redirect(url_for("customers.customer_detail", customer_id=customer.id))

    payment = CustomerPayment(
        customer_id=customer.id,
        amount=Decimal(str(form.amount.data)),
        payment_date=form.payment_date.data,
        method=form.method.data,
        account_id=account.id,
        notes=form.notes.data,
        created_by_id=current_user.id,
    )
    db.session.add(payment)
    db.session.flush()

    # TREASURY: a collection is the one inflow in the app — no mirror Expense
    acc.money_in(
        account, payment.amount, payment.payment_date,
        ref_type="customer_payment", ref_id=payment.id, user_id=current_user.id,
        notes=f"تحصيل من العميل {customer.name}",
    )
    # ACCOUNTING: post the double-entry alongside the treasury movement.
    from app.services import autoposting
    autoposting.on_customer_payment(payment, account, created_by=current_user.id)

    # PHASE 3: parse allocation rows if the form carried any. alloc_inv_<id>
    # holds the amount to allocate against that invoice; blank/0 = skip.
    # Anything not allocated stays on account as unallocated_amount.
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
        from app.services.allocations import allocate_payment, AllocationError
        try:
            allocate_payment(payment, allocations, created_by=current_user.id)
        except AllocationError as ae:
            db.session.rollback()
            flash(str(ae), "error")
            return redirect(url_for("customers.customer_detail", customer_id=customer.id))

    log_action(
        "customer_payment", "CustomerPayment", payment.id,
        details=f"customer={customer.id} amount={payment.amount} account={account.id} "
                f"allocations={len(allocations)}",
    )
    db.session.commit()
    flash(
        f"تم تسجيل دفعة {payment.amount} من {customer.name} في {account.name}. "
        f"رصيد {account.name} بقى {account.current_balance} جنيه.",
        "success",
    )
    return redirect(url_for("customers.customer_detail", customer_id=customer.id))


# ---------- US-4.3 Weekly settlement report ----------
@bp.route("/settlement")
@login_required
def weekly_settlement():
    end_str = request.args.get("end")
    end = date.fromisoformat(end_str) if end_str else date.today()
    start = end - timedelta(days=6)
    prev_end = end - timedelta(days=7)
    next_end = end + timedelta(days=7)

    customers = Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    rows = []
    for c in customers:
        agg = db.session.query(
            func.coalesce(func.sum(MilkDelivery.qty_kg), 0),
            func.coalesce(func.sum(MilkDelivery.total_value), 0),
            func.count(func.distinct(MilkDelivery.delivery_date)),
        ).filter(
            MilkDelivery.customer_id == c.id,
            MilkDelivery.delivery_date >= start,
            MilkDelivery.delivery_date <= end,
            MilkDelivery.is_archived.is_(False),
        ).one()
        total_qty = Decimal(str(agg[0] or 0))
        total_value = Decimal(str(agg[1] or 0))
        delivery_days = int(agg[2] or 0)
        if total_qty == 0 and c.balance == 0:
            continue

        # PHASE 21: has this customer already been invoiced for this window?
        existing_invoice = (
            MilkInvoice.query
            .filter_by(customer_id=c.id, is_archived=False,
                       period_from=start, period_to=end)
            .first()
        )

        # Status chip decision
        if total_qty == 0:
            status = "no-activity"   # only carries a historic balance
        elif existing_invoice:
            status = "invoiced"
        elif c.contract_type == "weekly":
            status = "ready"         # will be picked up by bulk generator
        else:
            status = "cash"          # cash contract — no invoice needed

        rows.append({
            "customer": c,
            "week_qty": total_qty,
            "week_value": total_value,
            "delivery_days": delivery_days,
            "total_owed": c.balance,
            "status": status,
            "invoice": existing_invoice,
        })

    # KPI totals for the hero strip + preview card
    active_count = sum(1 for r in rows if r["week_qty"] > 0)
    total_qty = sum((r["week_qty"] for r in rows), Decimal("0"))
    total_value = sum((r["week_value"] for r in rows), Decimal("0"))
    total_owed = sum((r["total_owed"] for r in rows if r["total_owed"] > 0),
                     Decimal("0"))
    ready_count = sum(1 for r in rows if r["status"] == "ready")
    ready_value = sum((r["week_value"] for r in rows if r["status"] == "ready"),
                      Decimal("0"))
    invoiced_count = sum(1 for r in rows if r["status"] == "invoiced")

    return render_template(
        "customers/settlement.html",
        rows=rows, start=start, end=end,
        prev_end=prev_end, next_end=next_end,
        active_count=active_count,
        total_qty=total_qty, total_value=total_value, total_owed=total_owed,
        ready_count=ready_count, ready_value=ready_value,
        invoiced_count=invoiced_count,
    )


# ---------- Customers report ----------
def _report_args():
    """Shared filter parsing so the screen and both exports can't drift apart."""
    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = date.fromisoformat(to) if to else today
    return d_from, d_to, request.args.get("customer_id", type=int)


def _report_data(d_from, d_to, customer_id):
    """Everything about a customer over a period: what he took, what he paid,
    what he still owes, and the invoices behind it."""
    def scope(q):
        return q.filter_by(customer_id=customer_id) if customer_id else q

    deliveries = scope(
        MilkDelivery.query.filter(
            MilkDelivery.is_archived.is_(False),
            MilkDelivery.delivery_date >= d_from,
            MilkDelivery.delivery_date <= d_to,
        )
    ).order_by(MilkDelivery.delivery_date.desc(), MilkDelivery.id.desc()).all()

    invoices = scope(
        MilkInvoice.query.filter(
            MilkInvoice.is_archived.is_(False),
            MilkInvoice.issue_date >= d_from,
            MilkInvoice.issue_date <= d_to,
        )
    ).order_by(MilkInvoice.issue_date.desc()).all()

    payments = scope(
        CustomerPayment.query.filter(
            CustomerPayment.is_archived.is_(False),
            CustomerPayment.payment_date >= d_from,
            CustomerPayment.payment_date <= d_to,
        )
    ).order_by(CustomerPayment.payment_date.desc()).all()

    all_customers = Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    statement = [c for c in all_customers if c.id == customer_id] if customer_id else all_customers

    return {
        "deliveries": deliveries,
        "invoices": invoices,
        "payments": payments,
        "all_customers": all_customers,
        "statement": statement,
        # an unpriced delivery has total_value = None and must not be counted
        "period_qty": sum((d.qty_kg for d in deliveries), Decimal("0")),
        "period_value": sum(
            (d.total_value for d in deliveries if d.total_value is not None), Decimal("0")
        ),
        "period_paid": sum((p.amount for p in payments), Decimal("0")),
        "balance_now": sum((c.balance for c in statement), Decimal("0")),
        "unpriced_count": sum(1 for d in deliveries if d.total_value is None),
    }


@bp.route("/report")
@login_required
def customers_report():
    d_from, d_to, customer_id = _report_args()
    data = _report_data(d_from, d_to, customer_id)
    return render_template(
        "customers/report.html",
        date_from=d_from, date_to=d_to,
        selected_customer_id=customer_id,
        **data,
    )


@bp.route("/report/excel")
@login_required
def customers_report_excel():
    d_from, d_to, customer_id = _report_args()
    data = _report_data(d_from, d_to, customer_id)
    rows = [
        [
            d.delivery_date.isoformat(),
            d.customer.name,
            float(d.qty_kg),
            float(d.unit_price) if d.unit_price is not None else "",
            float(d.base_value),
            float(d.subtotal),
            float(d.total_value) if d.total_value is not None else "بانتظار التسعير",
        ]
        for d in data["deliveries"]
    ]
    return excel_response(
        "Customers",
        ["التاريخ", "العميل", "الكمية (كيلو)", "السعر", "الثمن", "الإجمالي", "الصافي"],
        rows,
        f"customers_report_{d_from}_{d_to}.xlsx",
    )


@bp.route("/report/pdf")
@login_required
def customers_report_pdf():
    d_from, d_to, customer_id = _report_args()
    target = url_for(
        "customers.customers_report", date_from=d_from.isoformat(),
        date_to=d_to.isoformat(), customer_id=customer_id, _external=True,
    )
    return pdf_from_current_page(target, f"customers_report_{d_from}_{d_to}.pdf")


# ==================== PHASE 3: weekly generation + dues ====================

@bp.route("/generate-weekly-invoices", methods=["POST"])
@login_required
def generate_weekly_invoices():
    """Admin action — for every weekly-contract customer, gather every
    priced un-invoiced delivery from the past 7 days into ONE draft
    invoice per customer. Idempotent: a second POST with no new
    deliveries creates zero invoices; a POST while a draft already
    exists for the same customer + range attaches new deliveries to it
    instead of creating a duplicate."""
    if not current_user.is_admin:
        abort(403)
    from app.models.sales import MilkInvoice, MilkDelivery

    today = date.today()
    week_start = today - timedelta(days=6)

    customers = (
        Customer.query
        .filter_by(is_archived=False, contract_type=Customer.CONTRACT_WEEKLY)
        .all()
    )

    created = 0
    updated = 0
    for c in customers:
        deliveries = (
            MilkDelivery.query
            .filter_by(customer_id=c.id, is_archived=False)
            .filter(
                MilkDelivery.invoice_id.is_(None),
                MilkDelivery.total_value.isnot(None),
                MilkDelivery.delivery_date >= week_start,
                MilkDelivery.delivery_date <= today,
            )
            .order_by(MilkDelivery.delivery_date)
            .all()
        )
        if not deliveries:
            continue

        # Reuse an existing draft for the exact same window if any.
        inv = (
            MilkInvoice.query
            .filter_by(customer_id=c.id, status=MilkInvoice.STATUS_DRAFT,
                       period_from=week_start, period_to=today,
                       is_archived=False)
            .first()
        )
        if inv is None:
            inv = MilkInvoice(
                customer_id=c.id, period_from=week_start, period_to=today,
                issue_date=today, status=MilkInvoice.STATUS_DRAFT,
                created_by_id=current_user.id,
            )
            db.session.add(inv); db.session.flush()
            created += 1
        else:
            updated += 1

        for d in deliveries:
            d.invoice_id = inv.id
        inv.recompute_total()

    db.session.commit()
    if created == 0 and updated == 0:
        flash("مفيش توريدات جديدة تحتاج فاتورة أسبوعية.", "info")
    else:
        flash(
            f"تم إنشاء {created} فاتورة جديدة"
            + (f" وتحديث {updated}." if updated else "."),
            "success",
        )
    return redirect(url_for("customers.weekly_settlement"))


@bp.route("/dues")
@login_required
def dues():
    """المستحقات — every ISSUED milk invoice with outstanding > 0, aged
    into four buckets (0-14, 15-30, 31-60, >60 days), grouped per
    customer. Rendered as one big table with per-customer subtotals."""
    from app.models.sales import MilkInvoice

    today = date.today()
    invs = (
        MilkInvoice.query
        .filter_by(is_archived=False, status=MilkInvoice.STATUS_ISSUED)
        .order_by(MilkInvoice.customer_id, MilkInvoice.issue_date)
        .all()
    )

    # Cheap in-Python bucketing — the ledger is small enough this is fine
    def bucket(days):
        if days <= 14: return "b_0_14"
        if days <= 30: return "b_15_30"
        if days <= 60: return "b_31_60"
        return "b_60_plus"

    per_customer: dict[int, dict] = {}
    totals = {"b_0_14": Decimal("0"), "b_15_30": Decimal("0"),
              "b_31_60": Decimal("0"), "b_60_plus": Decimal("0"),
              "grand": Decimal("0")}

    for i in invs:
        out = i.outstanding_amount
        if out <= 0:
            continue
        days = (today - i.issue_date).days
        b = bucket(days)
        c = per_customer.setdefault(i.customer_id, {
            "customer": i.customer, "invoices": [],
            "b_0_14": Decimal("0"), "b_15_30": Decimal("0"),
            "b_31_60": Decimal("0"), "b_60_plus": Decimal("0"),
            "total": Decimal("0"),
        })
        c["invoices"].append({"inv": i, "days": days, "bucket": b, "outstanding": out})
        c[b] += out
        c["total"] += out
        totals[b] += out
        totals["grand"] += out

    rows = sorted(per_customer.values(), key=lambda r: r["customer"].name)
    return render_template("customers/dues.html", rows=rows, totals=totals, today=today)
