"""PHASE 8c — renumber COA to match Ibrahim's spec

Revision ID: b2a178b2c19f
Revises: 8368af18fdbe
Create Date: 2026-09-03

Pure code-column rewrite on `coa_accounts`. `JournalLine` holds
`account_id` (a foreign key), NOT the code string, so no JEs are
touched. The Python-side constants in `services/autoposting.py`,
`services/checks.py`, and `services/assets.py` all flip in the same
commit so a re-run of any autoposter after the migration lands on the
correctly-renumbered leaf.

Also inserts two new accounts the spec asks for that we never had:
- `2040` قروض (loans)
- `3030` مسحوبات صاحب العمل (owner draws)
"""
import sqlalchemy as sa
from alembic import op

revision = "b2a178b2c19f"
down_revision = "8368af18fdbe"
branch_labels = None
depends_on = None


# (old_code, new_code) pairs — every row that moves. Kept as a tuple
# list rather than a dict so the downgrade can walk it in reverse.
RENUMBER_MAP = [
    # Assets — rename the "current assets" sub-header from 1100 to 1000
    # so the number 1100 is free for customer receivables per spec.
    ("1100", "1000"),
    ("1110", "1010"),
    ("1120", "1020"),
    ("1130", "1030"),
    ("1300", "1100"),
    ("1500", "1300"),
    ("1510", "1310"),
    ("1520", "1320"),
    # Liabilities
    ("2100", "2010"),
    ("2110", "2020"),
    ("2200", "2030"),
    ("2900", "2090"),
    # Equity
    ("3100", "3010"),
    ("3200", "3020"),
    ("3900", "3090"),
    # Revenue
    ("4100", "4010"),
    ("4200", "4020"),
    ("4900", "4090"),
    # Expenses
    ("5100", "5010"),
    ("5400", "5020"),
    ("5200", "5030"),
    ("5300", "5040"),
    ("5310", "5050"),
    ("5320", "5060"),
    ("5600", "5070"),
    ("5500", "5075"),
    ("5900", "5080"),
]


def upgrade():
    # The rewrite is done via a temporary staging code so the UNIQUE
    # constraint on `coa_accounts.code` doesn't fire mid-flight when
    # two rows would briefly share the same code.
    conn = op.get_bind()
    for old, _new in RENUMBER_MAP:
        conn.execute(
            sa.text("UPDATE coa_accounts SET code = :tmp WHERE code = :old"),
            {"tmp": f"__TMP__{old}", "old": old},
        )
    for old, new in RENUMBER_MAP:
        conn.execute(
            sa.text("UPDATE coa_accounts SET code = :new WHERE code = :tmp"),
            {"new": new, "tmp": f"__TMP__{old}"},
        )

    # Insert the two accounts Ibrahim's spec asks for that we never had:
    # 2040 قروض and 3030 مسحوبات صاحب العمل. Raw SQL so alembic's own
    # transaction handles it — a nested app-context seed_default_coa()
    # call would deadlock on SQLite ("database is locked") because the
    # DDL txn isn't committed yet.
    liab_parent = conn.execute(sa.text(
        "SELECT id FROM coa_accounts WHERE code = '2'"
    )).scalar()
    equity_parent = conn.execute(sa.text(
        "SELECT id FROM coa_accounts WHERE code = '3'"
    )).scalar()

    for code, name_ar, name_en, atype, side, parent_id in [
        ("2040", "قروض", "Loans", "LIABILITY", "CREDIT", liab_parent),
        ("3030", "مسحوبات صاحب العمل", "Owner Draws", "EQUITY", "CREDIT", equity_parent),
    ]:
        exists = conn.execute(sa.text(
            "SELECT 1 FROM coa_accounts WHERE code = :c"
        ), {"c": code}).scalar()
        if not exists:
            conn.execute(sa.text("""
                INSERT INTO coa_accounts
                    (code, name, name_en, type, normal_side, parent_id,
                     is_postable, is_active, created_at)
                VALUES
                    (:code, :name, :name_en, :type, :side, :parent,
                     1, 1, CURRENT_TIMESTAMP)
            """), {
                "code": code, "name": name_ar, "name_en": name_en,
                "type": atype, "side": side, "parent": parent_id,
            })


def downgrade():
    conn = op.get_bind()
    for _old, new in RENUMBER_MAP:
        conn.execute(
            sa.text("UPDATE coa_accounts SET code = :tmp WHERE code = :new"),
            {"tmp": f"__TMP__{new}", "new": new},
        )
    for old, new in RENUMBER_MAP:
        conn.execute(
            sa.text("UPDATE coa_accounts SET code = :old WHERE code = :tmp"),
            {"old": old, "tmp": f"__TMP__{new}"},
        )
