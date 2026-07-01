from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms.feed import FeedRecipeForm, FeedRunForm
from app.models.feed import FeedRecipe, FeedRecipeLine, FeedRun, FeedRunLine
from app.models.herd import CattleGroup
from app.models.inventory import Ingredient, StockMovement
from app.utils.audit import log_action

bp = Blueprint("feed", __name__, template_folder="../../templates/feed")


def _to_decimal(raw: str, field_name: str) -> Decimal | None:
    try:
        val = Decimal(str(raw).strip())
        if val < 0:
            flash(f"{field_name}: القيمة لا يمكن أن تكون سالبة.", "error")
            return None
        return val
    except (InvalidOperation, ValueError, AttributeError):
        flash(f"قيمة غير صالحة في: {field_name}.", "error")
        return None


def _group_choices():
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    return [(g.id, g.name) for g in groups]


def _current_recipe_for_group(group_id: int) -> FeedRecipe | None:
    return (
        FeedRecipe.query.filter_by(group_id=group_id, is_archived=False)
        .order_by(FeedRecipe.effective_from.desc(), FeedRecipe.id.desc())
        .first()
    )


# ---------- Recipes overview ----------
@bp.route("/recipes")
@login_required
def list_recipes():
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    rows = []
    for g in groups:
        rows.append({"group": g, "recipe": _current_recipe_for_group(g.id)})
    return render_template("feed/recipes_list.html", rows=rows)


@bp.route("/recipes/<int:group_id>/edit", methods=["GET", "POST"])
@login_required
def edit_recipe(group_id: int):
    group = db.session.get(CattleGroup, group_id)
    if not group or group.is_archived:
        abort(404)

    current = _current_recipe_for_group(group_id)
    feed_ingredients = (
        Ingredient.query.filter_by(category=Ingredient.CATEGORY_FEED, is_archived=False)
        .order_by(Ingredient.name)
        .all()
    )

    form = FeedRecipeForm()
    form.group_id.choices = _group_choices()
    if request.method == "GET":
        form.group_id.data = group_id
        if current:
            form.notes.data = current.notes

    if form.validate_on_submit():
        # Parse dynamic lines
        line_items = []
        i = 0
        while True:
            ing_key = f"line_ingredient_{i}"
            if ing_key not in request.form:
                break
            ing_id_raw = request.form.get(ing_key)
            qty_raw = request.form.get(f"line_kg_{i}")
            i += 1
            if not ing_id_raw or not qty_raw:
                continue

            try:
                ing_id = int(ing_id_raw)
            except ValueError:
                flash("مادة غير صالحة في أحد البنود.", "error")
                return render_template(
                    "feed/recipe_form.html",
                    form=form,
                    group=group,
                    current=current,
                    feed_ingredients=feed_ingredients,
                )

            qty = _to_decimal(qty_raw, "الكمية")
            if qty is None:
                return render_template(
                    "feed/recipe_form.html",
                    form=form,
                    group=group,
                    current=current,
                    feed_ingredients=feed_ingredients,
                )
            if qty <= 0:
                flash("الكمية في كل بند لازم تكون أكبر من صفر.", "error")
                return render_template(
                    "feed/recipe_form.html",
                    form=form,
                    group=group,
                    current=current,
                    feed_ingredients=feed_ingredients,
                )

            ing = db.session.get(Ingredient, ing_id)
            if not ing or ing.category != Ingredient.CATEGORY_FEED:
                flash("لازم كل بنود الوصفة تكون من مواد العلف الخام.", "error")
                return render_template(
                    "feed/recipe_form.html",
                    form=form,
                    group=group,
                    current=current,
                    feed_ingredients=feed_ingredients,
                )

            line_items.append({"ingredient": ing, "kg": qty})

        if not line_items:
            flash("لازم تضيف بند واحد على الأقل.", "error")
            return render_template(
                "feed/recipe_form.html",
                form=form,
                group=group,
                current=current,
                feed_ingredients=feed_ingredients,
            )

        # Prevent duplicate ingredient lines
        seen = set()
        for item in line_items:
            if item["ingredient"].id in seen:
                flash("مادة متكررة في الوصفة — مادة كل بند مرة واحدة فقط.", "error")
                return render_template(
                    "feed/recipe_form.html",
                    form=form,
                    group=group,
                    current=current,
                    feed_ingredients=feed_ingredients,
                )
            seen.add(item["ingredient"].id)

        # Archive current version, create new
        if current:
            current.is_archived = True

        new_recipe = FeedRecipe(
            group_id=group_id,
            effective_from=form.effective_from.data,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(new_recipe)
        db.session.flush()

        for item in line_items:
            db.session.add(
                FeedRecipeLine(
                    recipe_id=new_recipe.id,
                    ingredient_id=item["ingredient"].id,
                    kg_per_batch=item["kg"],
                )
            )

        log_action(
            "recipe_saved",
            "FeedRecipe",
            new_recipe.id,
            details=f"group={group_id} lines={len(line_items)}",
        )
        db.session.commit()
        flash("تم حفظ الوصفة الجديدة. الوصفة القديمة اتأرشفت.", "success")
        return redirect(url_for("feed.list_recipes"))

    return render_template(
        "feed/recipe_form.html",
        form=form,
        group=group,
        current=current,
        feed_ingredients=feed_ingredients,
    )


# ---------- Feed runs ----------
@bp.route("/runs")
@login_required
def list_runs():
    runs = (
        FeedRun.query.filter_by(is_archived=False)
        .order_by(FeedRun.run_date.desc(), FeedRun.id.desc())
        .limit(200)
        .all()
    )
    return render_template("feed/runs_list.html", runs=runs)


@bp.route("/runs/new", methods=["GET", "POST"])
@login_required
def create_run():
    form = FeedRunForm()
    form.group_id.choices = _group_choices()

    prefill_group = request.args.get("group_id", type=int)
    if request.method == "GET" and prefill_group:
        form.group_id.data = prefill_group

    if form.validate_on_submit():
        group = db.session.get(CattleGroup, form.group_id.data)
        if not group or group.is_archived:
            flash("المجموعة غير صالحة.", "error")
            return render_template("feed/run_form.html", form=form)

        recipe = _current_recipe_for_group(group.id)
        if not recipe or not recipe.lines:
            flash(
                f"مافيش وصفة علف مفعّلة للمجموعة {group.name}. "
                "من فضلك أضف الوصفة الأول.",
                "error",
            )
            return render_template("feed/run_form.html", form=form)

        batches = form.batches_count.data
        # Check inventory sufficiency for every line
        insufficient = []
        for line in recipe.lines:
            need = line.kg_per_batch * batches
            if line.ingredient.current_qty < need:
                insufficient.append(
                    {
                        "name": line.ingredient.name,
                        "need": need,
                        "have": line.ingredient.current_qty,
                        "unit": line.ingredient.unit_label,
                    }
                )
        if insufficient:
            return render_template(
                "feed/run_form.html",
                form=form,
                recipe=recipe,
                group=group,
                batches=batches,
                insufficient=insufficient,
            )

        # Create the run + snapshot lines + deduct inventory
        run = FeedRun(
            run_date=form.run_date.data,
            group_id=group.id,
            recipe_id=recipe.id,
            batches_count=batches,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(run)
        db.session.flush()

        total_weight = Decimal("0")
        total_cost = Decimal("0")
        for rline in recipe.lines:
            qty_used = (rline.kg_per_batch * batches).quantize(Decimal("0.001"))
            unit_price = rline.ingredient.last_price or Decimal("0")
            line_cost = (qty_used * unit_price).quantize(Decimal("0.01"))

            total_weight += qty_used
            total_cost += line_cost

            db.session.add(
                FeedRunLine(
                    run_id=run.id,
                    ingredient_id=rline.ingredient.id,
                    qty_used=qty_used,
                    unit_price=unit_price,
                    line_cost=line_cost,
                )
            )

            # Deduct inventory
            rline.ingredient.current_qty = rline.ingredient.current_qty - qty_used

            db.session.add(
                StockMovement(
                    ingredient_id=rline.ingredient.id,
                    delta=-qty_used,
                    reason=StockMovement.REASON_FEED_RUN,
                    ref_id=run.id,
                    unit_price_at_move=unit_price,
                    moved_on=run.run_date,
                    notes=f"تشغيل علف #{run.id} — {group.name}",
                    created_by_id=current_user.id,
                )
            )

        run.total_weight_kg = total_weight
        run.total_cost = total_cost.quantize(Decimal("0.01"))
        run.cost_per_kg = (
            (total_cost / total_weight).quantize(Decimal("0.001")) if total_weight else Decimal("0")
        )

        log_action(
            "feed_run_created",
            "FeedRun",
            run.id,
            details=f"group={group.id} batches={batches} cost={run.total_cost}",
        )
        db.session.commit()
        flash(
            f"تم تسجيل التشغيل: {batches} خلطة، وزن {total_weight}kg، تكلفة {run.total_cost} جنيه.",
            "success",
        )
        return redirect(url_for("feed.view_run", run_id=run.id))

    return render_template("feed/run_form.html", form=form)


@bp.route("/runs/<int:run_id>")
@login_required
def view_run(run_id: int):
    run = db.session.get(FeedRun, run_id)
    if not run or run.is_archived:
        abort(404)
    return render_template("feed/run_view.html", run=run)


# ---------- US-2.2 BR1+BR2: edit feed run ----------
@bp.route("/runs/<int:run_id>/edit", methods=["GET", "POST"])
@login_required
def edit_run(run_id: int):
    from datetime import date as _date

    from flask_login import current_user

    run = db.session.get(FeedRun, run_id)
    if not run or run.is_archived:
        abort(404)

    today = _date.today()
    # BR1: same-day edit allowed for any authenticated user
    # BR2: after that (past date) → admin only
    if run.run_date != today and not current_user.is_admin:
        flash("تعديل تشغيل قديم يحتاج صلاحية Admin.", "error")
        return redirect(url_for("feed.view_run", run_id=run.id))

    if request.method == "POST":
        try:
            new_batches = int(request.form.get("batches_count", "0"))
        except ValueError:
            new_batches = 0
        if new_batches < 1 or new_batches > 200:
            flash("عدد الخلطات لازم يكون بين 1 و 200.", "error")
            return render_template("feed/run_edit.html", run=run)

        if new_batches == run.batches_count:
            flash("لا يوجد تغيير.", "info")
            return redirect(url_for("feed.view_run", run_id=run.id))

        diff_batches = new_batches - run.batches_count
        recipe = run.recipe

        # Check inventory for increase
        if diff_batches > 0:
            for line in recipe.lines:
                extra = line.kg_per_batch * diff_batches
                if line.ingredient.current_qty < extra:
                    flash(
                        f"مخزون غير كافي من {line.ingredient.name} "
                        f"(محتاج {extra}, متاح {line.ingredient.current_qty})",
                        "error",
                    )
                    return render_template("feed/run_edit.html", run=run)

        # Apply diff: update stock + add adjustment movements + refresh snapshot totals
        run.batches_count = new_batches
        total_weight = Decimal("0")
        total_cost = Decimal("0")

        # Delete old FeedRunLines & recreate from current prices? No — keep them
        # historically accurate. Instead, update qty_used per existing line.
        for rl in run.lines:
            recipe_line = next((l for l in recipe.lines if l.ingredient_id == rl.ingredient_id), None)
            if not recipe_line:
                continue
            new_qty = (recipe_line.kg_per_batch * new_batches).quantize(Decimal("0.001"))
            qty_diff = new_qty - rl.qty_used
            rl.qty_used = new_qty
            rl.line_cost = (new_qty * rl.unit_price).quantize(Decimal("0.01"))

            rl.ingredient.current_qty = rl.ingredient.current_qty - qty_diff
            db.session.add(
                StockMovement(
                    ingredient_id=rl.ingredient_id,
                    delta=-qty_diff,
                    reason=StockMovement.REASON_ADJUST,
                    ref_id=run.id,
                    unit_price_at_move=rl.unit_price,
                    moved_on=today,
                    notes=f"تعديل تشغيل #{run.id}",
                    created_by_id=current_user.id,
                )
            )

            total_weight += rl.qty_used
            total_cost += rl.line_cost

        run.total_weight_kg = total_weight
        run.total_cost = total_cost.quantize(Decimal("0.01"))
        run.cost_per_kg = (
            (total_cost / total_weight).quantize(Decimal("0.001")) if total_weight else Decimal("0")
        )

        log_action("feed_run_edited", "FeedRun", run.id, details=f"batches->{new_batches}")
        db.session.commit()
        flash("تم تعديل التشغيل.", "success")
        return redirect(url_for("feed.view_run", run_id=run.id))

    return render_template("feed/run_edit.html", run=run)
