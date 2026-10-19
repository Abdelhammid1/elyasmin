from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms.feed import MedicineDispenseForm
from app.models.feed import MedicineDispense
from app.models.herd import CattleGroup, Cow
from app.models.inventory import Ingredient
from app.utils.audit import log_action
from app.utils.decorators import write_required

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
    return [(c.id, c.ear_tag) for c in cows]


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
@write_required
def create_dispense():
    form = MedicineDispenseForm()
    med_choices, _meds = _medicine_choices()
    form.ingredient_id.choices = med_choices or [(0, "— مفيش أدوية —")]
    form.cow_id.choices = [(0, "— اختر بقرة —")] + _cow_choices()
    form.group_id.choices = [(0, "— اختر مجموعة —")] + _group_choices()

    if request.method == "POST":
        if form.validate_on_submit():
            from app.services.medicine_dispense import DispenseError, dispense_medicine
            ing = db.session.get(Ingredient, form.ingredient_id.data)
            try:
                input_qty = Decimal(str(form.qty.data).strip())
            except (InvalidOperation, ValueError):
                flash("قيمة الكمية غير صالحة.", "error")
                return render_template("medicine/form.html", form=form, meds=_meds)

            target = form.dispense_target.data
            cow_id = form.cow_id.data if target == "cow" and form.cow_id.data else None
            group_id = form.group_id.data if target == "group" and form.group_id.data else None
            if target == "cow" and not cow_id:
                flash("من فضلك اختار البقرة.", "error")
                return render_template("medicine/form.html", form=form, meds=_meds)
            if target == "group" and not group_id:
                flash("من فضلك اختار المجموعة.", "error")
                return render_template("medicine/form.html", form=form, meds=_meds)

            try:
                dispense = dispense_medicine(
                    ing, input_qty, request.form.get("unit_code"),
                    cow_id=cow_id, group_id=group_id, on=form.dispensed_on.data,
                    notes=form.notes.data, user_id=current_user.id,
                )
            except DispenseError as e:
                db.session.rollback()
                flash(str(e), "error")
                return render_template("medicine/form.html", form=form, meds=_meds)

            log_action(
                "medicine_dispensed",
                "MedicineDispense",
                dispense.id,
                details=f"ingredient={ing.id} qty={dispense.qty} target={dispense.target_label} "
                        f"lot={dispense.lot_id}",
            )
            db.session.commit()
            flash(f"تم صرف {dispense.qty} {ing.unit_label} من {ing.name}.", "success")
            return redirect(url_for("medicine.list_dispenses"))
        # form invalid — fall through to re-render with errors

    return render_template("medicine/form.html", form=form, meds=_meds)
