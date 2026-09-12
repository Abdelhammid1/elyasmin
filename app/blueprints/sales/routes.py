"""PHASE 36 (SALES-1): general sales invoice blueprint.

Handles mixed-line invoices — cow / inventory / free — with a
full cash / credit / mixed payment split, and calls the
`on_sales_invoice` autoposter so every save lands one atomic
balanced JE. Design references live inline; the plan file is
`hidden-humming-book.md` in the plans dir.

Copies the "flush → autopost → commit" pattern from
`milk/routes.py::create_delivery` and the payment-plumbing shape
from `milk/routes.py::collect_invoice`.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint, abort, flash, redirect, render_template,
    request, url_for,
)
from flask_login import current_user, login_required

from app.extensions import db
from app.models.finance import TreasuryAccount
from app.models.herd import AnimalSale, Cow
from app.models.inventory import (
    Ingredient, IngredientCategory, StockMovement,
)
from app.models.sales import Customer, CustomerPayment
from app.models.sales_general import (
    SalesInvoice, SalesInvoiceLine,
)
from app.services import autoposting
from app.services.allocations import (
    AllocationError, allocate_sales_invoice_payment,
)
from app.services.ledger import LedgerError
from app.utils import accounts as acc
from app.utils import inventory_cost
from app.utils.audit import log_action
from app.utils.decorators import write_required


bp = Blueprint("sales", __name__,
               template_folder="../../templates/sales")


MONEY = Decimal("0.01")


def _d(v) -> Decimal:
    try:
        return Decimal(str(v or 0)).quantize(MONEY)
    except (InvalidOperation, TypeError):
        return Decimal("0")


# ---------- list + filters ----------

@bp.route("/invoices")
@login_required
def list_invoices():
    """Sales-invoice ledger. Filter by status / payment_status /
    customer / date range."""
    q = SalesInvoice.query.filter_by(is_archived=False)

    f_status = request.args.get("status") or "all"
    if f_status in (SalesInvoice.STATUS_DRAFT, SalesInvoice.STATUS_ISSUED):
        q = q.filter(SalesInvoice.status == f_status)

    f_customer = request.args.get("customer_id", type=int)
    if f_customer:
        q = q.filter(SalesInvoice.customer_id == f_customer)

    invoices = q.order_by(SalesInvoice.invoice_date.desc(),
                         SalesInvoice.id.desc()).limit(200).all()
    customers = (
        Customer.query.filter_by(is_archived=False)
        .order_by(Customer.name).all()
    )

    total = sum((_d(i.grand_total) for i in invoices), Decimal("0"))
    outstanding = sum((_d(i.outstanding_amount) for i in invoices), Decimal("0"))

    return render_template(
        "sales/invoices_list.html",
        invoices=invoices, customers=customers,
        f_status=f_status, f_customer=f_customer,
        total=total, outstanding=outstanding,
    )


# ---------- new invoice builder ----------

def _picker_data():
    """Options for the three line pickers."""
    # Active female-or-male cows still on the farm — the cow picker.
    cows = (
        Cow.query.filter_by(status=Cow.STATUS_ACTIVE, is_archived=False)
        .order_by(Cow.ear_tag).all()
    )
    # Sellable ingredients — ingredients whose category was flagged
    # sellable in Commit 1. Reused by both new and edit views.
    ingredients = (
        Ingredient.query
        .join(IngredientCategory,
              IngredientCategory.name == Ingredient.category)
        .filter(IngredientCategory.is_sellable.is_(True))
        .filter(Ingredient.is_archived.is_(False))
        .order_by(Ingredient.name).all()
    )
    customers = (
        Customer.query.filter_by(is_archived=False)
        .order_by(Customer.name).all()
    )
    accounts = (
        TreasuryAccount.query.filter_by(is_archived=False)
        .order_by(TreasuryAccount.name).all()
    )
    return {
        "cows": cows, "ingredients": ingredients,
        "customers": customers, "accounts": accounts,
    }


@bp.route("/invoices/new", methods=["GET"])
@login_required
@write_required
def new_invoice():
    """Show the invoice builder. Accepts `?cow_id=<id>` from the
    deprecated /herd/<cow_id>/sell redirect — that cow is
    preselected as the first line."""
    data = _picker_data()
    preselect_cow_id = request.args.get("cow_id", type=int)
    return render_template(
        "sales/invoice_form.html",
        today=date.today().isoformat(),
        preselect_cow_id=preselect_cow_id,
        **data,
    )


# ---------- create ----------

def _parse_lines(form_data):
    """Extract the line array the invoice builder POSTs.

    The template renders every line as `line-<i>-<field>`; the
    server side reads them by scanning for `line-\\d+-kind`
    markers, so line rows can be non-contiguous when JS
    removes a row before submit.
    """
    ids = set()
    for k in form_data.keys():
        if k.startswith("line-") and k.endswith("-kind"):
            try:
                idx = int(k.split("-")[1])
            except (ValueError, IndexError):
                continue
            ids.add(idx)

    lines = []
    for idx in sorted(ids):
        kind = (form_data.get(f"line-{idx}-kind") or "").strip()
        if not kind:
            continue
        row = {
            "kind": kind,
            "sort_order": idx,
            "cow_id": form_data.get(f"line-{idx}-cow_id", type=int),
            "ingredient_id": form_data.get(f"line-{idx}-ingredient_id", type=int),
            "description": (form_data.get(f"line-{idx}-description") or "").strip() or None,
            "qty": _d(form_data.get(f"line-{idx}-qty") or "0"),
            "unit_price": _d(form_data.get(f"line-{idx}-unit_price") or "0"),
        }
        lines.append(row)
    return lines


@bp.route("/invoices", methods=["POST"])
@login_required
@write_required
def create_invoice():
    """Create + save + autopost, atomically.

    The whole thing lives inside one `db.session.commit()`. If
    anything fails — a cow already sold, insufficient stock, an
    unbalanced JE, an allocation over-limit — the transaction is
    rolled back and the user sees the Arabic error message.
    """
    form_data = request.form
    data = _picker_data()

    # ---- header ----
    customer_id = form_data.get("customer_id", type=int)
    walkin_name = (form_data.get("walkin_name") or "").strip() or None
    invoice_date_raw = form_data.get("invoice_date") or date.today().isoformat()
    try:
        invoice_date = date.fromisoformat(invoice_date_raw)
    except ValueError:
        invoice_date = date.today()
    due_date_raw = (form_data.get("due_date") or "").strip()
    due_date = None
    if due_date_raw:
        try:
            due_date = date.fromisoformat(due_date_raw)
        except ValueError:
            due_date = None
    invoice_number = (form_data.get("invoice_number") or "").strip() or None
    payment_type = form_data.get("payment_type") or SalesInvoice.PAYMENT_CASH
    if payment_type not in (SalesInvoice.PAYMENT_CASH,
                            SalesInvoice.PAYMENT_CREDIT,
                            SalesInvoice.PAYMENT_MIXED):
        payment_type = SalesInvoice.PAYMENT_CASH
    treasury_account_id = form_data.get("treasury_account_id", type=int)
    cash_amount = _d(form_data.get("cash_amount") or "0")
    credit_amount = _d(form_data.get("credit_amount") or "0")
    notes = (form_data.get("notes") or "").strip() or None

    # ---- validate ----
    if customer_id is None and not walkin_name:
        flash("لازم تختار عميل أو تكتب اسم المشتري (walk-in).", "error")
        return render_template("sales/invoice_form.html",
                               today=invoice_date_raw,
                               preselect_cow_id=None, **data), 400

    lines_raw = _parse_lines(form_data)
    if not lines_raw:
        flash("مفيش أي بند على الفاتورة — ضيف بند واحد على الأقل.",
              "error")
        return render_template("sales/invoice_form.html",
                               today=invoice_date_raw,
                               preselect_cow_id=None, **data), 400

    # ---- payment split sanity ----
    if payment_type == SalesInvoice.PAYMENT_CASH:
        credit_amount = Decimal("0")
    elif payment_type == SalesInvoice.PAYMENT_CREDIT:
        cash_amount = Decimal("0")
    if cash_amount < 0 or credit_amount < 0:
        flash("مبالغ الدفع لازم تكون موجبة.", "error")
        return render_template("sales/invoice_form.html",
                               today=invoice_date_raw,
                               preselect_cow_id=None, **data), 400
    if cash_amount > 0:
        if treasury_account_id is None:
            flash("لازم تختار خزنة عشان تسجل الجزء النقدي فيها.",
                  "error")
            return render_template("sales/invoice_form.html",
                                   today=invoice_date_raw,
                                   preselect_cow_id=None, **data), 400
    if credit_amount > 0 and customer_id is None:
        flash("مينفعش تعمل جزء آجل لبيع walk-in — لازم عميل مسجّل.",
              "error")
        return render_template("sales/invoice_form.html",
                               today=invoice_date_raw,
                               preselect_cow_id=None, **data), 400

    treasury = (
        db.session.get(TreasuryAccount, treasury_account_id)
        if treasury_account_id else None
    )
    if treasury and treasury.is_archived:
        treasury = None

    # ---- build invoice + lines + side-effects, one transaction ----
    try:
        invoice = SalesInvoice(
            customer_id=customer_id,
            walkin_name=walkin_name if customer_id is None else None,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=due_date,
            status=SalesInvoice.STATUS_ISSUED,  # v1: no separate draft step
            payment_type=payment_type,
            treasury_account_id=treasury.id if treasury else None,
            cash_amount=cash_amount,
            credit_amount=credit_amount,
            notes=notes,
            created_by_id=current_user.id,
        )
        db.session.add(invoice)
        db.session.flush()

        # Per-line side effects: cow status flip + AnimalSale mirror
        # for cow lines, stock draw-down for inventory lines.
        buyer_label = (
            invoice.customer.name if invoice.customer_id
            else (walkin_name or "عميل نقدي")
        )
        for raw in lines_raw:
            kind = raw["kind"]
            qty = raw["qty"]
            unit_price = raw["unit_price"]
            if qty <= 0:
                raise ValueError("الكمية على أي بند لازم تكون أكبر من صفر.")
            if unit_price < 0:
                raise ValueError("السعر لازم يكون موجب.")
            line_total = (qty * unit_price).quantize(MONEY)

            line = SalesInvoiceLine(
                invoice_id=invoice.id,
                sort_order=raw["sort_order"],
                line_kind=kind,
                qty=qty, unit_price=unit_price,
                line_total=line_total,
            )

            if kind == SalesInvoiceLine.KIND_COW:
                cow_id = raw["cow_id"]
                cow = db.session.get(Cow, cow_id) if cow_id else None
                if not cow or cow.status != Cow.STATUS_ACTIVE:
                    raise ValueError(
                        f"البقرة المطلوبة (#{cow_id}) مش موجودة أو مش نشطة."
                    )
                line.cow_id = cow.id
                line.qty = Decimal("1")
                line.line_total = unit_price.quantize(MONEY)
                # Snapshot the book value BEFORE we zero it so the JE
                # posts the exact 1400 close-out.
                line.cow_book_value_snapshot = _d(cow.current_value)
                # Flip lifecycle + zero the book-value column.
                cow.status = Cow.STATUS_SOLD
                cow.current_value = Decimal("0")
                # Mirror an AnimalSale row so the cow_detail template's
                # "سجل البيع" block keeps rendering for post-SALES-1
                # cow sales (same table pre-SALES-1 rows already live
                # in).
                db.session.add(AnimalSale(
                    cow_id=cow.id,
                    sale_date=invoice_date,
                    buyer_name=buyer_label[:120],
                    price=line.line_total,
                    notes=f"SALES-1 invoice #{invoice.id}",
                    created_by_id=current_user.id,
                ))

            elif kind == SalesInvoiceLine.KIND_INVENTORY:
                ing_id = raw["ingredient_id"]
                ing = db.session.get(Ingredient, ing_id) if ing_id else None
                if not ing or ing.is_archived:
                    raise ValueError(
                        f"الصنف المطلوب (#{ing_id}) مش موجود."
                    )
                # Snapshot avg_cost BEFORE withdraw — withdraw may
                # reset it to 0 when the last unit is drawn.
                snapshot = _d(ing.avg_cost)
                inventory_cost.withdraw(ing, qty)
                line.ingredient_id = ing.id
                line.inv_avg_cost_snapshot = snapshot
                # StockMovement row so the movements ledger shows the
                # sale side-by-side with purchases and dispenses.
                db.session.add(StockMovement(
                    ingredient_id=ing.id,
                    delta=-qty,
                    reason=StockMovement.REASON_SALE,
                    ref_id=invoice.id,
                    unit_price_at_move=unit_price,
                    moved_on=invoice_date,
                    notes=f"فاتورة بيع #{invoice.id}",
                    created_by_id=current_user.id,
                ))

            elif kind == SalesInvoiceLine.KIND_FREE:
                if not raw["description"]:
                    raise ValueError("الصنف الحر لازم يكون له وصف.")
                line.description = raw["description"]

            else:
                raise ValueError(f"نوع بند غير معروف: {kind}")

            db.session.add(line)

        invoice.recompute_total()

        # Payment split invariant — total must equal cash+credit.
        expected_paid = (cash_amount + credit_amount).quantize(MONEY)
        if expected_paid != _d(invoice.grand_total):
            raise ValueError(
                f"مجموع الدفعات ({expected_paid}) مش مساوي "
                f"إجمالي الفاتورة ({_d(invoice.grand_total)})."
            )

        # ---- ledger side ----
        autoposting.on_sales_invoice(invoice,
                                     created_by=current_user.id)

        # ---- cash portion → treasury movement + display allocation ----
        # The JE already DR-ed the treasury leaf; here we just record
        # the AccountMovement (treasury balance drift) and, when the
        # customer is real, a CustomerPayment allocation so the
        # "الجزء المدفوع" bar renders.
        if cash_amount > 0 and treasury is not None:
            acc.money_in(
                treasury, cash_amount, invoice_date,
                ref_type="sales_invoice_cash",
                ref_id=invoice.id, user_id=current_user.id,
                notes=f"جزء نقدي من فاتورة بيع #{invoice.id}",
            )
            if invoice.customer_id is not None:
                payment = CustomerPayment(
                    customer_id=invoice.customer_id,
                    amount=cash_amount,
                    payment_date=invoice_date,
                    method=CustomerPayment.METHOD_CASH,
                    account_id=treasury.id,
                    notes=f"جزء نقدي عند إصدار فاتورة #{invoice.id}",
                    created_by_id=current_user.id,
                )
                db.session.add(payment)
                db.session.flush()
                allocate_sales_invoice_payment(
                    payment, [(invoice.id, cash_amount)],
                    created_by=current_user.id,
                )

        log_action(
            "sales_invoice_created", "SalesInvoice", invoice.id,
            details=(
                f"customer={customer_id or 'walkin'} "
                f"total={invoice.grand_total} "
                f"cash={cash_amount} credit={credit_amount} "
                f"lines={len(lines_raw)}"
            ),
        )
        db.session.commit()
    except (ValueError, LedgerError, AllocationError) as e:
        db.session.rollback()
        flash(str(e), "error")
        return render_template(
            "sales/invoice_form.html",
            today=invoice_date_raw,
            preselect_cow_id=None,
            **_picker_data(),
        ), 400

    flash(
        f"تم إصدار فاتورة البيع #{invoice.id} — إجمالي {invoice.grand_total} جنيه.",
        "success",
    )
    return redirect(url_for("sales.view_invoice",
                            invoice_id=invoice.id))


# ---------- detail ----------

@bp.route("/invoices/<int:invoice_id>")
@login_required
def view_invoice(invoice_id: int):
    invoice = db.session.get(SalesInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        abort(404)
    return render_template(
        "sales/invoice_view.html", invoice=invoice,
    )


# ---------- delete ----------

@bp.route("/invoices/<int:invoice_id>/delete", methods=["POST"])
@login_required
@write_required
def delete_invoice(invoice_id: int):
    """Undo a sales invoice.

    Reverses in the same order the JE lines lived on:
      - Delete the JE (via autoposting._delete_prior_je)
      - Restore cow status + book value for each cow line
      - Delete the mirror AnimalSale rows
      - Restore stock via inventory_cost.blend_purchase (which
        also handles the avg_cost side)
      - Delete stock movements
      - Delete customer payments + allocations tied to this
        invoice
      - Soft-archive the invoice
    """
    invoice = db.session.get(SalesInvoice, invoice_id)
    if not invoice or invoice.is_archived:
        abort(404)

    try:
        # Kill the JE first so a rollback puts us back to a clean state.
        autoposting._delete_prior_je("SalesInvoice", invoice.id)

        for line in invoice.lines:
            if line.line_kind == SalesInvoiceLine.KIND_COW and line.cow:
                cow = line.cow
                cow.status = Cow.STATUS_ACTIVE
                cow.current_value = _d(line.cow_book_value_snapshot)
                # Drop the mirror AnimalSale row this invoice created
                # (identified by its notes tag). Pre-SALES-1 rows on
                # this cow (there shouldn't be any if it flipped to
                # sold via us, but be defensive) are left alone.
                AnimalSale.query.filter_by(cow_id=cow.id).filter(
                    AnimalSale.notes.like(f"SALES-1 invoice #{invoice.id}%")
                ).delete(synchronize_session=False)

            elif line.line_kind == SalesInvoiceLine.KIND_INVENTORY and line.ingredient:
                ing = line.ingredient
                snapshot = _d(line.inv_avg_cost_snapshot)
                # Return the stock at the exact cost we took it at,
                # so the avg_cost rolls back to its pre-sale value.
                inventory_cost.blend_purchase(ing, _d(line.qty),
                                              snapshot)

        # Wipe any StockMovement rows this invoice recorded.
        StockMovement.query.filter(
            StockMovement.reason == StockMovement.REASON_SALE,
            StockMovement.ref_id == invoice.id,
        ).delete(synchronize_session=False)

        # Payment allocations cascade with the invoice; the
        # cash-portion CustomerPayment we created at issue does
        # NOT — clean it up explicitly.
        for alloc in list(invoice.allocations):
            pay = alloc.payment
            if pay and pay.notes and f"فاتورة #{invoice.id}" in pay.notes:
                # Also drop the AccountMovement the money_in call
                # posted at issue time.
                from app.models.finance import AccountMovement
                AccountMovement.query.filter_by(
                    ref_type="sales_invoice_cash",
                    ref_id=invoice.id,
                ).delete(synchronize_session=False)
                # Reduce the treasury balance back down.
                if pay.account:
                    pay.account.current_balance = (
                        _d(pay.account.current_balance) - _d(pay.amount)
                    ).quantize(MONEY)
                db.session.delete(pay)

        invoice.is_archived = True
        log_action("sales_invoice_deleted", "SalesInvoice",
                   invoice.id,
                   details=f"total={invoice.grand_total}")
        db.session.commit()
    except (ValueError, LedgerError) as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("sales.view_invoice",
                                invoice_id=invoice.id))

    flash(f"تم حذف فاتورة البيع #{invoice.id} — كل التأثيرات اترجعت.",
          "info")
    return redirect(url_for("sales.list_invoices"))
