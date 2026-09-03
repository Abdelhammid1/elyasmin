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
    with op.batch_alter_table("ingredients") as batch:
        batch.add_column(sa.Column(
            "avg_cost", sa.Numeric(12, 4),
            nullable=False, server_default=sa.text("0"),
        ))
    # Seed avg_cost from last_price so a farm with existing stock keeps a
    # sensible valuation the moment the migration finishes. The opening
    # JE in commit 3 balances the ledger against this same starting point.
    op.execute("UPDATE ingredients SET avg_cost = last_price")


def downgrade():
    with op.batch_alter_table("ingredients") as batch:
        batch.drop_column("avg_cost")
