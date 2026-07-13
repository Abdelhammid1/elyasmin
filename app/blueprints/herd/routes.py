from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.extensions import db
from app.forms.herd import (
    BirthForm,
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
    Calf,
    CattleGroup,
    Cow,
    CowMovement,
    Death,
)
from app.utils.audit import log_action

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


# ---------- Groups CRUD ----------
@bp.route("/groups")
@login_required
def groups():
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    return render_template("herd/groups.html", groups=groups)


@bp.route("/groups/new", methods=["GET", "POST"])
@login_required
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
    return render_template("herd/list.html", cows=cows, form=form, q=q)


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
def create_cow():
    form = CowForm()
    form.group_id.choices = _group_choices()

    if form.validate_on_submit():
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
def edit_cow(cow_id: int):
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.is_archived:
        abort(404)

    form = CowForm(obj=cow)
    form.group_id.choices = _group_choices()

    if form.validate_on_submit():
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
        from datetime import timedelta
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

        # Auto-move mother to nursing group (US-1.5 AC3)
        nursing_id = _nursing_group_id()
        if nursing_id and mother.group_id != nursing_id:
            db.session.add(
                CowMovement(
                    cow_id=mother.id,
                    from_group_id=mother.group_id,
                    to_group_id=nursing_id,
                    moved_on=form.birth_date.data,
                    reason="ولادة — نقل تلقائي إلى الرضاعة",
                    created_by_id=current_user.id,
                )
            )
            mother.group_id = nursing_id

        # Register calves (US-1.5 BR: males → fattening, females → nursing)
        fattening_id = _fattening_group_id()
        for rec in calf_records:
            calf_cow_id = None
            if rec["is_alive"]:
                target_group = fattening_id if rec["gender"] == Cow.GENDER_MALE else nursing_id
                if not target_group:
                    flash("مجموعات التسمين/الرضاعة غير مضبوطة. من فضلك راجع الإعدادات.", "error")
                    db.session.rollback()
                    return render_template("herd/birth_form.html", form=form)

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
        flash("تم تسجيل الولادة بنجاح.", "success")
        return redirect(url_for("herd.cow_detail", cow_id=mother.id))

    return render_template("herd/birth_form.html", form=form)


# ---------- US-1.6 Death ----------
@bp.route("/<int:cow_id>/death", methods=["GET", "POST"])
@login_required
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
