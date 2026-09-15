"""SALES-3 — cow line pricing by weight

Revision ID: b181bacc1e0a
Revises: 2394e7b04367
Create Date: 2026-09-15 12:00:00.000000

Adds three columns on `sales_invoice_lines` so a cow line can
be priced by weight (weight_kg × price_per_kg) instead of the
current per-head fixed price. Only meaningful for cow lines;
the two nullable weight/price columns stay NULL for every other
kind.

  pricing_mode  VARCHAR(10) NOT NULL default 'per_head'
  weight_kg     NUMERIC(10, 3) NULL
  price_per_kg  NUMERIC(10, 2) NULL

Existing rows auto-backfill to `pricing_mode='per_head'` via
the DDL server_default, so nothing about their line_total
computation changes.

No effect on the cow lifecycle side of a sale (cow.status
STATUS_SOLD, cow.current_value zeroing, cow_book_value_snapshot,
AnimalSale mirror, JE close-out on 1400) — only the input for
line_total changes.

Postgres-safe (per postgres-safe-migration-seeds memory):
plain string server_default, no boolean literals, no
`IS :param` bindings.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b181bacc1e0a'
down_revision = '2394e7b04367'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sales_invoice_lines',
                              schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'pricing_mode', sa.String(length=10),
            nullable=False, server_default='per_head',
        ))
        batch_op.add_column(sa.Column(
            'weight_kg', sa.Numeric(10, 3), nullable=True,
        ))
        batch_op.add_column(sa.Column(
            'price_per_kg', sa.Numeric(10, 2), nullable=True,
        ))


def downgrade():
    with op.batch_alter_table('sales_invoice_lines',
                              schema=None) as batch_op:
        batch_op.drop_column('price_per_kg')
        batch_op.drop_column('weight_kg')
        batch_op.drop_column('pricing_mode')
