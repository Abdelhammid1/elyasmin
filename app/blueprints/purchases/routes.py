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
    invoices = (
        PurchaseInvoice.query.filter_by(is_archived=False)
        .order_by(PurchaseInvoice.invoice_date.desc(), PurchaseInvoice.id.desc())
        .limit(200)
        .all()
    )
    return render_template("purchases/list.html", invoices=invoices)


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
    return render_template("purchases/view.html", invoice=invoice)
