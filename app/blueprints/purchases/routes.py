from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms.suppliers import PurchaseInvoiceForm
from app.models.finance import TreasuryAccount, Expense
from app.models.inventory import Ingredient, StockMovement
from app.models.suppliers import PurchaseInvoice, PurchaseInvoiceCharge, PurchaseLine, Supplier
from app.utils import accounts as acc
from app.utils.audit import log_action
from app.utils.units import per_base_price, to_base

bp = Blueprint("purchases", __name__, template_folder="../../templates/purchases")


def _to_decimal(raw: str, field_name: str) -> Decimal | None:
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, AttributeError):
        flash(f"قيمة غير صالحة في: {field_name}.", "error")
        return None


@bp.route("/")
@login_required
def list_invoices():
    """PHASE 17 (STYLE P17-2): filter + KPI strip on top.

    Query params (all optional):
      date_from, date_to  — invoice_date range
      supplier_id         — filter to one supplier
      status              — 'all' | 'paid' | 'partial' | 'overdue' | 'unpaid'
      q                   — free-text search on supplier name or original invoice no
    """
    from datetime import date as _date, timedelta

    q = PurchaseInvoice.query.filter_by(is_archived=False)

    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    if fm:
        q = q.filter(PurchaseInvoice.invoice_date >= _date.fromisoformat(fm))
    if to:
        q = q.filter(PurchaseInvoice.invoice_date <= _date.fromisoformat(to))
    sid = request.args.get("supplier_id", type=int)
    if sid:
        q = q.filter(PurchaseInvoice.supplier_id == sid)
    text = (request.args.get("q") or "").strip()
    if text:
        q = q.join(Supplier).filter(
            db.or_(
                Supplier.name.ilike(f"%{text}%"),
                PurchaseInvoice.original_invoice_no.ilike(f"%{text}%"),
            )
        )

    invoices = q.order_by(
        PurchaseInvoice.invoice_date.desc(), PurchaseInvoice.id.desc()
    ).limit(500).all()

    # Status filter is applied in Python because 'outstanding_amount' is
    # a hybrid/derived property, not a column.
    status = (request.args.get("status") or "all").lower()
    today = _date.today()

    def _row_status(inv):
        if inv.outstanding_amount <= Decimal("0.01"):
            return "paid"
        # Anything older than 15 days that's still outstanding = overdue.
        days = (today - inv.invoice_date).days if inv.invoice_date else 0
        if days > 15:
            return "overdue"
        if float(inv.paid_amount or 0) > 0 or float(inv.allocated_amount or 0) > 0:
            return "partial"
        return "unpaid"

    for inv in invoices:
        inv._status_slug = _row_status(inv)
    if status != "all":
        invoices = [i for i in invoices if i._status_slug == status]

    # KPI strip
    kpi_count = len(invoices)
    kpi_total = sum((Decimal(str(i.total or 0)) for i in invoices), Decimal("0"))
    kpi_outstanding = sum(
        (Decimal(str(i.outstanding_amount or 0)) for i in invoices), Decimal("0")
    )
    kpi_overdue = sum(1 for i in invoices if i._status_slug == "overdue")

    suppliers = (
        Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    )
    return render_template(
        "purchases/list.html",
        invoices=invoices, suppliers=suppliers, today=today,
        kpi_count=kpi_count, kpi_total=kpi_total,
        kpi_outstanding=kpi_outstanding, kpi_overdue=kpi_overdue,
        f_date_from=fm or "", f_date_to=to or "",
        f_supplier_id=sid or 0, f_status=status, f_q=text,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_invoice():
    form = PurchaseInvoiceForm()
    form.account_id.choices = [(0, "— اختار الحساب —")] + acc.active_choices()
    suppliers = (
        Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    )
    ingredients = (
        Ingredient.query.filter_by(is_archived=False).order_by(Ingredient.category, Ingredient.name).all()
    )
    form.supplier_id.choices = [(s.id, s.name) for s in suppliers]

    # Pre-select supplier if coming from supplier page
    if request.method == "GET":
        prefill = request.args.get("supplier_id", type=int)
        if prefill:
            form.supplier_id.data = prefill

    if form.validate_on_submit():
        supplier = db.session.get(Supplier, form.supplier_id.data)
        if not supplier or supplier.is_archived:
            flash("المورد غير صالح.", "error")
            return render_template(
                "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
            )

        # TREASURY: a cash invoice pays out on the spot, so it must name the
        # account. A credit invoice moves no money and needs none.
        if form.payment_type.data == PurchaseInvoice.PAY_CASH and not form.account_id.data:
            form.account_id.errors.append("اختار الحساب اللي هيتدفع منه — الفاتورة نقدي.")
            flash("الفاتورة نقدي — لازم تحدد الحساب اللي الفلوس هتطلع منه.", "error")
            return render_template(
                "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
            )

        # Parse dynamic line items from request.form
        line_items = []
        i = 0
        while True:
            ing_key = f"line_ingredient_{i}"
            if ing_key not in request.form:
                break
            ing_id_raw = request.form.get(ing_key)
            qty_raw = request.form.get(f"line_qty_{i}")
            price_raw = request.form.get(f"line_price_{i}")
            unit_code_raw = (request.form.get(f"line_unit_{i}") or "").strip()  # TICKET-2
            # PHASE 6: medicine-only lot fields (both optional on the form,
            # but expires_on is REQUIRED when the ingredient is medicine)
            lot_number_raw = (request.form.get(f"line_lot_number_{i}") or "").strip()
            expires_on_raw = (request.form.get(f"line_expires_on_{i}") or "").strip()
            i += 1

            if not ing_id_raw or not qty_raw or not price_raw:
                continue  # skip blank rows

            try:
                ing_id = int(ing_id_raw)
            except ValueError:
                flash("مادة غير صالحة في أحد البنود.", "error")
                return render_template(
                    "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
                )

            qty = _to_decimal(qty_raw, "الكمية")
            price = _to_decimal(price_raw, "السعر")
            if qty is None or price is None:
                return render_template(
                    "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
                )

            if qty <= 0 or price < 0:
                flash("الكمية لازم تكون أكبر من صفر، والسعر مش سالب.", "error")
                return render_template(
                    "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
                )

            ing = db.session.get(Ingredient, ing_id)
            if not ing or ing.is_archived:
                flash("مادة غير موجودة في أحد البنود.", "error")
                return render_template(
                    "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
                )

            # TICKET-2: convert qty + price from input unit → base unit
            input_unit = unit_code_raw or ing.unit  # default = base unit
            if input_unit != ing.unit and ing.factor_for(input_unit) is None:
                flash(f"الوحدة {input_unit} مش معرّفة للصنف {ing.name}.", "error")
                return render_template(
                    "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
                )
            qty_base = to_base(qty, input_unit, ing) or qty
            price_base = per_base_price(price, input_unit, ing) or price

            # PHASE 6: medicine lines must carry an expiry date. Optional
            # lot_number identifies the batch on the packaging.
            expires_on = None
            if ing.category == Ingredient.CATEGORY_MEDICINE:
                if not expires_on_raw:
                    flash(
                        f"لازم تدخل تاريخ انتهاء صلاحية للدواء {ing.name}.",
                        "error",
                    )
                    return render_template(
                        "purchases/form.html", form=form,
                        ingredients=ingredients, suppliers=suppliers,
                    )
                from datetime import date as _date
                try:
                    expires_on = _date.fromisoformat(expires_on_raw)
                except ValueError:
                    flash(f"تاريخ انتهاء صلاحية غير صالح للدواء {ing.name}.", "error")
                    return render_template(
                        "purchases/form.html", form=form,
                        ingredients=ingredients, suppliers=suppliers,
                    )

            line_items.append({
                "ingredient": ing,
                "qty": qty_base,          # in base unit
                "price": price_base,      # per base unit
                "input_qty": qty,
                "input_unit": input_unit,
                "input_price": price,
                "lot_number": lot_number_raw or None,
                "expires_on": expires_on,
            })

        if not line_items:
            flash("لازم تضيف بند واحد على الأقل في الفاتورة.", "error")
            return render_template(
                "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
            )

        # TICKET-3: parse dynamic tax/discount rows from request.form
        def _resolve_type(picked: str, custom_val: str) -> str | None:
            picked = (picked or "").strip()
            if not picked:
                return None
            if picked == "__custom__":
                custom_val = (custom_val or "").strip()
                if not custom_val:
                    return None
                return "custom:" + custom_val
            return picked

        # rows are keyed as: charge_kind_0, charge_type_0, charge_custom_0, charge_mode_0, charge_value_0
        # mode = 'pct' or 'egp'
        charge_rows = []
        idx = 0
        while True:
            key = f"charge_kind_{idx}"
            if key not in request.form:
                break
            kind = (request.form.get(key) or "").strip()
            type_picked = (request.form.get(f"charge_type_{idx}") or "").strip()
            type_custom = (request.form.get(f"charge_custom_{idx}") or "").strip()
            mode = (request.form.get(f"charge_mode_{idx}") or "egp").strip()
            value_raw = (request.form.get(f"charge_value_{idx}") or "").strip()
            idx += 1

            if kind not in (PurchaseInvoiceCharge.KIND_TAX, PurchaseInvoiceCharge.KIND_DISCOUNT):
                continue
            resolved_type = _resolve_type(type_picked, type_custom)
            if not resolved_type or not value_raw:
                continue
            try:
                value = Decimal(value_raw)
            except (InvalidOperation, ValueError):
                continue
            if value <= 0:
                continue
            charge_rows.append({
                "kind": kind, "type": resolved_type,
                "is_pct": mode == "pct", "value": value,
            })

        # Build the invoice
        invoice = PurchaseInvoice(
            supplier_id=supplier.id,
            invoice_date=form.invoice_date.data,
            payment_type=form.payment_type.data,
            original_invoice_no=form.original_invoice_no.data or None,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(invoice)
        db.session.flush()

        total = Decimal("0")
        for item in line_items:
            line_total = (item["qty"] * item["price"]).quantize(Decimal("0.01"))
            total += line_total

            db.session.add(
                PurchaseLine(
                    invoice_id=invoice.id,
                    ingredient_id=item["ingredient"].id,
                    qty=item["qty"],
                    unit_price=item["price"],
                    line_total=line_total,
                    input_qty=item.get("input_qty"),
                    input_unit_code=item.get("input_unit"),
                    input_unit_price=item.get("input_price"),
                )
            )

            # PHASE 6: weighted-average blend replaces the naive last_price
            # overwrite. `blend_purchase` updates current_qty + avg_cost +
            # last_price (kept as reference). Everything downstream (feeding
            # additions cost, medicine dispense, stock valuation) now reads
            # avg_cost.
            ing = item["ingredient"]
            from app.utils import inventory_cost
            inventory_cost.blend_purchase(ing, item["qty"], item["price"])

            # PHASE 6: for medicine lines, spin up a lot linked to this
            # invoice so the FIFO dispenser has expiry-aware picks.
            new_lot = None
            if ing.category == Ingredient.CATEGORY_MEDICINE:
                from app.models.inventory import MedicineLot
                new_lot = MedicineLot(
                    ingredient_id=ing.id,
                    lot_number=item.get("lot_number"),
                    expires_on=item.get("expires_on"),
                    qty_received=item["qty"],
                    qty_remaining=item["qty"],
                    unit_cost=item["price"],
                    source_type=MedicineLot.SOURCE_PURCHASE,
                    source_id=invoice.id,
                    created_by_id=current_user.id,
                )
                db.session.add(new_lot)
                db.session.flush()

            db.session.add(
                StockMovement(
                    ingredient_id=ing.id,
                    delta=item["qty"],
                    reason=StockMovement.REASON_PURCHASE,
                    ref_id=invoice.id,
                    unit_price_at_move=item["price"],
                    input_qty=item.get("input_qty"),
                    input_unit_code=item.get("input_unit"),
                    lot_id=new_lot.id if new_lot else None,
                    moved_on=invoice.invoice_date,
                    notes=f"فاتورة #{invoice.id} — {supplier.name}",
                    created_by_id=current_user.id,
                )
            )

        invoice.subtotal = total

        # TICKET-3: persist each charge row + compute amount_egp from % if needed
        discount_total = Decimal("0")
        tax_total = Decimal("0")
        for order, row in enumerate(charge_rows):
            if row["is_pct"]:
                amount_egp = (total * row["value"] / Decimal("100")).quantize(Decimal("0.01"))
            else:
                amount_egp = row["value"].quantize(Decimal("0.01"))

            db.session.add(
                PurchaseInvoiceCharge(
                    invoice_id=invoice.id,
                    kind=row["kind"],
                    type_name=row["type"],
                    is_percentage=row["is_pct"],
                    rate_pct=row["value"] if row["is_pct"] else None,
                    amount_egp=amount_egp,
                    display_order=order,
                )
            )
            if row["kind"] == PurchaseInvoiceCharge.KIND_TAX:
                tax_total += amount_egp
            else:
                discount_total += amount_egp

        final_total = (total - discount_total + tax_total).quantize(Decimal("0.01"))
        invoice.total = final_total
        total = final_total  # rest of function uses this as the true total

        # ACCOUNTING: post the invoice-side JE (DR inventory / CR payable).
        # The cash-invoice pathway posts its own second JE below (payable
        # settlement); the on_expense autoposter special-cases ref_type
        # 'purchase_invoice_cash' so no double-count.
        from app.services import autoposting
        autoposting.on_purchase_invoice(invoice, created_by=current_user.id)

        # Cash → marked paid immediately + record as expense; Credit → paid_amount stays 0
        if invoice.payment_type == PurchaseInvoice.PAY_CASH:
            invoice.paid_amount = total
            # Auto-record as expense: feed or medicine bucket based on line contents
            has_medicine = any(
                l.ingredient.category == Ingredient.CATEGORY_MEDICINE for l in invoice.lines
            )
            has_feed = any(
                l.ingredient.category == Ingredient.CATEGORY_FEED for l in invoice.lines
            )
            if has_feed and not has_medicine:
                cat = Expense.CAT_FEED_PURCHASE
            elif has_medicine and not has_feed:
                cat = Expense.CAT_MEDICINE_PURCHASE
            else:
                cat = Expense.CAT_OTHER
            # TREASURY: a cash invoice is a real outflow with no payment model
            # behind it, so its Expense IS the cash event and posts the movement.
            cash_account = db.session.get(TreasuryAccount, form.account_id.data or 0)
            cash_expense = Expense(
                category=cat,
                amount=total,
                expense_date=invoice.invoice_date,
                description=f"فاتورة نقدي من {supplier.name} (#{invoice.id})",
                ref_type="purchase_invoice_cash",
                ref_id=invoice.id,
                account_id=cash_account.id if cash_account else None,
                created_by_id=current_user.id,
            )
            db.session.add(cash_expense)
            db.session.flush()
            if cash_account and not cash_account.is_archived:
                acc.money_out(
                    cash_account, total, invoice.invoice_date,
                    ref_type="purchase_invoice_cash", ref_id=invoice.id,
                    user_id=current_user.id,
                    notes=f"فاتورة نقدي من {supplier.name} (#{invoice.id})",
                )
                # ACCOUNTING: pay off the payable the invoice just created.
                # on_expense special-cases ref_type='purchase_invoice_cash'
                # and posts DR payable / CR cash instead of hitting an expense.
                autoposting.on_expense(cash_expense, cash_account, created_by=current_user.id)

        log_action(
            "purchase_invoice_created",
            "PurchaseInvoice",
            invoice.id,
            details=f"supplier={supplier.id} total={total} type={invoice.payment_type}",
        )
        db.session.commit()
        flash(
            f"تم حفظ الفاتورة #{invoice.id} بإجمالي {total} جنيه. "
            + ("(نقدي — مدفوعة)" if invoice.payment_type == PurchaseInvoice.PAY_CASH else "(آجل)"),
            "success",
        )
        return redirect(url_for("purchases.view_invoice", invoice_id=invoice.id))

    return render_template(
        "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
    )


@bp.route("/<int:invoice_id>")
@login_required
def view_invoice(invoice_id: int):
    invoice = db.session.get(PurchaseInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        abort(404)
    # PHASE 10 (YAS-UX-4): the "سدّد الآن" modal needs the treasury
    # accounts + today's date pre-populated so the form is one-click.
    from datetime import date
    return render_template(
        "purchases/view.html",
        invoice=invoice,
        treasury_choices=acc.active_choices(),
        today_iso=date.today().isoformat(),
    )


# ==================== PHASE 10 — invoice actions ====================

@bp.route("/<int:invoice_id>/excel")
@login_required
def invoice_excel(invoice_id: int):
    """YAS-UX-1: Excel export of a purchase invoice — mirrors the shape
    of the milk-side `invoice_excel` so both invoices offer the same
    action bar."""
    from app.utils.reports import excel_response
    invoice = db.session.get(PurchaseInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        abort(404)

    headers = ["المادة", "النوع", "الكمية", "الوحدة", "السعر / وحدة", "الإجمالي"]
    rows = [
        [line.ingredient.name,
         line.ingredient.category_label,
         float(line.qty),
         line.ingredient.unit_label,
         float(line.unit_price),
         float(line.line_total)]
        for line in invoice.lines
    ]
    # Trailer summary rows (subtotal, discounts, taxes, total, paid, outstanding)
    rows.append(["", "", "", "", "المجموع الفرعي", float(invoice.subtotal)])
    for c in invoice.discount_rows:
        rows.append(["", "", "", "", f"خصم — {c.type_label}", -float(c.amount_egp)])
    for c in invoice.tax_rows:
        rows.append(["", "", "", "", f"ضريبة — {c.type_label}", float(c.amount_egp)])
    rows.append(["", "", "", "", "إجمالي الفاتورة", float(invoice.total)])
    rows.append(["", "", "", "", "المدفوع", float(invoice.paid_amount + invoice.allocated_amount)])
    rows.append(["", "", "", "", "المتبقّي", float(invoice.outstanding_amount)])

    return excel_response(
        f"فاتورة {invoice.id}", headers, rows,
        f"purchase_invoice_{invoice.id}.xlsx",
    )


# PHASE 16: server-side PDF via headless Chromium — same pattern as
# customers.customers_report_pdf. The route re-fetches its own HTML page
# under an authenticated session and prints it to A4 with the print CSS.
@bp.route("/<int:invoice_id>/pdf")
@login_required
def invoice_pdf(invoice_id: int):
    from app.utils.reports import pdf_from_current_page
    invoice = db.session.get(PurchaseInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        abort(404)
    target = url_for("purchases.view_invoice",
                     invoice_id=invoice.id, _external=True)
    return pdf_from_current_page(
        target, f"purchase_invoice_{invoice.id}.pdf",
    )


@bp.route("/<int:invoice_id>/pay", methods=["POST"])
@login_required
def pay_invoice(invoice_id: int):
    """YAS-UX-4: pay this specific invoice from a modal on its own
    page. Creates a SupplierPayment + one Allocation for this invoice,
    reusing `allocate_supplier_payment` so the JE + treasury movement
    all fire the normal way."""
    from datetime import date as _date
    from app.models.suppliers import SupplierPayment
    from app.services.allocations import allocate_supplier_payment, AllocationError
    from app.services import autoposting

    invoice = db.session.get(PurchaseInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        abort(404)

    if invoice.outstanding_amount <= 0:
        flash("الفاتورة دي مسدّدة بالكامل — مفيش متبقّي.", "info")
        return redirect(url_for("purchases.view_invoice", invoice_id=invoice.id))

    try:
        amount = Decimal(str(request.form.get("amount", "0")))
    except (InvalidOperation, ValueError):
        flash("مبلغ غير صالح.", "error")
        return redirect(url_for("purchases.view_invoice", invoice_id=invoice.id))
    if amount <= 0:
        flash("المبلغ لازم أكبر من صفر.", "error")
        return redirect(url_for("purchases.view_invoice", invoice_id=invoice.id))

    # Cap at outstanding — the extra would go on-account, but the modal
    # is scoped to THIS invoice so anything above is a mistake.
    if amount > invoice.outstanding_amount:
        amount = invoice.outstanding_amount

    account_id = request.form.get("account_id", type=int)
    account = db.session.get(TreasuryAccount, account_id) if account_id else None
    if not account or account.is_archived:
        flash("لازم تختار حساب صحيح.", "error")
        return redirect(url_for("purchases.view_invoice", invoice_id=invoice.id))

    method = request.form.get("method") or "cash"
    notes = (request.form.get("notes") or "").strip() or None
    payment_date_raw = request.form.get("payment_date") or _date.today().isoformat()
    try:
        payment_date = _date.fromisoformat(payment_date_raw)
    except ValueError:
        payment_date = _date.today()

    payment = SupplierPayment(
        supplier_id=invoice.supplier_id,
        amount=amount,
        payment_date=payment_date,
        method=method,
        account_id=account.id,
        notes=notes,
        created_by_id=current_user.id,
    )
    db.session.add(payment)
    db.session.flush()

    # Treasury cash-out
    acc.money_out(
        account, amount, payment_date,
        ref_type="supplier_payment", ref_id=payment.id, user_id=current_user.id,
        notes=f"دفعة للمورد {invoice.supplier.name} (فاتورة #{invoice.id})",
    )
    # Double-entry JE via autoposter
    autoposting.on_supplier_payment(payment, account, created_by=current_user.id)

    # Mirror as Expense for the cash-outflow report
    db.session.add(Expense(
        category=Expense.CAT_SUPPLIER_PAYMENT,
        amount=amount,
        expense_date=payment_date,
        description=f"دفعة للمورد {invoice.supplier.name} (فاتورة #{invoice.id})",
        ref_type="supplier_payment",
        ref_id=payment.id,
        account_id=account.id,
        created_by_id=current_user.id,
    ))

    # Allocate the payment 1:1 to this invoice
    try:
        allocate_supplier_payment(
            payment, [(invoice.id, amount)], created_by=current_user.id,
        )
    except AllocationError as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("purchases.view_invoice", invoice_id=invoice.id))

    log_action("supplier_payment", "SupplierPayment", payment.id,
               details=f"invoice={invoice.id} amount={amount}")
    db.session.commit()
    flash(
        f"تم تسجيل دفعة {amount} جنيه للفاتورة #{invoice.id}. "
        f"رصيد {account.name} بقى {account.current_balance}.",
        "success",
    )
    return redirect(url_for("purchases.view_invoice", invoice_id=invoice.id))


@bp.route("/<int:invoice_id>/delete", methods=["POST"])
@login_required
def delete_invoice(invoice_id: int):
    """YAS-UX-2: soft-delete a purchase invoice.

    Reverses the auto-posted JE, walks the invoice's stock movements
    and reverses them (avg_cost blended back via
    `inventory_cost.reverse_purchase`), then archives the invoice row.
    Guarded: if the invoice has allocations or returns, requires
    `?force=1` — otherwise flashes a warning and stays on the page.
    """
    if not current_user.is_admin:
        abort(403)

    invoice = db.session.get(PurchaseInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        abort(404)

    from app.utils import inventory_cost
    from app.services.autoposting import _delete_prior_je

    # Guard: activity linked to the invoice? Force needed.
    force = request.args.get("force") == "1"
    linked_allocs = invoice.allocations
    linked_returns = list(invoice.returns.filter_by(is_archived=False))
    if (linked_allocs or linked_returns) and not force:
        flash(
            f"الفاتورة عليها {len(linked_allocs)} دفعة مخصصة "
            f"و {len(linked_returns)} مرتجع. لو متأكد أضف ?force=1 على الرابط.",
            "warning",
        )
        return redirect(url_for("purchases.view_invoice", invoice_id=invoice.id))

    # 1) reverse the JE
    _delete_prior_je("PurchaseInvoice", invoice.id)

    # 2) walk stock movements + reverse each
    moves = StockMovement.query.filter_by(
        reason=StockMovement.REASON_PURCHASE, ref_id=invoice.id,
    ).all()
    for m in moves:
        ing = m.ingredient
        # delta positive on purchase — subtracting the same qty here
        try:
            inventory_cost.reverse_purchase(ing, m.delta)
        except ValueError:
            # stock has since been consumed — reset the ingredient's qty
            # to zero manually and let the accountant reconcile.
            ing.current_qty = max(Decimal("0"), Decimal(str(ing.current_qty)) - Decimal(str(m.delta)))
        db.session.delete(m)

    # 3) archive the invoice
    invoice.is_archived = True

    log_action("invoice_deleted", "PurchaseInvoice", invoice.id,
               details=f"forced={force} allocs={len(linked_allocs)} returns={len(linked_returns)}")
    db.session.commit()
    flash(
        f"تم حذف الفاتورة #{invoice.id}. القيد المحاسبي والمخزون رجعوا لما قبلها.",
        "success",
    )
    return redirect(url_for("purchases.list_invoices"))


@bp.route("/<int:invoice_id>/duplicate")
@login_required
def duplicate(invoice_id: int):
    """YAS-UX-3: open the create-invoice form pre-filled with every
    line + charge from an existing invoice. No auto-scheduling — the
    user reviews and saves as a fresh invoice."""
    source = db.session.get(PurchaseInvoice, invoice_id)
    if not source or source.is_archived:
        abort(404)

    form = PurchaseInvoiceForm()
    form.supplier_id.choices = [
        (s.id, s.name) for s in
        Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    ]
    form.account_id.choices = [(0, "— اختار الحساب —")] + acc.active_choices()
    form.supplier_id.data = source.supplier_id
    form.payment_type.data = source.payment_type

    suppliers = Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    ingredients = (
        Ingredient.query.filter_by(is_archived=False)
        .order_by(Ingredient.category, Ingredient.name).all()
    )

    # Pre-serialise the source's lines + charges so the template can
    # feed them straight into the JS row-builder.
    prefill_lines = [{
        "ingredient_id": line.ingredient_id,
        "qty": str(line.qty),
        "unit_price": str(line.unit_price),
        "unit_code": line.input_unit_code or line.ingredient.unit,
        "lot_number": "",   # never carried across — every duplicate is a fresh batch
        "expires_on": "",
    } for line in source.lines]
    prefill_charges = [{
        "kind": c.kind,
        "type_name": c.type_name,
        "is_percentage": c.is_percentage,
        "rate_pct": str(c.rate_pct or 0),
        "amount_egp": str(c.amount_egp or 0),
    } for c in source.charges]

    flash(
        f"عرض فاتورة جديدة معبّاة ببيانات الفاتورة #{source.id}. "
        f"راجع البنود وضغط حفظ.",
        "info",
    )
    return render_template(
        "purchases/form.html",
        form=form, ingredients=ingredients, suppliers=suppliers,
        prefill_lines=prefill_lines, prefill_charges=prefill_charges,
        duplicate_source_id=source.id,
    )
