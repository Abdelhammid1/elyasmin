from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.finance import (
    CompanyProfileForm,
    ExpenseForm,
    ReportFilterForm,
    SettingsForm,
)
from app.models.feed import FeedingSession, FeedTank, FeedTankMovement
from app.models.finance import CompanyProfile, TreasuryAccount, Expense, Setting
from app.models.herd import AnimalSale, CattleGroup
from app.models.sales import MilkDelivery
from app.utils import accounts as acc
from app.utils.audit import log_action
from app.utils.decorators import admin_required, write_required
from app.utils.reports import excel_response, pdf_from_current_page

bp = Blueprint("finance", __name__, template_folder="../../templates/finance")


# ---------- Settings — tabbed page (PHASE 11 YAS-SET-2) ----------
@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    """Tabbed settings page. Which tab was submitted is carried in a
    hidden `_tab` field so we only validate + save the form that was
    actually filled in.

    Tabs:
      - company   → CompanyProfileForm (YAS-SET-1..4)
      - pricing   → existing SettingsForm (milk pricing / cost split)
      - reminders → placeholder for a later phase
    """
    active_tab = request.args.get("tab") or request.form.get("_tab") or "company"

    # ---- always-loaded forms ----
    pricing_form = SettingsForm()
    company_form = CompanyProfileForm()
    profile = CompanyProfile.current()

    if request.method == "GET":
        # Pricing (unchanged) — read from Setting table
        pricing_form.cost_split_milk_pct.data = Setting.get_decimal(Setting.KEY_COST_SPLIT_MILK_PCT, Decimal("80"))
        pricing_form.cost_split_others_pct.data = Setting.get_decimal(Setting.KEY_COST_SPLIT_OTHERS_PCT, Decimal("20"))
        pricing_form.quality_price_base.data = Setting.get_decimal(Setting.KEY_QUALITY_PRICE_BASE, Decimal("6"))
        pricing_form.quality_protein_adj.data = Setting.get_decimal(Setting.KEY_QUALITY_PROTEIN_ADJ, Decimal("0.5"))
        pricing_form.quality_bacteria_penalty.data = Setting.get_decimal(Setting.KEY_QUALITY_BACTERIA_PENALTY, Decimal("0.25"))
        pricing_form.quality_fat_ref.data = Setting.get_decimal(Setting.KEY_QUALITY_FAT_REF, Decimal("3.0"))
        pricing_form.quality_fat_adj.data = Setting.get_decimal(Setting.KEY_QUALITY_FAT_ADJ, Decimal("0"))

        # Company — read from CompanyProfile
        company_form.name.data = profile.name
        company_form.base_currency.data = profile.base_currency
        company_form.tax_rate_pct.data = profile.tax_rate_pct
        company_form.region.data = profile.region
        company_form.legal_name.data = profile.legal_name
        company_form.commercial_register_no.data = profile.commercial_register_no
        company_form.tax_registration_no.data = profile.tax_registration_no
        company_form.address.data = profile.address
        company_form.bank_account_holder.data = profile.bank_account_holder
        company_form.bank_name.data = profile.bank_name
        company_form.bank_account_no.data = profile.bank_account_no
        company_form.bank_iban.data = profile.bank_iban
        company_form.invoice_number_prefix_sale.data = profile.invoice_number_prefix_sale
        company_form.invoice_number_prefix_purchase.data = profile.invoice_number_prefix_purchase
        company_form.reminder_days_before_due.data = profile.reminder_days_before_due

    # ---- POST → save whichever tab was submitted ----
    if request.method == "POST" and active_tab == "pricing":
        if pricing_form.validate_on_submit():
            milk_pct = Decimal(str(pricing_form.cost_split_milk_pct.data))
            others_pct = Decimal(str(pricing_form.cost_split_others_pct.data))
            if milk_pct + others_pct != Decimal("100"):
                flash("مجموع النسبتين لازم يساوي 100.", "error")
            else:
                Setting.set(Setting.KEY_COST_SPLIT_MILK_PCT, str(milk_pct), "نسبة تحميل التكاليف على الحليب")
                Setting.set(Setting.KEY_COST_SPLIT_OTHERS_PCT, str(others_pct), "نسبة تحميل التكاليف على باقي المجموعات")
                Setting.set(Setting.KEY_QUALITY_PRICE_BASE, str(pricing_form.quality_price_base.data), "سعر أساس اللبن بالتحليل")
                Setting.set(Setting.KEY_QUALITY_PROTEIN_ADJ, str(pricing_form.quality_protein_adj.data), "زيادة السعر لكل +1% بروتين")
                Setting.set(Setting.KEY_QUALITY_BACTERIA_PENALTY, str(pricing_form.quality_bacteria_penalty.data), "خصم لكل +100k بكتيريا")
                Setting.set(Setting.KEY_QUALITY_FAT_REF,
                            str(pricing_form.quality_fat_ref.data if pricing_form.quality_fat_ref.data is not None else Decimal("3.0")),
                            "نسبة الدهن اللي الزيادة بتبدأ فوقها")
                Setting.set(Setting.KEY_QUALITY_FAT_ADJ,
                            str(pricing_form.quality_fat_adj.data if pricing_form.quality_fat_adj.data is not None else Decimal("0")),
                            "زيادة السعر لكل +1% دهن")
                log_action("settings_updated", "Setting", 0)
                db.session.commit()
                flash("تم حفظ إعدادات التسعير.", "success")
                return redirect(url_for("finance.settings", tab="pricing"))

    if request.method == "POST" and active_tab == "company":
        if company_form.validate_on_submit():
            profile.name = company_form.name.data.strip()
            profile.base_currency = company_form.base_currency.data
            profile.tax_rate_pct = Decimal(str(company_form.tax_rate_pct.data or 0))
            profile.region = (company_form.region.data or "").strip() or None
            profile.legal_name = (company_form.legal_name.data or "").strip() or None
            profile.commercial_register_no = (company_form.commercial_register_no.data or "").strip() or None
            profile.tax_registration_no = (company_form.tax_registration_no.data or "").strip() or None
            profile.address = (company_form.address.data or "").strip() or None
            profile.bank_account_holder = (company_form.bank_account_holder.data or "").strip() or None
            profile.bank_name = (company_form.bank_name.data or "").strip() or None
            profile.bank_account_no = (company_form.bank_account_no.data or "").strip() or None
            profile.bank_iban = (company_form.bank_iban.data or "").strip() or None
            profile.invoice_number_prefix_sale = (company_form.invoice_number_prefix_sale.data or "INV").strip()
            profile.invoice_number_prefix_purchase = (company_form.invoice_number_prefix_purchase.data or "PUR").strip()
            profile.reminder_days_before_due = int(company_form.reminder_days_before_due.data or 3)
            profile.updated_by_id = current_user.id

            # ---- logo upload handling ----
            uploaded = request.files.get("logo") if request.files else None
            if uploaded and uploaded.filename:
                import os
                from werkzeug.utils import secure_filename
                from flask import current_app

                target_dir = os.path.join(
                    current_app.root_path, "static", "img", "company",
                )
                os.makedirs(target_dir, exist_ok=True)
                # Delete the old file if it was ours (best-effort)
                if profile.logo_path:
                    old = os.path.join(current_app.root_path, "static",
                                       profile.logo_path.lstrip("/"))
                    try:
                        if os.path.isfile(old):
                            os.remove(old)
                    except OSError:
                        pass
                ext = uploaded.filename.rsplit(".", 1)[-1].lower()
                fname = f"logo_{profile.id}.{ext}"
                path_abs = os.path.join(target_dir, secure_filename(fname))
                uploaded.save(path_abs)
                profile.logo_path = f"img/company/{secure_filename(fname)}"

            log_action("company_profile_updated", "CompanyProfile", profile.id)
            db.session.commit()
            flash("تم حفظ بيانات الشركة.", "success")
            return redirect(url_for("finance.settings", tab="company"))

    return render_template(
        "finance/settings.html",
        company_form=company_form,
        pricing_form=pricing_form,
        profile=profile,
        active_tab=active_tab,
        # Keep legacy `form` name pointing at pricing_form so any external
        # link that expects the old shape still works.
        form=pricing_form,
    )


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
@write_required
def create_expense():
    form = ExpenseForm()
    form.account_id.choices = acc.active_choices()
    if not form.account_id.choices:
        flash("لازم تضيف حساب (خزنة أو بنك) الأول عشان تسجّل مصروف.", "error")
        return redirect(url_for("accounts.create_account"))

    if form.validate_on_submit():
        # US-5.2 AC4: allow custom category
        cat = form.category.data
        if cat == "__custom__":
            custom = (form.custom_category.data or "").strip()
            if not custom:
                flash("لازم تكتب اسم النوع الجديد.", "error")
                return render_template("finance/expense_form.html", form=form)
            cat = "custom:" + custom

        account = db.session.get(TreasuryAccount, form.account_id.data)
        if not account or account.is_archived:
            flash("الحساب غير صالح.", "error")
            return render_template("finance/expense_form.html", form=form)

        e = Expense(
            category=cat,
            amount=Decimal(str(form.amount.data)),
            expense_date=form.expense_date.data,
            description=form.description.data,
            ref_type="manual",
            account_id=account.id,
            created_by_id=current_user.id,
        )
        db.session.add(e)
        db.session.flush()

        # TREASURY: a manual expense is a cash event in its own right — unlike
        # the Expense rows that mirror a supplier or worker payment.
        if acc.expense_moves_money(e):
            acc.money_out(
                account, e.amount, e.expense_date,
                ref_type="expense", ref_id=e.id, user_id=current_user.id,
                notes=f"مصروف: {e.category_label}",
            )
            # ACCOUNTING: only the real cash expenses post JEs; the mirror
            # rows (supplier_payment / worker_payment) are skipped by the
            # autoposter itself, so no branch needed here.
            from app.services import autoposting
            autoposting.on_expense(e, account, created_by=current_user.id)

        log_action("expense_manual", "Expense", e.id, details=f"cat={e.category} amt={e.amount}")
        db.session.commit()
        flash(
            f"تم تسجيل مصروف {e.amount} في {e.category_label} من {account.name}. "
            f"رصيد {account.name} بقى {account.current_balance} جنيه.",
            "success",
        )
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

    FEED-TANK: feed cost is now taken from what was actually WITHDRAWN and fed,
    not from what was mixed. A run that produces a batch stored for later moves
    nothing here until the feeding worker draws it out.

    TICKET-3: feed cost = what came out of the tank (the recipe) PLUS the
    additions tipped in from general stores at feeding time. Both are eaten;
    only their sources differ.

    direct_milk_feed_cost = Σ (milk-group tank withdrawals + milk-group additions)
    other_direct_feed_cost = Σ (other-group tank withdrawals + other-group additions)
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

    def _withdrawn_cost(milk_groups: bool):
        """FEED-TANK: cost of feed actually withdrawn and fed in the period.

        Withdrawal rows are stored with a negative total_cost (see the sign
        convention on FeedTankMovement), so the sum is negated to get a positive
        cost.
        """
        group_filter = (
            FeedTank.group_id.in_(milk_group_ids or [0])
            if milk_groups
            else ~FeedTank.group_id.in_(milk_group_ids or [0])
        )
        total = (
            db.session.query(func.coalesce(func.sum(FeedTankMovement.total_cost), 0))
            .join(FeedTank, FeedTankMovement.tank_id == FeedTank.id)
            .filter(
                FeedTankMovement.movement_type == FeedTankMovement.TYPE_WITHDRAWAL,
                FeedTankMovement.moved_on >= date_from,
                FeedTankMovement.moved_on <= date_to,
                group_filter,
            )
            .scalar()
        ) or 0
        return -Decimal(str(total))

    def _additions_cost(milk_groups: bool):
        """TICKET-3: cost of materials tipped in at the trough from general
        stores (سيلاج، تبن، دريس، قش).

        These are real feed the animals ate — on the client's own numbers a milk
        meal is 400kg of recipe against 800kg of additions. Leaving them out
        would report barely a third of what the milk actually cost.
        """
        group_filter = (
            FeedingSession.group_id.in_(milk_group_ids or [0])
            if milk_groups
            else ~FeedingSession.group_id.in_(milk_group_ids or [0])
        )
        total = (
            db.session.query(func.coalesce(func.sum(FeedingSession.additions_cost), 0))
            .filter(
                FeedingSession.session_date >= date_from,
                FeedingSession.session_date <= date_to,
                group_filter,
            )
            .scalar()
        ) or 0
        return Decimal(str(total))

    direct_milk = _withdrawn_cost(milk_groups=True) + _additions_cost(milk_groups=True)
    other_direct = _withdrawn_cost(milk_groups=False) + _additions_cost(milk_groups=False)

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
