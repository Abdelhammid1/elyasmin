"""TICKET-5: the reports screen.

Deliberately separate from the existing suppliers report — the client asked for
a place of its own. Every report renders as a page and downloads as both Excel
and PDF, reusing excel_response and pdf_from_current_page (app/utils/reports.py)
so the look and the auth handling match the reports already in the app.
"""
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request, url_for
from flask_login import login_required

from app.models.herd import Birth, Calf, CattleGroup, Cow
from app.models.inventory import Ingredient
from app.utils.reports import excel_response, pdf_from_current_page

bp = Blueprint("reports", __name__, template_folder="../../templates/reports")


def _group_filter():
    """The selected group id, or None for all."""
    gid = request.args.get("group_id", type=int)
    return gid or None


def _period():
    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today - timedelta(days=365)
    d_to = date.fromisoformat(to) if to else today
    return d_from, d_to


def _groups():
    return CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()


# ---------- index ----------
@bp.route("/")
@login_required
def index():
    return render_template("reports/index.html")


# ---------- herd ages ----------
def _herd_age_rows():
    q = Cow.query.filter_by(is_archived=False, status=Cow.STATUS_ACTIVE)
    gid = _group_filter()
    if gid:
        q = q.filter_by(group_id=gid)
    cows = q.order_by(Cow.group_id, Cow.ear_tag).all()
    rows = []
    for c in cows:
        months = c.age_months
        rows.append({
            "cow": c,
            "months": months,
            # Years/months reads better than a raw month count on a printed sheet.
            "age_label": (f"{months // 12} سنة و {months % 12} شهر" if months is not None
                          else "—"),
        })
    return rows


@bp.route("/herd-ages")
@login_required
def herd_ages():
    rows = _herd_age_rows()
    known = [r["months"] for r in rows if r["months"] is not None]
    return render_template(
        "reports/herd_ages.html", rows=rows, groups=_groups(),
        selected_group=_group_filter(),
        avg_months=round(sum(known) / len(known), 1) if known else None,
    )


@bp.route("/herd-ages/excel")
@login_required
def herd_ages_excel():
    rows = _herd_age_rows()
    return excel_response(
        "أعمار القطيع",
        ["رقم الأذن", "الاسم", "الجنس", "المجموعة", "تاريخ الميلاد", "العمر (شهور)", "العمر"],
        [[r["cow"].ear_tag, r["cow"].name or "", r["cow"].gender_label,
          r["cow"].group.name if r["cow"].group else "",
          r["cow"].date_of_birth.isoformat() if r["cow"].date_of_birth else "",
          r["months"] if r["months"] is not None else "", r["age_label"]] for r in rows],
        "herd_ages.xlsx",
    )


@bp.route("/herd-ages/pdf")
@login_required
def herd_ages_pdf():
    target = url_for("reports.herd_ages", group_id=_group_filter(), _external=True)
    return pdf_from_current_page(target, "herd_ages.pdf")


# ---------- births ----------
def _birth_rows():
    d_from, d_to = _period()
    births = (
        Birth.query.filter(Birth.birth_date >= d_from, Birth.birth_date <= d_to)
        .order_by(Birth.birth_date.desc())
        .all()
    )
    rows = []
    for b in births:
        calves = Calf.query.filter_by(birth_id=b.id).all()
        rows.append({
            "birth": b,
            "calves": calves,
            "alive": sum(1 for c in calves if c.is_alive),
            "dead": sum(1 for c in calves if not c.is_alive),
            "males": sum(1 for c in calves if c.gender == Cow.GENDER_MALE),
            "females": sum(1 for c in calves if c.gender == Cow.GENDER_FEMALE),
        })
    return rows


@bp.route("/births")
@login_required
def births():
    d_from, d_to = _period()
    rows = _birth_rows()
    return render_template(
        "reports/births.html", rows=rows, date_from=d_from, date_to=d_to,
        total_calves=sum(len(r["calves"]) for r in rows),
        total_alive=sum(r["alive"] for r in rows),
        total_dead=sum(r["dead"] for r in rows),
    )


@bp.route("/births/excel")
@login_required
def births_excel():
    rows = _birth_rows()
    return excel_response(
        "المواليد",
        ["التاريخ", "الأم", "عدد المواليد", "ذكور", "إناث", "أحياء", "نافقة", "نوع الولادة"],
        [[r["birth"].birth_date.isoformat(),
          r["birth"].mother.ear_tag if r["birth"].mother else "",
          len(r["calves"]), r["males"], r["females"], r["alive"], r["dead"],
          r["birth"].delivery_label] for r in rows],
        "births.xlsx",
    )


@bp.route("/births/pdf")
@login_required
def births_pdf():
    d_from, d_to = _period()
    target = url_for("reports.births", date_from=d_from.isoformat(),
                     date_to=d_to.isoformat(), _external=True)
    return pdf_from_current_page(target, f"births_{d_from}_{d_to}.pdf")


# ---------- inventory ----------
def _stock_rows():
    q = Ingredient.query.filter_by(is_archived=False)
    cat = request.args.get("category")
    if cat == "custom":
        q = q.filter(Ingredient.category.like("custom:%"))
    elif cat in (Ingredient.CATEGORY_FEED, Ingredient.CATEGORY_MEDICINE):
        q = q.filter_by(category=cat)
    return q.order_by(Ingredient.category, Ingredient.name).all()


@bp.route("/stock")
@login_required
def stock():
    items = _stock_rows()
    return render_template(
        "reports/stock.html", items=items,
        selected_category=request.args.get("category") or "",
        total_value=sum((i.stock_value for i in items), Decimal("0")),
        low_count=sum(1 for i in items if i.is_low_stock),
    )


@bp.route("/stock/excel")
@login_required
def stock_excel():
    items = _stock_rows()
    return excel_response(
        "المخزون",
        ["المادة", "النوع", "الوحدة", "الرصيد الحالي", "الحد الأدنى", "آخر سعر", "قيمة المخزون"],
        [[i.name, i.category_label, i.unit_label, float(i.current_qty),
          float(i.min_qty), float(i.last_price or 0), float(i.stock_value)] for i in items],
        "stock.xlsx",
    )


@bp.route("/stock/pdf")
@login_required
def stock_pdf():
    target = url_for("reports.stock", category=request.args.get("category"), _external=True)
    return pdf_from_current_page(target, "stock.pdf")
