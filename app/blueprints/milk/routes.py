from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.sales import DailyProductionForm, MilkDeliveryForm
from app.models.finance import Setting
from app.models.sales import Customer, DailyProduction, MilkDelivery, MilkInvoice
from app.utils.reports import excel_response
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
        protein = Decimal(str(form.protein_pct.data)) if form.protein_pct.data is not None else None
        bacteria = Decimal(str(form.bacteria_count.data)) if form.bacteria_count.data is not None else None

        # Unit price: form override > customer fixed price > quality formula
        if form.unit_price.data is not None and form.unit_price.data != 0:
            unit_price = Decimal(str(form.unit_price.data))
        elif customer.pricing_type == Customer.PRICING_FIXED and customer.fixed_price:
            unit_price = Decimal(str(customer.fixed_price))
        elif protein is not None and bacteria is not None:
            unit_price = price_for_quality(protein, bacteria)
        else:
            flash("لازم تدخل سعر يدوي أو تحدد سعر ثابت للعميل أو تدخل بروتين + بكتيريا.", "error")
            return render_template("milk/delivery_form.html", form=form, customers=customers)

        def dec(v):
            return Decimal(str(v)) if v is not None else Decimal("0")

        base = (qty * unit_price).quantize(Decimal("0.01"))
        fat_b = dec(form.fat_bonus.data)
        prot_b = dec(form.protein_bonus.data)
        bact_a = dec(form.bacteria_adj.data)
        trans = dec(form.transport.data)
        other = dec(form.other_adj.data)
        subtotal = (base + fat_b + prot_b + bact_a + trans + other).quantize(Decimal("0.01"))

        qty_d = dec(form.qty_deduction.data)
        cash_d = dec(form.cash_deduction.data)
        rnd = dec(form.rounding.data)
        total = (subtotal - qty_d - cash_d + rnd).quantize(Decimal("0.01"))

        delivery = MilkDelivery(
            customer_id=customer.id,
            delivery_date=form.delivery_date.data,
            qty_kg=qty,
            protein_pct=protein,
            bacteria_count=int(bacteria) if bacteria is not None else None,
            base_value=base,
            fat_bonus=fat_b, protein_bonus=prot_b, bacteria_adj=bact_a,
            transport=trans, other_adj=other,
            subtotal=subtotal,
            qty_deduction=qty_d, cash_deduction=cash_d, rounding=rnd,
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


# ---------- Milk invoices (client's Excel format) ----------
@bp.route("/invoices")
@login_required
def list_invoices():
    invoices = (
        MilkInvoice.query.filter_by(is_archived=False)
        .order_by(MilkInvoice.issue_date.desc(), MilkInvoice.id.desc())
        .limit(200)
        .all()
    )
    return render_template("milk/invoices_list.html", invoices=invoices)


@bp.route("/invoices/new", methods=["GET", "POST"])
@login_required
def create_invoice():
    """Consolidator: pick customer + period, generate an invoice linking all
    matching deliveries. Existing invoiced deliveries are excluded."""
    customers = Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()

    if request.method == "POST":
        customer_id = request.form.get("customer_id", type=int)
        period_from = request.form.get("period_from")
        period_to = request.form.get("period_to")
        invoice_number = (request.form.get("invoice_number") or "").strip() or None

        if not (customer_id and period_from and period_to):
            flash("املأ كل الحقول: العميل + الفترة.", "error")
            return render_template("milk/invoice_form.html", customers=customers)

        d_from = date.fromisoformat(period_from)
        d_to = date.fromisoformat(period_to)

        deliveries = (
            MilkDelivery.query
            .filter(
                MilkDelivery.customer_id == customer_id,
                MilkDelivery.is_archived.is_(False),
                MilkDelivery.invoice_id.is_(None),
                MilkDelivery.delivery_date >= d_from,
                MilkDelivery.delivery_date <= d_to,
            )
            .order_by(MilkDelivery.delivery_date)
            .all()
        )
        if not deliveries:
            flash("مفيش توريدات غير مفوترة للعميل ده في الفترة دي.", "error")
            return render_template("milk/invoice_form.html", customers=customers)

        invoice = MilkInvoice(
            customer_id=customer_id,
            invoice_number=invoice_number,
            period_from=d_from,
            period_to=d_to,
            status=MilkInvoice.STATUS_DRAFT,
            created_by_id=current_user.id,
        )
        db.session.add(invoice)
        db.session.flush()
        for d in deliveries:
            d.invoice_id = invoice.id
        invoice.recompute_total()
        log_action("milk_invoice_created", "MilkInvoice", invoice.id,
                   details=f"customer={customer_id} lines={len(deliveries)} total={invoice.grand_total}")
        db.session.commit()
        flash(f"تم إنشاء فاتورة #{invoice.id} بإجمالي {invoice.grand_total} جنيه.", "success")
        return redirect(url_for("milk.view_invoice", invoice_id=invoice.id))

    return render_template("milk/invoice_form.html", customers=customers)


@bp.route("/invoices/<int:invoice_id>")
@login_required
def view_invoice(invoice_id: int):
    invoice = db.session.get(MilkInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        return render_template("errors/404.html"), 404
    return render_template("milk/invoice_view.html", invoice=invoice)


@bp.route("/invoices/<int:invoice_id>/excel")
@login_required
def invoice_excel(invoice_id: int):
    invoice = db.session.get(MilkInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        return render_template("errors/404.html"), 404

    headers = [
        "م", "التاريخ", "يوم", "شهر", "اسم العميل", "نوع العملية", "النشاط", "البند",
        "اسم المنتج", "الكمية", "الوحدة", "السعر", "الثمن",
        "الدهن", "البروتين", "البكتيريا", "النقل", "أخرى", "الإجمالي",
        "خ كمية", "خ نقدي", "كسور", "الصافي", "إجمالي الفاتورة", "ملاحظات",
    ]
    DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    rows = []
    running_total = Decimal("0")
    for idx, d in enumerate(invoice.deliveries, start=1):
        running_total += d.total_value
        rows.append([
            idx,
            d.delivery_date.isoformat(),
            DAY_LABELS[d.delivery_date.weekday()],
            MONTH_LABELS[d.delivery_date.month - 1],
            invoice.customer.name,
            "أجل" if invoice.customer.contract_type == "weekly" else "نقدي",
            "الإنتاج الحيواني",
            "مبيعات ألبان خام",
            "ألبان خام",
            float(d.qty_kg),
            "كيلوجرام",
            float(d.unit_price),
            float(d.base_value),
            float(d.fat_bonus),
            float(d.protein_bonus),
            float(d.bacteria_adj),
            float(d.transport),
            float(d.other_adj),
            float(d.subtotal),
            float(d.qty_deduction),
            float(d.cash_deduction),
            float(d.rounding),
            float(d.total_value),
            float(running_total) if idx == len(invoice.deliveries) else "",
            d.notes or "",
        ])

    return excel_response(
        "فاتورة بيع اللبن",
        headers,
        rows,
        f"milk_invoice_{invoice.id}.xlsx",
    )


@bp.route("/invoices/<int:invoice_id>/issue", methods=["POST"])
@login_required
def issue_invoice(invoice_id: int):
    invoice = db.session.get(MilkInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        return render_template("errors/404.html"), 404
    if invoice.status == MilkInvoice.STATUS_DRAFT:
        invoice.status = MilkInvoice.STATUS_ISSUED
        log_action("milk_invoice_issued", "MilkInvoice", invoice.id)
        db.session.commit()
        flash(f"تم اعتماد الفاتورة #{invoice.id}.", "success")
    return redirect(url_for("milk.view_invoice", invoice_id=invoice.id))
