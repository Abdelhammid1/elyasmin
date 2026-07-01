from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms.feed import MedicineDispenseForm
from app.models.feed import MedicineDispense
from app.models.herd import CattleGroup, Cow
from app.models.inventory import Ingredient, StockMovement
from app.utils.audit import log_action

bp = Blueprint("medicine", __name__, template_folder="../../templates/medicine")


def _medicine_choices():
    meds = (
        Ingredient.query.filter_by(category=Ingredient.CATEGORY_MEDICINE, is_archived=False)
        .order_by(Ingredient.name)
        .all()
    )
    return [(m.id, f"{m.name} — رصيد: {m.current_qty} {m.unit_label}") for m in meds], meds


def _cow_choices():
    cows = (
        Cow.query.filter_by(status=Cow.STATUS_ACTIVE, is_archived=False)
        .order_by(Cow.ear_tag)
        .all()
    )
    return [(c.id, f"{c.ear_tag}{(' — ' + c.name) if c.name else ''}") for c in cows]


def _group_choices():
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    return [(g.id, g.name) for g in groups]


@bp.route("/")
@login_required
def list_dispenses():
    from datetime import date

    today = date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = date.fromisoformat(to) if to else today

    dispenses = (
        MedicineDispense.query.filter(
            MedicineDispense.is_archived.is_(False),
            MedicineDispense.dispensed_on >= d_from,
            MedicineDispense.dispensed_on <= d_to,
        )
        .order_by(MedicineDispense.dispensed_on.desc(), MedicineDispense.id.desc())
        .all()
    )
    total_cost = sum((d.total_cost for d in dispenses), Decimal("0"))
    return render_template(
        "medicine/list.html",
        dispenses=dispenses,
        date_from=d_from,
        date_to=d_to,
        total_cost=total_cost,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_dispense():
    form = MedicineDispenseForm()
    med_choices, _meds = _medicine_choices()
    form.ingredient_id.choices = med_choices or [(0, "— مفيش أدوية —")]
    form.cow_id.choices = [(0, "— اختر بقرة —")] + _cow_choices()
    form.group_id.choices = [(0, "— اختر مجموعة —")] + _group_choices()

    if request.method == "POST":
        if form.validate_on_submit():
            ing = db.session.get(Ingredient, form.ingredient_id.data)
            if not ing or ing.category != Ingredient.CATEGORY_MEDICINE or ing.is_archived:
                flash("لازم تختار دواء صحيح.", "error")
                return render_template("medicine/form.html", form=form)

            try:
                qty = Decimal(str(form.qty.data).strip())
            except (InvalidOperation, ValueError):
                flash("قيمة الكمية غير صالحة.", "error")
                return render_template("medicine/form.html", form=form)
            if qty <= 0:
                flash("الكمية لازم تكون أكبر من صفر.", "error")
                return render_template("medicine/form.html", form=form)
            if qty > ing.current_qty:
                flash(
                    f"مفيش رصيد كافي. المتاح: {ing.current_qty} {ing.unit_label}.",
                    "error",
                )
                return render_template("medicine/form.html", form=form)

            target = form.dispense_target.data
            cow_id = form.cow_id.data if target == "cow" and form.cow_id.data else None
            group_id = form.group_id.data if target == "group" and form.group_id.data else None

            if target == "cow" and not cow_id:
                flash("من فضلك اختار البقرة.", "error")
                return render_template("medicine/form.html", form=form)
            if target == "group" and not group_id:
                flash("من فضلك اختار المجموعة.", "error")
                return render_template("medicine/form.html", form=form)

            unit_price = ing.last_price or Decimal("0")
            total_cost = (qty * unit_price).quantize(Decimal("0.01"))

            dispense = MedicineDispense(
                ingredient_id=ing.id,
                qty=qty,
                unit_price_at_dispense=unit_price,
                total_cost=total_cost,
                cow_id=cow_id,
                group_id=group_id,
                dispensed_on=form.dispensed_on.data,
                notes=form.notes.data,
                created_by_id=current_user.id,
            )
            db.session.add(dispense)
            db.session.flush()

            # Deduct inventory
            ing.current_qty = ing.current_qty - qty
            db.session.add(
                StockMovement(
                    ingredient_id=ing.id,
                    delta=-qty,
                    reason=StockMovement.REASON_MEDICINE,
                    ref_id=dispense.id,
                    unit_price_at_move=unit_price,
                    moved_on=form.dispensed_on.data,
                    notes=f"صرف دواء — {dispense.target_label}",
                    created_by_id=current_user.id,
                )
            )

            log_action(
                "medicine_dispensed",
                "MedicineDispense",
                dispense.id,
                details=f"ingredient={ing.id} qty={qty} target={dispense.target_label}",
            )
            db.session.commit()
            flash(f"تم صرف {qty} {ing.unit_label} من {ing.name}.", "success")
            return redirect(url_for("medicine.list_dispenses"))
        # form invalid — fall through to re-render with errors

    return render_template("medicine/form.html", form=form)
