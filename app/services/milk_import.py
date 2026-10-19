"""HERD-11: per-cow daily milk from the parlor's Excel.

We don't have the parlor's daily milk export layout yet, so nothing
here is tied to one layout: the header row is found, the tag and milk
columns are guessed from their titles, and the preview screen lets the
user pick other columns before anything is saved.

A two-row header (like ALL HERD's "Avg" over "Milk") is merged into one
title per column.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from app.extensions import db
from app.models.herd import Cow, CowMilkRecord
from app.services.herd_import import ImportError_, _text, check_upload  # noqa: F401

# Above this a day's milk is shown as suspicious (still importable).
SUSPICIOUS_KG = Decimal("80")
# Above this the number can't be one cow's day — refused as a bad row.
MAX_KG = Decimal("999")

_TAG_WORDS = ("num", "cow", "tag", "id", "رقم")
_MILK_WORDS = ("milk", "yield", "kg", "حليب", "لبن", "إنتاج", "انتاج")
_DAY_WORDS = ("total", "day", "today", "يوم", "إجمالي", "اجمالي")
_AVG_WORDS = ("avg", "average", "متوسط")


@dataclass
class MilkSheet:
    headers: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)   # cells as text/number/None

    def to_json(self) -> dict:
        return {"kind": "milk", "headers": self.headers, "rows": self.rows}

    @classmethod
    def from_json(cls, data: dict) -> "MilkSheet":
        if data.get("kind") != "milk":
            raise ValueError("not a milk import")
        return cls(headers=data["headers"], rows=data["rows"])


def _is_text(v) -> bool:
    if v is None or isinstance(v, (int, float, datetime, date)):
        return False
    s = str(v).strip()
    return bool(s) and not any(ch.isdigit() for ch in s)


def _cell(v):
    """JSON-safe cell."""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (int, float)) or v is None:
        return v
    return str(v).strip()


def parse_sheet(stream) -> MilkSheet:
    try:
        wb = load_workbook(stream, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — any unreadable file
        raise ImportError_("مش قادر أقرا الملف — اتأكد إنه ملف إكسيل من برنامج المحلب.")
    ws = wb.worksheets[0]
    sheet = MilkSheet()
    header_done = False
    for raw in ws.iter_rows(values_only=True):
        row = [_cell(v) for v in raw]
        if not any(v not in (None, "") for v in row):
            continue
        if not sheet.headers:
            if sum(1 for v in row if _is_text(v)) >= 2:
                sheet.headers = ["" if v is None else str(v) for v in row]
            continue
        if not header_done:
            header_done = True
            # Second header row: no tag-like number in the first cell and
            # mostly titles → merge into the first.
            first = _text(row[0]) if row else ""
            if not any(ch.isdigit() for ch in first) and sum(1 for v in row if _is_text(v)) >= 2:
                width = max(len(sheet.headers), len(row))
                sheet.headers += [""] * (width - len(sheet.headers))
                sheet.headers = [
                    f"{sheet.headers[i]} {row[i] if i < len(row) and row[i] is not None else ''}".strip()
                    for i in range(width)
                ]
                continue
        sheet.rows.append(row)
    wb.close()
    if not sheet.headers:
        raise ImportError_("مش لاقي صف عناوين في الملف (زي Num / Milk).")
    if not sheet.rows:
        raise ImportError_("الملف مفيهوش صفوف بيانات.")
    return sheet


def guess_columns(headers: list[str]) -> tuple[int, int | None]:
    """(tag column, milk column or None if no title looks like milk)."""
    low = [h.strip().lower() for h in headers]
    tag_col = next((i for i, h in enumerate(low)
                    if h in _TAG_WORDS or h.startswith("cow") or "رقم" in h), 0)
    milk = [i for i, h in enumerate(low)
            if i != tag_col and any(w in h for w in _MILK_WORDS)]
    if not milk:
        return tag_col, None

    def rank(i):
        h = low[i]
        return (any(w in h for w in _AVG_WORDS), not any(w in h for w in _DAY_WORDS), i)
    return tag_col, min(milk, key=rank)


def _kg(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).strip().replace(",", "."))
    except InvalidOperation:
        return Decimal("-1")  # flagged as bad by the caller


@dataclass
class Preview:
    matched: list[dict] = field(default_factory=list)   # tag, cow_id, kg, exists, suspicious
    unknown: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    bad: list[str] = field(default_factory=list)
    blank: int = 0

    @property
    def existing_count(self) -> int:
        return sum(1 for m in self.matched if m["exists"])


def build_preview(sheet: MilkSheet, tag_col: int, milk_col: int,
                  record_date: date) -> Preview:
    cows = {c.ear_tag: c.id for c in Cow.query.filter(
        Cow.is_archived.is_(False), Cow.status == Cow.STATUS_ACTIVE)}
    out = Preview()
    seen = set()
    for row in sheet.rows:
        tag = _text(row[tag_col]) if tag_col < len(row) else ""
        if not tag or not any(ch.isdigit() for ch in tag):
            continue
        kg = _kg(row[milk_col]) if milk_col < len(row) else None
        if kg is None:
            out.blank += 1   # no milk that day, or the export's total line
            continue
        if kg < 0 or kg > MAX_KG:
            out.bad.append(tag)
            continue
        if tag in seen:
            out.duplicates.append(tag)
            continue
        seen.add(tag)
        if tag not in cows:
            out.unknown.append(tag)
            continue
        out.matched.append({"tag": tag, "cow_id": cows[tag], "kg": str(kg.quantize(Decimal("0.01"))),
                            "exists": False, "suspicious": kg > SUSPICIOUS_KG})
    if out.matched:
        have = {cid for (cid,) in db.session.query(CowMilkRecord.cow_id).filter(
            CowMilkRecord.record_date == record_date,
            CowMilkRecord.cow_id.in_([m["cow_id"] for m in out.matched]))}
        for m in out.matched:
            m["exists"] = m["cow_id"] in have
    return out


def import_milk(matched: list[dict], record_date: date, overwrite: bool,
                user_id: int | None) -> tuple[int, int, int]:
    """(added, updated, skipped). The caller commits."""
    existing = {r.cow_id: r for r in CowMilkRecord.query.filter(
        CowMilkRecord.record_date == record_date,
        CowMilkRecord.cow_id.in_([m["cow_id"] for m in matched] or [0]))}
    added = updated = skipped = 0
    for m in matched:
        kg = Decimal(m["kg"])
        rec = existing.get(m["cow_id"])
        if rec is None:
            db.session.add(CowMilkRecord(cow_id=m["cow_id"], record_date=record_date, kg=kg,
                                         source=CowMilkRecord.SOURCE_IMPORT,
                                         created_by_id=user_id))
            added += 1
        elif overwrite:
            rec.kg = kg
            rec.source = CowMilkRecord.SOURCE_IMPORT
            updated += 1
        else:
            skipped += 1
    return added, updated, skipped
