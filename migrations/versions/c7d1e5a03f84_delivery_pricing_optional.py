"""TICKET-4: allow a milk delivery to be saved before it is priced

The client records a delivery first and the prices later, sometimes days later.
Drops NOT NULL on milk_deliveries.unit_price and .total_value; a NULL
total_value means "awaiting pricing".

Revision ID: c7d1e5a03f84
Revises: a1c4e8f92b31
Create Date: 2026-08-03 18:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d1e5a03f84'
down_revision = 'a1c4e8f92b31'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('milk_deliveries', schema=None) as batch_op:
        batch_op.alter_column(
            'unit_price',
            existing_type=sa.Numeric(precision=10, scale=3),
            nullable=True,
        )
        batch_op.alter_column(
            'total_value',
            existing_type=sa.Numeric(precision=14, scale=2),
            nullable=True,
        )


def downgrade():
    # Rows added while pricing was optional would violate the restored NOT NULL,
    # so zero them out first. This loses the "not priced yet" distinction — it is
    # the only way back to a non-null column.
    op.execute("UPDATE milk_deliveries SET unit_price = 0 WHERE unit_price IS NULL")
    op.execute("UPDATE milk_deliveries SET total_value = 0 WHERE total_value IS NULL")

    with op.batch_alter_table('milk_deliveries', schema=None) as batch_op:
        batch_op.alter_column(
            'total_value',
            existing_type=sa.Numeric(precision=14, scale=2),
            nullable=False,
        )
        batch_op.alter_column(
            'unit_price',
            existing_type=sa.Numeric(precision=10, scale=3),
            nullable=False,
        )
