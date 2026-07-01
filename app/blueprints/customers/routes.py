from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.sales import CustomerForm, CustomerPaymentForm
from app.models.sales import Customer, CustomerPayment, MilkDelivery
from app.utils.audit import log_action

bp = Blueprint("customers", __name__, template_folder="../../templates/customers")


@bp.route("/")
@login_required
def list_customers():
    customers = Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    total_owed_to_us = sum((c.balance for c in customers), Decimal("0"))
    return render_template("customers/list.html", customers=customers, total_owed_to_us=total_owed_to_us)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_customer():
    form = CustomerForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if Customer.query.filter(func.lower(Customer.name) == name.lower()).first():
            flash("عميل بنفس الاسم مسجّل قبل كده.", "error")
        else:
            c = Customer(
                name=name,
                phone=(form.phone.data or "").strip() or None,
                contract_type=form.contract_type.data,
                pricing_type=form.pricing_type.data,
                fixed_price=form.fixed_price.data
                if form.pricing_type.data == Customer.PRICING_FIXED
                else None,
                notes=form.notes.data,
                created_by_id=current_user.id,
            )
            db.session.add(c)
            db.session.flush()
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
    deliveries = (
        MilkDelivery.query.filter_by(customer_id=customer.id, is_archived=False)
        .order_by(MilkDelivery.delivery_date.desc())
        .limit(50)
        .all()
    )
    payments = (
        CustomerPayment.query.filter_by(customer_id=customer.id, is_archived=False)
        .order_by(CustomerPayment.payment_date.desc())
        .limit(50)
        .all()
    )
    payment_form = CustomerPaymentForm()
    return render_template(
        "customers/detail.html",
        customer=customer,
        deliveries=deliveries,
        payments=payments,
        payment_form=payment_form,
    )


@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id: int):
    customer = db.session.get(Customer, customer_id)
    if not customer or customer.is_archived:
        abort(404)
    form = CustomerForm(obj=customer)
    if form.validate_on_submit():
        customer.name = form.name.data.strip()
        customer.phone = (form.phone.data or "").strip() or None
        customer.contract_type = form.contract_type.data
        customer.pricing_type = form.pricing_type.data
        customer.fixed_price = (
            form.fixed_price.data if form.pricing_type.data == Customer.PRICING_FIXED else None
        )
        customer.notes = form.notes.data
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
    if not form.validate_on_submit():
        for _, errors in form.errors.items():
            for e in errors:
                flash(e, "error")
        return redirect(url_for("customers.customer_detail", customer_id=customer.id))

    payment = CustomerPayment(
        customer_id=customer.id,
        amount=Decimal(str(form.amount.data)),
        payment_date=form.payment_date.data,
        method=form.method.data,
        notes=form.notes.data,
        created_by_id=current_user.id,
    )
    db.session.add(payment)
    db.session.flush()
    log_action(
        "customer_payment", "CustomerPayment", payment.id,
        details=f"customer={customer.id} amount={payment.amount}",
    )
    db.session.commit()
    flash(f"تم تسجيل دفعة {payment.amount} من {customer.name}.", "success")
    return redirect(url_for("customers.customer_detail", customer_id=customer.id))


# ---------- US-4.3 Weekly settlement report ----------
@bp.route("/settlement")
@login_required
def weekly_settlement():
    end_str = request.args.get("end")
    end = date.fromisoformat(end_str) if end_str else date.today()
    start = end - timedelta(days=6)

    customers = Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    rows = []
    for c in customers:
        agg = db.session.query(
            func.coalesce(func.sum(MilkDelivery.qty_kg), 0),
            func.coalesce(func.sum(MilkDelivery.total_value), 0),
        ).filter(
            MilkDelivery.customer_id == c.id,
            MilkDelivery.delivery_date >= start,
            MilkDelivery.delivery_date <= end,
            MilkDelivery.is_archived.is_(False),
        ).one()
        total_qty = Decimal(str(agg[0] or 0))
        total_value = Decimal(str(agg[1] or 0))
        if total_qty == 0 and c.balance == 0:
            continue
        rows.append({
            "customer": c,
            "week_qty": total_qty,
            "week_value": total_value,
            "total_owed": c.balance,
        })
    return render_template(
        "customers/settlement.html", rows=rows, start=start, end=end,
    )
