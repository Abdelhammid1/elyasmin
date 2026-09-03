"""PHASE 6 (1/3) — weighted-average cost on ingredients

Adds `avg_cost` to `ingredients` and backfills it from the running
`last_price`. `last_price` stays on the row as a reference-only display
value; from this migration on nothing in the ledger reads it.

Revision ID: 6c0d074ad3f1
Revises: a2e0c5f18b91
Create Date: 2026-09-03
"""
import sqlalchemy as sa
from alembic import op

revision = "6c0d074ad3f1"
down_revision = "a2e0c5f18b91"
branch_labels = None
depends_on = None


def upgrade():
    # avg_cost was moved into PHASE 1 (c1f2d3e40a11._add_early_columns) at
    # deploy time — the P1 backfill needed the column to exist before it
    # queries the full Ingredient model. This migration is now a documented
    # no-op kept for revision-history continuity; nothing left to do here.
    pass


def downgrade():
    with op.batch_alter_table("ingredients") as batch:
        batch.drop_column("avg_cost")
