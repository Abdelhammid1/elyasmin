"""HERD-9 regression suite — import from the parlor's ALL HERD export.

A small workbook is built in memory with the parlor layout (title row,
two header rows, cows, total line).

  1. Parser: duplicates / existing tags / total line handled.
  2. Full flow: upload → preview → confirm creates cows with status,
     group, season, gender and history; herd list shows DIM and days
     pregnant.
  3. Unmapped group → refused, nothing imported.
  4. Non-xlsx refused; bad token → 404.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
import re

from openpyxl import Workbook

from app.extensions import db
from app.models.herd import (
    BreedingEvent, BreedingStatusChange, CattleGroup, Cow, CowMovement,
)
from app.services.herd_import import parse_workbook
from app.services.herd_list import build_herd_rows

_P = "99"  # tag prefix — numeric like the real file, unlikely to clash
_EXISTING = _P + "0001"


def _workbook() -> BytesIO:
    today = datetime.now()
    wb = Workbook()
    ws = wb.active
    ws.append([None] * 13)
    ws.append(["ALL HERD", None, today])
    ws.append(["Num", "Birth", "Group", "AGE", "Status", "Laction",
               "Last Calve", "DIM", "Breeding", "Days", "Avg", "AGE", "Age"])
    ws.append([None, None, None, "Year", None, None, "Date", None, "Date",
               "Preg", "Milk", "Month", "DAYS"])
    ws.append([int(_P + "0102"), datetime(2020, 4, 24), 1, 6, "Preg2", 4,
               today - timedelta(days=161), 161, today - timedelta(days=109),
               109, 20.3, 71, 2180])
    ws.append([int(_P + "0105"), datetime(2020, 10, 1), 1, 5.5, "Insem", 4,
               today - timedelta(days=237), 237, today - timedelta(days=45),
               45, 31.7, 66, 2020])
    ws.append([int(_P + "1139"), datetime(2025, 11, 9), 8, 0.5, "B-Clf", 0,
               None, None, None, 0, 0, 5, 160])
    ws.append([int(_P + "0102"), datetime(2020, 4, 24), 1, 6, "Preg2", 4,
               None, None, None, 0, 0, 71, 2180])          # duplicate
    ws.append([int(_EXISTING), datetime(2021, 1, 1), 1, 5, "Dry", 3,
               None, None, None, 0, 0, 60, 1800])          # already in DB
    ws.append(["T  5", None, None, None, None, 1, None, None, None, None, None, None, None])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _cleanup(app):
    with app.app_context():
        for c in Cow.query.filter(Cow.ear_tag.like(_P + "%")).all():
            BreedingStatusChange.query.filter_by(cow_id=c.id).delete()
            BreedingEvent.query.filter_by(cow_id=c.id).delete()
            CowMovement.query.filter_by(cow_id=c.id).delete()
            db.session.delete(c)
        db.session.commit()


def _seed_existing(app):
    with app.app_context():
        db.session.add(Cow(ear_tag=_EXISTING, gender=Cow.GENDER_FEMALE,
                           group_id=CattleGroup.query.first().id,
                           status=Cow.STATUS_ACTIVE))
        db.session.commit()


def _groups(app):
    with app.app_context():
        milk = CattleGroup.query.filter_by(type="milk", is_archived=False).first().id
        nursing = CattleGroup.query.filter_by(type="nursing", is_archived=False).first().id
        return milk, nursing


def test_parser(app):
    _cleanup(app)
    _seed_existing(app)
    try:
        with app.app_context():
            r = parse_workbook(_workbook())
        assert r.total == 5
        assert [x["tag"] for x in r.rows] == [_P + "0102", _P + "0105", _P + "1139"]
        assert r.duplicates == [_P + "0102"]
        assert r.existing == [_EXISTING]
        assert r.groups == ["1", "8"]
        first = r.rows[0]
        assert first["birth"] == "2020-04-24" and first["lactation"] == 4
        assert first["last_insem"] == (date.today() - timedelta(days=109)).isoformat()
    finally:
        _cleanup(app)


def _upload(admin_client):
    r = admin_client.post("/herd/import",
                          data={"file": (_workbook(), "ALL_HERD.xlsx")},
                          content_type="multipart/form-data")
    assert r.status_code in (302, 303), r.data[:300]
    return r.headers["Location"]


def test_full_import(admin_client, app):
    _cleanup(app)
    _seed_existing(app)
    milk, nursing = _groups(app)
    try:
        preview_url = _upload(admin_client)
        body = admin_client.get(preview_url).get_data(as_text=True)
        assert 'name="group_1"' in body and 'name="group_8"' in body
        assert re.search(r'<option value="preg2"\s+selected', body)

        r = admin_client.post(preview_url, data={
            "group_1": str(milk), "group_8": str(nursing),
            "status_Preg2": "preg2", "status_Insem": "insem", "status_B-Clf": "b_calf",
        })
        assert r.status_code == 200
        assert "اتسجل" in r.get_data(as_text=True)

        with app.app_context():
            c = Cow.query.filter_by(ear_tag=_P + "0102").first()
            assert c.breeding_status == "preg2" and c.group_id == milk
            assert c.season_count == 4 and c.gender == Cow.GENDER_FEMALE
            calf = Cow.query.filter_by(ear_tag=_P + "1139").first()
            assert calf.gender == Cow.GENDER_MALE and calf.group_id == nursing
            assert BreedingStatusChange.query.filter_by(cow_id=c.id).count() == 1
            assert BreedingEvent.query.filter_by(cow_id=c.id).count() == 2
            # existing cow untouched, duplicate not added twice
            assert Cow.query.filter_by(ear_tag=_P + "0102").count() == 1
            row = [x for x in build_herd_rows({"q": _P + "0102"}) if x["tag"] == _P + "0102"][0]
            assert row["dim"] == 161
            assert row["days_preg"] == 109
        # the temp file is gone
        assert admin_client.get(preview_url).status_code == 404
    finally:
        _cleanup(app)


def test_unmapped_group_refused(admin_client, app):
    _cleanup(app)
    milk, _ = _groups(app)
    try:
        preview_url = _upload(admin_client)
        r = admin_client.post(preview_url, data={"group_1": str(milk)})  # group 8 missing
        assert r.status_code == 200
        with app.app_context():
            assert Cow.query.filter(Cow.ear_tag.like(_P + "%")).count() == 0
    finally:
        _cleanup(app)


def test_bad_upload_and_token(admin_client, app):
    r = admin_client.post("/herd/import",
                          data={"file": (BytesIO(b"not excel"), "herd.csv")},
                          content_type="multipart/form-data")
    assert r.status_code == 200 and "xlsx" in r.get_data(as_text=True)
    assert admin_client.get("/herd/import/" + "../" * 5 + "x").status_code == 404
    assert admin_client.get("/herd/import/" + "z" * 32).status_code == 404
