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
                return render_template("medicine/form.html", form=form, meds=_meds)

            try:
                input_qty = Decimal(str(form.qty.data).strip())
            except (InvalidOperation, ValueError):
                flash("قيمة الكمية غير صالحة.", "error")
                return render_template("medicine/form.html", form=form, meds=_meds)
            if input_qty <= 0:
                flash("الكمية لازم تكون أكبر من صفر.", "error")
                return render_template("medicine/form.html", form=form, meds=_meds)

            # TICKET-2: convert from user's unit to base unit
            from app.utils.units import to_base
            unit_code = (request.form.get("unit_code") or ing.unit).strip()
            if unit_code != ing.unit and ing.factor_for(unit_code) is None:
                flash(f"الوحدة {unit_code} مش معرّفة للدواء ده.", "error")
                return render_template("medicine/form.html", form=form, meds=_meds)
            qty = to_base(input_qty, unit_code, ing) or input_qty

            if qty > ing.current_qty:
                flash(
                    f"مفيش رصيد كافي. المتاح: {ing.current_qty} {ing.unit_label}.",
                    "error",
                )
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

            # PHASE 6: pick lots FIFO by expiry, decrement each lot, and
            # value the dispense at the weighted average of what was
            # actually pulled (which is exact for a single-lot case and
            # correctly blended when the pick spans lots).
            from app.utils import inventory_cost
            from app.services import autoposting
            try:
                picks = inventory_cost.pick_lots_fifo(ing, qty)
            except ValueError as ve:
                flash(str(ve), "error")
                return render_template("medicine/form.html", form=form, meds=_meds)

            money_out = sum(
                ((q * c).quantize(Decimal("0.01")) for _, q, c in picks),
                Decimal("0"),
            )
            unit_price = (money_out / qty).quantize(Decimal("0.01")) if qty else Decimal("0")
            total_cost = money_out.quantize(Decimal("0.01"))
            primary_lot_id = picks[0][0].id if picks else None

            dispense = MedicineDispense(
                ingredient_id=ing.id,
                qty=qty,
                unit_price_at_dispense=unit_price,
                total_cost=total_cost,
                input_qty=input_qty,
                input_unit_code=unit_code,
                cow_id=cow_id,
                group_id=group_id,
                lot_id=primary_lot_id,
                dispensed_on=form.dispensed_on.data,
                notes=form.notes.data,
                created_by_id=current_user.id,
            )
            db.session.add(dispense)
            db.session.flush()

            # Deduct the ingredient's operational qty, decrement each lot,
            # and record one StockMovement per lot so the audit trail
            # names which batch each dose came from.
            ing.current_qty = ing.current_qty - qty
            if ing.current_qty == 0:
                # avg_cost carries no meaning when the shelf is empty
                ing.avg_cost = Decimal("0")
            for lot, take, cost in picks:
                lot.qty_remaining = (
                    Decimal(str(lot.qty_remaining)) - take
                ).quantize(Decimal("0.001"))
                db.session.add(
                    StockMovement(
                        ingredient_id=ing.id,
                        delta=-take,
                        reason=StockMovement.REASON_MEDICINE,
                        ref_id=dispense.id,
                        unit_price_at_move=cost,
                        input_qty=input_qty,
                        input_unit_code=unit_code,
                        lot_id=lot.id,
                        moved_on=form.dispensed_on.data,
                        notes=f"صرف دواء — {dispense.target_label} — دفعة #{lot.id}",
                        created_by_id=current_user.id,
                    )
                )

            # New in P6: the dispense hits the ledger.
            autoposting.on_medicine_dispense(dispense, created_by=current_user.id)

            log_action(
                "medicine_dispensed",
                "MedicineDispense",
                dispense.id,
                details=f"ingredient={ing.id} qty={qty} target={dispense.target_label} "
                        f"lots={[lot.id for lot,_,_ in picks]}",
            )
            db.session.commit()
            flash(f"تم صرف {qty} {ing.unit_label} من {ing.name}.", "success")
            return redirect(url_for("medicine.list_dispenses"))
        # form invalid — fall through to re-render with errors

    return render_template("medicine/form.html", form=form, meds=_meds)
