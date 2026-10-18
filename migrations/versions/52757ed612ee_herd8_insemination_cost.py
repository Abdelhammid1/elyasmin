"""HERD-8 — insemination cost

Revision ID: 52757ed612ee
Revises: a67820860a42
Create Date: 2026-10-19 12:00:00.000000

- breeding_events: cost_source / cost_amount / cost_ingredient_id /
  cost_qty / cost_expense_id.
- CoA leaf 5025 تكاليف التلقيح (EXPENSE, under 5).
- Expense category "insemination" (system row).
- Ingredient category "سائل منوي" for semen straws.

Idempotent seeds (NOT EXISTS); booleans bound as Python True/False.
"""
from alembic import op
import sqlalchemy as sa


revision = '52757ed612ee'
down_revision = 'a67820860a42'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('breeding_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cost_source', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('cost_amount', sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column('cost_ingredient_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('cost_qty', sa.Numeric(10, 3), nullable=True))
        batch_op.add_column(sa.Column('cost_expense_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_breeding_events_cost_ingredient',
                                    'ingredients', ['cost_ingredient_id'], ['id'])
        batch_op.create_foreign_key('fk_breeding_events_cost_expense',
                                    'expenses', ['cost_expense_id'], ['id'])

    bind = op.get_bind()
    bind.execute(sa.text("""
        INSERT INTO coa_accounts
            (code, name, name_en, type, normal_side,
             parent_id, is_postable, is_active, created_at)
        SELECT '5025', :name, 'Insemination Cost', 'EXPENSE', 'DEBIT',
               p.id, :yes, :yes, CURRENT_TIMESTAMP
        FROM coa_accounts p
        WHERE p.code = '5'
          AND NOT EXISTS (SELECT 1 FROM coa_accounts WHERE code = '5025')
    """), {"name": "تكاليف التلقيح", "yes": True})

    bind.execute(sa.text("""
        INSERT INTO expense_categories
            (name, display_label, is_active, is_system, created_at)
        SELECT 'insemination', :label, :yes, :yes, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (SELECT 1 FROM expense_categories WHERE name = 'insemination')
    """), {"label": "تكاليف التلقيح", "yes": True})

    bind.execute(sa.text("""
        INSERT INTO ingredient_categories (name, is_active, is_sellable, created_at)
        SELECT :name, :yes, :no, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (SELECT 1 FROM ingredient_categories WHERE name = :name)
    """), {"name": "سائل منوي", "yes": True, "no": False})


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM ingredient_categories WHERE name = :name "
        "AND NOT EXISTS (SELECT 1 FROM ingredients WHERE category = :name)"
    ), {"name": "سائل منوي"})
    bind.execute(sa.text(
        "DELETE FROM expense_categories WHERE name = 'insemination' "
        "AND NOT EXISTS (SELECT 1 FROM expenses WHERE category = 'insemination')"
    ))
    bind.execute(sa.text(
        "DELETE FROM coa_accounts WHERE code = '5025' "
        "AND NOT EXISTS (SELECT 1 FROM journal_lines jl "
        "JOIN coa_accounts a ON a.id = jl.account_id WHERE a.code = '5025')"
    ))
    with op.batch_alter_table('breeding_events', schema=None) as batch_op:
        batch_op.drop_constraint('fk_breeding_events_cost_expense', type_='foreignkey')
        batch_op.drop_constraint('fk_breeding_events_cost_ingredient', type_='foreignkey')
        batch_op.drop_column('cost_expense_id')
        batch_op.drop_column('cost_qty')
        batch_op.drop_column('cost_ingredient_id')
        batch_op.drop_column('cost_amount')
        batch_op.drop_column('cost_source')
