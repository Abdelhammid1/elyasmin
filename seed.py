"""Seed the database with the 5 fixed cattle groups and the first Admin user.

Usage:
    python seed.py
"""
import os
import sys

from app import create_app
from app.extensions import db
from app.models.auth import User
from app.models.finance import Setting
from app.models.herd import CattleGroup


GROUPS = [
    ("مجموعة الحليب", CattleGroup.TYPE_MILK, "الأبقار الحلابة (مصدر الإيراد)"),
    ("مجموعة الجفاف", CattleGroup.TYPE_DRY, "أبقار جافة قبل الولادة"),
    ("مجموعة انتظار الولادة", CattleGroup.TYPE_PRE_BIRTH, "الأبقار الحوامل قبل الوضع"),
    ("مجموعة الرضاعة", CattleGroup.TYPE_NURSING, "الإناث الصغيرة والمواليد الأنثوية"),
    ("مجموعة التسمين", CattleGroup.TYPE_FATTENING, "الذكور المخصصة للبيع"),
]


ADMIN_EMAIL = os.getenv("SEED_ADMIN_EMAIL", "admin@yasmin-farm.com")
ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "Admin@12345")
ADMIN_NAME = os.getenv("SEED_ADMIN_NAME", "مدير النظام")


def run() -> None:
    app = create_app()
    with app.app_context():
        # Groups
        for name, type_, desc in GROUPS:
            existing = CattleGroup.query.filter_by(name=name).first()
            if existing:
                print(f"[=] group exists: {name}")
                continue
            db.session.add(CattleGroup(name=name, type=type_, description=desc))
            print(f"[+] created group: {name}")

        # Admin
        admin = User.query.filter_by(email=ADMIN_EMAIL).first()
        if not admin:
            admin = User(
                email=ADMIN_EMAIL,
                full_name=ADMIN_NAME,
                role=User.ROLE_ADMIN,
                is_active=True,
            )
            admin.set_password(ADMIN_PASSWORD)
            db.session.add(admin)
            print(f"[+] created admin user: {ADMIN_EMAIL}")
            print(f"    temporary password: {ADMIN_PASSWORD}")
        else:
            print(f"[=] admin exists: {ADMIN_EMAIL}")

        # Default settings (US-5.1 BR: 80/20 configurable + quality-price formula)
        DEFAULT_SETTINGS = [
            (Setting.KEY_COST_SPLIT_MILK_PCT, "80", "نسبة تحميل التكاليف على الحليب"),
            (Setting.KEY_COST_SPLIT_OTHERS_PCT, "20", "نسبة تحميل التكاليف على باقي المجموعات"),
            (Setting.KEY_QUALITY_PRICE_BASE, "6", "سعر أساس اللبن بالتحليل"),
            (Setting.KEY_QUALITY_PROTEIN_ADJ, "0.5", "زيادة السعر لكل +1% بروتين"),
            (Setting.KEY_QUALITY_BACTERIA_PENALTY, "0.25", "خصم لكل +100k بكتيريا"),
        ]
        for key, val, desc in DEFAULT_SETTINGS:
            if not db.session.get(Setting, key):
                db.session.add(Setting(key=key, value=val, description=desc))
                print(f"[+] setting: {key} = {val}")

        db.session.commit()
        print("\n✓ Seed complete.")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"[x] seed failed: {exc}", file=sys.stderr)
        raise
