from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms.suppliers import PurchaseInvoiceForm
from app.models.finance import Expense
from app.models.inventory import Ingredient, StockMovement
from app.models.suppliers import PurchaseInvoice, PurchaseLine, Supplier
from app.utils.audit import log_action

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

            line_items.append({"ingredient": ing, "qty": qty, "price": price})

        if not line_items:
            flash("لازم تضيف بند واحد على الأقل في الفاتورة.", "error")
            return render_template(
                "purchases/form.html", form=form, ingredients=ingredients, suppliers=suppliers
            )

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
                )
            )

            # US-2.3: update inventory + last purchase price
            ing = item["ingredient"]
            ing.current_qty = (ing.current_qty or Decimal("0")) + item["qty"]
            ing.last_price = item["price"]

            db.session.add(
                StockMovement(
                    ingredient_id=ing.id,
                    delta=item["qty"],
                    reason=StockMovement.REASON_PURCHASE,
                    ref_id=invoice.id,
                    unit_price_at_move=item["price"],
                    moved_on=invoice.invoice_date,
                    notes=f"فاتورة #{invoice.id} — {supplier.name}",
                    created_by_id=current_user.id,
                )
            )

        invoice.total = total
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
            db.session.add(
                Expense(
                    category=cat,
                    amount=total,
                    expense_date=invoice.invoice_date,
                    description=f"فاتورة نقدي من {supplier.name} (#{invoice.id})",
                    ref_type="purchase_invoice_cash",
                    ref_id=invoice.id,
                    created_by_id=current_user.id,
                )
            )

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
