"""PHASE 3 — payment allocation between CustomerPayment and MilkInvoice

Revision ID: e7b0c8a91d3f
Revises: d5a8f0b3c722
Create Date: 2026-09-03

New payment_allocations table linking a slice of a customer payment to a
specific milk invoice. No backfill — old payments stay unallocated ("on
account"), which is the natural default for a payment recorded before the
allocation concept existed.
"""
import sqlalchemy as sa
from alembic import op

revision = "e7b0c8a91d3f"
down_revision = "d5a8f0b3c722"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_allocations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payment_id", sa.Integer,
                  sa.ForeignKey("customer_payments.id"),
                  nullable=False, index=True),
        sa.Column("invoice_id", sa.Integer,
                  sa.ForeignKey("milk_invoices.id"),
                  nullable=False, index=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("payment_id", "invoice_id",
                            name="uq_payment_invoice"),
    )


def downgrade():
    op.drop_table("payment_allocations")
