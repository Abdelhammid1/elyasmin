from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.suppliers import SupplierForm, SupplierPaymentForm
from app.models.finance import Expense
from app.models.suppliers import PurchaseInvoice, Supplier, SupplierPayment
from app.utils.audit import log_action
from app.utils.reports import excel_response

bp = Blueprint("suppliers", __name__, template_folder="../../templates/suppliers")


@bp.route("/")
@login_required
def list_suppliers():
    suppliers = Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    total_owed = sum((s.balance_due for s in suppliers), Decimal("0"))
    return render_template("suppliers/list.html", suppliers=suppliers, total_owed=total_owed)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_supplier():
    form = SupplierForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if Supplier.query.filter(func.lower(Supplier.name) == name.lower()).first():
            flash("مورد بنفس الاسم مسجّل قبل كده.", "error")
        else:
            supplier = Supplier(
                name=name,
                phone=(form.phone.data or "").strip() or None,
                supplied_categories=",".join(form.supplied_categories.data),
                notes=form.notes.data,
                created_by_id=current_user.id,
            )
            db.session.add(supplier)
            db.session.flush()
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
    return render_template(
        "suppliers/detail.html",
        supplier=supplier,
        invoices=invoices,
        payments=payments,
        payment_form=payment_form,
    )


@bp.route("/<int:supplier_id>/edit", methods=["GET", "POST"])
@login_required
def edit_supplier(supplier_id: int):
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier or supplier.is_archived:
        abort(404)

    form = SupplierForm(obj=supplier)
    if request.method == "GET":
        form.supplied_categories.data = supplier.categories_list

    if form.validate_on_submit():
        supplier.name = form.name.data.strip()
        supplier.phone = (form.phone.data or "").strip() or None
        supplier.supplied_categories = ",".join(form.supplied_categories.data)
        supplier.notes = form.notes.data
        log_action("supplier_updated", "Supplier", supplier.id)
        db.session.commit()
        flash("تم تحديث بيانات المورد.", "success")
        return redirect(url_for("suppliers.supplier_detail", supplier_id=supplier.id))

    return render_template("suppliers/form.html", form=form, mode="edit", supplier=supplier)


# ---------- US-3.3 Supplier payment ----------
@bp.route("/<int:supplier_id>/pay", methods=["POST"])
@login_required
def record_payment(supplier_id: int):
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier or supplier.is_archived:
        abort(404)

    form = SupplierPaymentForm()
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

    payment = SupplierPayment(
        supplier_id=supplier.id,
        amount=amount,
        payment_date=form.payment_date.data,
        method=form.method.data,
        notes=form.notes.data,
        created_by_id=current_user.id,
    )
    db.session.add(payment)
    db.session.flush()

    # US-3.3 AC3: record as expense (cash outflow)
    db.session.add(
        Expense(
            category=Expense.CAT_SUPPLIER_PAYMENT,
            amount=amount,
            expense_date=payment.payment_date,
            description=f"دفعة للمورد {supplier.name}",
            ref_type="supplier_payment",
            ref_id=payment.id,
            created_by_id=current_user.id,
        )
    )

    log_action(
        "supplier_payment", "SupplierPayment", payment.id, details=f"supplier={supplier.id} amount={amount}"
    )
    db.session.commit()
    flash(f"تم تسجيل دفعة {amount} للمورد {supplier.name}.", "success")
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

    suppliers = Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()
    total_owed_now = sum((s.balance_due for s in suppliers), Decimal("0"))

    return render_template(
        "suppliers/report.html",
        invoices=invoices,
        suppliers=suppliers,
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
    invoices = (
        PurchaseInvoice.query.filter(
            PurchaseInvoice.is_archived.is_(False),
            PurchaseInvoice.invoice_date >= d_from,
            PurchaseInvoice.invoice_date <= d_to,
        )
        .order_by(PurchaseInvoice.invoice_date.desc())
        .all()
    )
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
