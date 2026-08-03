"""TICKET-1: supplier opening balance

Debt already owed to a supplier before the system started. Folded into
Supplier.balance_due so every existing balance readout picks it up.

Revision ID: a1c4e8f92b31
Revises: 78565d24d06b
Create Date: 2026-08-03 17:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c4e8f92b31'
down_revision = '78565d24d06b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'opening_balance',
                sa.Numeric(precision=14, scale=2),
                nullable=False,
                server_default='0',
            )
        )


def downgrade():
    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.drop_column('opening_balance')
