from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.inventory import CATEGORY_CHOICES, IngredientForm, StockAdjustForm
from app.models.inventory import Ingredient, IngredientUnit, StockMovement
from app.utils.audit import log_action


def _parse_alt_units(form_data):
    """Parse dynamic alt-unit rows (unit_code_N, unit_label_N, unit_factor_N) from form."""
    rows = []
    idx = 0
    while True:
        key = f"altunit_code_{idx}"
        if key not in form_data:
            break
        code = (form_data.get(key) or "").strip()
        label = (form_data.get(f"altunit_label_{idx}") or "").strip()
        factor_raw = (form_data.get(f"altunit_factor_{idx}") or "").strip()
        idx += 1
        if not code or not label or not factor_raw:
            continue
        try:
            factor = Decimal(factor_raw)
        except Exception:  # noqa: BLE001
            continue
        if factor <= 0:
            continue
        rows.append({"code": code, "label": label, "factor": factor})
    return rows

bp = Blueprint("inventory", __name__, template_folder="../../templates/inventory")


# ---------- TICKET-2: custom types must survive into the dropdown ----------
# A custom type is stored as the string "custom:<name>" on each Ingredient;
# there is no type table. The form's choices were a fixed list, so a type the
# user created yesterday was not offered today and he retyped it every time —
# which is also how the same type ends up spelled three different ways.

def _existing_custom_categories() -> list[str]:
    """Every custom type already in use, as stored ("custom:<name>")."""
    rows = (
        db.session.query(Ingredient.category)
        .filter(Ingredient.category.like("custom:%"))
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})


def _category_choices() -> list[tuple[str, str]]:
    """Fixed types + every custom type already created + "add a new one"."""
    choices = list(CATEGORY_CHOICES[:-1])  # the two fixed types
    for cat in _existing_custom_categories():
        choices.append((cat, cat[len("custom:"):]))
    choices.append(CATEGORY_CHOICES[-1])  # keep "➕ نوع جديد" last
    return choices


def _normalise_type_name(raw: str) -> str:
    """Trim and collapse inner whitespace so 'اعلاف  جافه ' == 'اعلاف جافه'."""
    return " ".join((raw or "").split())


def _resolve_category(form) -> str | None:
    """The category to store, or None if the user asked for a new type and left
    the name blank (the caller flashes and re-renders).

    A new name that matches an existing type case-insensitively reuses that
    type's exact stored value, so the same type never exists twice.
    """
    cat = form.category.data
    if cat != "__custom__":
        return cat

    name = _normalise_type_name(form.custom_category.data)
    if not name:
        return None

    for existing in _existing_custom_categories():
        if existing[len("custom:"):].casefold() == name.casefold():
            return existing
    return "custom:" + name


@bp.route("/")
@login_required
def list_ingredients():
    category = request.args.get("category", "all")
    query = Ingredient.query.filter_by(is_archived=False)
    if category in (Ingredient.CATEGORY_FEED, Ingredient.CATEGORY_MEDICINE):
        query = query.filter_by(category=category)
    elif category == "custom":
        query = query.filter(Ingredient.category.like("custom:%"))
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
    form.category.choices = _category_choices()  # TICKET-2

    # TICKET-4: the medicine screen links here with ?category=medicine so adding
    # a medicine item lands on the right type without hunting for it.
    if request.method == "GET":
        preset = request.args.get("category")
        if preset in dict(form.category.choices):
            form.category.data = preset

    if form.validate_on_submit():
        name = form.name.data.strip()

        cat = _resolve_category(form)
        if cat is None:
            flash("لازم تكتب اسم النوع الجديد.", "error")
            return render_template("inventory/form.html", form=form, mode="create")

        existing = Ingredient.query.filter(
            func.lower(Ingredient.name) == name.lower(),
            Ingredient.category == cat,
        ).first()
        if existing:
            flash("مادة بنفس الاسم في نفس التصنيف مسجّلة قبل كده.", "error")
        else:
            ing = Ingredient(
                name=name,
                category=cat,
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
            # TICKET-2: persist alt units the user added on the form
            for row in _parse_alt_units(request.form):
                if row["code"] == ing.unit:
                    continue  # can't duplicate the base unit as an alt unit
                db.session.add(IngredientUnit(
                    ingredient_id=ing.id,
                    unit_code=row["code"],
                    unit_label=row["label"],
                    factor_to_base=row["factor"],
                ))
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
    form.category.choices = _category_choices()  # TICKET-2
    if form.validate_on_submit():
        cat = _resolve_category(form)
        if cat is None:
            flash("لازم تكتب اسم النوع الجديد.", "error")
            return render_template("inventory/form.html", form=form, mode="edit", ingredient=ing)

        # Name change: ensure uniqueness within category
        new_name = form.name.data.strip()
        if new_name != ing.name or cat != ing.category:
            conflict = Ingredient.query.filter(
                func.lower(Ingredient.name) == new_name.lower(),
                Ingredient.category == cat,
                Ingredient.id != ing.id,
            ).first()
            if conflict:
                flash("مادة بنفس الاسم في نفس التصنيف موجودة.", "error")
                return render_template("inventory/form.html", form=form, mode="edit", ingredient=ing)

        ing.name = new_name
        ing.category = cat
        ing.unit = form.unit.data
        ing.min_qty = form.min_qty.data or Decimal("0")
        ing.notes = form.notes.data

        # TICKET-2: sync alt units — delete removed rows, upsert current
        submitted = _parse_alt_units(request.form)
        submitted_codes = {r["code"] for r in submitted if r["code"] != ing.unit}
        # Remove alt units no longer submitted
        for existing in list(ing.alt_units or []):
            if existing.unit_code not in submitted_codes:
                db.session.delete(existing)
        # Upsert
        existing_by_code = {u.unit_code: u for u in (ing.alt_units or [])}
        for row in submitted:
            if row["code"] == ing.unit:
                continue
            if row["code"] in existing_by_code:
                existing_by_code[row["code"]].unit_label = row["label"]
                existing_by_code[row["code"]].factor_to_base = row["factor"]
            else:
                db.session.add(IngredientUnit(
                    ingredient_id=ing.id,
                    unit_code=row["code"],
                    unit_label=row["label"],
                    factor_to_base=row["factor"],
                ))

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

    input_delta = Decimal(str(form.delta.data))
    # TICKET-2: convert delta from user's unit to base
    unit_code = (request.form.get("unit_code") or ing.unit).strip()
    if unit_code != ing.unit and ing.factor_for(unit_code) is None:
        flash(f"الوحدة {unit_code} مش معرّفة للصنف ده.", "error")
        return redirect(url_for("inventory.ingredient_detail", ingredient_id=ing.id))
    factor = ing.factor_for(unit_code) or Decimal("1")
    delta = (input_delta * Decimal(str(factor))).quantize(Decimal("0.001"))

    if ing.current_qty + delta < 0:
        flash("مينفعش الرصيد يبقى بالسالب.", "error")
        return redirect(url_for("inventory.ingredient_detail", ingredient_id=ing.id))

    ing.current_qty = ing.current_qty + delta
    movement = StockMovement(
        ingredient_id=ing.id,
        delta=delta,
        reason=StockMovement.REASON_ADJUST,
        input_qty=input_delta,
        input_unit_code=unit_code,
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
