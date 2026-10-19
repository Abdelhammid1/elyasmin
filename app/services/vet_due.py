"""VET-1: vaccine doses that are due.

For every active cow and vaccine, only her latest dose counts: if its
next_due falls within the window (or is already past) she is listed.
A newer dose of the same vaccine replaces the old due date.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.finance import Setting
from app.models.herd import Cow
from app.models.vet import VetRecord

DEFAULT_DUE_DAYS = 7


def due_window_days() -> int:
    return max(0, int(Setting.get_decimal(Setting.KEY_VET_DUE_DAYS, Decimal(DEFAULT_DUE_DAYS))))


def build_due(today: date | None = None, days: int | None = None) -> list[dict]:
    today = today or date.today()
    days = due_window_days() if days is None else days
    limit = today + timedelta(days=days)

    latest: dict[tuple[int, int], VetRecord] = {}
    records = (
        VetRecord.query.join(Cow, Cow.id == VetRecord.cow_id)
        .filter(VetRecord.kind == VetRecord.KIND_VACCINATION,
                VetRecord.item_id.isnot(None),
                Cow.is_archived.is_(False), Cow.status == Cow.STATUS_ACTIVE)
        .order_by(VetRecord.record_date, VetRecord.id)
        .all()
    )
    for r in records:
        latest[(r.cow_id, r.item_id)] = r   # later rows overwrite earlier

    rows = []
    for r in latest.values():
        if r.next_due is None or r.next_due > limit:
            continue
        rows.append({
            "record": r,
            "cow": r.cow,
            "tag": r.cow.ear_tag,
            "group": r.cow.group.name if r.cow.group else "",
            "group_id": r.cow.group_id,
            "vaccine": r.item.name,
            "item_id": r.item_id,
            "last": r.record_date,
            "next_due": r.next_due,
            "days": (r.next_due - today).days,   # negative = overdue
        })
    rows.sort(key=lambda x: (x["next_due"], x["tag"]))
    return rows


def due_count() -> int:
    return len(build_due())
