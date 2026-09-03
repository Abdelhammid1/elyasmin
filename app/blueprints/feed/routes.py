from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms.feed import FeedingSessionForm, FeedRecipeForm, FeedRunForm
from app.models.feed import (
    FeedRecipe,
    FeedRecipeLine,
    FeedRun,
    FeedRunLine,
    FeedingAddition,
    FeedingSession,
    FeedTank,
    GroupFeedAllowance,
)
from app.models.herd import CattleGroup
from app.models.inventory import Ingredient, StockMovement
from app.utils import feed_tank
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
    """TC-3.6: date range filter + summary totals."""
    from datetime import date as _date

    today = _date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = _date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = _date.fromisoformat(to) if to else today
    group_id = request.args.get("group_id", type=int)

    q = FeedRun.query.filter(
        FeedRun.is_archived.is_(False),
        FeedRun.run_date >= d_from,
        FeedRun.run_date <= d_to,
    )
    if group_id:
        q = q.filter(FeedRun.group_id == group_id)

    runs = q.order_by(FeedRun.run_date.desc(), FeedRun.id.desc()).all()
    total_cost = sum((r.total_cost for r in runs), Decimal("0"))
    total_weight = sum((r.total_weight_kg for r in runs), Decimal("0"))
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()

    return render_template(
        "feed/runs_list.html",
        runs=runs, date_from=d_from, date_to=d_to,
        selected_group_id=group_id, groups=groups,
        total_cost=total_cost, total_weight=total_weight,
    )


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
            # PHASE 6: value at weighted-average cost (was last_price)
            unit_price = rline.ingredient.avg_cost or Decimal("0")
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

        # FEED-TANK: the batch goes into storage, it is not eaten today. The
        # cost reports read the withdrawals from this tank, not the run itself.
        tank = feed_tank.get_or_create_tank(group.id)
        feed_tank.add_production(
            tank, run.total_weight_kg, run.cost_per_kg, run.run_date,
            run_id=run.id, user_id=current_user.id,
            notes=f"تشغيل علف #{run.id} — {group.name}",
        )

        log_action(
            "feed_run_created",
            "FeedRun",
            run.id,
            details=f"group={group.id} batches={batches} cost={run.total_cost} "
                    f"tank_qty={tank.current_qty} tank_avg={tank.avg_cost_per_kg}",
        )
        db.session.commit()
        flash(
            f"تم تسجيل التشغيل: {batches} خلطة، وزن {total_weight}kg، تكلفة {run.total_cost} جنيه. "
            f"اتضافت للخزان — الرصيد دلوقتي {tank.current_qty} كيلو بمتوسط {tank.avg_cost_per_kg} جنيه/كيلو.",
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


# ---------- FEED-TANK: storage tanks ----------
@bp.route("/tanks")
@login_required
def list_tanks():
    """Every group's tank — what is in storage right now and what it is worth."""
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    tanks_by_group = {t.group_id: t for t in FeedTank.query.all()}
    rows = [{"group": g, "tank": tanks_by_group.get(g.id)} for g in groups]

    total_qty = sum(
        (Decimal(str(r["tank"].current_qty)) for r in rows if r["tank"]), Decimal("0")
    )
    total_value = sum(
        (r["tank"].current_value for r in rows if r["tank"]), Decimal("0")
    )
    return render_template(
        "feed/tanks_list.html", rows=rows, total_qty=total_qty, total_value=total_value
    )


@bp.route("/tanks/<int:group_id>/withdraw")
@login_required
def withdraw_from_tank(group_id: int):
    """TICKET-3: superseded by the feeding screen.

    Feeding is never just a tank withdrawal — the worker also tips in سيلاج/تبن/
    دريس from general inventory in the same act. Keeping a bare withdrawal
    alongside it would let someone record the feed and silently lose the
    additions, which is exactly the cost gap this ticket exists to close. Kept as
    a redirect so old links still land somewhere sensible.
    """
    return redirect(url_for("feed.create_feeding", group_id=group_id))


# ---------- TICKET-3: daily feeding log ----------
def _allowed_ingredients(group_id: int):
    """The whitelist for this group, or every raw material if none is set yet.

    Falling back to everything keeps the screen usable on day one, before anyone
    has configured a whitelist.
    """
    allowed = (
        Ingredient.query.join(
            GroupFeedAllowance, GroupFeedAllowance.ingredient_id == Ingredient.id
        )
        .filter(GroupFeedAllowance.group_id == group_id, Ingredient.is_archived.is_(False))
        .order_by(Ingredient.name)
        .all()
    )
    if allowed:
        return allowed, True
    fallback = (
        Ingredient.query.filter(
            Ingredient.is_archived.is_(False),
            Ingredient.category != Ingredient.CATEGORY_MEDICINE,
        )
        .order_by(Ingredient.name)
        .all()
    )
    return fallback, False


def _parse_additions(form_data):
    """Rows posted as addition_ingredient_N / addition_qty_N."""
    rows, idx = [], 0
    while True:
        key = f"addition_ingredient_{idx}"
        if key not in form_data:
            break
        raw_id = (form_data.get(key) or "").strip()
        raw_qty = (form_data.get(f"addition_qty_{idx}") or "").strip()
        idx += 1
        if not raw_id or not raw_qty:
            continue
        try:
            ing_id, qty = int(raw_id), Decimal(raw_qty)
        except (ValueError, InvalidOperation):
            continue
        if qty <= 0:
            continue
        rows.append({"ingredient_id": ing_id, "qty": qty})
    return rows


@bp.route("/feeding")
@login_required
def list_feedings():
    day_str = request.args.get("day")
    day = date.fromisoformat(day_str) if day_str else date.today()
    sessions = (
        FeedingSession.query.filter_by(session_date=day)
        .order_by(FeedingSession.group_id, FeedingSession.id)
        .all()
    )
    by_group = {}
    for s in sessions:
        by_group.setdefault(s.group_id, {"group": s.group, "sessions": [], "total": Decimal("0")})
        by_group[s.group_id]["sessions"].append(s)
        by_group[s.group_id]["total"] += Decimal(str(s.total_cost))
    return render_template(
        "feed/feedings_list.html",
        day=day, rows=list(by_group.values()),
        day_total=sum((Decimal(str(s.total_cost)) for s in sessions), Decimal("0")),
        day_feed=sum((Decimal(str(s.feed_cost)) for s in sessions), Decimal("0")),
        day_additions=sum((Decimal(str(s.additions_cost)) for s in sessions), Decimal("0")),
    )


@bp.route("/feeding/new", methods=["GET", "POST"])
@bp.route("/feeding/new/<int:group_id>", methods=["GET", "POST"])
@login_required
def create_feeding(group_id=None):
    """Record one meal: feed drawn from the group tank + additions from stores.

    The two sides stay separate in the data on purpose — the tank withdrawal
    never gains an ingredient, and the additions never enter recipe composition.
    They meet only in this meal's cost.
    """
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    if not groups:
        flash("مفيش مجموعات. أضف مجموعة الأول.", "error")
        return redirect(url_for("herd.groups"))

    group = db.session.get(CattleGroup, group_id) if group_id else groups[0]
    if not group or group.is_archived:
        abort(404)

    tank = feed_tank.get_or_create_tank(group.id)
    db.session.commit()  # a first-touch tank shouldn't vanish if the form errors

    allowed, has_whitelist = _allowed_ingredients(group.id)
    form = FeedingSessionForm()
    form.meal.choices = [
        (m, FeedingSession.MEAL_LABELS[m]) for m in FeedingSession.meals_for(group)
    ]

    def render(**extra):
        return render_template(
            "feed/feeding_form.html", form=form, group=group, groups=groups,
            tank=tank, allowed=allowed, has_whitelist=has_whitelist, **extra,
        )

    if form.validate_on_submit():
        rows = _parse_additions(request.form)
        allowed_ids = {i.id for i in allowed}
        feed_qty = Decimal(str(form.feed_qty.data or 0))

        if feed_qty <= 0 and not rows:
            flash("سجّل كمية علف أو إضافة واحدة على الأقل.", "error")
            return render()

        session = FeedingSession(
            group_id=group.id,
            session_date=form.session_date.data,
            meal=form.meal.data,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(session)
        db.session.flush()

        # --- the feed side: strictly the group's own tank ---
        feed_cost = Decimal("0")
        unit_cost = Decimal(str(tank.avg_cost_per_kg or 0))
        if feed_qty > 0:
            try:
                mv = feed_tank.withdraw(
                    tank, feed_qty, form.session_date.data,
                    notes=f"تغذية {group.name}",
                    user_id=current_user.id,
                )
            except ValueError as exc:
                db.session.rollback()
                form.feed_qty.errors.append(str(exc))
                flash(str(exc), "error")
                return render()
            unit_cost = Decimal(str(mv.unit_cost))
            feed_cost = mv.abs_cost

        # --- the additions side: general inventory, never the recipe ---
        additions_cost = Decimal("0")
        for row in rows:
            ing = db.session.get(Ingredient, row["ingredient_id"])
            if not ing or ing.is_archived:
                db.session.rollback()
                flash("مادة غير صالحة في الإضافات.", "error")
                return render()
            if ing.id not in allowed_ids:
                db.session.rollback()
                flash(
                    f"«{ing.name}» مش مسموح تتضاف لمجموعة {group.name}. "
                    "عدّل قائمة المسموح لو محتاجها.",
                    "error",
                )
                return render()
            if ing.current_qty < row["qty"]:
                db.session.rollback()
                flash(
                    f"مخزون {ing.name} مش كفاية — متاح {ing.current_qty} "
                    f"وانت طالب {row['qty']}.",
                    "error",
                )
                return render()

            # PHASE 6: value additions at avg_cost so the JE matches actual cost
            price = Decimal(str(ing.avg_cost or 0))
            line_cost = (row["qty"] * price).quantize(Decimal("0.01"))
            additions_cost += line_cost

            db.session.add(FeedingAddition(
                session_id=session.id, ingredient_id=ing.id, qty=row["qty"],
                unit_cost=price, total_cost=line_cost,
            ))
            ing.current_qty = ing.current_qty - row["qty"]
            db.session.add(StockMovement(
                ingredient_id=ing.id,
                delta=-row["qty"],
                reason=StockMovement.REASON_FEED_RUN,
                ref_id=session.id,
                unit_price_at_move=price,
                moved_on=form.session_date.data,
                notes=f"إضافة تغذية — {group.name}",
                created_by_id=current_user.id,
            ))

        session.feed_qty = feed_qty
        session.feed_unit_cost = unit_cost
        session.feed_cost = feed_cost
        session.additions_cost = additions_cost.quantize(Decimal("0.01"))
        session.total_cost = (feed_cost + additions_cost).quantize(Decimal("0.01"))

        # ACCOUNTING P2: post the feeding as feed-cost against the herd group.
        # This is what turns "تكلفة كيلو اللبن" from an 80/20 shadow calc into
        # a real ledger query — every meal is DR 5100 (تكلفة الأعلاف) tagged
        # with the group being fed, CR the raw-material accounts.
        from app.services import autoposting
        autoposting.on_feeding_session(session, created_by=current_user.id)

        log_action(
            "feeding_recorded", "FeedingSession", session.id,
            details=f"group={group.id} meal={session.meal} feed={feed_qty} "
                    f"additions={len(rows)} total={session.total_cost}",
        )
        db.session.commit()
        flash(
            f"تم تسجيل تغذية {group.name} ({session.meal_label}): "
            f"علف {feed_qty} كيلو بـ {feed_cost} جنيه + إضافات {session.additions_cost} جنيه "
            f"= {session.total_cost} جنيه.",
            "success",
        )
        return redirect(url_for("feed.list_feedings", day=session.session_date.isoformat()))

    return render()


@bp.route("/feeding/allowances/<int:group_id>", methods=["GET", "POST"])
@login_required
def group_allowances(group_id: int):
    """TICKET-3: pick which raw materials may be added to this group."""
    group = db.session.get(CattleGroup, group_id)
    if not group or group.is_archived:
        abort(404)

    materials = (
        Ingredient.query.filter(
            Ingredient.is_archived.is_(False),
            Ingredient.category != Ingredient.CATEGORY_MEDICINE,
        )
        .order_by(Ingredient.name)
        .all()
    )

    if request.method == "POST":
        picked = {int(v) for v in request.form.getlist("ingredient_ids") if v.isdigit()}
        GroupFeedAllowance.query.filter_by(group_id=group.id).delete()
        for ing_id in picked:
            db.session.add(GroupFeedAllowance(group_id=group.id, ingredient_id=ing_id))
        log_action("group_allowances_saved", "CattleGroup", group.id,
                   details=f"count={len(picked)}")
        db.session.commit()
        flash(f"تم حفظ المواد المسموح إضافتها لمجموعة {group.name} ({len(picked)} مادة).",
              "success")
        return redirect(url_for("feed.list_tanks"))

    current = {a.ingredient_id for a in
               GroupFeedAllowance.query.filter_by(group_id=group.id).all()}
    return render_template(
        "feed/group_allowances.html", group=group, materials=materials, current=current,
        groups=CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all(),
    )


@bp.route("/tanks/<int:group_id>/statement")
@login_required
def tank_statement(group_id: int):
    """Movements in date order with a running balance — same shape as the
    supplier statement the app already has."""
    group = db.session.get(CattleGroup, group_id)
    if not group or group.is_archived:
        abort(404)

    tank = FeedTank.query.filter_by(group_id=group_id).first()
    rows = []
    running = Decimal("0")
    if tank:
        for mv in tank.movements:
            running += Decimal(str(mv.qty))
            rows.append({"mv": mv, "balance": running})
        rows.reverse()  # newest first for reading, balance already computed

    return render_template(
        "feed/tank_statement.html", group=group, tank=tank, rows=rows
    )


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

        # FEED-TANK: the produced weight changed, so the tank has to follow or it
        # drifts out of sync with the runs that filled it.
        old_weight = run.total_weight_kg
        run.total_weight_kg = total_weight
        run.total_cost = total_cost.quantize(Decimal("0.01"))
        run.cost_per_kg = (
            (total_cost / total_weight).quantize(Decimal("0.001")) if total_weight else Decimal("0")
        )

        weight_diff = Decimal(str(total_weight)) - Decimal(str(old_weight or 0))
        if weight_diff != 0:
            tank = feed_tank.get_or_create_tank(run.group_id)
            try:
                feed_tank.adjust(
                    tank, weight_diff, run.cost_per_kg, today,
                    run_id=run.id, user_id=current_user.id,
                    notes=f"تعديل تشغيل #{run.id} — الخلطات من {run.batches_count - diff_batches} لـ {new_batches}",
                )
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "error")
                return render_template("feed/run_edit.html", run=run)

        log_action("feed_run_edited", "FeedRun", run.id, details=f"batches->{new_batches}")
        db.session.commit()
        flash("تم تعديل التشغيل، والخزان اتحدّث.", "success")
        return redirect(url_for("feed.view_run", run_id=run.id))

    return render_template("feed/run_edit.html", run=run)
