"""HERD-7: herd alerts — which cows need a pregnancy check, drying, or
a move to waiting.

Built on HERD-6's `build_herd_rows`, so the dates (last insemination,
days pregnant, expected calving) are exactly the ones the herd list
shows. Alerts follow the cow's status: recording the matching event
changes her status (HERD-5) and she drops out on her own.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.finance import Setting
from app.models.herd import Cow
from app.services.herd_list import build_herd_rows

RULES = {
    "check": {
        "title": "محتاجة جس",
        "hint": "ملقحة وعدّى على آخر تلقيح {n} يوم أو أكتر",
        "days_label": "أيام من التلقيح",
        "event_type": "pregnancy_check",
        "action": "جس",
    },
    "dry": {
        "title": "محتاجة تجفيف",
        "hint": "عشار وعدّى على التلقيح {n} يوم أو أكتر",
        "days_label": "أيام العشار",
        "event_type": "drying",
        "action": "تجفيف",
    },
    "waiting": {
        "title": "محتاجة انتظار",
        "hint": "باقي على الولادة المتوقعة {n} يوم أو أقل",
        "days_label": "باقي على الولادة",
        "event_type": "waiting",
        "action": "انتظار",
    },
}

DEFAULTS = {
    Setting.KEY_HERD_ALERT_CHECK_DAYS: 40,
    Setting.KEY_HERD_ALERT_DRY_DAYS: 210,
    Setting.KEY_HERD_ALERT_WAITING_DAYS: 20,
    Setting.KEY_GESTATION_DAYS: 280,
}


def setting_int(key: str) -> int:
    return int(Setting.get_decimal(key, Decimal(DEFAULTS[key])))


def thresholds() -> dict[str, int]:
    return {
        "check": setting_int(Setting.KEY_HERD_ALERT_CHECK_DAYS),
        "dry": setting_int(Setting.KEY_HERD_ALERT_DRY_DAYS),
        "waiting": setting_int(Setting.KEY_HERD_ALERT_WAITING_DAYS),
    }


def build_alerts(today: date | None = None) -> dict[str, list[dict]]:
    today = today or date.today()
    t = thresholds()
    rows = [r for r in build_herd_rows({"life": "active"}, today=today)
            if r["cow"].gender == Cow.GENDER_FEMALE]

    check, dry, waiting = [], [], []
    for r in rows:
        status = r["status_code"]
        if status == Cow.BS_INSEM and r["last_insem"]:
            since = (today - r["last_insem"]).days
            if since >= t["check"]:
                check.append({**r, "days": since})
        if status in (Cow.BS_PREG, Cow.BS_PREG2) and r["days_preg"] is not None:
            if r["days_preg"] >= t["dry"]:
                dry.append({**r, "days": r["days_preg"]})
        if status in (Cow.BS_PREG, Cow.BS_PREG2, Cow.BS_DRY) and r["expected"]:
            left = (r["expected"] - today).days
            if left <= t["waiting"]:
                waiting.append({**r, "days": left})

    check.sort(key=lambda r: -r["days"])
    dry.sort(key=lambda r: -r["days"])
    waiting.sort(key=lambda r: r["days"])
    return {"check": check, "dry": dry, "waiting": waiting}


def alert_counts() -> dict[str, int]:
    return {k: len(v) for k, v in build_alerts().items()}
