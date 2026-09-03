"""PHASE 9 UI: dashboard rewritten to marsoud shape, farm-adapted.

Sections that render on the page (matching marsoud verbatim):
    1. Header + period switcher
    2. 💚 الصحة المالية — 4 KPI cards with sparklines
    3. 🐄 نظرة على المزرعة — farm ops tiles
    4. 🔔 يحتاج انتباهك الآن — overdue-invoices panels
    5. 📊 الاتجاه المالي — 6-month revenue-vs-feed-cost + expense breakdown
    6. ⚡ إجراءات سريعة — quick-action tiles

Every helper returns simple Decimal / list-of-dict so the template can
render without extra logic. All ledger queries pin to the current head
JournalEntry.is_active=True filter.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models.accounting import JournalEntry, JournalLine, LedgerAccount
from app.models.checks import Check
from app.models.finance import TreasuryAccount
from app.models.herd import CattleGroup, Cow
from app.models.inventory import Ingredient, MedicineLot
from app.models.sales import Customer, DailyProduction, MilkDelivery, MilkInvoice
from app.models.suppliers import PurchaseInvoice, Supplier

bp = Blueprint("dashboard", __name__)


# ---------------- period helpers ----------------
PERIODS = ("day", "week", "month", "year")
PERIOD_LABELS = {
    "day":   "اليوم",
    "week":  "الأسبوع",
    "month": "الشهر",
    "year":  "السنة",
}


def _period_range(period: str, ref: date = None) -> tuple[date, date]:
    ref = ref or date.today()
    if period == "day":
        return ref, ref
    if period == "week":
        return ref - timedelta(days=6), ref
    if period == "year":
        return ref.replace(month=1, day=1), ref
    # default: month
    return ref.replace(day=1), ref


def _previous_range(period: str, ref: date = None) -> tuple[date, date]:
    ref = ref or date.today()
    d_from, d_to = _period_range(period, ref)
    span = (d_to - d_from).days + 1
    return d_from - timedelta(days=span), d_from - timedelta(days=1)


# ---------------- ledger totals per account code ----------------
def _account_net(code: str, d_from: date = None, d_to: date = None) -> Decimal:
    """Signed net (DR - CR) on the LedgerAccount identified by `code`,
    optionally clipped to a date range."""
    acc = LedgerAccount.query.filter_by(code=code).first()
    if acc is None:
        return Decimal("0")
    q = (
        db.session.query(func.coalesce(
            func.sum(JournalLine.debit - JournalLine.credit), 0))
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .filter(JournalEntry.is_active.is_(True),
                JournalLine.account_id == acc.id)
    )
    if d_from is not None:
        q = q.filter(JournalEntry.date >= d_from)
    if d_to is not None:
        q = q.filter(JournalEntry.date <= d_to)
    return Decimal(str(q.scalar() or 0))


def _sum_expense_codes(d_from: date, d_to: date, codes: list[str]) -> Decimal:
    """Sum the debit side (positive expense) across a list of expense
    codes for the given range."""
    total = Decimal("0")
    for c in codes:
        total += _account_net(c, d_from, d_to)
    return total


# ---------------- trend & breakdown ----------------
def _trend_6m(today: date) -> list[dict]:
    """Milk revenue vs feed cost per month, last 6 calendar months (inclusive)."""
    months = []
    y, m = today.year, today.month
    # Walk back 6 months.
    for i in range(5, -1, -1):
        month = m - i
        year = y
        while month <= 0:
            month += 12
            year -= 1
        d_from = date(year, month, 1)
        # last day of that month
        if month == 12:
            d_to = date(year, 12, 31)
        else:
            d_to = date(year, month + 1, 1) - timedelta(days=1)
        # Revenue = negative net on 4010 (credit-normal). Flip sign.
        revenue = -_account_net("4010", d_from, d_to)
        # Feed cost = positive net on 5010 (debit-normal).
        feed_cost = _account_net("5010", d_from, d_to)
        months.append({
            "label": d_from.strftime("%b"),
            "revenue": float(revenue),
            "feed_cost": float(feed_cost),
        })
    return months


def _expense_breakdown(d_from: date, d_to: date) -> list[dict]:
    """Top 5 expense codes for the period + Other. Returns rows with
    name / amount / pct / colour."""
    palette = ["#43b9e9", "#dc4640", "#d97706", "#6a52c4", "#0e9b86", "#94a3b8"]
    expense_codes = [
        ("5010", "الأعلاف"),
        ("5020", "الأدوية"),
        ("5030", "العمالة"),
        ("5040", "الكهرباء"),
        ("5050", "الصيانة"),
        ("5060", "الإيجار"),
        ("5070", "الإهلاك"),
        ("5075", "النقل"),
        ("5080", "أخرى"),
    ]
    rows = []
    for code, label in expense_codes:
        amount = _account_net(code, d_from, d_to)
        if amount > 0:
            rows.append({"code": code, "name": label, "amount": amount})
    rows.sort(key=lambda r: r["amount"], reverse=True)
    top = rows[:5]
    other = rows[5:]
    if other:
        top.append({
            "code": "OTHER",
            "name": "أخرى",
            "amount": sum((r["amount"] for r in other), Decimal("0")),
        })
    total = sum((r["amount"] for r in top), Decimal("0")) or Decimal("1")
    for i, r in enumerate(top):
        r["pct"] = int((r["amount"] / total) * 100)
        r["color"] = palette[i % len(palette)]
    return top


# ---------------- KPI sparkline series ----------------
def _spark_series(period: str, today: date, kind: str) -> list[float]:
    """A short daily series suitable for a 32-px sparkline. `kind`:
       cash | revenue | ar | ap. Cheap approximations, not a full P&L."""
    if period == "day":
        pts = 7
    elif period == "week":
        pts = 14
    elif period == "year":
        pts = 12   # months for the sparkline in the year view
    else:
        pts = 30

    if kind == "revenue":
        series = []
        for i in range(pts, 0, -1):
            d = today - timedelta(days=i - 1)
            series.append(float(-_account_net("4010", d, d)))
        return series

    if kind == "cash":
        # Simple daily end-of-day cumulative on 1010 + 1020 up to that day.
        series = []
        for i in range(pts, 0, -1):
            d = today - timedelta(days=i - 1)
            v = _account_net("1010", None, d) + _account_net("1020", None, d)
            series.append(float(v))
        return series

    # ar / ap end-of-day balances
    code = "1100" if kind == "ar" else "2010"
    series = []
    for i in range(pts, 0, -1):
        d = today - timedelta(days=i - 1)
        series.append(float(abs(_account_net(code, None, d))))
    return series


# ---------------- overdue rows for the "needs attention" panels ----------------
def _customer_initials(name: str) -> str:
    words = [w for w in (name or "").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:1]
    return (words[0][:1] + words[-1][:1])


def _overdue_customer_invoices(today: date, limit: int = 5) -> list[dict]:
    rows = []
    invoices = (
        MilkInvoice.query
        .filter_by(is_archived=False, status=MilkInvoice.STATUS_ISSUED)
        .all()
    )
    for inv in invoices:
        outstanding = Decimal(str(inv.outstanding_amount or 0))
        if outstanding <= 0:
            continue
        days_late = (today - inv.issue_date).days
        if days_late <= 0:
            continue
        rows.append({
            "id": inv.id,
            "customer_name": inv.customer.name if inv.customer else "—",
            "customer_initials": _customer_initials(
                inv.customer.name if inv.customer else "?"),
            "number": f"#{inv.id}",
            "issue_date": inv.issue_date,
            "amount": outstanding,
            "days_late": days_late,
        })
    rows.sort(key=lambda r: r["days_late"], reverse=True)
    return rows[:limit]


def _overdue_supplier_bills(today: date, limit: int = 5) -> list[dict]:
    rows = []
    invoices = (
        PurchaseInvoice.query
        .filter_by(is_archived=False, payment_type=PurchaseInvoice.PAY_CREDIT)
        .all()
    )
    for inv in invoices:
        outstanding = Decimal(str(inv.outstanding_amount or 0))
        if outstanding <= 0:
            continue
        days_late = (today - inv.invoice_date).days - 30  # simple 30-day term
        if days_late <= 0:
            continue
        rows.append({
            "id": inv.id,
            "supplier_name": inv.supplier.name if inv.supplier else "—",
            "supplier_initials": _customer_initials(
                inv.supplier.name if inv.supplier else "?"),
            "number": f"#{inv.id}",
            "invoice_date": inv.invoice_date,
            "amount": outstanding,
            "days_late": days_late,
        })
    rows.sort(key=lambda r: r["days_late"], reverse=True)
    return rows[:limit]


# ==================== route ====================
@bp.route("/dashboard")
@login_required
def index():
    period = request.args.get("period", "month")
    if period not in PERIODS:
        return redirect(url_for("dashboard.index", period="month"))

    today = date.today()
    d_from, d_to = _period_range(period, today)
    pd_from, pd_to = _previous_range(period, today)

    # ---------------- financial health KPIs ----------------
    cash_total = Decimal("0")
    for t in TreasuryAccount.query.filter_by(is_archived=False).all():
        cash_total += Decimal(str(t.current_balance or 0))

    milk_revenue = -_account_net("4010", d_from, d_to)
    prev_milk_revenue = -_account_net("4010", pd_from, pd_to)

    all_expense_codes = ["5010", "5020", "5030", "5040", "5050",
                         "5060", "5070", "5075", "5080"]
    expenses = _sum_expense_codes(d_from, d_to, all_expense_codes)
    prev_expenses = _sum_expense_codes(pd_from, pd_to, all_expense_codes)
    net_profit = milk_revenue - expenses
    prev_net_profit = prev_milk_revenue - prev_expenses

    def _pct_change(cur, prev):
        if prev == 0:
            return 0
        return int(((cur - prev) / abs(prev)) * 100)

    ar_total = Decimal("0")
    for c in Customer.query.filter_by(is_archived=False).all():
        ar_total += c.balance

    # AP total — reuse the existing computation
    invoices_total = (
        db.session.query(func.coalesce(func.sum(PurchaseInvoice.total), 0))
        .filter(PurchaseInvoice.is_archived.is_(False))
        .scalar()
    ) or 0
    paid_invoices_total = (
        db.session.query(func.coalesce(func.sum(PurchaseInvoice.paid_amount), 0))
        .filter(PurchaseInvoice.is_archived.is_(False))
        .scalar()
    ) or 0
    ap_total = max(Decimal("0"),
                   Decimal(str(invoices_total)) - Decimal(str(paid_invoices_total)))

    # Sparklines
    sparklines = {
        "cash":    _spark_series(period, today, "cash"),
        "revenue": _spark_series(period, today, "revenue"),
        "ar":      _spark_series(period, today, "ar"),
        "ap":      _spark_series(period, today, "ap"),
    }

    kpis = {
        "cash": {
            "value": cash_total,
            "spark": sparklines["cash"],
            "trend": 0,   # no historical cash snapshot to compare
        },
        "net_profit": {
            "value": net_profit,
            "spark": sparklines["revenue"],
            "trend": _pct_change(net_profit, prev_net_profit),
        },
        "ar": {
            "value": ar_total,
            "spark": sparklines["ar"],
            "trend": 0,
        },
        "ap": {
            "value": ap_total,
            "spark": sparklines["ap"],
            "trend": 0,
        },
    }

    # ---------------- farm overview ops ----------------
    active_by_group = dict(
        db.session.query(Cow.group_id, func.count(Cow.id))
        .filter(Cow.status == Cow.STATUS_ACTIVE, Cow.is_archived.is_(False))
        .group_by(Cow.group_id).all()
    )
    total_active = sum(active_by_group.values())

    milking_group_count = (
        db.session.query(func.count(Cow.id))
        .join(CattleGroup, Cow.group_id == CattleGroup.id)
        .filter(Cow.status == Cow.STATUS_ACTIVE,
                Cow.is_archived.is_(False),
                CattleGroup.type == CattleGroup.TYPE_MILK)
        .scalar()
    ) or 0

    yesterday = today - timedelta(days=1)
    yesterday_milk_kg = (
        db.session.query(func.coalesce(func.sum(MilkDelivery.qty_kg), 0))
        .filter(MilkDelivery.delivery_date == yesterday,
                MilkDelivery.is_archived.is_(False))
        .scalar()
    ) or 0
    yesterday_milk_value = (
        db.session.query(func.coalesce(func.sum(MilkDelivery.total_value), 0))
        .filter(MilkDelivery.delivery_date == yesterday,
                MilkDelivery.is_archived.is_(False))
        .scalar()
    ) or 0
    yesterday_prod = DailyProduction.query.filter_by(production_date=yesterday).first()
    yesterday_waste = None
    if yesterday_prod:
        yesterday_waste = max(
            Decimal("0"),
            Decimal(str(yesterday_prod.total_kg)) - Decimal(str(yesterday_milk_kg))
        )

    # Low stock + expiring medicine + upcoming checks
    low_stock_ings = (
        Ingredient.query.filter(
            Ingredient.is_archived.is_(False),
            Ingredient.min_qty > 0,
            Ingredient.current_qty <= Ingredient.min_qty,
        ).order_by(Ingredient.current_qty).all()
    )
    expiring_soon_lots = (
        MedicineLot.query.filter(
            MedicineLot.qty_remaining > 0,
            MedicineLot.expires_on.isnot(None),
            MedicineLot.expires_on <= today + timedelta(days=30),
        ).order_by(MedicineLot.expires_on.asc()).limit(20).all()
    )
    upcoming_checks = (
        Check.query.filter(
            Check.status == Check.STATUS_PENDING,
            Check.is_archived.is_(False),
            Check.due_date <= today + timedelta(days=7),
        ).order_by(Check.due_date.asc()).limit(20).all()
    )
    overdue_checks_count = sum(1 for c in upcoming_checks if c.days_until_due() < 0)

    active_suppliers_count = (
        db.session.query(func.count(Supplier.id))
        .filter(Supplier.is_archived.is_(False)).scalar()
    ) or 0

    # Feed tank overview — average cost across all tanks
    from app.models.feed import FeedTank
    tanks = FeedTank.query.all()
    tank_count = len(tanks)
    if tanks:
        total_kg = sum((Decimal(str(t.current_qty or 0)) for t in tanks), Decimal("0"))
        weighted = sum(
            (Decimal(str(t.current_qty or 0)) * Decimal(str(t.avg_cost_per_kg or 0))
             for t in tanks),
            Decimal("0"),
        )
        avg_feed_cost = (weighted / total_kg).quantize(Decimal("0.001")) if total_kg > 0 else Decimal("0")
    else:
        avg_feed_cost = Decimal("0")

    from app.models.labor import Worker
    active_workers_count = (
        db.session.query(func.count(Worker.id))
        .filter(Worker.is_archived.is_(False)).scalar()
    ) or 0

    # ---------------- attention panels ----------------
    overdue_customers = _overdue_customer_invoices(today)
    overdue_suppliers = _overdue_supplier_bills(today)
    overdue_customers_total = sum((r["amount"] for r in overdue_customers), Decimal("0"))
    overdue_suppliers_total = sum((r["amount"] for r in overdue_suppliers), Decimal("0"))

    # ---------------- trend + breakdown ----------------
    trend_6m = _trend_6m(today)
    expense_breakdown = _expense_breakdown(d_from, d_to)

    return render_template(
        "dashboard/index.html",
        period=period, period_label=PERIOD_LABELS.get(period, "الشهر"),
        d_from=d_from, d_to=d_to,
        today=today,
        kpis=kpis,
        # farm ops
        total_active=total_active,
        milking_group_count=milking_group_count,
        yesterday_milk_kg=yesterday_milk_kg,
        yesterday_milk_value=yesterday_milk_value,
        yesterday_waste=yesterday_waste,
        low_stock_ings=low_stock_ings,
        expiring_soon_lots=expiring_soon_lots,
        upcoming_checks=upcoming_checks,
        overdue_checks_count=overdue_checks_count,
        active_suppliers_count=active_suppliers_count,
        tank_count=tank_count,
        avg_feed_cost=avg_feed_cost,
        active_workers_count=active_workers_count,
        # attention
        overdue_customers=overdue_customers,
        overdue_suppliers=overdue_suppliers,
        overdue_customers_total=overdue_customers_total,
        overdue_suppliers_total=overdue_suppliers_total,
        # trend
        trend_6m=trend_6m,
        expense_breakdown=expense_breakdown,
    )
