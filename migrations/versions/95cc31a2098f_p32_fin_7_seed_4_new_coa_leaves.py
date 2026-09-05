"""P32 FIN-7 seed 4 new coa leaves

Revision ID: 95cc31a2098f
Revises: 9b5a0ded2fae
Create Date: 2026-09-05 12:33:20.771072

Data-only migration. `seed_default_coa()` (app/services/coa_seed.py)
is idempotent but only runs at boot on a fresh DB — existing dev/prod
DBs need a one-shot INSERT for the four accounts FIN-7 added:

  1090  أمانات مدفوعة لأطراف أخرى     ASSET     under 1000
  2041  قروض قصيرة الأجل              LIABILITY under 2
  2042  قروض طويلة الأجل              LIABILITY under 2
  2050  أمانات مستلمة من الغير        LIABILITY under 2

The INSERTs are conditional (NOT EXISTS + parent lookup by code) so a
DB that already got these by any other path stays untouched. Portable
SQL — works on both SQLite and Postgres.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '95cc31a2098f'
down_revision = '9b5a0ded2fae'
branch_labels = None
depends_on = None


_NEW_ACCOUNTS = [
    # (code, name_ar, name_en, type, normal_side, parent_code)
    ("1090", "أمانات مدفوعة لأطراف أخرى", "Deposits Paid",
        "ASSET", "DEBIT", "1000"),
    ("2041", "قروض قصيرة الأجل", "Short-term Loans",
        "LIABILITY", "CREDIT", "2"),
    ("2042", "قروض طويلة الأجل", "Long-term Loans",
        "LIABILITY", "CREDIT", "2"),
    ("2050", "أمانات مستلمة من الغير", "Deposits Received",
        "LIABILITY", "CREDIT", "2"),
]


def upgrade():
    for code, name_ar, name_en, atype, side, parent_code in _NEW_ACCOUNTS:
        op.execute(f"""
            INSERT INTO coa_accounts
                (code, name, name_en, type, normal_side,
                 parent_id, is_postable, is_active, created_at)
            SELECT '{code}', '{name_ar}', '{name_en}',
                   '{atype}', '{side}',
                   p.id, 1, 1, CURRENT_TIMESTAMP
            FROM coa_accounts p
            WHERE p.code = '{parent_code}'
              AND NOT EXISTS (
                  SELECT 1 FROM coa_accounts WHERE code = '{code}'
              )
        """)


def downgrade():
    # Delete the four accounts we added — safe iff they carry no
    # posted lines. Existing dev/prod DBs that started using them will
    # need to zero-out any postings first before a rollback.
    for code, *_ in _NEW_ACCOUNTS:
        op.execute(f"DELETE FROM coa_accounts WHERE code = '{code}'")
