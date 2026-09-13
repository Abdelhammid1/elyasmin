"""EXP-CAT — expense_categories table + backfill

Revision ID: 2394e7b04367
Revises: 1dd39fd40830
Create Date: 2026-09-13 12:00:00.000000

Three ops:
  1. Bump `expenses.category` width 40 → 80 so long Arabic
     custom labels don't truncate.
  2. Create `expense_categories` (id, name, display_label,
     is_active, is_system, created_at) — mirror of
     `ingredient_categories`.
  3. Seed the 8 built-ins (electricity, maintenance, rent, ...)
     as system rows; back-fill every DISTINCT `custom:*` value
     currently sitting on `expenses.category` as a user row so
     nothing typed pre-ticket is lost.

Postgres-safe (per postgres-safe-migration-seeds memory):
  - Boolean literals in raw SQL are `true` / `false`.
  - No `IS :param` bindings.
  - `LIKE 'custom:%'` + `SUBSTR(category, 8)` behave
    identically on SQLite and Postgres.
"""
from alembic import op
import sqlalchemy as sa


revision = '2394e7b04367'
down_revision = '1dd39fd40830'
branch_labels = None
depends_on = None


_SYSTEM_SEED = [
    # (name = the value written to Expense.category, display_label)
    ("electricity",       "كهرباء"),
    ("maintenance",       "صيانة"),
    ("rent",              "إيجار"),
    ("feed_purchase",     "شراء علف"),
    ("medicine_purchase", "شراء أدوية"),
    ("supplier_payment",  "دفعة مورد"),
    ("worker_wage",       "أجور عمالة"),
    ("other",             "أخرى"),
]


def upgrade():
    # 1. Widen the column BEFORE anything else — the seed +
    #    back-fill both push longer values in.
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.alter_column(
            'category',
            existing_type=sa.String(length=40),
            type_=sa.String(length=80),
            existing_nullable=False,
        )

    # 2. Create the table.
    op.create_table(
        'expense_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('display_label', sa.String(length=120),
                  nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),
        sa.Column('is_system', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name',
                            name='uq_expense_categories_name'),
    )
    with op.batch_alter_table('expense_categories',
                              schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_expense_categories_name'),
            ['name'], unique=False,
        )

    # 3a. Seed the 8 built-ins as system rows. Idempotent — NOT
    #     EXISTS keeps a re-run safe.
    for name, label in _SYSTEM_SEED:
        op.execute(sa.text("""
            INSERT INTO expense_categories
                (name, display_label, is_active, is_system,
                 created_at)
            SELECT :name, :label, true, true, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (
                SELECT 1 FROM expense_categories WHERE name = :name
            )
        """).bindparams(name=name, label=label))

    # 3b. Back-fill: every DISTINCT `custom:*` value the user
    #     has typed becomes a user row. Same NOT EXISTS guard so
    #     a rerun is a no-op.
    op.execute(sa.text("""
        INSERT INTO expense_categories
            (name, display_label, is_active, is_system,
             created_at)
        SELECT DISTINCT e.category, SUBSTR(e.category, 8),
               true, false, CURRENT_TIMESTAMP
        FROM expenses e
        WHERE e.category LIKE 'custom:%'
          AND NOT EXISTS (
              SELECT 1 FROM expense_categories c
              WHERE c.name = e.category
          )
    """))


def downgrade():
    with op.batch_alter_table('expense_categories',
                              schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f('ix_expense_categories_name')
        )
    op.drop_table('expense_categories')

    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.alter_column(
            'category',
            existing_type=sa.String(length=80),
            type_=sa.String(length=40),
            existing_nullable=False,
        )
