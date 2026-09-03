"""PHASE 4 — supplier payment allocation between SupplierPayment and PurchaseInvoice

Revision ID: f9e3d2a5b7c4
Revises: e7b0c8a91d3f
Create Date: 2026-09-03

Mirror of phase 3's payment_allocations table, other direction. No
backfill — existing supplier payments stay unallocated ("on account"),
same as their customer-side counterparts.
"""
import sqlalchemy as sa
from alembic import op

revision = "f9e3d2a5b7c4"
down_revision = "e7b0c8a91d3f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "supplier_payment_allocations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payment_id", sa.Integer,
                  sa.ForeignKey("supplier_payments.id"),
                  nullable=False, index=True),
        sa.Column("invoice_id", sa.Integer,
                  sa.ForeignKey("purchase_invoices.id"),
                  nullable=False, index=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("payment_id", "invoice_id",
                            name="uq_supplier_payment_invoice"),
    )


def downgrade():
    op.drop_table("supplier_payment_allocations")
