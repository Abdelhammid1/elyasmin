from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.inventory import IngredientForm, StockAdjustForm
from app.models.inventory import Ingredient, StockMovement
from app.utils.audit import log_action

bp = Blueprint("inventory", __name__, template_folder="../../templates/inventory")


@bp.route("/")
@login_required
def list_ingredients():
    category = request.args.get("category", "all")
    query = Ingredient.query.filter_by(is_archived=False)
    if category in (Ingredient.CATEGORY_FEED, Ingredient.CATEGORY_MEDICINE):
        query = query.filter_by(category=category)
    ingredients = query.order_by(Ingredient.category, Ingredient.name).all()
    total_stock_value = sum((i.stock_value for i in ingredients), Decimal("0"))
    low_stock_count = sum(1 for i in ingredients if i.is_low_stock)
    return render_template(
        "inventory/list.html",
        ingredients=ingredients,
        category=category,
        total_stock_value=total_stock_value,
        low_stock_count=low_stock_count,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_ingredient():
    form = IngredientForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        existing = Ingredient.query.filter(
            func.lower(Ingredient.name) == name.lower(),
            Ingredient.category == form.category.data,
        ).first()
        if existing:
            flash("مادة بنفس الاسم في نفس التصنيف مسجّلة قبل كده.", "error")
        else:
            ing = Ingredient(
                name=name,
                category=form.category.data,
                unit=form.unit.data,
                min_qty=form.min_qty.data or Decimal("0"),
                notes=form.notes.data,
                created_by_id=current_user.id,
            )
            # TC-4.1: seed the opening stock if provided
            init_qty = form.initial_qty.data or Decimal("0")
            init_price = form.initial_price.data or Decimal("0")
            if init_qty > 0:
                ing.current_qty = init_qty
                if init_price > 0:
                    ing.last_price = init_price
            db.session.add(ing)
            db.session.flush()
            if init_qty > 0:
                db.session.add(
                    StockMovement(
                        ingredient_id=ing.id,
                        delta=init_qty,
                        reason=StockMovement.REASON_ADJUST,
                        unit_price_at_move=init_price if init_price > 0 else None,
                        notes="جرد افتتاحي",
                        created_by_id=current_user.id,
                    )
                )
            log_action("ingredient_created", "Ingredient", ing.id)
            db.session.commit()
            flash(f"تم إضافة المادة {ing.name}.", "success")
            return redirect(url_for("inventory.ingredient_detail", ingredient_id=ing.id))
    return render_template("inventory/form.html", form=form, mode="create")


@bp.route("/<int:ingredient_id>")
@login_required
def ingredient_detail(ingredient_id: int):
    ing = db.session.get(Ingredient, ingredient_id)
    if not ing or ing.is_archived:
        abort(404)
    movements = (
        StockMovement.query.filter_by(ingredient_id=ing.id)
        .order_by(StockMovement.moved_on.desc(), StockMovement.id.desc())
        .limit(100)
        .all()
    )
    adjust_form = StockAdjustForm()
    return render_template(
        "inventory/detail.html",
        ingredient=ing,
        movements=movements,
        adjust_form=adjust_form,
    )


@bp.route("/<int:ingredient_id>/edit", methods=["GET", "POST"])
@login_required
def edit_ingredient(ingredient_id: int):
    ing = db.session.get(Ingredient, ingredient_id)
    if not ing or ing.is_archived:
        abort(404)
    form = IngredientForm(obj=ing)
    if form.validate_on_submit():
        # Name change: ensure uniqueness within category
        new_name = form.name.data.strip()
        if new_name != ing.name or form.category.data != ing.category:
            conflict = Ingredient.query.filter(
                func.lower(Ingredient.name) == new_name.lower(),
                Ingredient.category == form.category.data,
                Ingredient.id != ing.id,
            ).first()
            if conflict:
                flash("مادة بنفس الاسم في نفس التصنيف موجودة.", "error")
                return render_template("inventory/form.html", form=form, mode="edit", ingredient=ing)

        ing.name = new_name
        ing.category = form.category.data
        ing.unit = form.unit.data
        ing.min_qty = form.min_qty.data or Decimal("0")
        ing.notes = form.notes.data
        log_action("ingredient_updated", "Ingredient", ing.id)
        db.session.commit()
        flash("تم تحديث المادة.", "success")
        return redirect(url_for("inventory.ingredient_detail", ingredient_id=ing.id))
    return render_template("inventory/form.html", form=form, mode="edit", ingredient=ing)


@bp.route("/<int:ingredient_id>/adjust", methods=["POST"])
@login_required
def adjust_stock(ingredient_id: int):
    ing = db.session.get(Ingredient, ingredient_id)
    if not ing or ing.is_archived:
        abort(404)
    form = StockAdjustForm()
    if not form.validate_on_submit():
        for _, errors in form.errors.items():
            for e in errors:
                flash(e, "error")
        return redirect(url_for("inventory.ingredient_detail", ingredient_id=ing.id))

    delta = Decimal(str(form.delta.data))
    if ing.current_qty + delta < 0:
        flash("مينفعش الرصيد يبقى بالسالب.", "error")
        return redirect(url_for("inventory.ingredient_detail", ingredient_id=ing.id))

    ing.current_qty = ing.current_qty + delta
    movement = StockMovement(
        ingredient_id=ing.id,
        delta=delta,
        reason=StockMovement.REASON_ADJUST,
        notes=form.reason.data.strip(),
        created_by_id=current_user.id,
    )
    db.session.add(movement)
    log_action("stock_adjust", "Ingredient", ing.id, details=f"delta={delta}")
    db.session.commit()
    flash("تم تسجيل تعديل الجرد.", "success")
    return redirect(url_for("inventory.ingredient_detail", ingredient_id=ing.id))


@bp.route("/movements")
@login_required
def all_movements():
    movements = (
        StockMovement.query.order_by(StockMovement.moved_on.desc(), StockMovement.id.desc())
        .limit(200)
        .all()
    )
    return render_template("inventory/movements.html", movements=movements)
