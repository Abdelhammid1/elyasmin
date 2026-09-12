"""SALES-1 Part 3 — seed CoA leaves 4030 + 4096

Revision ID: 21df6569fbff
Revises: 709045bc1eab
Create Date: 2026-09-12 15:30:00.000000

Data-only, following the shape of 95cc31a2098f (idempotent NOT
EXISTS + parent lookup, Postgres-safe boolean literals `true`).
Two revenue leaves the auto-posting for a sales invoice needs:

  4030  مبيعات مخزون                        REVENUE  under 4
  4096  مكاسب/خسائر بيع الحيوانات            REVENUE  under 4

Both are also in DEFAULT_COA now so a fresh install gets them
via seed_default_coa(); this migration is for dev/prod DBs
already on the previous CoA.
"""
from alembic import op


revision = '21df6569fbff'
down_revision = '709045bc1eab'
branch_labels = None
depends_on = None


_NEW_ACCOUNTS = [
    # (code, name_ar, name_en, type, normal_side, parent_code)
    ("4030", "مبيعات مخزون", "Inventory Sales",
        "REVENUE", "CREDIT", "4"),
    ("4096", "مكاسب/خسائر بيع الحيوانات", "Gain/Loss on Livestock Sale",
        "REVENUE", "CREDIT", "4"),
]


def upgrade():
    for code, name_ar, name_en, atype, side, parent_code in _NEW_ACCOUNTS:
        op.execute(f"""
            INSERT INTO coa_accounts
                (code, name, name_en, type, normal_side,
                 parent_id, is_postable, is_active, created_at)
            SELECT '{code}', '{name_ar}', '{name_en}',
                   '{atype}', '{side}',
                   p.id, true, true, CURRENT_TIMESTAMP
            FROM coa_accounts p
            WHERE p.code = '{parent_code}'
              AND NOT EXISTS (
                  SELECT 1 FROM coa_accounts WHERE code = '{code}'
              )
        """)


def downgrade():
    for code, *_ in _NEW_ACCOUNTS:
        op.execute(f"DELETE FROM coa_accounts WHERE code = '{code}'")
