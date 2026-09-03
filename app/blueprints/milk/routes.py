from datetime import date
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


def price_for_quality(
    protein_pct: Decimal, bacteria_cfu: Decimal, fat_pct: Decimal | None = None
) -> Decimal:
    """Configurable quality-based price formula.

    price = base + max(0, (protein − 3.0)) × protein_adj
                + max(0, (fat − fat_ref)) × fat_adj
                − max(0, (bacteria − 100k) / 100k) × bacteria_penalty
    All coefficients editable in Settings without code change.

    TICKET-A: fat is optional. It is skipped entirely when not measured, and
    fat_adj ships at 0 so adding the term reprices nothing until the client
    enters his own rate.
    """
    base = Setting.get_decimal(Setting.KEY_QUALITY_PRICE_BASE, Decimal("6"))
    p_adj = Setting.get_decimal(Setting.KEY_QUALITY_PROTEIN_ADJ, Decimal("0.5"))
    b_pen = Setting.get_decimal(Setting.KEY_QUALITY_BACTERIA_PENALTY, Decimal("0.25"))
    f_ref = Setting.get_decimal(Setting.KEY_QUALITY_FAT_REF, Decimal("3.0"))
    f_adj = Setting.get_decimal(Setting.KEY_QUALITY_FAT_ADJ, Decimal("0"))

    p_bonus = max(Decimal("0"), Decimal(str(protein_pct)) - Decimal("3.0")) * p_adj
    b_units_over = max(Decimal("0"), (Decimal(str(bacteria_cfu)) - Decimal("100000")) / Decimal("100000"))
    b_penalty = b_units_over * b_pen
    f_bonus = (
        max(Decimal("0"), Decimal(str(fat_pct)) - f_ref) * f_adj
        if fat_pct is not None
        else Decimal("0")
    )

    price = base + p_bonus + f_bonus - b_penalty
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
    # TICKET-4: unpriced deliveries have total_value = None
    day_value = sum((d.total_value for d in deliveries if d.total_value is not None), Decimal("0"))
    unpriced_count = sum(1 for d in deliveries if not d.is_priced)

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
        unpriced_count=unpriced_count,
    )


def _dec(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _resolve_unit_price(form, customer):
    """Unit price: form override > customer fixed price > quality formula.

    Returns the price, or None when it cannot be resolved — in which case the
    reason has already been flashed and pinned to the offending field.
    """
    if form.unit_price.data is not None and form.unit_price.data != 0:
        return Decimal(str(form.unit_price.data))

    if customer.pricing_type == Customer.PRICING_FIXED and customer.fixed_price:
        return Decimal(str(customer.fixed_price))

    protein = form.protein_pct.data
    bacteria = form.bacteria_count.data
    fat = form.fat_pct.data
    if protein is not None and bacteria is not None:
        return price_for_quality(
            Decimal(str(protein)),
            Decimal(str(bacteria)),
            Decimal(str(fat)) if fat is not None else None,
        )

    if customer.pricing_type == Customer.PRICING_QUALITY:
        # TICKET-3: quality pricing needs BOTH readings — say which one is missing
        # and pin it to the field so it renders next to the input, not just as a flash.
        missing = []
        if protein is None:
            missing.append("نسبة البروتين")
            form.protein_pct.errors.append("مطلوب لأن تسعير العميل على أساس التحليل.")
        if bacteria is None:
            missing.append("عدد البكتيريا")
            form.bacteria_count.errors.append("مطلوب لأن تسعير العميل على أساس التحليل.")
        flash(
            f"العميل ({customer.name}) تسعيره على أساس التحليل — لازم تدخل: "
            f"{' و '.join(missing)}. أو اكتب سعر يدوي في خانة السعر.",
            "error",
        )
        return None

    flash("لازم تدخل سعر يدوي أو تحدد سعر ثابت للعميل أو تدخل بروتين + بكتيريا.", "error")
    return None


def _apply_form(delivery, form, customer, *, unpriced: bool) -> bool:
    """TICKET-4: fill a delivery from the form, priced or not.

    Shared by create and edit so the two can't drift apart. Returns False when
    pricing was required but couldn't be resolved (reason already flashed).
    """
    unit_price = None
    if not unpriced:
        unit_price = _resolve_unit_price(form, customer)
        if unit_price is None:
            return False

    qty = Decimal(str(form.qty_kg.data))
    protein = Decimal(str(form.protein_pct.data)) if form.protein_pct.data is not None else None
    bacteria = Decimal(str(form.bacteria_count.data)) if form.bacteria_count.data is not None else None
    fat = Decimal(str(form.fat_pct.data)) if form.fat_pct.data is not None else None

    fat_b = _dec(form.fat_bonus.data)
    prot_b = _dec(form.protein_bonus.data)
    bact_a = _dec(form.bacteria_adj.data)
    trans = _dec(form.transport.data)
    other = _dec(form.other_adj.data)
    qty_d = _dec(form.qty_deduction.data)
    cash_d = _dec(form.cash_deduction.data)
    rnd = _dec(form.rounding.data)

    base = (qty * unit_price).quantize(Decimal("0.01")) if unit_price is not None else Decimal("0")
    # TICKET-A: التعديلات are rates per kilo, so they scale with the delivery.
    # They used to be added as flat amounts, which made every quality adjustment
    # the client entered wrong by a factor of the quantity.
    additions = ((fat_b + prot_b + bact_a + trans + other) * qty).quantize(Decimal("0.01"))
    subtotal = (base + additions).quantize(Decimal("0.01"))
    # An unpriced delivery has no net value at all — that NULL is what keeps it
    # out of balances, invoices and the settlement report until it is priced.
    total = (subtotal - qty_d - cash_d + rnd).quantize(Decimal("0.01")) if unit_price is not None else None

    delivery.customer_id = customer.id
    delivery.delivery_date = form.delivery_date.data
    delivery.qty_kg = qty
    delivery.protein_pct = protein
    delivery.bacteria_count = int(bacteria) if bacteria is not None else None
    delivery.fat_pct = fat
    delivery.base_value = base
    delivery.fat_bonus, delivery.protein_bonus, delivery.bacteria_adj = fat_b, prot_b, bact_a
    delivery.transport, delivery.other_adj = trans, other
    delivery.subtotal = subtotal
    delivery.qty_deduction, delivery.cash_deduction, delivery.rounding = qty_d, cash_d, rnd
    delivery.unit_price = unit_price
    delivery.total_value = total
    delivery.notes = form.notes.data
    return True


@bp.route("/deliveries/new", methods=["GET", "POST"])
@login_required
def create_delivery():
    form = MilkDeliveryForm()
    customers = (
        Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    )
    form.customer_id.choices = [(c.id, f"{c.name} ({c.pricing_label})") for c in customers]
    # TICKET-4: second submit button — save now, price later
    unpriced = "save_unpriced" in request.form

    if form.validate_on_submit():
        customer = db.session.get(Customer, form.customer_id.data)
        if not customer or customer.is_archived:
            flash("العميل غير صالح.", "error")
            return render_template("milk/delivery_form.html", form=form, customers=customers, mode="create")

        delivery = MilkDelivery(created_by_id=current_user.id)
        if not _apply_form(delivery, form, customer, unpriced=unpriced):
            return render_template("milk/delivery_form.html", form=form, customers=customers, mode="create")

        db.session.add(delivery)
        db.session.flush()
        # ACCOUNTING: post the sale JE. Skipped for unpriced deliveries by
        # the autoposter itself — nothing to post until there's a real number.
        from app.services import autoposting
        autoposting.on_milk_delivery_priced(delivery, created_by=current_user.id)
        log_action(
            "milk_delivery_created", "MilkDelivery", delivery.id,
            details=f"customer={customer.id} qty={delivery.qty_kg} "
                    f"price={delivery.unit_price} total={delivery.total_value}",
        )
        db.session.commit()
        if delivery.is_priced:
            flash(
                f"تم تسجيل توريد {delivery.qty_kg}kg لـ {customer.name} "
                f"بسعر {delivery.unit_price} = {delivery.total_value} جنيه.",
                "success",
            )
        else:
            flash(
                f"تم تسجيل توريد {delivery.qty_kg}kg لـ {customer.name} بدون سعر. "
                "التوريد مش هيدخل في حساب العميل ولا في أي فاتورة لحد ما تسعّره.",
                "warning",
            )
        return redirect(url_for("milk.list_deliveries", day=delivery.delivery_date.isoformat()))

    return render_template("milk/delivery_form.html", form=form, customers=customers, mode="create")


@bp.route("/deliveries/<int:delivery_id>/edit", methods=["GET", "POST"])
@login_required
def edit_delivery(delivery_id: int):
    """TICKET-4: add or correct a delivery's price after the fact."""
    delivery = db.session.get(MilkDelivery, delivery_id)
    if not delivery or delivery.is_archived:
        abort(404)

    if delivery.is_locked:
        flash(
            f"التوريد ده على فاتورة صادرة (#{delivery.invoice_id}) — مش ممكن يتعدل. "
            "لو فيه غلط، اعمل تسوية على الفاتورة نفسها.",
            "error",
        )
        return redirect(url_for("milk.list_deliveries", day=delivery.delivery_date.isoformat()))

    customers = Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    form = MilkDeliveryForm(obj=delivery)
    form.customer_id.choices = [(c.id, f"{c.name} ({c.pricing_label})") for c in customers]
    if request.method == "GET":
        form.customer_id.data = delivery.customer_id
    unpriced = "save_unpriced" in request.form

    if form.validate_on_submit():
        customer = db.session.get(Customer, form.customer_id.data)
        if not customer or customer.is_archived:
            flash("العميل غير صالح.", "error")
            return render_template("milk/delivery_form.html", form=form, customers=customers,
                                   mode="edit", delivery=delivery)

        # The customer and the date are what decide which invoice a line belongs
        # to. Once the delivery is on an invoice they must not move, or the
        # invoice ends up holding another customer's milk — and invoice_excel
        # prints invoice.customer.name on every row, so it would mislabel it.
        if delivery.invoice:
            if customer.id != delivery.customer_id:
                flash(
                    f"مش ممكن تغيّر العميل — التوريد ده على فاتورة #{delivery.invoice_id}. "
                    "لو غلط، شيله من الفاتورة الأول.",
                    "error",
                )
                return render_template("milk/delivery_form.html", form=form, customers=customers,
                                       mode="edit", delivery=delivery)
            if form.delivery_date.data != delivery.delivery_date:
                flash(
                    f"مش ممكن تغيّر التاريخ — التوريد ده على فاتورة #{delivery.invoice_id} "
                    f"لفترة {delivery.invoice.period_from} إلى {delivery.invoice.period_to}.",
                    "error",
                )
                return render_template("milk/delivery_form.html", form=form, customers=customers,
                                       mode="edit", delivery=delivery)

        old_price, old_total = delivery.unit_price, delivery.total_value
        if not _apply_form(delivery, form, customer, unpriced=unpriced):
            return render_template("milk/delivery_form.html", form=form, customers=customers,
                                   mode="edit", delivery=delivery)

        # A draft invoice's total has to follow the line it contains
        if delivery.invoice and delivery.invoice.status == MilkInvoice.STATUS_DRAFT:
            delivery.invoice.recompute_total()

        # ACCOUNTING: re-post the delivery's JE. The autoposter deletes any
        # prior JE and posts a fresh one so a re-price updates the ledger.
        from app.services import autoposting
        autoposting.on_milk_delivery_priced(delivery, created_by=current_user.id)

        log_action(
            "milk_delivery_updated", "MilkDelivery", delivery.id,
            details=f"price {old_price} -> {delivery.unit_price}; "
                    f"total {old_total} -> {delivery.total_value}",
        )
        db.session.commit()
        flash(
            f"تم تحديث التوريد — الصافي {delivery.total_value} جنيه."
            if delivery.is_priced else "تم تحديث التوريد — لسه بدون سعر.",
            "success" if delivery.is_priced else "warning",
        )
        return redirect(url_for("milk.list_deliveries", day=delivery.delivery_date.isoformat()))

    return render_template("milk/delivery_form.html", form=form, customers=customers,
                           mode="edit", delivery=delivery)


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

        period_filter = (
            MilkDelivery.customer_id == customer_id,
            MilkDelivery.is_archived.is_(False),
            MilkDelivery.invoice_id.is_(None),
            MilkDelivery.delivery_date >= d_from,
            MilkDelivery.delivery_date <= d_to,
        )
        # TICKET-4: an unpriced delivery has no value to bill, and the Excel
        # export would crash on float(None). Leave it for a later invoice.
        deliveries = (
            MilkDelivery.query
            .filter(*period_filter, MilkDelivery.total_value.isnot(None))
            .order_by(MilkDelivery.delivery_date)
            .all()
        )
        skipped = (
            MilkDelivery.query
            .filter(*period_filter, MilkDelivery.total_value.is_(None))
            .count()
        )
        if not deliveries:
            if skipped:
                flash(
                    f"كل توريدات الفترة دي ({skipped}) لسه بدون سعر — سعّرها الأول "
                    "عشان تقدر تعمل فاتورة.",
                    "error",
                )
            else:
                flash("مفيش توريدات غير مفوترة للعميل ده في الفترة دي.", "error")
            return render_template("milk/invoice_form.html", customers=customers)
        if skipped:
            flash(
                f"تم استثناء {skipped} توريد لسه بدون سعر من الفاتورة. "
                "سعّرهم واعمل لهم فاتورة بعدين.",
                "warning",
            )

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
    # PHASE 10 (YAS-UX-4): the "تحصيل الآن" modal needs the treasury
    # accounts + today's date pre-populated.
    from datetime import date
    from app.utils import accounts as acc
    return render_template(
        "milk/invoice_view.html",
        invoice=invoice,
        treasury_choices=acc.active_choices(),
        today_iso=date.today().isoformat(),
    )


# ==================== PHASE 10 — invoice actions ====================

@bp.route("/invoices/<int:invoice_id>/collect", methods=["POST"])
@login_required
def collect_invoice(invoice_id: int):
    """YAS-UX-4: collect payment for a specific milk invoice from a modal
    on its own page. Mirror of `purchases.pay_invoice`."""
    from datetime import date as _date
    from decimal import Decimal, InvalidOperation
    from flask import flash, redirect, request, url_for
    from app.extensions import db
    from app.models.finance import TreasuryAccount, Expense  # noqa: F401
    from app.models.sales import CustomerPayment
    from app.services.allocations import allocate_payment, AllocationError
    from app.services import autoposting
    from app.utils import accounts as acc
    from app.utils.audit import log_action

    invoice = db.session.get(MilkInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        return render_template("errors/404.html"), 404

    if invoice.outstanding_amount <= 0:
        flash("الفاتورة دي مُحصّلة بالكامل — مفيش متبقّي.", "info")
        return redirect(url_for("milk.view_invoice", invoice_id=invoice.id))

    try:
        amount = Decimal(str(request.form.get("amount", "0")))
    except (InvalidOperation, ValueError):
        flash("مبلغ غير صالح.", "error")
        return redirect(url_for("milk.view_invoice", invoice_id=invoice.id))
    if amount <= 0:
        flash("المبلغ لازم أكبر من صفر.", "error")
        return redirect(url_for("milk.view_invoice", invoice_id=invoice.id))
    if amount > invoice.outstanding_amount:
        amount = invoice.outstanding_amount

    account_id = request.form.get("account_id", type=int)
    account = db.session.get(TreasuryAccount, account_id) if account_id else None
    if not account or account.is_archived:
        flash("لازم تختار حساب صحيح.", "error")
        return redirect(url_for("milk.view_invoice", invoice_id=invoice.id))

    method = request.form.get("method") or "cash"
    notes = (request.form.get("notes") or "").strip() or None
    payment_date_raw = request.form.get("payment_date") or _date.today().isoformat()
    try:
        payment_date = _date.fromisoformat(payment_date_raw)
    except ValueError:
        payment_date = _date.today()

    payment = CustomerPayment(
        customer_id=invoice.customer_id,
        amount=amount,
        payment_date=payment_date,
        method=method,
        account_id=account.id,
        notes=notes,
        created_by_id=current_user.id,
    )
    db.session.add(payment)
    db.session.flush()

    # Treasury cash-in
    acc.money_in(
        account, amount, payment_date,
        ref_type="customer_payment", ref_id=payment.id, user_id=current_user.id,
        notes=f"تحصيل من {invoice.customer.name} (فاتورة #{invoice.id})",
    )
    # Double-entry JE
    autoposting.on_customer_payment(payment, account, created_by=current_user.id)

    # Allocate 1:1 to this invoice
    try:
        allocate_payment(payment, [(invoice.id, amount)], created_by=current_user.id)
    except AllocationError as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("milk.view_invoice", invoice_id=invoice.id))

    log_action("customer_payment", "CustomerPayment", payment.id,
               details=f"invoice={invoice.id} amount={amount}")
    db.session.commit()
    flash(
        f"تم تحصيل {amount} جنيه من {invoice.customer.name} على فاتورة #{invoice.id}. "
        f"رصيد {account.name} بقى {account.current_balance}.",
        "success",
    )
    return redirect(url_for("milk.view_invoice", invoice_id=invoice.id))


@bp.route("/invoices/<int:invoice_id>/delete", methods=["POST"])
@login_required
def delete_invoice(invoice_id: int):
    """YAS-UX-2: soft-delete a milk invoice. Reverses the JE, unlinks
    the deliveries (they stop being invoice-bound), archives the row.
    Admin-only, guarded when allocations/returns exist."""
    from flask import abort, flash, redirect, request, url_for
    from app.extensions import db
    from app.services.autoposting import _delete_prior_je
    from app.utils.audit import log_action

    if not current_user.is_admin:
        abort(403)

    invoice = db.session.get(MilkInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        return render_template("errors/404.html"), 404

    force = request.args.get("force") == "1"
    linked_allocs = invoice.allocations
    linked_returns = list(invoice.returns.filter_by(is_archived=False))
    if (linked_allocs or linked_returns) and not force:
        flash(
            f"الفاتورة عليها {len(linked_allocs)} دفعة مخصصة "
            f"و {len(linked_returns)} مرتجع. لو متأكد أضف ?force=1 على الرابط.",
            "warning",
        )
        return redirect(url_for("milk.view_invoice", invoice_id=invoice.id))

    # 1) reverse the JE (autoposter for milk delivery pricing / milk invoice)
    _delete_prior_je("MilkInvoice", invoice.id)

    # 2) unlink deliveries — they stay in the DB, just no longer tied
    # to this invoice, so they're free for a future re-invoice
    for d in invoice.deliveries:
        d.invoice_id = None

    # 3) archive the invoice
    invoice.is_archived = True

    log_action("invoice_deleted", "MilkInvoice", invoice.id,
               details=f"forced={force} allocs={len(linked_allocs)} returns={len(linked_returns)}")
    db.session.commit()
    flash(
        f"تم حذف الفاتورة #{invoice.id}. القيد المحاسبي رجع، والتوريدات "
        f"اترجعت متاحة لإصدار فاتورة جديدة.",
        "success",
    )
    return redirect(url_for("milk.list_invoices"))


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
            # TICKET-A: the columns hold rates now, but the invoice is a
            # money document — it keeps printing EGP.
            float(d.fat_amount),
            float(d.protein_amount),
            float(d.bacteria_amount),
            float(d.transport_amount),
            float(d.other_amount),
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


# PHASE 16: server-side PDF, mirrors purchases.invoice_pdf.
@bp.route("/invoices/<int:invoice_id>/pdf")
@login_required
def invoice_pdf(invoice_id: int):
    from app.utils.reports import pdf_from_current_page
    invoice = db.session.get(MilkInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        return render_template("errors/404.html"), 404
    target = url_for("milk.view_invoice",
                     invoice_id=invoice.id, _external=True)
    return pdf_from_current_page(target, f"milk_invoice_{invoice.id}.pdf")


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
