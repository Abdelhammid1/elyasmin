"""HERD-11 regression suite — per-cow daily milk from the parlor's Excel.

  1. Column guessing (incl. a two-row header like ALL HERD's
     "Avg / Milk"); unknown tags, bad and blank rows reported.
  2. Upload → preview → confirm writes records; herd list avg_milk.
  3. Re-import the same day: skipped without overwrite, updated with it.
  4. Non-xlsx refused; future date refused; bad token 404.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
import re

from openpyxl import Workbook

from app.extensions import db
from app.models.herd import CattleGroup, Cow, CowMilkRecord, CowMovement
from app.services.herd_list import build_herd_rows
from app.services.milk_import import build_preview, guess_columns, parse_sheet

_P = "98"  # tag prefix — numeric like the parlor's, unlikely to clash
_A, _B = _P + "0001", _P + "0002"


def _workbook(a_kg=25.5, b_kg=30) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.append(["Daily milk", None, date.today()])
    ws.append(["Cow No", "Group", "Avg Milk", "Milk Today"])
    ws.append([int(_A), 1, 24, a_kg])
    ws.append([int(_B), 1, 28, b_kg])
    ws.append([int(_P + "9999"), 1, 10, 12])    # not in the herd
    ws.append([int(_P + "0003"), 1, 10, "x"])   # bad number
    ws.append([int(_A), 1, 24, 99])             # duplicate
    ws.append(["T  5", None, None, None])       # total line, blank milk
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _seed(app):
    with app.app_context():
        g = CattleGroup.query.filter_by(type="milk", is_archived=False).first().id
        for tag in (_A, _B):
            db.session.add(Cow(ear_tag=tag, gender=Cow.GENDER_FEMALE, group_id=g,
                               status=Cow.STATUS_ACTIVE))
        db.session.commit()


def _cleanup(app):
    with app.app_context():
        for c in Cow.query.filter(Cow.ear_tag.like(_P + "%")).all():
            CowMilkRecord.query.filter_by(cow_id=c.id).delete()
            CowMovement.query.filter_by(cow_id=c.id).delete()
            db.session.delete(c)
        db.session.commit()


def test_guess_and_preview(app):
    _cleanup(app)
    _seed(app)
    try:
        with app.app_context():
            sheet = parse_sheet(_workbook())
            tag_col, milk_col = guess_columns(sheet.headers)
            assert sheet.headers[tag_col] == "Cow No"
            assert sheet.headers[milk_col] == "Milk Today"   # not the average
            p = build_preview(sheet, tag_col, milk_col, date.today())
            assert [m["tag"] for m in p.matched] == [_A, _B]
            assert p.unknown == [_P + "9999"]
            assert p.bad == [_P + "0003"]
            assert p.duplicates == [_A]
            assert p.blank == 1
        # two-row header: "Avg" over "Milk" → one title
        assert guess_columns(["Num", "Group", "Avg Milk"]) == (0, 2)
        assert guess_columns(["Num", "Group"]) == (0, None)
    finally:
        _cleanup(app)


def _upload(admin_client, day=None):
    r = admin_client.post("/herd/milk-import", data={
        "file": (_workbook(), "milk.xlsx"),
        "record_date": (day or date.today()).isoformat(),
    }, content_type="multipart/form-data")
    assert r.status_code in (302, 303), r.data[:300]
    return r.headers["Location"]


def test_import_and_average(admin_client, app):
    _cleanup(app)
    _seed(app)
    try:
        # yesterday from an earlier file, so the average spans two days
        with app.app_context():
            a = Cow.query.filter_by(ear_tag=_A).first()
            db.session.add(CowMilkRecord(cow_id=a.id, record_date=date.today() - timedelta(days=1),
                                         kg=Decimal("20.5")))
            db.session.commit()
        url = _upload(admin_client)
        body = admin_client.get(url).get_data(as_text=True)
        assert re.search(r'<option value="3"\s+selected', body)
        r = admin_client.post(url, data={"tag_col": "0", "milk_col": "3"})
        assert r.status_code in (302, 303)
        with app.app_context():
            a = Cow.query.filter_by(ear_tag=_A).first()
            assert CowMilkRecord.query.filter_by(cow_id=a.id, record_date=date.today()).one().kg \
                == Decimal("25.50")
            row = [x for x in build_herd_rows({"q": _A}) if x["tag"] == _A][0]
            assert row["avg_milk"] == 23.0
        assert admin_client.get(url).status_code == 404   # temp file gone
        detail = admin_client.get(f"/herd/{row['cow'].id}").get_data(as_text=True)
        assert "آخر 30 يوم" in detail
    finally:
        _cleanup(app)


def test_reimport_overwrite(admin_client, app):
    _cleanup(app)
    _seed(app)
    try:
        admin_client.post(_upload(admin_client), data={"tag_col": "0", "milk_col": "3"})
        # same day again with a new number: kept without overwrite …
        url = _upload(admin_client)
        admin_client.post(url, data={"tag_col": "0", "milk_col": "2"})
        with app.app_context():
            a = Cow.query.filter_by(ear_tag=_A).first()
            assert CowMilkRecord.query.filter_by(cow_id=a.id).one().kg == Decimal("25.50")
        # … replaced with it
        url = _upload(admin_client)
        admin_client.post(url, data={"tag_col": "0", "milk_col": "2", "overwrite": "1"})
        with app.app_context():
            a = Cow.query.filter_by(ear_tag=_A).first()
            assert CowMilkRecord.query.filter_by(cow_id=a.id).one().kg == Decimal("24.00")
    finally:
        _cleanup(app)


def test_bad_upload_date_and_token(admin_client, app):
    r = admin_client.post("/herd/milk-import", data={
        "file": (BytesIO(b"not excel"), "milk.csv"), "record_date": date.today().isoformat(),
    }, content_type="multipart/form-data")
    assert r.status_code == 200 and "xlsx" in r.get_data(as_text=True)
    r = admin_client.post("/herd/milk-import", data={
        "file": (_workbook(), "milk.xlsx"),
        "record_date": (date.today() + timedelta(days=1)).isoformat(),
    }, content_type="multipart/form-data")
    assert r.status_code == 200 and "المستقبل" in r.get_data(as_text=True)
    assert admin_client.get("/herd/milk-import/" + "z" * 32).status_code == 404
    assert admin_client.get("/herd/milk-import/" + "0" * 32).status_code == 404
