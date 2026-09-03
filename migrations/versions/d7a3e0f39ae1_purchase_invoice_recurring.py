"""PHASE 10 (YAS-UX-3) — is_recurring + recurrence_interval on PurchaseInvoice

Two flag columns for future scheduling. The user-visible "كرر" button
already works via the duplicate flow (no schedule); these columns
become meaningful when a scheduler is added later.

Revision ID: d7a3e0f39ae1
Revises: b2a178b2c19f
Create Date: 2026-09-03
"""
import sqlalchemy as sa
from alembic import op

revision = "d7a3e0f39ae1"
down_revision = "b2a178b2c19f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_invoices") as batch:
        batch.add_column(sa.Column(
            "is_recurring", sa.Boolean, nullable=False,
            server_default=sa.text("false"),
        ))
        batch.add_column(sa.Column(
            "recurrence_interval", sa.String(20), nullable=True,
        ))


def downgrade():
    with op.batch_alter_table("purchase_invoices") as batch:
        batch.drop_column("recurrence_interval")
        batch.drop_column("is_recurring")
