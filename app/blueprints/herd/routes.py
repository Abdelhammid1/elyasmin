from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.extensions import db
from app.forms.herd import (
    BirthForm,
    BreedingEventForm,
    CowForm,
    CowMoveForm,
    CowSearchForm,
    DeathForm,
    GroupForm,
    SaleForm,
)
from app.models.herd import (
    AnimalSale,
    Birth,
    BreedingEvent,
    BreedingStatusChange,
    Calf,
    CattleGroup,
    Cow,
    CowMovement,
    Death,
    CowValuation,
    EventStatusSuggestion,
)
from app.utils.audit import log_action
from app.utils.decorators import write_required

bp = Blueprint("herd", __name__, template_folder="../../templates/herd")


def _group_choices(include_all: bool = False):
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    choices = [(g.id, g.name) for g in groups]
    if include_all:
        choices = [(0, "كل المجموعات")] + choices
    return choices


def _groups_with_types():
    """List of (id, name, type) for JS-side filtering."""
    return [
        {"id": g.id, "name": g.name, "type": g.type}
        for g in CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    ]


def _nursing_group_id():
    grp = CattleGroup.query.filter_by(type=CattleGroup.TYPE_NURSING, is_archived=False).first()
    return grp.id if grp else None


def _fattening_group_id():
    grp = CattleGroup.query.filter_by(type=CattleGroup.TYPE_FATTENING, is_archived=False).first()
    return grp.id if grp else None


def _fattening_blocked(form) -> bool:
    """TICKET-1: a female must not land in التسمين by accident.

    The dropdown already hides it for females, but a filtered <select> is a
    convenience, not a rule — it can be bypassed by a stale page or a crafted
    post. The explicit "خنثة" checkbox is what makes it allowed.
    """
    if form.gender.data != Cow.GENDER_FEMALE:
        return False
    if form.allow_fattening_override.data:
        return False
    fattening_id = _fattening_group_id()
    return bool(fattening_id) and form.group_id.data == fattening_id


# ---------- Groups CRUD ----------
@bp.route("/groups")
@login_required
def groups():
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    return render_template("herd/groups.html", groups=groups)


@bp.route("/groups/new", methods=["GET", "POST"])
@login_required
@write_required
def create_group():
    form = GroupForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        # Case-insensitive uniqueness across all groups (including archived)
        from sqlalchemy import func as _func
        exists = CattleGroup.query.filter(_func.lower(CattleGroup.name) == name.lower()).first()
        if exists:
            flash("مجموعة بنفس الاسم موجودة قبل كده.", "error")
        else:
            g = CattleGroup(
                name=name,
                type=form.type.data,
                description=(form.description.data or "").strip() or None,
            )
            db.session.add(g)
            db.session.flush()
            log_action("group_created", "CattleGroup", g.id, details=f"type={g.type}")
            db.session.commit()
            flash(f"تم إضافة المجموعة {g.name}.", "success")
            return redirect(url_for("herd.groups"))
    return render_template("herd/group_form.html", form=form, mode="create")


@bp.route("/groups/<int:group_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
def edit_group(group_id: int):
    group = db.session.get(CattleGroup, group_id)
    if not group or group.is_archived:
        abort(404)
    form = GroupForm(obj=group)
    if form.validate_on_submit():
        new_name = form.name.data.strip()
        from sqlalchemy import func as _func
        if new_name != group.name:
            conflict = CattleGroup.query.filter(
                _func.lower(CattleGroup.name) == new_name.lower(),
                CattleGroup.id != group.id,
            ).first()
            if conflict:
                flash("مجموعة بنفس الاسم موجودة قبل كده.", "error")
                return render_template("herd/group_form.html", form=form, mode="edit", group=group)
        group.name = new_name
        group.type = form.type.data
        group.description = (form.description.data or "").strip() or None
        log_action("group_updated", "CattleGroup", group.id)
        db.session.commit()
        flash("تم تحديث بيانات المجموعة.", "success")
        return redirect(url_for("herd.groups"))
    return render_template("herd/group_form.html", form=form, mode="edit", group=group)


@bp.route("/groups/<int:group_id>/archive", methods=["POST"])
@login_required
@write_required
def archive_group(group_id: int):
    group = db.session.get(CattleGroup, group_id)
    if not group or group.is_archived:
        abort(404)
    if group.active_count > 0:
        flash(
            f"مينفعش تأرشف المجموعة '{group.name}' — فيها {group.active_count} رأس نشط. "
            "انقلهم لمجموعة تانية الأول.",
            "error",
        )
        return redirect(url_for("herd.groups"))

    # Warn if this is the last group of a critical type (nursing/fattening) needed by births
    if group.type in (CattleGroup.TYPE_NURSING, CattleGroup.TYPE_FATTENING):
        remaining = CattleGroup.query.filter(
            CattleGroup.type == group.type,
            CattleGroup.id != group.id,
            CattleGroup.is_archived.is_(False),
        ).count()
        if remaining == 0:
            flash(
                f"⚠️ دي آخر مجموعة من نوع {group.type_label} — تسجيل الولادات هيقف "
                "لحد ما تضيف مجموعة تانية بنفس النوع.",
                "warning",
            )

    group.is_archived = True
    log_action("group_archived", "CattleGroup", group.id)
    db.session.commit()
    flash(f"تم أرشفة المجموعة '{group.name}'.", "success")
    return redirect(url_for("herd.groups"))


# ---------- Cow list & search (US-1.3 AC5) ----------
@bp.route("/")
@login_required
def list_cows():
    """PHASE 22: adds KPI aggregates for the top strip (total / male /
    female / avg age)."""
    form = CowSearchForm(request.args, meta={"csrf": False})
    form.group_id.choices = _group_choices(include_all=True)

    query = Cow.query.filter_by(is_archived=False)

    q = (form.q.data or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Cow.ear_tag.ilike(like), Cow.name.ilike(like)))

    if form.group_id.data and form.group_id.data > 0:
        query = query.filter_by(group_id=form.group_id.data)

    status = form.status.data or "active"
    if status != "all":
        query = query.filter_by(status=status)

    cows = query.order_by(Cow.ear_tag).all()

    # KPIs computed on the currently filtered set — the counts move with
    # the filter so the operator always knows what he's looking at.
    total_count = len(cows)
    male_count = sum(1 for c in cows if c.gender == "male")
    female_count = sum(1 for c in cows if c.gender == "female")
    ages = [c.age_months for c in cows if c.age_months is not None]
    avg_age = round(sum(ages) / len(ages), 1) if ages else 0

    return render_template(
        "herd/list.html",
        cows=cows, form=form, q=q,
        total_count=total_count,
        male_count=male_count,
        female_count=female_count,
        avg_age=avg_age,
    )


# ---------- Cow detail ----------
@bp.route("/<int:cow_id>")
@login_required
def cow_detail(cow_id: int):
    cow = db.session.get(Cow, cow_id)
    if not cow:
        abort(404)

    movements = (
        CowMovement.query.filter_by(cow_id=cow.id)
        .order_by(CowMovement.moved_on.desc())
        .all()
    )
    death = Death.query.filter_by(cow_id=cow.id).first()
    sale = AnimalSale.query.filter_by(cow_id=cow.id).first()

    from app.models.feed import MedicineDispense
    dispenses = (
        MedicineDispense.query.filter_by(cow_id=cow.id, is_archived=False)
        .order_by(MedicineDispense.dispensed_on.desc())
        .all()
    )

    return render_template(
        "herd/detail.html",
        cow=cow,
        movements=movements,
        death=death,
        sale=sale,
        dispenses=dispenses,
    )


# ---------- US-1.3 Add cow ----------
@bp.route("/new", methods=["GET", "POST"])
@login_required
@write_required
def create_cow():
    form = CowForm()
    form.group_id.choices = _group_choices()

    if form.validate_on_submit():
        if _fattening_blocked(form):
            form.group_id.errors.append(
                "الأنثى مابتدخلش التسمين. لو دي حالة خنثة، علّم على الخانة تحت."
            )
            flash("الأنثى مابتدخلش مجموعة التسمين إلا لو علّمت إنها حالة خاصة (خنثة).", "error")
            return render_template(
                "herd/form.html", form=form, mode="create",
                groups_json=_groups_with_types(),
            )

        ear_tag = form.ear_tag.data.strip()
        existing = Cow.query.filter_by(ear_tag=ear_tag).first()
        if existing:
            flash(f"رقم الأذن {ear_tag} مستخدم قبل كده.", "error")
        else:
            cow = Cow(
                ear_tag=ear_tag,
                name=form.name.data.strip() if form.name.data else None,
                date_of_birth=form.date_of_birth.data,
                gender=form.gender.data,
                group_id=form.group_id.data,
                notes=form.notes.data,
                created_by_id=current_user.id,
            )
            db.session.add(cow)
            db.session.flush()

            db.session.add(
                CowMovement(
                    cow_id=cow.id,
                    from_group_id=None,
                    to_group_id=cow.group_id,
                    moved_on=date.today(),
                    reason="إضافة جديدة",
                    created_by_id=current_user.id,
                )
            )
            log_action("cow_created", "Cow", cow.id, details=f"tag={cow.ear_tag}")
            db.session.commit()
            flash(f"تم إضافة البقرة رقم {cow.ear_tag} بنجاح.", "success")
            return redirect(url_for("herd.cow_detail", cow_id=cow.id))

    return render_template(
        "herd/form.html", form=form, mode="create", groups_json=_groups_with_types()
    )


# ---------- Edit cow (limited fields) ----------
@bp.route("/<int:cow_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
def edit_cow(cow_id: int):
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.is_archived:
        abort(404)

    form = CowForm(obj=cow)
    form.group_id.choices = _group_choices()

    if form.validate_on_submit():
        if _fattening_blocked(form):
            form.group_id.errors.append(
                "الأنثى مابتدخلش التسمين. لو دي حالة خنثة، علّم على الخانة تحت."
            )
            flash("الأنثى مابتدخلش مجموعة التسمين إلا لو علّمت إنها حالة خاصة (خنثة).", "error")
            return render_template(
                "herd/form.html", form=form, mode="edit", cow=cow,
                groups_json=_groups_with_types(),
            )

        # Ear tag change requires uniqueness check
        new_tag = form.ear_tag.data.strip()
        if new_tag != cow.ear_tag:
            existing = Cow.query.filter_by(ear_tag=new_tag).first()
            if existing:
                flash(f"رقم الأذن {new_tag} مستخدم قبل كده.", "error")
                return render_template(
                    "herd/form.html", form=form, mode="edit", cow=cow,
                    groups_json=_groups_with_types(),
                )
            cow.ear_tag = new_tag

        cow.name = form.name.data.strip() if form.name.data else None
        cow.date_of_birth = form.date_of_birth.data
        cow.gender = form.gender.data
        cow.notes = form.notes.data

        # If the group was changed via this form, log it as a movement too
        if form.group_id.data != cow.group_id:
            db.session.add(
                CowMovement(
                    cow_id=cow.id,
                    from_group_id=cow.group_id,
                    to_group_id=form.group_id.data,
                    moved_on=date.today(),
                    reason="تعديل بيانات",
                    created_by_id=current_user.id,
                )
            )
            cow.group_id = form.group_id.data

        log_action("cow_updated", "Cow", cow.id)
        db.session.commit()
        flash("تم تحديث بيانات البقرة.", "success")
        return redirect(url_for("herd.cow_detail", cow_id=cow.id))

    return render_template(
        "herd/form.html", form=form, mode="edit", cow=cow, groups_json=_groups_with_types()
    )


# ---------- US-1.4 Move cow between groups ----------
@bp.route("/<int:cow_id>/move", methods=["GET", "POST"])
@login_required
@write_required
def move_cow(cow_id: int):
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.is_archived or cow.status != Cow.STATUS_ACTIVE:
        abort(404)

    form = CowMoveForm()
    form.to_group_id.choices = _group_choices()

    if form.validate_on_submit():
        if form.to_group_id.data == cow.group_id:
            flash("مينفعش تنقل البقرة لنفس مجموعتها الحالية.", "error")
        else:
            movement = CowMovement(
                cow_id=cow.id,
                from_group_id=cow.group_id,
                to_group_id=form.to_group_id.data,
                moved_on=form.moved_on.data,
                reason=form.reason.data,
                created_by_id=current_user.id,
            )
            db.session.add(movement)
            cow.group_id = form.to_group_id.data
            log_action("cow_moved", "Cow", cow.id, details=f"to_group={form.to_group_id.data}")
            db.session.commit()
            flash("تم نقل البقرة للمجموعة الجديدة.", "success")
            return redirect(url_for("herd.cow_detail", cow_id=cow.id))

    return render_template("herd/move.html", form=form, cow=cow)


# ---------- US-1.5 Births ----------
@bp.route("/births")
@login_required
def births():
    all_births = Birth.query.order_by(Birth.birth_date.desc()).limit(100).all()
    return render_template("herd/births.html", births=all_births)


@bp.route("/births/new", methods=["GET", "POST"])
@login_required
@write_required
def create_birth():
    form = BirthForm()
    # Mothers = active female cows
    mothers = (
        Cow.query.filter_by(
            gender=Cow.GENDER_FEMALE, status=Cow.STATUS_ACTIVE, is_archived=False
        )
        .order_by(Cow.ear_tag)
        .all()
    )
    form.mother_id.choices = [(c.id, f"{c.ear_tag} — {c.name or 'بدون اسم'}") for c in mothers]

    if form.validate_on_submit():
        mother = db.session.get(Cow, form.mother_id.data)
        if not mother or mother.gender != Cow.GENDER_FEMALE:
            flash("الأم المحددة غير صالحة.", "error")
            return render_template("herd/birth_form.html", form=form)

        # US-1.5 AC4: warn if mother gave birth <6 months ago (needs confirm)
        last_birth = (
            Birth.query.filter_by(mother_id=mother.id)
            .order_by(Birth.birth_date.desc())
            .first()
        )
        if last_birth and (form.birth_date.data - last_birth.birth_date).days < 180:
            if request.form.get("confirm_6mo") != "1":
                flash(
                    f"⚠️ الأم {mother.ear_tag} ولدت آخر مرة في "
                    f"{last_birth.birth_date} — أقل من 6 شهور. "
                    "لو أكيد اضغط 'تأكيد' وارسل تاني.",
                    "warning",
                )
                return render_template(
                    "herd/birth_form.html", form=form, need_confirm_6mo=True,
                )

        # Parse calf details from POST (dynamic fields)
        count = form.calves_count.data
        calf_records = []
        for i in range(count):
            gender = request.form.get(f"calf_gender_{i}", Cow.GENDER_FEMALE)
            is_alive_raw = request.form.get(f"calf_alive_{i}", "1")
            ear_tag_raw = (request.form.get(f"calf_tag_{i}") or "").strip()
            calf_records.append(
                {
                    "gender": gender,
                    "is_alive": is_alive_raw == "1",
                    "ear_tag": ear_tag_raw or None,
                }
            )

        birth = Birth(
            mother_id=mother.id,
            birth_date=form.birth_date.data,
            calves_count=count,
            delivery_type=form.delivery_type.data,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(birth)
        db.session.flush()

        # HERD-2 Part 1 (PHASE 35): mirror the calving into the
        # breeding-events log so the reproductive-cycle timeline and
        # the season counter (Part 2) stay in sync with Birth. The
        # dam's breeding_status is NOT auto-changed here — the
        # suggest-confirm flow is skipped for the calving path
        # because create_birth already runs its own multi-step UX
        # (group choice, calf attributes). The user updates the
        # mother's breeding_status afterward via the retro-edit
        # screen if needed.
        db.session.add(BreedingEvent(
            cow_id=mother.id,
            event_type=BreedingEvent.EVENT_CALVING,
            event_date=form.birth_date.data,
            birth_id=birth.id,
            created_by_id=current_user.id,
        ))

        # HERD-1 (PHASE 31): no auto-move to nursing. The dam stays in
        # her current group; every live calf is created in the dam's
        # current group (mother-and-calf together). The user picks the
        # final destination on the confirmation page — skipping it
        # leaves everyone in their initial spot, but the birth itself
        # is always recorded.
        for rec in calf_records:
            calf_cow_id = None
            if rec["is_alive"]:
                target_group = mother.group_id

                tag = rec["ear_tag"] or f"TEMP-{birth.id}-{len(calf_records)}"
                # Ensure uniqueness for auto tag
                base_tag = tag
                suffix = 1
                while Cow.query.filter_by(ear_tag=tag).first():
                    tag = f"{base_tag}-{suffix}"
                    suffix += 1

                calf_cow = Cow(
                    ear_tag=tag,
                    date_of_birth=form.birth_date.data,
                    gender=rec["gender"],
                    group_id=target_group,
                    mother_id=mother.id,
                    notes=f"مولود من الأم {mother.ear_tag}",
                    created_by_id=current_user.id,
                )
                db.session.add(calf_cow)
                db.session.flush()
                calf_cow_id = calf_cow.id

                db.session.add(
                    CowMovement(
                        cow_id=calf_cow.id,
                        from_group_id=None,
                        to_group_id=target_group,
                        moved_on=form.birth_date.data,
                        reason="مولود جديد",
                        created_by_id=current_user.id,
                    )
                )

            db.session.add(
                Calf(
                    birth_id=birth.id,
                    cow_id=calf_cow_id,
                    gender=rec["gender"],
                    is_alive=rec["is_alive"],
                )
            )

        log_action("birth_registered", "Birth", birth.id, details=f"mother={mother.id}")
        db.session.commit()
        flash("تم تسجيل الولادة. اختار مجموعة الأم والمواليد بالأسفل.", "success")
        return redirect(url_for("herd.assign_birth_groups", birth_id=birth.id))

    return render_template("herd/birth_form.html", form=form)


# ---------- HERD-1 (PHASE 31): manual group choice after birth ----------
@bp.route("/births/<int:birth_id>/assign-groups", methods=["GET", "POST"])
@login_required
@write_required
def assign_birth_groups(birth_id: int):
    """Confirmation page shown after `create_birth`. User picks the
    final group for the dam and each live calf — nursing is the
    suggested default (falls back to the current group if no nursing
    group exists). Skipping the page (any other URL) leaves the birth
    recorded but no moves happen.

    HERD-1: replaces the silent auto-move to nursing. No consent, no
    move.
    """
    birth = db.session.get(Birth, birth_id)
    if birth is None:
        abort(404)

    dam = birth.mother
    live_calves = [c for c in birth.calves if c.is_alive and c.cow_id]
    live_cows = [db.session.get(Cow, c.cow_id) for c in live_calves]
    live_cows = [c for c in live_cows if c is not None]

    all_groups = CattleGroup.query.filter_by(
        is_archived=False).order_by(CattleGroup.name).all()
    group_choices = [(g.id, g.name) for g in all_groups]

    nursing_id = _nursing_group_id()

    def _default_group_id(cow):
        """Nursing if it exists; else the cow's current group."""
        return nursing_id if nursing_id else cow.group_id

    if request.method == "POST":
        moved = 0
        # Dam
        new_dam_group = request.form.get("dam_group_id", type=int)
        if new_dam_group and new_dam_group != dam.group_id:
            db.session.add(CowMovement(
                cow_id=dam.id,
                from_group_id=dam.group_id,
                to_group_id=new_dam_group,
                moved_on=birth.birth_date,
                reason="اختيار يدوي بعد الولادة (الأم)",
                created_by_id=current_user.id,
            ))
            dam.group_id = new_dam_group
            moved += 1

        # Calves
        for cow in live_cows:
            new_group = request.form.get(f"calf_group_{cow.id}", type=int)
            if new_group and new_group != cow.group_id:
                db.session.add(CowMovement(
                    cow_id=cow.id,
                    from_group_id=cow.group_id,
                    to_group_id=new_group,
                    moved_on=birth.birth_date,
                    reason="اختيار يدوي بعد الولادة (المولود)",
                    created_by_id=current_user.id,
                ))
                cow.group_id = new_group
                moved += 1

        log_action("birth_groups_assigned", "Birth", birth.id,
                   details=f"moves={moved}")
        db.session.commit()
        flash(f"تم تنفيذ {moved} نقل بناءً على اختيارك.", "success")
        return redirect(url_for("herd.cow_detail", cow_id=dam.id))

    return render_template(
        "herd/assign_birth_groups.html",
        birth=birth,
        dam=dam,
        live_cows=live_cows,
        group_choices=group_choices,
        default_group_id=_default_group_id,
        nursing_id=nursing_id,
    )


# ---------- US-1.6 Death ----------
@bp.route("/<int:cow_id>/death", methods=["GET", "POST"])
@login_required
@write_required
def register_death(cow_id: int):
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.status != Cow.STATUS_ACTIVE:
        abort(404)

    form = DeathForm()
    if form.validate_on_submit():
        death = Death(
            cow_id=cow.id,
            death_date=form.death_date.data,
            reason=form.reason.data,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(death)
        cow.status = Cow.STATUS_DEAD
        log_action("death_registered", "Cow", cow.id, details=f"reason={form.reason.data}")
        db.session.commit()
        flash("تم تسجيل النفوق. البقرة انتقلت للسجل التاريخي.", "info")
        return redirect(url_for("herd.cow_detail", cow_id=cow.id))

    return render_template("herd/death_form.html", form=form, cow=cow)


# ---------- US-1.7 Sale ----------
@bp.route("/<int:cow_id>/sell", methods=["GET", "POST"])
@login_required
@write_required
def sell_cow(cow_id: int):
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.status != Cow.STATUS_ACTIVE:
        abort(404)

    form = SaleForm()
    if form.validate_on_submit():
        sale = AnimalSale(
            cow_id=cow.id,
            sale_date=form.sale_date.data,
            buyer_name=form.buyer_name.data.strip(),
            price=form.price.data,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(sale)
        cow.status = Cow.STATUS_SOLD
        log_action("cow_sold", "Cow", cow.id, details=f"price={form.price.data}")
        db.session.commit()
        flash(f"تم بيع البقرة {cow.ear_tag} بمبلغ {form.price.data} — تم تسجيلها كإيراد.", "success")
        return redirect(url_for("herd.cow_detail", cow_id=cow.id))

    return render_template("herd/sale_form.html", form=form, cow=cow)


# ---------- Sales list ----------
@bp.route("/sales")
@login_required
def sales_list():
    sales = AnimalSale.query.order_by(AnimalSale.sale_date.desc()).limit(200).all()
    return render_template("herd/sales.html", sales=sales)


# ==================== HERD-2 Part 1 (PHASE 35): breeding routes ====================


def _suggestion_choices(event_type: str, event_result: str | None):
    """Look up the config-driven suggestion list for an event kind.
    Returns (choices_for_radio, default_status_or_None) where
    choices_for_radio is a list of (value, label) tuples in sort order.

    Sourced from `event_status_suggestions`; the ticket said the
    mapping must be config, not hardcoded — this reads live from the
    table every request so admin edits take effect immediately."""
    q = EventStatusSuggestion.query.filter_by(event_type=event_type)
    # event_result discriminator applies only to pregnancy_check;
    # every other event stores NULL for event_result. Query for
    # NULL when the caller didn't pass a result, so a hand-crafted
    # POST with a spurious result on drying/insemination doesn't
    # miss the seed rows.
    if event_result:
        q = q.filter_by(event_result=event_result)
    else:
        q = q.filter(EventStatusSuggestion.event_result.is_(None))
    rows = q.order_by(
        EventStatusSuggestion.sort_order, EventStatusSuggestion.id
    ).all()

    choices = []
    default = None
    for r in rows:
        label = Cow.BREEDING_STATUS_LABELS.get(
            r.suggested_status, r.suggested_status
        )
        choices.append((r.suggested_status, label))
        if r.is_default and default is None:
            default = r.suggested_status
    return choices, default


@bp.route("/<int:cow_id>/breeding/new", methods=["GET", "POST"])
@login_required
@write_required
def create_breeding_event(cow_id: int):
    """HERD-2 Part 1: record a reproductive-cycle event, then hand
    off to the suggest-confirm page. GET renders the small form;
    POST writes the BreedingEvent and redirects to the confirm
    screen. Never changes `Cow.breeding_status` on this leg."""
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.is_archived:
        abort(404)

    form = BreedingEventForm()
    if form.validate_on_submit():
        result = form.result.data or None
        # Guard: `result` is meaningful only on pregnancy_check. Silently
        # drop it if the user picked something on a different event
        # (rare, but keeps the DB clean).
        if form.event_type.data != BreedingEvent.EVENT_PREGNANCY_CHECK:
            result = None

        event = BreedingEvent(
            cow_id=cow.id,
            event_type=form.event_type.data,
            event_date=form.event_date.data,
            result=result,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(event)
        db.session.flush()
        log_action(
            "breeding_event_recorded", "BreedingEvent", event.id,
            details=f"cow={cow.id} type={event.event_type}",
        )
        db.session.commit()

        flash("تم تسجيل الإجراء. اختار الحالة المقترحة بالأسفل.", "info")
        return redirect(url_for(
            "herd.confirm_breeding_status",
            cow_id=cow.id, event_id=event.id,
        ))

    return render_template(
        "herd/breeding_event_form.html", cow=cow, form=form,
    )


@bp.route(
    "/<int:cow_id>/breeding/<int:event_id>/confirm-status",
    methods=["GET", "POST"],
)
@login_required
@write_required
def confirm_breeding_status(cow_id: int, event_id: int):
    """HERD-2 Part 1: the "suggest & confirm" step. Renders the
    suggestions with a default preselected; user picks one (or
    "no change") and clicks تأكيد.

    On confirm: write a BreedingStatusChange row AND update
    cow.breeding_status in the same transaction. Nothing writes to
    breeding_status without landing here."""
    cow = db.session.get(Cow, cow_id)
    event = db.session.get(BreedingEvent, event_id)
    if not cow or cow.is_archived or not event or event.cow_id != cow.id:
        abort(404)

    choices, default = _suggestion_choices(
        event.event_type, event.result,
    )

    if request.method == "POST":
        picked = (request.form.get("new_status") or "").strip()
        # Special sentinel: user chose "لا تغيير" (no status change)
        if picked == "" or picked == "__no_change__":
            flash("تم حفظ الإجراء بدون تغيير حالة إنجابية.", "info")
            return redirect(url_for("herd.cow_detail", cow_id=cow.id))

        valid_statuses = set(Cow.BREEDING_STATUS_LABELS.keys())
        if picked not in valid_statuses:
            flash("الحالة المختارة غير صالحة.", "error")
            return render_template(
                "herd/breeding_confirm_status.html",
                cow=cow, event=event, choices=choices, default=default,
            )

        prior = cow.breeding_status
        db.session.add(BreedingStatusChange(
            cow_id=cow.id,
            from_status=prior,
            to_status=picked,
            changed_by_id=current_user.id,
            event_id=event.id,
        ))
        cow.breeding_status = picked
        log_action(
            "breeding_status_confirmed",
            "BreedingStatusChange", 0,
            details=f"cow={cow.id} {prior}->{picked} via event={event.id}",
        )
        db.session.commit()
        flash(
            f"تم تحديث الحالة الإنجابية إلى "
            f"{Cow.BREEDING_STATUS_LABELS[picked]}.",
            "success",
        )
        return redirect(url_for("herd.cow_detail", cow_id=cow.id))

    return render_template(
        "herd/breeding_confirm_status.html",
        cow=cow, event=event, choices=choices, default=default,
    )


@bp.route("/<int:cow_id>/breeding")
@login_required
def breeding_timeline(cow_id: int):
    """HERD-2 Part 1: two-column log — reproductive events on one
    side, status changes on the other. Newest first on both."""
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.is_archived:
        abort(404)
    events = (
        BreedingEvent.query.filter_by(cow_id=cow.id)
        .order_by(BreedingEvent.event_date.desc(),
                  BreedingEvent.id.desc())
        .all()
    )
    changes = (
        BreedingStatusChange.query.filter_by(cow_id=cow.id)
        .order_by(BreedingStatusChange.changed_at.desc())
        .all()
    )
    return render_template(
        "herd/breeding_timeline.html",
        cow=cow, events=events, changes=changes,
    )


@bp.route("/<int:cow_id>/breeding/retro-status", methods=["GET", "POST"])
@login_required
@write_required
def retro_breeding_status(cow_id: int):
    """HERD-2 Part 1: retro-edit the reproductive status without
    touching any BreedingEvent row. Writes one BreedingStatusChange
    with event_id=NULL and a reason string, then flips
    cow.breeding_status."""
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.is_archived:
        abort(404)

    if request.method == "POST":
        picked = (request.form.get("new_status") or "").strip()
        reason = (request.form.get("reason") or "").strip() or None

        valid_statuses = set(Cow.BREEDING_STATUS_LABELS.keys())
        # Allow clearing to NULL via a sentinel
        if picked in ("", "__clear__"):
            new_val = None
        elif picked not in valid_statuses:
            flash("الحالة المختارة غير صالحة.", "error")
            return redirect(url_for(
                "herd.retro_breeding_status", cow_id=cow.id,
            ))
        else:
            new_val = picked

        prior = cow.breeding_status
        if new_val == prior:
            flash("الحالة المختارة نفس الحالة الحالية — مفيش تغيير.", "info")
            return redirect(url_for("herd.cow_detail", cow_id=cow.id))

        db.session.add(BreedingStatusChange(
            cow_id=cow.id,
            from_status=prior,
            to_status=new_val or "",   # NOT NULL column; empty = cleared
            changed_by_id=current_user.id,
            event_id=None,
            reason=reason or "تعديل رجعي بدون سبب مذكور",
        ))
        cow.breeding_status = new_val
        log_action(
            "breeding_status_retro_edit",
            "BreedingStatusChange", 0,
            details=f"cow={cow.id} {prior}->{new_val}",
        )
        db.session.commit()
        flash("تم تعديل الحالة الإنجابية رجعياً.", "success")
        return redirect(url_for("herd.cow_detail", cow_id=cow.id))

    all_status_choices = [
        ("__clear__", "— بدون حالة —"),
    ] + list(Cow.BREEDING_STATUS_LABELS.items())
    return render_template(
        "herd/breeding_retro_edit.html",
        cow=cow, all_status_choices=all_status_choices,
    )


# ==================== HERD-2 Part 3 (PHASE 35): herd valuation ====================


def _post_revaluation_je(valuation, cow, created_by_id: int):
    """HERD-2 Part 3: post the balancing JE for a revaluation.
    Gain → Dr 1400 حيوانات المزرعة / Cr 4095 أرباح إعادة تقييم.
    Loss → Dr 4095 / Cr 1400. Zero delta → nothing posted (caller
    should guard).

    Every JE's `source_type='CowValuation'` + `source_id=valuation.id`
    so journal_detail's "قيد المصدر" link walks back to this row."""
    from decimal import Decimal
    from app.models.accounting import LedgerAccount
    from app.services.ledger import LedgerError, post_journal

    delta = Decimal(str(valuation.value)) - Decimal(str(valuation.prior_value))
    if delta == 0:
        return None

    livestock = LedgerAccount.query.filter_by(
        code="1400", is_active=True,
    ).first()
    reval = LedgerAccount.query.filter_by(
        code="4095", is_active=True,
    ).first()
    if livestock is None or reval is None:
        raise LedgerError(
            "حسابات إعادة التقييم (1400 / 4095) مش موجودة في دليل الحسابات."
        )

    if delta > 0:
        # Gain: DR livestock (asset up) / CR revaluation P&L
        lines = [
            {"account_id": livestock.id, "debit": delta, "credit": 0,
             "memo": f"إعادة تقييم {cow.ear_tag} — ربح"},
            {"account_id": reval.id, "debit": 0, "credit": delta,
             "memo": f"إعادة تقييم {cow.ear_tag} — ربح"},
        ]
    else:
        loss = -delta
        # Loss: DR revaluation P&L / CR livestock (asset down)
        lines = [
            {"account_id": reval.id, "debit": loss, "credit": 0,
             "memo": f"إعادة تقييم {cow.ear_tag} — خسارة"},
            {"account_id": livestock.id, "debit": 0, "credit": loss,
             "memo": f"إعادة تقييم {cow.ear_tag} — خسارة"},
        ]

    return post_journal(
        description=(
            f"إعادة تقييم البقرة {cow.ear_tag}: "
            f"{valuation.prior_value} → {valuation.value}"
        ),
        lines=lines,
        entry_date=valuation.valuation_date,
        source_type="CowValuation",
        source_id=valuation.id,
        created_by=created_by_id,
    )


@bp.route("/valuation")
@login_required
def valuation_bulk():
    """HERD-2 Part 3: bulk revaluation screen. Every active cow (both
    sexes — livestock is livestock) with its current_value +
    input for the new value."""
    from decimal import Decimal
    cows = (
        Cow.query.filter_by(status=Cow.STATUS_ACTIVE, is_archived=False)
        .order_by(Cow.ear_tag).all()
    )
    total_current = sum(
        (Decimal(str(c.current_value or 0)) for c in cows), Decimal("0"),
    )
    return render_template(
        "herd/valuation_bulk.html",
        cows=cows, total_current=total_current,
    )


@bp.route("/valuation/save", methods=["POST"])
@login_required
@write_required
def valuation_save():
    """HERD-2 Part 3: process the bulk revaluation form. One row =
    one cow input `value_<cow_id>` + optional `notes_<cow_id>`. Skip
    rows where the new value equals the current value (no-op)."""
    from decimal import Decimal, InvalidOperation
    from app.services.ledger import LedgerError

    val_date = request.form.get("valuation_date") or None
    if val_date:
        val_date = date.fromisoformat(val_date)
    else:
        val_date = date.today()

    cows = (
        Cow.query.filter_by(status=Cow.STATUS_ACTIVE, is_archived=False)
        .all()
    )
    n_saved = 0
    total_delta = Decimal("0")
    for cow in cows:
        raw = (request.form.get(f"value_{cow.id}") or "").strip()
        if not raw:
            continue
        try:
            new_val = Decimal(raw).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            flash(
                f"القيمة المدخلة للبقرة {cow.ear_tag} غير صالحة — اتخطيها.",
                "warning",
            )
            continue
        prior = Decimal(str(cow.current_value or 0)).quantize(Decimal("0.01"))
        if new_val == prior:
            continue

        notes = (request.form.get(f"notes_{cow.id}") or "").strip() or None
        valuation = CowValuation(
            cow_id=cow.id,
            valuation_date=val_date,
            value=new_val,
            prior_value=prior,
            notes=notes,
            created_by_id=current_user.id,
        )
        db.session.add(valuation)
        db.session.flush()

        try:
            _post_revaluation_je(valuation, cow, current_user.id)
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
            return redirect(url_for("herd.valuation_bulk"))

        cow.current_value = new_val
        log_action(
            "cow_revaluated", "CowValuation", valuation.id,
            details=f"cow={cow.id} {prior}->{new_val}",
        )
        n_saved += 1
        total_delta += (new_val - prior)

    db.session.commit()
    if n_saved:
        flash(
            f"تم حفظ {n_saved} إعادة تقييم — إجمالي التغيير: "
            f"{total_delta}.",
            "success",
        )
    else:
        flash("مفيش تغيرات مدخلة.", "info")
    return redirect(url_for("herd.valuation_bulk"))


@bp.route("/<int:cow_id>/valuations")
@login_required
def valuation_history(cow_id: int):
    """HERD-2 Part 3: per-cow valuation history."""
    cow = db.session.get(Cow, cow_id)
    if not cow:
        abort(404)
    rows = (
        CowValuation.query.filter_by(cow_id=cow.id)
        .order_by(CowValuation.valuation_date.desc(),
                  CowValuation.id.desc())
        .all()
    )
    return render_template(
        "herd/valuation_history.html", cow=cow, valuations=rows,
    )


@bp.route("/valuation/report")
@login_required
def valuation_report():
    """HERD-2 Part 3: total herd value at a given date. For today's
    date, sums Cow.current_value directly. For a past date, walks
    cow_valuations to find each cow's latest value on-or-before T
    (or 0 if no valuation existed by then)."""
    from decimal import Decimal
    raw = request.args.get("date")
    try:
        target = date.fromisoformat(raw) if raw else date.today()
    except (ValueError, TypeError):
        target = date.today()

    cows = (
        Cow.query.filter_by(status=Cow.STATUS_ACTIVE, is_archived=False)
        .order_by(Cow.ear_tag).all()
    )
    is_today = target >= date.today()
    rows = []
    total = Decimal("0")
    for cow in cows:
        if is_today:
            v = Decimal(str(cow.current_value or 0))
        else:
            last = (
                CowValuation.query.filter_by(cow_id=cow.id)
                .filter(CowValuation.valuation_date <= target)
                .order_by(CowValuation.valuation_date.desc(),
                          CowValuation.id.desc()).first()
            )
            v = Decimal(str(last.value)) if last else Decimal("0")
        rows.append({"cow": cow, "value": v})
        total += v

    return render_template(
        "herd/valuation_report.html",
        rows=rows, total=total, target=target, is_today=is_today,
    )
