"""SALES-1 Part 1 — ingredient_categories.is_sellable

Revision ID: 9aa0d0eeda08
Revises: 428e1f54a635
Create Date: 2026-09-12 14:00:00.000000

Adds one boolean column so admins can flag a category as
sellable. Categories with the flag on drive the item picker on
the new SalesInvoice line editor. No data backfill: every
existing category defaults to False (server_default="0"), which
is Postgres-safe — SQLite reads "0" as False and Postgres reads
it as boolean false too when the column is declared BOOLEAN with
a DEFAULT literal.
"""
from alembic import op
import sqlalchemy as sa


revision = '9aa0d0eeda08'
down_revision = '428e1f54a635'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ingredient_categories', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'is_sellable', sa.Boolean(), nullable=False,
            server_default=sa.text('false'),
        ))


def downgrade():
    with op.batch_alter_table('ingredient_categories', schema=None) as batch_op:
        batch_op.drop_column('is_sellable')
