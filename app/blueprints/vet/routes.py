"""VET-1: vet records — per-cow entries, group vaccination, due doses,
and the editable vaccine / disease lists."""
from __future__ import annotations

import secrets
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.herd import CattleGroup, Cow, breeding_status_choices
from app.models.inventory import Ingredient
from app.models.vet import VetCatalogItem, VetRecord, vet_items
from app.utils.audit import log_action
from app.utils.decorators import admin_required, write_required

bp = Blueprint("vet", __name__, template_folder="../../templates/vet")


# ---------- helpers ----------

def _date(raw: str | None, default: date | None = None) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return None


def _medicines():
    return (Ingredient.query
            .filter(Ingredient.category == Ingredient.CATEGORY_MEDICINE,
                    Ingredient.is_archived.is_(False), Ingredient.current_qty > 0)
            .order_by(Ingredient.name).all())


def _read_medicine(form) -> tuple[Ingredient | None, Decimal | None, str | None]:
    """(ingredient, qty, error). No medicine picked → (None, None, None)."""
    raw = (form.get("ingredient_id") or "").strip()
    if not raw:
        return None, None, None
    ing = db.session.get(Ingredient, int(raw)) if raw.isdigit() else None
    if ing is None:
        return None, None, "اختار دواء صحيح."
    try:
        qty = Decimal((form.get("qty") or "").strip())
    except InvalidOperation:
        return None, None, "اكتب كمية الدواء."
    if qty <= 0:
        return None, None, "كمية الدواء لازم تكون أكبر من صفر."
    return ing, qty, None


def _repeat_map(items) -> dict[int, int]:
    return {i.id: i.repeat_days for i in items if i.repeat_days}


def _active_cow(cow_id: int) -> Cow:
    cow = db.session.get(Cow, cow_id)
    if not cow or cow.is_archived:
        abort(404)
    return cow


# ---------- records list ----------

def _filtered_records(args):
    today = date.today()
    d_from = _date(args.get("from"), today.replace(day=1)) or today.replace(day=1)
    d_to = _date(args.get("to"), today) or today
    kind = args.get("kind") or ""
    item = args.get("item") or ""
    q = (VetRecord.query.join(Cow, Cow.id == VetRecord.cow_id)
         .filter(VetRecord.record_date >= d_from, VetRecord.record_date <= d_to))
    if kind in VetRecord.KIND_LABELS:
        q = q.filter(VetRecord.kind == kind)
    if item.isdigit():
        q = q.filter(VetRecord.item_id == int(item))
    tag = (args.get("q") or "").strip()
    if tag:
        q = q.filter(Cow.ear_tag.ilike(f"%{tag}%"))
    records = q.order_by(VetRecord.record_date.desc(), VetRecord.id.desc()).all()
    return records, {"from": d_from, "to": d_to, "kind": kind, "item": item, "q": tag}


def _export_row(r: VetRecord) -> list:
    return [
        r.record_date.isoformat(), r.cow.ear_tag, r.kind_label,
        r.item.name if r.item else "",
        r.next_due.isoformat() if r.next_due else "",
        (f"{r.dispense.ingredient.name} × {r.dispense.qty}" if r.dispense else ""),
        r.notes or "",
    ]


_EXPORT_HEADERS = ["التاريخ", "رقم الأذن", "النوع", "التحصين / المرض",
                   "الجرعة الجاية", "الدواء", "ملاحظات"]


@bp.route("/")
@login_required
def records_list():
    records, filters = _filtered_records(request.args)
    items = VetCatalogItem.query.order_by(VetCatalogItem.kind, VetCatalogItem.name).all()
    return render_template("vet/records.html", records=records, filters=filters,
                           kinds=VetRecord.KIND_LABELS, items=items)


@bp.route("/export.xlsx")
@login_required
def records_excel():
    from app.utils.reports import excel_response
    records, f = _filtered_records(request.args)
    return excel_response("السجل البيطري", _EXPORT_HEADERS,
                          [_export_row(r) for r in records],
                          f"vet_{f['from'].isoformat()}_{f['to'].isoformat()}.xlsx")


# ---------- one cow ----------

@bp.route("/cow/<int:cow_id>/new", methods=["GET", "POST"])
@login_required
@write_required
def create_record(cow_id: int):
    from app.services.medicine_dispense import DispenseError, dispense_medicine
    cow = _active_cow(cow_id)
    if cow.status != Cow.STATUS_ACTIVE:
        flash("البقرة مش نشطة.", "error")
        return redirect(url_for("herd.cow_detail", cow_id=cow.id))
    vaccines = vet_items(VetCatalogItem.KIND_VACCINE)
    diseases = vet_items(VetCatalogItem.KIND_DISEASE)
    form = request.form if request.method == "POST" else request.args
    ctx = dict(cow=cow, vaccines=vaccines, diseases=diseases, medicines=_medicines(),
               kinds=VetRecord.KIND_LABELS, repeat=_repeat_map(vaccines), form=form,
               today_iso=date.today().isoformat())

    if request.method == "POST":
        kind = form.get("kind") or ""
        on = _date(form.get("record_date"))
        allowed = {i.id: i for i in (vaccines if kind == VetRecord.KIND_VACCINATION else diseases)}
        raw_item = (form.get("item_id") or "").strip()
        item = allowed.get(int(raw_item)) if raw_item.isdigit() else None
        error = None
        if kind not in VetRecord.KIND_LABELS:
            error = "اختار نوع الإجراء."
        elif on is None or on > date.today():
            error = "اختار تاريخ صحيح (مش في المستقبل)."
        elif kind != VetRecord.KIND_TREATMENT and item is None:
            error = "اختار التحصين." if kind == VetRecord.KIND_VACCINATION else "اختار المرض."
        next_due = None
        if not error and on is not None and kind == VetRecord.KIND_VACCINATION:
            raw_next = (form.get("next_due") or "").strip()
            if raw_next:
                next_due = _date(raw_next)
                if next_due is None or next_due <= on:
                    error = "ميعاد الجرعة الجاية لازم يكون بعد تاريخ التحصين."
            elif item and item.repeat_days:
                next_due = on + timedelta(days=item.repeat_days)
        ing, qty, med_error = _read_medicine(form)
        error = error or med_error
        if error or on is None:
            flash(error or "اختار تاريخ صحيح.", "error")
            return render_template("vet/record_form.html", **ctx)

        rec = VetRecord(cow_id=cow.id, kind=kind, item_id=item.id if item else None,
                        record_date=on, next_due=next_due,
                        notes=(form.get("notes") or "").strip()[:1000] or None,
                        created_by_id=current_user.id)
        if ing is not None:
            label = item.name if item else rec.kind_label
            try:
                d = dispense_medicine(ing, qty, None, cow_id=cow.id, group_id=None, on=on,
                                      notes=f"{rec.kind_label}: {label}", user_id=current_user.id)
            except DispenseError as e:
                db.session.rollback()
                flash(str(e), "error")
                return render_template("vet/record_form.html", **ctx)
            rec.dispense_id = d.id
        db.session.add(rec)
        db.session.flush()
        log_action("vet_record_added", "VetRecord", rec.id,
                   details=f"cow={cow.id} kind={kind} item={rec.item_id} dispense={rec.dispense_id}")
        db.session.commit()
        flash(f"تم تسجيل {rec.kind_label}"
              + (f" — الجرعة الجاية {next_due.isoformat()}" if next_due else "") + ".", "success")
        return redirect(url_for("herd.cow_detail", cow_id=cow.id))
    return render_template("vet/record_form.html", **ctx)


@bp.route("/records/<int:record_id>/delete", methods=["POST"])
@login_required
@write_required
def delete_record(record_id: int):
    rec = db.session.get(VetRecord, record_id)
    if rec is None:
        abort(404)
    cow_id = rec.cow_id
    if rec.dispense_id:
        flash("السجل ده صرف دواء من المخزن — مينفعش يتحذف من هنا عشان المخزن والقيد. "
              "صحح من شاشة صرف الأدوية.", "error")
    else:
        log_action("vet_record_deleted", "VetRecord", rec.id,
                   details=f"cow={cow_id} kind={rec.kind} item={rec.item_id} date={rec.record_date}")
        db.session.delete(rec)
        db.session.commit()
        flash("تم حذف السجل.", "success")
    return redirect(url_for("herd.cow_detail", cow_id=cow_id))


# ---------- group vaccination ----------

def _group_targets(group_ids: list[int], statuses: list[str]) -> list[Cow]:
    if not group_ids:
        return []
    q = Cow.query.filter(Cow.group_id.in_(group_ids), Cow.is_archived.is_(False),
                         Cow.status == Cow.STATUS_ACTIVE)
    if statuses:
        q = q.filter(Cow.breeding_status.in_(statuses))
    return q.order_by(Cow.ear_tag).all()


@bp.route("/group-vaccination", methods=["GET", "POST"])
@login_required
@write_required
def group_vaccination():
    from app.services.medicine_dispense import DispenseError, dispense_medicine
    vaccines = vet_items(VetCatalogItem.KIND_VACCINE)
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    form = request.form if request.method == "POST" else request.args
    group_ids = [int(g) for g in form.getlist("group") if g.isdigit()]
    statuses = [s for s in form.getlist("status") if s]
    targets = _group_targets(group_ids, statuses)
    ctx = dict(vaccines=vaccines, groups=groups, status_options=breeding_status_choices(),
               medicines=_medicines(), form=form, group_ids=group_ids, statuses=statuses,
               targets=targets, repeat=_repeat_map(vaccines),
               today_iso=date.today().isoformat())

    if request.method == "POST":
        raw_item = (form.get("item_id") or "").strip()
        item = next((v for v in vaccines if raw_item.isdigit() and v.id == int(raw_item)), None)
        on = _date(form.get("record_date"))
        error = None
        if item is None:
            error = "اختار التحصين."
        elif on is None or on > date.today():
            error = "اختار تاريخ صحيح (مش في المستقبل)."
        elif not targets:
            error = "مفيش أبقار نشطة في الاختيار ده."
        next_due = None
        if not error and item is not None and on is not None:
            raw_next = (form.get("next_due") or "").strip()
            if raw_next:
                next_due = _date(raw_next)
                if next_due is None or next_due <= on:
                    error = "ميعاد الجرعة الجاية لازم يكون بعد تاريخ التحصين."
            elif item.repeat_days:
                next_due = on + timedelta(days=item.repeat_days)
        ing, per_head, med_error = _read_medicine(form)
        error = error or med_error
        if error or item is None or on is None:
            flash(error or "اختار التحصين والتاريخ.", "error")
            return render_template("vet/group_form.html", **ctx)

        batch = secrets.token_hex(16)
        notes = (form.get("notes") or "").strip()[:1000] or None
        # One group dispense per group, linked to every record of that group.
        dispense_for: dict[int, int] = {}
        if ing is not None:
            by_group: dict[int, int] = {}
            for c in targets:
                by_group[c.group_id] = by_group.get(c.group_id, 0) + 1
            try:
                for gid, heads in by_group.items():
                    d = dispense_medicine(ing, per_head * heads, None, cow_id=None, group_id=gid,
                                          on=on, notes=f"تحصين {item.name} — {heads} رأس",
                                          user_id=current_user.id)
                    dispense_for[gid] = d.id
            except DispenseError as e:
                db.session.rollback()
                flash(str(e), "error")
                return render_template("vet/group_form.html", **ctx)
        for c in targets:
            db.session.add(VetRecord(cow_id=c.id, kind=VetRecord.KIND_VACCINATION,
                                     item_id=item.id, record_date=on, next_due=next_due,
                                     dispense_id=dispense_for.get(c.group_id), batch=batch,
                                     notes=notes, created_by_id=current_user.id))
        log_action("vet_group_vaccination", "VetRecord", 0,
                   details=f"batch={batch} item={item.id} cows={len(targets)} "
                           f"groups={group_ids} dispenses={list(dispense_for.values())}")
        db.session.commit()
        flash(f"تم تسجيل تحصين {item.name} لـ {len(targets)} رأس"
              + (f" — الجرعة الجاية {next_due.isoformat()}" if next_due else "") + ".", "success")
        return redirect(url_for("vet.records_list", item=item.id, **{"from": on.isoformat()}))
    return render_template("vet/group_form.html", **ctx)


# ---------- due doses ----------

@bp.route("/due/settings", methods=["POST"])
@login_required
@write_required
def due_settings():
    from app.models.finance import Setting
    raw = (request.form.get("days") or "").strip()
    if not raw.isdigit() or int(raw) > 365:
        flash("عدد الأيام لازم رقم من 0 لـ 365.", "error")
    else:
        Setting.set(Setting.KEY_VET_DUE_DAYS, raw)
        log_action("vet_due_days_saved", "Setting", 0, details=f"days={raw}")
        db.session.commit()
        flash("تم الحفظ.", "success")
    return redirect(url_for("vet.due_list"))


@bp.route("/due")
@login_required
def due_list():
    from app.services.vet_due import build_due, due_window_days
    rows = build_due()
    by_vaccine: dict[tuple[int, str], int] = {}
    for r in rows:
        key = (r["item_id"], r["vaccine"])
        by_vaccine[key] = by_vaccine.get(key, 0) + 1
    return render_template("vet/due.html", rows=rows, days=due_window_days(),
                           by_vaccine=sorted(by_vaccine.items(), key=lambda kv: kv[0][1]))


@bp.route("/due.xlsx")
@login_required
def due_excel():
    from app.services.vet_due import build_due
    from app.utils.reports import excel_response
    rows = build_due()
    return excel_response(
        "تحصينات مستحقة",
        ["رقم الأذن", "المجموعة", "التحصين", "آخر جرعة", "الجرعة الجاية", "باقي (يوم)"],
        [[r["tag"], r["group"], r["vaccine"], r["last"].isoformat(),
          r["next_due"].isoformat(), r["days"]] for r in rows],
        f"vet_due_{date.today().isoformat()}.xlsx",
    )


# ---------- lists admin ----------

def _read_catalog_form(form) -> tuple[str, int | None, str | None]:
    name = (form.get("name") or "").strip()[:120]
    raw = (form.get("repeat_days") or "").strip()
    if not name:
        return name, None, "اكتب الاسم."
    if raw and (not raw.isdigit() or not 1 <= int(raw) <= 3650):
        return name, None, "التكرار لازم رقم أيام من 1 لـ 3650 (أو فاضي)."
    return name, int(raw) if raw else None, None


@bp.route("/catalog", methods=["GET", "POST"])
@login_required
@admin_required
def catalog():
    if request.method == "POST":
        kind = request.form.get("kind") or ""
        name, repeat, error = _read_catalog_form(request.form)
        if kind not in VetCatalogItem.KIND_LABELS:
            error = "اختار النوع."
        elif not error and VetCatalogItem.query.filter_by(kind=kind, name=name).first():
            error = "الاسم ده موجود."
        if error:
            flash(error, "error")
        else:
            item = VetCatalogItem(kind=kind, name=name, is_active=True,
                                  repeat_days=repeat if kind == VetCatalogItem.KIND_VACCINE else None)
            db.session.add(item)
            db.session.flush()
            log_action("vet_catalog_added", "VetCatalogItem", item.id, details=f"{kind} {name}")
            db.session.commit()
            flash(f"تمت إضافة {name}.", "success")
        return redirect(url_for("vet.catalog"))
    items = VetCatalogItem.query.order_by(VetCatalogItem.kind.desc(), VetCatalogItem.name).all()
    return render_template("vet/catalog.html", items=items, kinds=VetCatalogItem.KIND_LABELS)


@bp.route("/catalog/<int:item_id>", methods=["POST"])
@login_required
@admin_required
def catalog_update(item_id: int):
    item = db.session.get(VetCatalogItem, item_id)
    if item is None:
        abort(404)
    if request.form.get("action") == "toggle":
        item.is_active = not item.is_active
        msg = "اتفعّل" if item.is_active else "اتوقف (السجلات القديمة باقية)"
    else:
        name, repeat, error = _read_catalog_form(request.form)
        clash = VetCatalogItem.query.filter(VetCatalogItem.kind == item.kind,
                                            VetCatalogItem.name == name,
                                            VetCatalogItem.id != item.id).first()
        if error or clash:
            flash(error or "الاسم ده موجود.", "error")
            return redirect(url_for("vet.catalog"))
        item.name = name
        if item.kind == VetCatalogItem.KIND_VACCINE:
            item.repeat_days = repeat
        msg = "اتحفظ"
    log_action("vet_catalog_updated", "VetCatalogItem", item.id,
               details=f"name={item.name} repeat={item.repeat_days} active={item.is_active}")
    db.session.commit()
    flash(f"{item.name}: {msg}.", "success")
    return redirect(url_for("vet.catalog"))
