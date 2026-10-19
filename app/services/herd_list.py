"""HERD-6: the herd list rows — the columns of the client's parlor
report (ALL_HERD) — with filters and sorting.

One function feeds the page, the Excel export and the PDF so the
three always show the same cows. Births and inseminations are read
with one grouped query each, never per cow.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models.finance import Setting
from app.models.herd import Birth, BreedingEvent, Cow, breeding_status_label

# Statuses where "days pregnant" and the expected calving date mean
# something.
PREGNANT_STATUSES = {Cow.BS_PREG, Cow.BS_PREG2, Cow.BS_DRY, Cow.BS_WAITING}

SORT_KEYS = (
    "tag", "group", "age", "status", "season", "last_calving", "dim",
    "last_insem", "days_preg", "expected",
)

LIFE_CHOICES = [
    ("active", "نشط"),
    ("sold", "مباع"),
    ("dead", "نافق"),
    ("all", "الكل"),
]

EXPORT_HEADERS = [
    "رقم الأذن", "المجموعة", "العمر (سنة)", "الحالة", "الموسم",
    "آخر ولادة", "أيام الحليب", "آخر تلقيح", "أيام العشار",
    "الولادة المتوقعة",
]


def gestation_days() -> int:
    return int(Setting.get_decimal(Setting.KEY_GESTATION_DAYS, Decimal("280")))


def read_filters(args) -> dict:
    """Pull the list filters out of a request.args MultiDict."""
    def ints(name):
        out = []
        for v in args.getlist(name):
            if str(v).isdigit():
                out.append(int(v))
        return out

    life = args.get("life") or "active"
    if life not in {k for k, _ in LIFE_CHOICES}:
        life = "active"
    return {
        "q": (args.get("q") or "").strip(),
        "life": life,
        "group": ints("group"),
        "status": [s for s in args.getlist("status") if s],
        "season": ints("season"),
    }


def _age_years(dob: date | None, today: date) -> float | None:
    if not dob:
        return None
    return round((today - dob).days / 365.25, 1)


def _tag_key(tag: str):
    """Numeric tags sort as numbers, the rest after them as text."""
    return (0, int(tag), "") if tag.isdigit() else (1, 0, tag)


def build_herd_rows(filters: dict, sort: str = "tag", direction: str = "asc",
                    today: date | None = None) -> list[dict]:
    today = today or date.today()
    gest = gestation_days()

    query = Cow.query.filter(Cow.is_archived.is_(False))
    if filters.get("life", "active") != "all":
        query = query.filter(Cow.status == filters.get("life", "active"))
    if filters.get("q"):
        query = query.filter(Cow.ear_tag.ilike(f"%{filters['q']}%"))
    if filters.get("group"):
        query = query.filter(Cow.group_id.in_(filters["group"]))
    statuses = filters.get("status") or []
    if statuses:
        codes = [s for s in statuses if s != "none"]
        conds = []
        if codes:
            conds.append(Cow.breeding_status.in_(codes))
        if "none" in statuses:
            conds.append(Cow.breeding_status.is_(None))
            conds.append(Cow.breeding_status == "")
        query = query.filter(db.or_(*conds))
    cows = query.all()

    ids = [c.id for c in cows]
    births = {}
    insems = {}
    calvings = {}
    if ids:
        for mother_id, n, last in (
            db.session.query(Birth.mother_id, func.count(Birth.id),
                             func.max(Birth.birth_date))
            .filter(Birth.mother_id.in_(ids))
            .group_by(Birth.mother_id)
        ):
            births[mother_id] = (n, last)
        for cow_id, etype, last in (
            db.session.query(BreedingEvent.cow_id, BreedingEvent.event_type,
                             func.max(BreedingEvent.event_date))
            .filter(BreedingEvent.cow_id.in_(ids),
                    BreedingEvent.event_type.in_([BreedingEvent.EVENT_INSEMINATION,
                                                  BreedingEvent.EVENT_CALVING]))
            .group_by(BreedingEvent.cow_id, BreedingEvent.event_type)
        ):
            if etype == BreedingEvent.EVENT_INSEMINATION:
                insems[cow_id] = last
            else:
                calvings[cow_id] = last

    rows = []
    for c in cows:
        n_births, last_birth = births.get(c.id, (0, None))
        # HERD-9: a calving event without a Birth row (imported from the
        # parlor) still counts as the last calving. create_birth writes
        # both for the same date, so taking the latest never double-counts.
        last_calving = max((d for d in (last_birth, calvings.get(c.id)) if d),
                           default=None)
        last_insem = insems.get(c.id)
        season = n_births + (c.season_count_offset or 0)
        days_preg = expected = None
        if (c.breeding_status in PREGNANT_STATUSES and last_insem
                and (last_calving is None or last_insem > last_calving)):
            days_preg = (today - last_insem).days
            expected = last_insem + timedelta(days=gest)
        rows.append({
            "cow": c,
            "tag": c.ear_tag,
            "group": c.group.name if c.group else "",
            "age": _age_years(c.date_of_birth, today),
            "status_code": c.breeding_status or "",
            "status": breeding_status_label(c.breeding_status) if c.breeding_status else "",
            "season": season,
            "last_calving": last_calving,
            "dim": (today - last_calving).days if last_calving else None,
            "last_insem": last_insem,
            "days_preg": days_preg,
            "expected": expected,
        })

    seasons = filters.get("season") or []
    if seasons:
        rows = [r for r in rows if r["season"] in seasons]

    if sort not in SORT_KEYS:
        sort = "tag"
    reverse = direction == "desc"
    if sort == "tag":
        rows.sort(key=lambda r: _tag_key(r["tag"]), reverse=reverse)
    else:
        present = [r for r in rows if r[sort] not in (None, "")]
        missing = [r for r in rows if r[sort] in (None, "")]
        present.sort(key=lambda r: r[sort], reverse=reverse)
        rows = present + sorted(missing, key=lambda r: _tag_key(r["tag"]))
    return rows


def export_row(r: dict) -> list:
    def d(v):
        return v.isoformat() if v else ""
    return [
        r["tag"], r["group"], r["age"] if r["age"] is not None else "",
        r["status"], r["season"], d(r["last_calving"]),
        r["dim"] if r["dim"] is not None else "", d(r["last_insem"]),
        r["days_preg"] if r["days_preg"] is not None else "", d(r["expected"]),
    ]
