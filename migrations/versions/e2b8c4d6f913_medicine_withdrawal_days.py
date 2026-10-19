"""Medicine default withdrawal days — ingredients.withdrawal_days

Revision ID: e2b8c4d6f913
Revises: d7a3f9b2c614
Create Date: 2026-10-19 23:30:00.000000

Decided default for the open question on milk withdrawal: the days are
set once per medicine and filled into the vet record when it's used.
NULL = no withdrawal.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e2b8c4d6f913'
down_revision = 'd7a3f9b2c614'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ingredients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('withdrawal_days', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('ingredients', schema=None) as batch_op:
        batch_op.drop_column('withdrawal_days')
