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
    DeathForm,
    GroupForm,
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
    BreedingStatus,
    BreedingEventType,
    active_event_types,
    breeding_status_choices,
    breeding_status_label,
)
from app.utils.audit import log_action
from app.utils.decorators import admin_required, write_required

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
    """HERD-6: the parlor-report columns, combined filters (group /
    status / season, each multi-pick) and sortable headers. KPIs are
    computed on the filtered set."""
    from app.services.herd_list import (
        LIFE_CHOICES, build_herd_rows, read_filters,
    )
    filters = read_filters(request.args)
    sort = request.args.get("sort") or "tag"
    direction = "desc" if request.args.get("dir") == "desc" else "asc"
    rows = build_herd_rows(filters, sort, direction)

    cows = [r["cow"] for r in rows]
    ages = [c.age_months for c in cows if c.age_months is not None]

    # Season options: every season in the unfiltered-by-season set
    # plus whatever is already picked.
    season_base = build_herd_rows({**filters, "season": []}) if filters["season"] else rows
    season_options = [
        (s, f"الموسم {s}")
        for s in sorted({r["season"] for r in season_base} | set(filters["season"]))
    ]

    return render_template(
        "herd/list.html",
        rows=rows, filters=filters, sort=sort, direction=direction,
        life_choices=LIFE_CHOICES,
        group_options=_group_choices(),
        status_options=[("none", "بدون حالة")] + breeding_status_choices(),
        season_options=season_options,
        print_mode=bool(request.args.get("print")),
        today_iso=date.today().isoformat(),
        total_count=len(cows),
        male_count=sum(1 for c in cows if c.gender == "male"),
        female_count=sum(1 for c in cows if c.gender == "female"),
        avg_age=round(sum(ages) / len(ages), 1) if ages else 0,
    )


@bp.route("/export.xlsx")
@login_required
def export_cows_excel():
    """HERD-6: the filtered herd list as Excel (same args as the page)."""
    from app.services.herd_list import (
        EXPORT_HEADERS, build_herd_rows, export_row, read_filters,
    )
    from app.utils.reports import excel_response
    rows = build_herd_rows(
        read_filters(request.args),
        request.args.get("sort") or "tag",
        "desc" if request.args.get("dir") == "desc" else "asc",
    )
    return excel_response(
        "القطيع", EXPORT_HEADERS, [export_row(r) for r in rows],
        f"herd_{date.today().isoformat()}.xlsx",
    )


@bp.route("/export.pdf")
@login_required
def export_cows_pdf():
    """HERD-6: the filtered herd list as PDF — renders the list page in
    print mode through the shared Chromium pipeline."""
    from urllib.parse import urlencode
    from app.utils.reports import pdf_from_current_page
    pairs = [(k, v) for k, v in request.args.items(multi=True) if k != "print"]
    pairs.append(("print", "1"))
    target = url_for("herd.list_cows", _external=True) + "?" + urlencode(pairs)
    return pdf_from_current_page(target, f"herd_{date.today().isoformat()}.pdf")


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


def _status_choices(current: str | None = None) -> list[tuple[str, str]]:
    """HERD-4: picker for the cow form. Keeps the cow's current status
    selectable even if it was disabled since, so editing other fields
    doesn't fail validation."""
    choices = [("", "— بدون حالة —")] + breeding_status_choices()
    if current and current not in {code for code, _ in choices}:
        choices.append((current, breeding_status_label(current)))
    return choices


# ---------- US-1.3 Add cow ----------
@bp.route("/new", methods=["GET", "POST"])
@login_required
@write_required
def create_cow():
    form = CowForm()
    form.group_id.choices = _group_choices()
    form.breeding_status.choices = _status_choices()

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
            status = form.breeding_status.data or None
            cow = Cow(
                ear_tag=ear_tag,
                breeding_status=status,
                date_of_birth=form.date_of_birth.data,
                gender=form.gender.data,
                group_id=form.group_id.data,
                notes=form.notes.data,
                # SEASON-OFFSET (PHASE 37): defaults to 0 for cows
                # born inside the system.
                season_count_offset=form.season_count_offset.data or 0,
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
            if status:
                # Start the status history on day one.
                db.session.add(BreedingStatusChange(
                    cow_id=cow.id, from_status=None, to_status=status,
                    changed_by_id=current_user.id, event_id=None,
                    reason="الحالة عند إضافة البقرة",
                ))
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
    form.breeding_status.choices = _status_choices(cow.breeding_status)

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

        new_status = form.breeding_status.data or None
        if new_status != cow.breeding_status:
            db.session.add(BreedingStatusChange(
                cow_id=cow.id, from_status=cow.breeding_status,
                to_status=new_status or "",  # NOT NULL; empty = cleared
                changed_by_id=current_user.id, event_id=None,
                reason="تعديل من بيانات البقرة",
            ))
            cow.breeding_status = new_status
        cow.date_of_birth = form.date_of_birth.data
        cow.gender = form.gender.data
        cow.notes = form.notes.data
        # SEASON-OFFSET (PHASE 37): edit lets the user backfill
        # historical seasons for a cow already in the system.
        cow.season_count_offset = form.season_count_offset.data or 0

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
    form.mother_id.choices = [(c.id, c.ear_tag) for c in mothers]
    # HERD-5: the event form sends calving here with the cow picked.
    if request.method == "GET" and request.args.get("mother_id", type=int):
        form.mother_id.data = request.args.get("mother_id", type=int)

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
        # the season counter (Part 2) stay in sync with Birth.
        calving_event = BreedingEvent(
            cow_id=mother.id,
            event_type=BreedingEvent.EVENT_CALVING,
            event_date=form.birth_date.data,
            birth_id=birth.id,
            created_by_id=current_user.id,
        )
        db.session.add(calving_event)
        db.session.flush()

        # HERD-5: apply the "ولادة" event type's status to the dam
        # (حليب by default). Her group is set on the next page, where
        # the calving type's group is preselected.
        calving_type = BreedingEventType.query.filter_by(
            code=BreedingEvent.EVENT_CALVING).first()
        dam_status = calving_type.target_status if calving_type else None
        if dam_status and dam_status != mother.breeding_status:
            db.session.add(BreedingStatusChange(
                cow_id=mother.id,
                from_status=mother.breeding_status,
                to_status=dam_status,
                changed_by_id=current_user.id,
                event_id=calving_event.id,
            ))
            mother.breeding_status = dam_status

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
    # HERD-5: the dam goes where the "ولادة" event type says (the milk
    # group by default). Calves keep the nursing default.
    calving = BreedingEventType.query.filter_by(
        code=BreedingEvent.EVENT_CALVING).first()
    dam_target = calving.target_group_id if calving else None

    def _default_group_id(cow):
        """Dam: calving type's group. Calves: nursing if it exists.
        Fallback: the cow's current group."""
        if cow.id == dam.id and dam_target:
            return dam_target
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


# ---------- US-1.7 Sale → SALES-1 redirect ----------
@bp.route("/<int:cow_id>/sell", methods=["GET", "POST"])
@login_required
@write_required
def sell_cow(cow_id: int):
    """SALES-1: the old standalone sale form is gone (it wrote an
    AnimalSale row and skipped the ledger — the "تم تسجيلها كإيراد"
    flash was a lie). Every cow sale now flows through the general
    SalesInvoice with kind=cow, which posts a real JE, closes the
    book value out of 1400, and books gain/loss on 4096.

    Kept as a 302 so sidebar buttons and any bookmarks / QA notes
    pointing at `/herd/<id>/sell` keep working. Historical
    AnimalSale rows created by the pre-SALES-1 flow are preserved
    and still render on cow_detail — the same table is used for the
    mirror row that on_sales_invoice creates alongside each cow
    line."""
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.status != Cow.STATUS_ACTIVE:
        abort(404)
    return redirect(url_for("sales.new_invoice", cow_id=cow.id))


# ---------- Sales list ----------
@bp.route("/sales")
@login_required
def sales_list():
    sales = AnimalSale.query.order_by(AnimalSale.sale_date.desc()).limit(200).all()
    return render_template("herd/sales.html", sales=sales)


# ==================== HERD-2 Part 1 (PHASE 35): breeding routes ====================


def _group_select_choices():
    return [("", "— بدون تغيير —")] + [(str(i), n) for i, n in _group_choices()]


def _event_type_defaults(types):
    """{code: {...}} the event form's JS uses to pre-fill the group and
    status when the user picks a type (or a result)."""
    return {
        t.code: {
            "group": str(t.target_group_id) if t.target_group_id else "",
            "status": t.target_status or "",
            "has_result": bool(t.has_result),
            "positive": t.status_if_positive or "",
            "negative": t.status_if_negative or "",
            "is_calving": t.code == BreedingEvent.EVENT_CALVING,
            "has_cost": t.code == BreedingEvent.EVENT_INSEMINATION,
        }
        for t in types
    }


SEMEN_CATEGORY = "سائل منوي"


def _semen_items():
    """HERD-8: inventory items in the semen category with stock left."""
    from app.models.inventory import Ingredient
    return (
        Ingredient.query.filter_by(category=SEMEN_CATEGORY, is_archived=False)
        .filter(Ingredient.current_qty > 0)
        .order_by(Ingredient.name).all()
    )


def _apply_insemination_cost(event, cow, form) -> str | None:
    """HERD-8: record the insemination's cost on `event` in the current
    transaction. Returns an Arabic error message, or None when done (or
    when no cost was picked).

    stock — draw the straws at average cost, StockMovement, JE
            DR 5025 / CR inventory.
    cash  — an Expense (category insemination) paid from a treasury,
            money out, JE DR 5025 / CR treasury.
    """
    from decimal import Decimal
    from app.models.finance import Expense, TreasuryAccount
    from app.models.inventory import Ingredient, StockMovement
    from app.services import autoposting
    from app.utils import accounts as acc
    from app.utils import inventory_cost

    source = form.cost_source.data or ""
    if not source:
        return None

    if source == BreedingEvent.COST_STOCK:
        raw = form.cost_ingredient_id.data or ""
        ing = db.session.get(Ingredient, int(raw)) if raw.isdigit() else None
        if ing is None:
            return "اختار صنف السائل المنوي من المخزون."
        qty = Decimal(str(form.cost_qty.data or 0))
        if qty <= 0:
            return "الكمية لازم تكون أكبر من صفر."
        unit_cost = ing.avg_cost
        try:
            value = inventory_cost.withdraw(ing, qty)
        except ValueError as e:
            return str(e)
        event.cost_source = BreedingEvent.COST_STOCK
        event.cost_ingredient_id = ing.id
        event.cost_qty = qty
        event.cost_amount = value
        db.session.add(StockMovement(
            ingredient_id=ing.id, delta=-qty,
            reason=StockMovement.REASON_INSEMINATION, ref_id=event.id,
            unit_price_at_move=unit_cost, moved_on=event.event_date,
            notes=f"تلقيح البقرة {cow.ear_tag}",
            created_by_id=current_user.id,
        ))
        db.session.flush()
        autoposting.on_insemination_cost(event, created_by=current_user.id)
        return None

    if source == BreedingEvent.COST_CASH:
        amount = Decimal(str(form.cost_amount.data or 0))
        if amount <= 0:
            return "اكتب مبلغ التلقيح."
        raw = form.cost_account_id.data or ""
        account = db.session.get(TreasuryAccount, int(raw)) if raw.isdigit() else None
        if account is None or account.is_archived:
            return "اختار الخزنة اللي اتدفع منها."
        expense = Expense(
            category=Expense.CAT_INSEMINATION, amount=amount,
            expense_date=event.event_date,
            description=f"تلقيح البقرة {cow.ear_tag}",
            ref_type="breeding_event", ref_id=event.id,
            account_id=account.id, created_by_id=current_user.id,
        )
        db.session.add(expense)
        db.session.flush()
        acc.money_out(account, amount, event.event_date,
                      ref_type="expense", ref_id=expense.id,
                      user_id=current_user.id,
                      notes=f"تلقيح البقرة {cow.ear_tag}")
        autoposting.on_expense(expense, account, created_by=current_user.id)
        event.cost_source = BreedingEvent.COST_CASH
        event.cost_amount = amount
        event.cost_expense_id = expense.id
        return None

    return "مصدر التكلفة غير صالح."


@bp.route("/<int:cow_id>/breeding/new", methods=["GET", "POST"])
@login_required
@write_required
def create_breeding_event(cow_id: int):
    """HERD-5: one-page event form. The event type pre-fills the new
    group and status (from `breeding_event_types`); the user can
    change either before saving. Saving writes the event, the group
    move and the status change in one transaction.

    Calving goes through the birth form instead — that's where the
    calves and the season count are recorded."""
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.is_archived:
        abort(404)

    types = active_event_types()
    by_code = {t.code: t for t in types}

    form = BreedingEventForm()
    form.event_type.choices = [(t.code, t.name) for t in types]
    # HERD-7: alert buttons open the form with the type picked.
    if request.method == "GET" and request.args.get("type") in by_code:
        form.event_type.data = request.args.get("type")
    form.new_group_id.choices = _group_select_choices()
    form.new_status.choices = _status_choices()
    # HERD-8: insemination cost pickers.
    form.cost_ingredient_id.choices = [("", "— اختار —")] + [
        (str(i.id), f"{i.name} (متوفر {i.current_qty:g} {i.unit_label})")
        for i in _semen_items()
    ]
    from app.utils import accounts as _acc
    form.cost_account_id.choices = [("", "— اختار —")] + [
        (str(i), label) for i, label in _acc.active_choices()
    ]

    if form.validate_on_submit():
        etype = by_code.get(form.event_type.data)
        if etype is None:
            flash("نوع الإجراء غير صالح.", "error")
            return redirect(url_for("herd.create_breeding_event", cow_id=cow.id))

        if etype.code == BreedingEvent.EVENT_CALVING:
            flash("الولادة بتتسجل من فورم الولادات عشان المواليد وعدد المواسم.", "info")
            return redirect(url_for("herd.create_birth", mother_id=cow.id))

        result = (form.result.data or None) if etype.has_result else None

        # "" = leave as is. A field missing from the POST entirely
        # (scripted posts) falls back to the type's defaults.
        if "new_group_id" in request.form:
            group_raw = form.new_group_id.data or ""
        else:
            group_raw = str(etype.target_group_id or "")
        if "new_status" in request.form:
            new_status = form.new_status.data or None
        else:
            new_status = etype.default_status(result)

        event = BreedingEvent(
            cow_id=cow.id,
            event_type=etype.code,
            event_date=form.event_date.data,
            result=result,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(event)
        db.session.flush()

        if etype.code == BreedingEvent.EVENT_INSEMINATION:
            cost_error = _apply_insemination_cost(event, cow, form)
            if cost_error:
                db.session.rollback()
                flash(cost_error, "error")
                return render_template(
                    "herd/breeding_event_form.html", cow=cow, form=form,
                    type_defaults=_event_type_defaults(types),
                )

        changed = []
        new_group_id = int(group_raw) if group_raw.isdigit() else None
        if new_group_id and new_group_id != cow.group_id:
            db.session.add(CowMovement(
                cow_id=cow.id,
                from_group_id=cow.group_id,
                to_group_id=new_group_id,
                moved_on=form.event_date.data,
                reason=f"إجراء: {etype.name}",
                created_by_id=current_user.id,
                event_id=event.id,
            ))
            cow.group_id = new_group_id
            changed.append(f"المجموعة → {db.session.get(CattleGroup, new_group_id).name}")

        if new_status and new_status != cow.breeding_status:
            db.session.add(BreedingStatusChange(
                cow_id=cow.id,
                from_status=cow.breeding_status,
                to_status=new_status,
                changed_by_id=current_user.id,
                event_id=event.id,
            ))
            cow.breeding_status = new_status
            changed.append(f"الحالة → {breeding_status_label(new_status)}")

        log_action(
            "breeding_event_recorded", "BreedingEvent", event.id,
            details=f"cow={cow.id} type={etype.code} " + " ".join(changed),
        )
        db.session.commit()

        msg = f"تم تسجيل {etype.name}."
        if changed:
            msg += " " + "، ".join(changed)
        flash(msg, "success")
        return redirect(url_for("herd.breeding_timeline", cow_id=cow.id))

    return render_template(
        "herd/breeding_event_form.html", cow=cow, form=form,
        type_defaults=_event_type_defaults(types),
    )


@bp.route("/<int:cow_id>/breeding/<int:event_id>/delete", methods=["POST"])
@login_required
@write_required
def delete_breeding_event(cow_id: int, event_id: int):
    """HERD-10: delete an event and undo what it did (status, group,
    insemination cost)."""
    from app.services.breeding_delete import DeleteRefused, delete_breeding_event as _delete
    cow = db.session.get(Cow, cow_id)
    event = db.session.get(BreedingEvent, event_id)
    if not cow or cow.is_archived or event is None or event.cow_id != cow.id:
        abort(404)
    label = event.event_label
    try:
        undone = _delete(event)
    except DeleteRefused as e:
        flash(str(e), "error")
        return redirect(url_for("herd.breeding_timeline", cow_id=cow_id))
    log_action("breeding_event_deleted", "BreedingEvent", event_id,
               details=f"cow={cow_id} type={label} " + " | ".join(undone))
    db.session.commit()
    msg = f"تم حذف {label}."
    if undone:
        msg += " " + "، ".join(undone)
    flash(msg, "success")
    return redirect(url_for("herd.breeding_timeline", cow_id=cow_id))


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

        valid_statuses = {code for code, _ in breeding_status_choices()}
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
    ] + breeding_status_choices()
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


# ==================== HERD-3: status list admin ====================

@bp.route("/statuses")
@login_required
def statuses_list():
    """The client's status list (STATUS.txt + milking), with how many
    active cows carry each one."""
    statuses = BreedingStatus.query.order_by(
        BreedingStatus.sort_order, BreedingStatus.id,
    ).all()
    rows = [{"s": s, "count": s.cow_count} for s in statuses]
    return render_template("herd/statuses.html", rows=rows)


def _status_name_taken(name: str, exclude_id: int | None = None) -> bool:
    from sqlalchemy import func as _func
    q = BreedingStatus.query.filter(
        _func.lower(BreedingStatus.name) == name.casefold()
    )
    if exclude_id is not None:
        q = q.filter(BreedingStatus.id != exclude_id)
    return q.first() is not None


@bp.route("/statuses/new", methods=["GET", "POST"])
@login_required
@write_required
def create_status():
    if request.method == "POST":
        name = " ".join((request.form.get("name") or "").split())
        if not name:
            flash("اسم الحالة مطلوب.", "error")
        elif len(name) > 60:
            flash("الاسم طويل — حد أقصى 60 حرف.", "error")
        elif _status_name_taken(name):
            flash(f"الحالة \"{name}\" موجودة فعلاً.", "error")
        else:
            last = db.session.query(db.func.max(BreedingStatus.sort_order)).scalar() or 0
            s = BreedingStatus(name=name, code="pending", sort_order=last + 1,
                               is_active=True)
            db.session.add(s)
            db.session.flush()
            # Code is generated, never typed: cows store it, so it must
            # stay stable even if the name is renamed later.
            s.code = f"custom_{s.id}"
            log_action("breeding_status_created", "BreedingStatus", s.id,
                       details=f"name={name}")
            db.session.commit()
            flash(f"تم إضافة الحالة \"{name}\".", "success")
            return redirect(url_for("herd.statuses_list"))
        return render_template("herd/status_form.html", mode="new",
                               status=None, name_val=name)
    return render_template("herd/status_form.html", mode="new",
                           status=None, name_val="")


@bp.route("/statuses/<int:status_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
def edit_status(status_id: int):
    s = db.session.get(BreedingStatus, status_id)
    if s is None:
        abort(404)
    if request.method == "POST":
        name = " ".join((request.form.get("name") or "").split())
        sort_raw = (request.form.get("sort_order") or "").strip()
        if not name:
            flash("اسم الحالة مطلوب.", "error")
        elif len(name) > 60:
            flash("الاسم طويل — حد أقصى 60 حرف.", "error")
        elif _status_name_taken(name, exclude_id=s.id):
            flash(f"الحالة \"{name}\" موجودة على صف تاني.", "error")
        else:
            old = s.name
            s.name = name
            if sort_raw.lstrip("-").isdigit():
                s.sort_order = int(sort_raw)
            log_action("breeding_status_renamed", "BreedingStatus", s.id,
                       details=f"{old} → {name}")
            db.session.commit()
            flash(f"تم تعديل الحالة \"{name}\".", "success")
            return redirect(url_for("herd.statuses_list"))
        return render_template("herd/status_form.html", mode="edit",
                               status=s, name_val=name)
    return render_template("herd/status_form.html", mode="edit",
                           status=s, name_val=s.name)


@bp.route("/statuses/<int:status_id>/toggle", methods=["POST"])
@login_required
@write_required
def toggle_status(status_id: int):
    """Disable / re-enable. A disabled status drops out of every
    picker but still labels history. Refused while active cows carry
    it — they'd be stuck with a status nobody can pick."""
    s = db.session.get(BreedingStatus, status_id)
    if s is None:
        abort(404)
    if s.is_active:
        n = s.cow_count
        if n:
            flash(f"مينفعش تعطّل \"{s.name}\" — فيه {n} بقرة عليها الحالة دي.", "error")
            return redirect(url_for("herd.statuses_list"))
    s.is_active = not s.is_active
    log_action("breeding_status_toggled", "BreedingStatus", s.id,
               details=f"active={s.is_active}")
    db.session.commit()
    flash(f"تم {'تفعيل' if s.is_active else 'تعطيل'} الحالة \"{s.name}\".", "info")
    return redirect(url_for("herd.statuses_list"))


# ==================== HERD-5: event types admin ====================

@bp.route("/event-types")
@login_required
def event_types_list():
    types = BreedingEventType.query.order_by(
        BreedingEventType.sort_order, BreedingEventType.id,
    ).all()
    return render_template("herd/event_types.html", types=types)


def _event_type_form(etype=None):
    from app.forms.herd import EventTypeForm
    form = EventTypeForm(obj=etype)
    if etype is not None and request.method == "GET":
        form.target_group_id.data = str(etype.target_group_id or "")
    form.target_group_id.choices = _group_select_choices()
    for field in (form.target_status, form.status_if_positive,
                  form.status_if_negative):
        field.choices = _status_choices(field.data)
    return form


def _apply_event_type_form(etype, form):
    group_raw = form.target_group_id.data or ""
    etype.name = " ".join(form.name.data.split())
    etype.target_group_id = int(group_raw) if group_raw.isdigit() else None
    etype.has_result = bool(form.has_result.data)
    if etype.has_result:
        etype.target_status = None
        etype.status_if_positive = form.status_if_positive.data or None
        etype.status_if_negative = form.status_if_negative.data or None
    else:
        etype.target_status = form.target_status.data or None
        etype.status_if_positive = None
        etype.status_if_negative = None
    if form.sort_order.data is not None:
        etype.sort_order = form.sort_order.data


def _event_type_name_taken(name, exclude_id=None):
    from sqlalchemy import func as _func
    q = BreedingEventType.query.filter(
        _func.lower(BreedingEventType.name) == " ".join(name.split()).casefold()
    )
    if exclude_id is not None:
        q = q.filter(BreedingEventType.id != exclude_id)
    return q.first() is not None


@bp.route("/event-types/new", methods=["GET", "POST"])
@login_required
@write_required
def create_event_type():
    form = _event_type_form()
    if form.validate_on_submit():
        if _event_type_name_taken(form.name.data):
            flash("الإجراء ده موجود فعلاً.", "error")
        else:
            last = db.session.query(db.func.max(BreedingEventType.sort_order)).scalar() or 0
            etype = BreedingEventType(code="pending", is_active=True,
                                      is_system=False, sort_order=last + 1)
            _apply_event_type_form(etype, form)
            if not form.sort_order.data:
                etype.sort_order = last + 1
            db.session.add(etype)
            db.session.flush()
            etype.code = f"custom_{etype.id}"
            log_action("event_type_created", "BreedingEventType", etype.id,
                       details=f"name={etype.name}")
            db.session.commit()
            flash(f"تم إضافة الإجراء \"{etype.name}\".", "success")
            return redirect(url_for("herd.event_types_list"))
    return render_template("herd/event_type_form.html", form=form,
                           mode="new", etype=None)


@bp.route("/event-types/<int:type_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
def edit_event_type(type_id: int):
    etype = db.session.get(BreedingEventType, type_id)
    if etype is None:
        abort(404)
    form = _event_type_form(etype)
    if form.validate_on_submit():
        if _event_type_name_taken(form.name.data, exclude_id=etype.id):
            flash("فيه إجراء تاني بنفس الاسم.", "error")
        else:
            _apply_event_type_form(etype, form)
            log_action("event_type_updated", "BreedingEventType", etype.id,
                       details=f"name={etype.name}")
            db.session.commit()
            flash(f"تم حفظ الإجراء \"{etype.name}\".", "success")
            return redirect(url_for("herd.event_types_list"))
    return render_template("herd/event_type_form.html", form=form,
                           mode="edit", etype=etype)


@bp.route("/event-types/<int:type_id>/toggle", methods=["POST"])
@login_required
@write_required
def toggle_event_type(type_id: int):
    etype = db.session.get(BreedingEventType, type_id)
    if etype is None:
        abort(404)
    if etype.is_system:
        flash(f"\"{etype.name}\" إجراء أساسي — مينفعش يتعطّل (تقدر تعدّل إعداداته).", "error")
        return redirect(url_for("herd.event_types_list"))
    etype.is_active = not etype.is_active
    log_action("event_type_toggled", "BreedingEventType", etype.id,
               details=f"active={etype.is_active}")
    db.session.commit()
    flash(f"تم {'تفعيل' if etype.is_active else 'تعطيل'} \"{etype.name}\".", "info")
    return redirect(url_for("herd.event_types_list"))


# ==================== HERD-7: herd alerts ====================

@bp.route("/alerts")
@login_required
def herd_alerts():
    """Cows that need a pregnancy check, drying, or a move to waiting.
    `?rule=check|dry|waiting` shows one list (used by the dashboard
    tiles and the per-list PDF)."""
    from app.services.herd_alerts import RULES, build_alerts, thresholds
    rule = request.args.get("rule")
    if rule not in RULES:
        rule = None
    alerts = build_alerts()
    return render_template(
        "herd/alerts.html",
        alerts=alerts, rules=RULES, thresholds=thresholds(),
        only=rule, print_mode=bool(request.args.get("print")),
        today_iso=date.today().isoformat(),
    )


@bp.route("/alerts.xlsx")
@login_required
def herd_alerts_excel():
    from app.services.herd_alerts import RULES, build_alerts
    from app.utils.reports import excel_response
    rule = request.args.get("rule")
    if rule not in RULES:
        abort(404)
    meta = RULES[rule]
    rows = [
        [r["tag"], r["group"], r["status"],
         r["last_insem"].isoformat() if r["last_insem"] else "",
         r["days"], r["expected"].isoformat() if r["expected"] else ""]
        for r in build_alerts()[rule]
    ]
    return excel_response(
        meta["title"],
        ["رقم الأذن", "المجموعة", "الحالة", "آخر تلقيح", meta["days_label"],
         "الولادة المتوقعة"],
        rows, f"herd_alert_{rule}_{date.today().isoformat()}.xlsx",
    )


@bp.route("/alerts.pdf")
@login_required
def herd_alerts_pdf():
    from app.services.herd_alerts import RULES
    from app.utils.reports import pdf_from_current_page
    rule = request.args.get("rule")
    args = {"print": 1}
    if rule in RULES:
        args["rule"] = rule
    target = url_for("herd.herd_alerts", _external=True, **args)
    return pdf_from_current_page(
        target, f"herd_alerts_{rule or 'all'}_{date.today().isoformat()}.pdf",
    )


@bp.route("/alert-settings", methods=["GET", "POST"])
@login_required
@write_required
def herd_alert_settings():
    """Day thresholds for the alerts + gestation length (used by the
    herd list's expected calving date too)."""
    from app.models.finance import Setting
    from app.services.herd_alerts import DEFAULTS, setting_int
    fields = [
        (Setting.KEY_HERD_ALERT_CHECK_DAYS, "الجس بعد التلقيح بـ (يوم)"),
        (Setting.KEY_HERD_ALERT_DRY_DAYS, "التجفيف بعد التلقيح بـ (يوم)"),
        (Setting.KEY_HERD_ALERT_WAITING_DAYS, "النقل للانتظار قبل الولادة بـ (يوم)"),
        (Setting.KEY_GESTATION_DAYS, "مدة الحمل (يوم)"),
    ]
    if request.method == "POST":
        values = {}
        for key, label in fields:
            raw = (request.form.get(key) or "").strip()
            if not raw.isdigit() or not (1 <= int(raw) <= 400):
                flash(f"{label}: لازم رقم من 1 لـ 400.", "error")
                return redirect(url_for("herd.herd_alert_settings"))
            values[key] = raw
        for key, raw in values.items():
            Setting.set(key, raw)
        log_action("herd_alert_settings_saved", "Setting", 0,
                   details=" ".join(f"{k}={v}" for k, v in values.items()))
        db.session.commit()
        flash("تم حفظ إعدادات التنبيهات.", "success")
        return redirect(url_for("herd.herd_alerts"))
    current = [(key, label, setting_int(key), DEFAULTS[key]) for key, label in fields]
    return render_template("herd/alert_settings.html", fields=current)


# ==================== HERD-8: insemination cost report ====================

def _insemination_cost_rows(d_from, d_to):
    events = (
        BreedingEvent.query
        .filter(BreedingEvent.event_type == BreedingEvent.EVENT_INSEMINATION,
                BreedingEvent.event_date >= d_from,
                BreedingEvent.event_date <= d_to)
        .order_by(BreedingEvent.event_date.desc(), BreedingEvent.id.desc())
        .all()
    )
    per_cow = {}
    for e in events:
        row = per_cow.setdefault(e.cow_id, {"cow": e.cow, "count": 0, "cost": 0})
        row["count"] += 1
        row["cost"] += e.cost_amount or 0
    return events, sorted(per_cow.values(), key=lambda r: -r["cost"])


def _date_range_args():
    today = date.today()
    try:
        d_from = date.fromisoformat(request.args.get("from") or "")
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = date.fromisoformat(request.args.get("to") or "")
    except ValueError:
        d_to = today
    return d_from, d_to


@bp.route("/insemination-costs")
@login_required
def insemination_costs():
    """HERD-8: inseminations in a date range with their cost, totals,
    and a per-cow summary."""
    d_from, d_to = _date_range_args()
    events, per_cow = _insemination_cost_rows(d_from, d_to)
    total = sum((e.cost_amount or 0) for e in events)
    costed = [e for e in events if e.cost_amount]
    return render_template(
        "herd/insemination_costs.html",
        events=events, per_cow=per_cow, d_from=d_from, d_to=d_to,
        total=total, count=len(events),
        avg=(total / len(costed)) if costed else 0,
    )


@bp.route("/insemination-costs.xlsx")
@login_required
def insemination_costs_excel():
    from app.utils.reports import excel_response
    d_from, d_to = _date_range_args()
    events, _ = _insemination_cost_rows(d_from, d_to)
    rows = [
        [e.event_date.isoformat(), e.cow.ear_tag, e.cost_source_label,
         e.cost_ingredient.name if e.cost_ingredient else "",
         float(e.cost_qty) if e.cost_qty else "",
         float(e.cost_amount) if e.cost_amount else 0]
        for e in events
    ]
    return excel_response(
        "تكاليف التلقيح",
        ["التاريخ", "البقرة", "المصدر", "الصنف", "الكمية", "التكلفة"],
        rows, f"insemination_costs_{d_from}_{d_to}.xlsx",
    )


# ==================== HERD-9: import from the parlor's Excel ====================

_IMPORT_TOKEN_CHARS = set("0123456789abcdef")


def _import_path(token: str):
    """instance/imports/<token>.json — token must be our 32 hex chars, so
    a crafted URL can't reach any other file."""
    from pathlib import Path
    from flask import current_app
    if len(token) != 32 or not set(token) <= _IMPORT_TOKEN_CHARS:
        abort(404)
    folder = Path(current_app.instance_path) / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{token}.json"


def _load_import(token: str):
    import json
    from app.services.herd_import import ParseResult
    path = _import_path(token)
    if not path.exists():
        abort(404)
    return path, ParseResult.from_json(json.loads(path.read_text(encoding="utf-8")))


@bp.route("/import", methods=["GET", "POST"])
@login_required
@admin_required
def herd_import_upload():
    """Step 1: upload the parlor's ALL HERD export."""
    import json
    import secrets
    from app.services.herd_import import ImportError_, check_upload, parse_workbook
    if request.method == "POST":
        f = request.files.get("file")
        if f is None or not f.filename:
            flash("اختار ملف الإكسيل.", "error")
            return render_template("herd/import_upload.html")
        try:
            check_upload(f)
            result = parse_workbook(f.stream)
        except ImportError_ as e:
            flash(str(e), "error")
            return render_template("herd/import_upload.html")
        token = secrets.token_hex(16)
        _import_path(token).write_text(json.dumps(result.to_json(), ensure_ascii=False),
                                       encoding="utf-8")
        return redirect(url_for("herd.herd_import_preview", token=token))
    return render_template("herd/import_upload.html")


@bp.route("/import/<token>", methods=["GET", "POST"])
@login_required
@admin_required
def herd_import_preview(token: str):
    """Step 2: map parlor groups / statuses to ours, then import."""
    from app.services.herd_import import default_status_for, import_rows
    path, result = _load_import(token)
    group_choices = _group_choices()
    status_choices = [("", "— بدون حالة —")] + breeding_status_choices()

    if request.method == "POST":
        valid_groups = {gid for gid, _ in group_choices}
        group_map, missing = {}, []
        for g in result.groups:
            raw = request.form.get(f"group_{g}", "")
            if raw.isdigit() and int(raw) in valid_groups:
                group_map[g] = int(raw)
            else:
                missing.append(g)
        if missing:
            flash("لازم تختار مجموعة لكل رقم مجموعة في الملف: " + "، ".join(missing), "error")
        else:
            valid_status = {code for code, _ in status_choices}
            status_map = {}
            for s in result.statuses:
                picked = request.form.get(f"status_{s}", "")
                status_map[s] = picked if picked in valid_status else ""
            imported = import_rows(result.rows, group_map, status_map, current_user.id)
            log_action("herd_imported", "Cow", 0,
                       details=f"imported={imported} duplicates={len(result.duplicates)} "
                               f"existing={len(result.existing)}")
            db.session.commit()
            path.unlink(missing_ok=True)
            return render_template("herd/import_done.html", imported=imported,
                                   duplicates=result.duplicates, existing=result.existing)

    return render_template(
        "herd/import_preview.html", token=token, result=result,
        group_choices=group_choices, status_choices=status_choices,
        default_status=default_status_for, sample=result.rows[:20],
    )
