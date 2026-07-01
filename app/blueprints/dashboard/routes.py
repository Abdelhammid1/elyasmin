from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, render_template
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models.herd import AnimalSale, Birth, CattleGroup, Cow, Death
from app.models.inventory import Ingredient
from app.models.sales import Customer, MilkDelivery
from app.models.suppliers import PurchaseInvoice, Supplier, SupplierPayment

bp = Blueprint("dashboard", __name__)


def _month_start() -> date:
    today = date.today()
    return today.replace(day=1)


@bp.route("/")
@login_required
def index():
    month_start = _month_start()

    # Active herd counts by group (single grouped query — fast even for 750+ rows)
    active_by_group = dict(
        db.session.query(Cow.group_id, func.count(Cow.id))
        .filter(Cow.status == Cow.STATUS_ACTIVE, Cow.is_archived.is_(False))
        .group_by(Cow.group_id)
        .all()
    )
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    group_stats = [{"group": g, "count": active_by_group.get(g.id, 0)} for g in groups]
    total_active = sum(active_by_group.values())

    # Monthly counters
    births_this_month = (
        db.session.query(func.count(Birth.id)).filter(Birth.birth_date >= month_start).scalar()
    ) or 0
    deaths_this_month = (
        db.session.query(func.count(Death.id)).filter(Death.death_date >= month_start).scalar()
    ) or 0
    sales_this_month_count = (
        db.session.query(func.count(AnimalSale.id))
        .filter(AnimalSale.sale_date >= month_start)
        .scalar()
    ) or 0
    sales_this_month_value = (
        db.session.query(func.coalesce(func.sum(AnimalSale.price), 0))
        .filter(AnimalSale.sale_date >= month_start)
        .scalar()
    ) or 0

    # Low-stock ingredients (US-2.4 AC2)
    low_stock_ings = (
        Ingredient.query.filter(
            Ingredient.is_archived.is_(False),
            Ingredient.min_qty > 0,
            Ingredient.current_qty <= Ingredient.min_qty,
        )
        .order_by(Ingredient.current_qty)
        .all()
    )

    # Suppliers total balance owed
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
    payments_total = (
        db.session.query(func.coalesce(func.sum(SupplierPayment.amount), 0))
        .filter(SupplierPayment.is_archived.is_(False))
        .scalar()
    ) or 0
    total_owed_to_suppliers = (
        Decimal(str(invoices_total)) - Decimal(str(paid_invoices_total)) - Decimal(str(payments_total))
    )
    if total_owed_to_suppliers < 0:
        total_owed_to_suppliers = Decimal("0")

    active_suppliers_count = (
        db.session.query(func.count(Supplier.id)).filter(Supplier.is_archived.is_(False)).scalar()
    ) or 0

    # Recent activity
    recent_births = Birth.query.order_by(Birth.birth_date.desc()).limit(5).all()
    recent_sales = AnimalSale.query.order_by(AnimalSale.sale_date.desc()).limit(5).all()

    # US-1.8 AC1: yesterday's milk delivery total
    yesterday = date.today() - timedelta(days=1)
    yesterday_milk_kg = (
        db.session.query(func.coalesce(func.sum(MilkDelivery.qty_kg), 0))
        .filter(
            MilkDelivery.delivery_date == yesterday,
            MilkDelivery.is_archived.is_(False),
        )
        .scalar()
    ) or 0
    yesterday_milk_value = (
        db.session.query(func.coalesce(func.sum(MilkDelivery.total_value), 0))
        .filter(
            MilkDelivery.delivery_date == yesterday,
            MilkDelivery.is_archived.is_(False),
        )
        .scalar()
    ) or 0

    total_owed_from_customers = Decimal("0")
    for c in Customer.query.filter_by(is_archived=False).all():
        total_owed_from_customers += c.balance

    return render_template(
        "dashboard/index.html",
        yesterday_milk_kg=yesterday_milk_kg,
        yesterday_milk_value=yesterday_milk_value,
        total_owed_from_customers=total_owed_from_customers,
        group_stats=group_stats,
        total_active=total_active,
        births_this_month=births_this_month,
        deaths_this_month=deaths_this_month,
        sales_this_month_count=sales_this_month_count,
        sales_this_month_value=sales_this_month_value,
        recent_births=recent_births,
        recent_sales=recent_sales,
        low_stock_ings=low_stock_ings,
        total_owed_to_suppliers=total_owed_to_suppliers,
        active_suppliers_count=active_suppliers_count,
    )
