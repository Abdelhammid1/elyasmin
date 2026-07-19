from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.finance import ExpenseForm, ReportFilterForm, SettingsForm
from app.models.feed import FeedRun
from app.models.finance import Expense, Setting
from app.models.herd import AnimalSale, CattleGroup
from app.models.labor import Attendance, Worker, WorkerPayment
from app.models.sales import Customer, DailyProduction, MilkDelivery
from app.models.suppliers import PurchaseInvoice, Supplier, SupplierPayment
from app.utils.audit import log_action
from app.utils.decorators import admin_required
from app.utils.reports import excel_response, pdf_from_current_page

bp = Blueprint("finance", __name__, template_folder="../../templates/finance")


# ---------- Settings ----------
@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    form = SettingsForm()
    if request.method == "GET":
        form.cost_split_milk_pct.data = Setting.get_decimal(Setting.KEY_COST_SPLIT_MILK_PCT, Decimal("80"))
        form.cost_split_others_pct.data = Setting.get_decimal(Setting.KEY_COST_SPLIT_OTHERS_PCT, Decimal("20"))
        form.quality_price_base.data = Setting.get_decimal(Setting.KEY_QUALITY_PRICE_BASE, Decimal("6"))
        form.quality_protein_adj.data = Setting.get_decimal(Setting.KEY_QUALITY_PROTEIN_ADJ, Decimal("0.5"))
        form.quality_bacteria_penalty.data = Setting.get_decimal(Setting.KEY_QUALITY_BACTERIA_PENALTY, Decimal("0.25"))

    if form.validate_on_submit():
        milk_pct = Decimal(str(form.cost_split_milk_pct.data))
        others_pct = Decimal(str(form.cost_split_others_pct.data))
        if milk_pct + others_pct != Decimal("100"):
            flash("مجموع النسبتين لازم يساوي 100.", "error")
        else:
            Setting.set(Setting.KEY_COST_SPLIT_MILK_PCT, str(milk_pct), "نسبة تحميل التكاليف على الحليب")
            Setting.set(Setting.KEY_COST_SPLIT_OTHERS_PCT, str(others_pct), "نسبة تحميل التكاليف على باقي المجموعات")
            Setting.set(Setting.KEY_QUALITY_PRICE_BASE, str(form.quality_price_base.data), "سعر أساس اللبن بالتحليل")
            Setting.set(Setting.KEY_QUALITY_PROTEIN_ADJ, str(form.quality_protein_adj.data), "زيادة السعر لكل +1% بروتين")
            Setting.set(Setting.KEY_QUALITY_BACTERIA_PENALTY, str(form.quality_bacteria_penalty.data), "خصم لكل +100k بكتيريا")
            log_action("settings_updated", "Setting", 0)
            db.session.commit()
            flash("تم حفظ الإعدادات.", "success")
            return redirect(url_for("finance.settings"))

    return render_template("finance/settings.html", form=form)


# ---------- Expenses ----------
@bp.route("/expenses")
@login_required
def list_expenses():
    filter_form = ReportFilterForm(request.args, meta={"csrf": False})
    today = date.today()
    date_from = filter_form.date_from.data or today.replace(day=1)
    date_to = filter_form.date_to.data or today

    expenses = (
        Expense.query.filter(
            Expense.is_archived.is_(False),
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        )
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .all()
    )
    total = sum((e.amount for e in expenses), Decimal("0"))
    by_category = {}
    for e in expenses:
        by_category[e.category_label] = by_category.get(e.category_label, Decimal("0")) + e.amount

    return render_template(
        "finance/expenses_list.html",
        expenses=expenses,
        total=total,
        by_category=by_category,
        date_from=date_from,
        date_to=date_to,
        filter_form=filter_form,
    )


@bp.route("/expenses/new", methods=["GET", "POST"])
@login_required
def create_expense():
    form = ExpenseForm()
    if form.validate_on_submit():
        # US-5.2 AC4: allow custom category
        cat = form.category.data
        if cat == "__custom__":
            custom = (form.custom_category.data or "").strip()
            if not custom:
                flash("لازم تكتب اسم النوع الجديد.", "error")
                return render_template("finance/expense_form.html", form=form)
            cat = "custom:" + custom

        e = Expense(
            category=cat,
            amount=Decimal(str(form.amount.data)),
            expense_date=form.expense_date.data,
            description=form.description.data,
            ref_type="manual",
            created_by_id=current_user.id,
        )
        db.session.add(e)
        db.session.flush()
        log_action("expense_manual", "Expense", e.id, details=f"cat={e.category} amt={e.amount}")
        db.session.commit()
        flash(f"تم تسجيل مصروف {e.amount} في {e.category_label}.", "success")
        return redirect(url_for("finance.list_expenses"))
    return render_template("finance/expense_form.html", form=form)


# ---------- Milk cost per kg (US-5.1) ----------
def _period_bounds():
    fm = request.args.get("from")
    to = request.args.get("to")
    today = date.today()
    d_from = date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = date.fromisoformat(to) if to else today
    return d_from, d_to


def _compute_milk_cost(date_from: date, date_to: date) -> dict:
    """The core Sprint 5 calculation.

    direct_milk_feed_cost = Σ FeedRun.total_cost where group is TYPE_MILK
    other_direct_feed_cost = Σ FeedRun.total_cost where group ≠ TYPE_MILK
    indirect_total = Expenses in period (excluding those already counted via feed runs / supplier payments)
                     — we take ALL non-archived expenses in the period (mgr enters generals monthly)
    indirect_milk_share = indirect_total × milk_pct / 100
    total_milk_cost = direct_milk_feed_cost + indirect_milk_share
    total_milk_kg = Σ MilkDelivery.qty_kg in period
    cost_per_kg = total_milk_cost / total_milk_kg
    """
    milk_group_ids = [
        g.id for g in CattleGroup.query.filter_by(type=CattleGroup.TYPE_MILK, is_archived=False).all()
    ]

    direct_milk = (
        db.session.query(func.coalesce(func.sum(FeedRun.total_cost), 0))
        .filter(
            FeedRun.is_archived.is_(False),
            FeedRun.run_date >= date_from,
            FeedRun.run_date <= date_to,
            FeedRun.group_id.in_(milk_group_ids or [0]),
        )
        .scalar()
    ) or 0

    other_direct = (
        db.session.query(func.coalesce(func.sum(FeedRun.total_cost), 0))
        .filter(
            FeedRun.is_archived.is_(False),
            FeedRun.run_date >= date_from,
            FeedRun.run_date <= date_to,
            ~FeedRun.group_id.in_(milk_group_ids or [0]),
        )
        .scalar()
    ) or 0

    indirect_total = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(
            Expense.is_archived.is_(False),
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        )
        .scalar()
    ) or 0

    milk_pct = Setting.get_decimal(Setting.KEY_COST_SPLIT_MILK_PCT, Decimal("80"))
    others_pct = Setting.get_decimal(Setting.KEY_COST_SPLIT_OTHERS_PCT, Decimal("20"))

    direct_milk = Decimal(str(direct_milk))
    other_direct = Decimal(str(other_direct))
    indirect_total = Decimal(str(indirect_total))

    indirect_milk_share = (indirect_total * milk_pct / Decimal("100")).quantize(Decimal("0.01"))
    indirect_others_share = (indirect_total * others_pct / Decimal("100")).quantize(Decimal("0.01"))
    total_milk_cost = direct_milk + indirect_milk_share

    total_milk_kg = (
        db.session.query(func.coalesce(func.sum(MilkDelivery.qty_kg), 0))
        .filter(
            MilkDelivery.is_archived.is_(False),
            MilkDelivery.delivery_date >= date_from,
            MilkDelivery.delivery_date <= date_to,
        )
        .scalar()
    ) or 0
    total_milk_kg = Decimal(str(total_milk_kg))
    cost_per_kg = (total_milk_cost / total_milk_kg).quantize(Decimal("0.001")) if total_milk_kg > 0 else Decimal("0")

    return {
        "direct_milk": direct_milk.quantize(Decimal("0.01")),
        "other_direct": other_direct.quantize(Decimal("0.01")),
        "indirect_total": indirect_total.quantize(Decimal("0.01")),
        "indirect_milk_share": indirect_milk_share,
        "indirect_others_share": indirect_others_share,
        "total_milk_cost": total_milk_cost.quantize(Decimal("0.01")),
        "total_milk_kg": total_milk_kg,
        "cost_per_kg": cost_per_kg,
        "milk_pct": milk_pct,
        "others_pct": others_pct,
    }


@bp.route("/milk-cost")
@login_required
def milk_cost():
    d_from, d_to = _period_bounds()
    r = _compute_milk_cost(d_from, d_to)
    return render_template(
        "finance/milk_cost.html",
        r=r,
        date_from=d_from,
        date_to=d_to,
    )


# ---------- P&L report (US-5.3) ----------
def _compute_pnl(date_from: date, date_to: date) -> dict:
    milk_rev = (
        db.session.query(func.coalesce(func.sum(MilkDelivery.total_value), 0))
        .filter(
            MilkDelivery.is_archived.is_(False),
            MilkDelivery.delivery_date >= date_from,
            MilkDelivery.delivery_date <= date_to,
        )
        .scalar()
    ) or 0
    animal_rev = (
        db.session.query(func.coalesce(func.sum(AnimalSale.price), 0))
        .filter(
            AnimalSale.sale_date >= date_from,
            AnimalSale.sale_date <= date_to,
        )
        .scalar()
    ) or 0

    # Expenses = Expense table entries
    total_expenses = (
        db.session.query(func.coalesce(func.sum(Expense.amount), 0))
        .filter(
            Expense.is_archived.is_(False),
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        )
        .scalar()
    ) or 0

    by_cat = dict(
        db.session.query(Expense.category, func.coalesce(func.sum(Expense.amount), 0))
        .filter(
            Expense.is_archived.is_(False),
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        )
        .group_by(Expense.category)
        .all()
    )

    milk_rev = Decimal(str(milk_rev))
    animal_rev = Decimal(str(animal_rev))
    total_expenses = Decimal(str(total_expenses))
    total_revenue = milk_rev + animal_rev
    net = total_revenue - total_expenses

    return {
        "milk_rev": milk_rev,
        "animal_rev": animal_rev,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net": net,
        "by_cat": [(Expense.LABELS.get(k, k), Decimal(str(v))) for k, v in by_cat.items()],
    }


@bp.route("/pnl")
@login_required
def pnl():
    d_from, d_to = _period_bounds()
    r = _compute_pnl(d_from, d_to)
    return render_template("finance/pnl.html", r=r, date_from=d_from, date_to=d_to)


@bp.route("/pnl/pdf")
@login_required
def pnl_pdf():
    """TC-7.5: real server-side PDF download."""
    d_from, d_to = _period_bounds()
    target = url_for("finance.pnl", **{"from": d_from.isoformat(), "to": d_to.isoformat()}, _external=True)
    return pdf_from_current_page(target, f"pnl_{d_from}_{d_to}.pdf")


@bp.route("/milk-cost/pdf")
@login_required
def milk_cost_pdf():
    """TC-7.3: PDF save for milk-cost report."""
    d_from, d_to = _period_bounds()
    target = url_for("finance.milk_cost", **{"from": d_from.isoformat(), "to": d_to.isoformat()}, _external=True)
    return pdf_from_current_page(target, f"milk_cost_{d_from}_{d_to}.pdf")


@bp.route("/pnl/excel")
@login_required
def pnl_excel():
    d_from, d_to = _period_bounds()
    r = _compute_pnl(d_from, d_to)
    rows = [
        ["إيرادات اللبن", float(r["milk_rev"])],
        ["إيرادات بيع الحيوانات", float(r["animal_rev"])],
        ["إجمالي الإيرادات", float(r["total_revenue"])],
        ["", ""],
    ]
    for label, amt in r["by_cat"]:
        rows.append([f"مصروف: {label}", float(amt)])
    rows.append(["إجمالي المصروفات", float(r["total_expenses"])])
    rows.append(["", ""])
    rows.append(["صافي الربح / (الخسارة)", float(r["net"])])
    return excel_response(
        "PnL",
        ["البند", f"القيمة (من {d_from} إلى {d_to})"],
        rows,
        f"pnl_{d_from}_{d_to}.xlsx",
    )


@bp.route("/expenses/excel")
@login_required
def expenses_excel():
    filter_form = ReportFilterForm(request.args, meta={"csrf": False})
    today = date.today()
    date_from = filter_form.date_from.data or today.replace(day=1)
    date_to = filter_form.date_to.data or today
    expenses = (
        Expense.query.filter(
            Expense.is_archived.is_(False),
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        )
        .order_by(Expense.expense_date.desc())
        .all()
    )
    rows = [
        [e.expense_date.isoformat(), e.category_label, float(e.amount), e.description or ""]
        for e in expenses
    ]
    return excel_response(
        "Expenses",
        ["التاريخ", "النوع", "المبلغ", "الوصف"],
        rows,
        f"expenses_{date_from}_{date_to}.xlsx",
    )
