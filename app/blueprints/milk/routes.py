from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.sales import DailyProductionForm, MilkDeliveryForm
from app.models.finance import Setting
from app.models.sales import Customer, DailyProduction, MilkDelivery
from app.utils.audit import log_action

bp = Blueprint("milk", __name__, template_folder="../../templates/milk")


def price_for_quality(protein_pct: Decimal, bacteria_cfu: Decimal) -> Decimal:
    """Configurable quality-based price formula.

    price = base + max(0, (protein − 3.0)) × protein_adj
                − max(0, (bacteria − 100k) / 100k) × bacteria_penalty
    All coefficients editable in Settings without code change.
    """
    base = Setting.get_decimal(Setting.KEY_QUALITY_PRICE_BASE, Decimal("6"))
    p_adj = Setting.get_decimal(Setting.KEY_QUALITY_PROTEIN_ADJ, Decimal("0.5"))
    b_pen = Setting.get_decimal(Setting.KEY_QUALITY_BACTERIA_PENALTY, Decimal("0.25"))

    p_bonus = max(Decimal("0"), Decimal(str(protein_pct)) - Decimal("3.0")) * p_adj
    b_units_over = max(Decimal("0"), (Decimal(str(bacteria_cfu)) - Decimal("100000")) / Decimal("100000"))
    b_penalty = b_units_over * b_pen

    price = base + p_bonus - b_penalty
    return max(Decimal("0"), price).quantize(Decimal("0.001"))


@bp.route("/deliveries")
@login_required
def list_deliveries():
    day_str = request.args.get("day")
    day = date.fromisoformat(day_str) if day_str else date.today()

    deliveries = (
        MilkDelivery.query.filter_by(delivery_date=day, is_archived=False)
        .order_by(MilkDelivery.id.desc())
        .all()
    )
    day_qty = sum((d.qty_kg for d in deliveries), Decimal("0"))
    day_value = sum((d.total_value for d in deliveries), Decimal("0"))

    production = DailyProduction.query.filter_by(production_date=day).first()
    waste = (production.total_kg - day_qty) if production else None
    if waste is not None and waste < 0:
        waste = Decimal("0")

    return render_template(
        "milk/deliveries.html",
        deliveries=deliveries,
        day=day,
        day_qty=day_qty,
        day_value=day_value,
        production=production,
        waste=waste,
    )


@bp.route("/deliveries/new", methods=["GET", "POST"])
@login_required
def create_delivery():
    form = MilkDeliveryForm()
    customers = (
        Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    )
    form.customer_id.choices = [(c.id, f"{c.name} ({c.pricing_label})") for c in customers]

    if form.validate_on_submit():
        customer = db.session.get(Customer, form.customer_id.data)
        if not customer or customer.is_archived:
            flash("العميل غير صالح.", "error")
            return render_template("milk/delivery_form.html", form=form, customers=customers)

        qty = Decimal(str(form.qty_kg.data))

        if customer.pricing_type == Customer.PRICING_FIXED:
            if not customer.fixed_price:
                flash("العميل مسعّر ثابت بس مفيش سعر محدد. عدّل بياناته أول.", "error")
                return render_template("milk/delivery_form.html", form=form, customers=customers)
            unit_price = Decimal(str(customer.fixed_price))
            protein = None
            bacteria = None
        else:
            if form.protein_pct.data is None or form.bacteria_count.data is None:
                flash("لازم تدخل البروتين والبكتيريا للعميل المسعّر بالتحليل.", "error")
                return render_template("milk/delivery_form.html", form=form, customers=customers)
            protein = Decimal(str(form.protein_pct.data))
            bacteria = Decimal(str(form.bacteria_count.data))
            unit_price = price_for_quality(protein, bacteria)

        total = (qty * unit_price).quantize(Decimal("0.01"))
        delivery = MilkDelivery(
            customer_id=customer.id,
            delivery_date=form.delivery_date.data,
            qty_kg=qty,
            protein_pct=protein,
            bacteria_count=int(bacteria) if bacteria is not None else None,
            unit_price=unit_price,
            total_value=total,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(delivery)
        db.session.flush()
        log_action(
            "milk_delivery_created", "MilkDelivery", delivery.id,
            details=f"customer={customer.id} qty={qty} price={unit_price} total={total}",
        )
        db.session.commit()
        flash(f"تم تسجيل توريد {qty}kg لـ {customer.name} بسعر {unit_price} = {total} جنيه.", "success")
        return redirect(url_for("milk.list_deliveries", day=delivery.delivery_date.isoformat()))

    return render_template("milk/delivery_form.html", form=form, customers=customers)


# ---------- US-4.4 Daily production ----------
@bp.route("/production", methods=["GET", "POST"])
@login_required
def daily_production():
    day_str = request.args.get("day")
    day = date.fromisoformat(day_str) if day_str else date.today()

    existing = DailyProduction.query.filter_by(production_date=day).first()
    form = DailyProductionForm()
    if request.method == "GET":
        form.production_date.data = day
        if existing:
            form.total_kg.data = existing.total_kg
            form.notes.data = existing.notes

    if form.validate_on_submit():
        target_day = form.production_date.data
        existing = DailyProduction.query.filter_by(production_date=target_day).first()
        if existing:
            existing.total_kg = form.total_kg.data
            existing.notes = form.notes.data
        else:
            existing = DailyProduction(
                production_date=target_day,
                total_kg=form.total_kg.data,
                notes=form.notes.data,
                created_by_id=current_user.id,
            )
            db.session.add(existing)
        log_action("daily_production", "DailyProduction", existing.id or 0, details=str(target_day))
        db.session.commit()
        flash(f"تم حفظ إنتاج يوم {target_day}.", "success")
        return redirect(url_for("milk.list_deliveries", day=target_day.isoformat()))

    # Monthly summary (for AC "تقرير شهري")
    today = date.today()
    month_start = today.replace(day=1)
    rows = (
        DailyProduction.query
        .filter(DailyProduction.production_date >= month_start)
        .order_by(DailyProduction.production_date.desc())
        .all()
    )
    total_prod = sum((r.total_kg for r in rows), Decimal("0"))
    total_delivered_month = (
        db.session.query(func.coalesce(func.sum(MilkDelivery.qty_kg), 0))
        .filter(
            MilkDelivery.delivery_date >= month_start,
            MilkDelivery.is_archived.is_(False),
        )
        .scalar()
    )
    total_delivered_month = Decimal(str(total_delivered_month or 0))
    total_waste = total_prod - total_delivered_month
    if total_waste < 0:
        total_waste = Decimal("0")
    waste_pct = (
        (total_waste / total_prod * 100).quantize(Decimal("0.01")) if total_prod > 0 else Decimal("0")
    )
    return render_template(
        "milk/production.html",
        form=form, day=day, existing=existing,
        month_rows=rows,
        total_prod=total_prod,
        total_delivered_month=total_delivered_month,
        total_waste=total_waste,
        waste_pct=waste_pct,
    )
