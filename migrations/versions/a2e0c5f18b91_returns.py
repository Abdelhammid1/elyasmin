"""PHASE 5 — sales & purchase returns (credit / debit notes)

Revision ID: a2e0c5f18b91
Revises: f9e3d2a5b7c4
Create Date: 2026-09-03

Two mirror tables. Both link to their party (customer / supplier) and
optionally to an invoice. mode='credit' reduces the party balance;
mode='cash' moves money via a treasury account (nullable, required by
the create route when mode is cash — the DB itself keeps it optional so
old rows or a legacy import isn't refused).
"""
import sqlalchemy as sa
from alembic import op

revision = "a2e0c5f18b91"
down_revision = "f9e3d2a5b7c4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sales_returns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("customer_id", sa.Integer,
                  sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("invoice_id", sa.Integer,
                  sa.ForeignKey("milk_invoices.id"), nullable=True, index=True),
        sa.Column("return_date", sa.Date, nullable=False, index=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("mode", sa.String(10), nullable=False, server_default="credit"),
        sa.Column("treasury_account_id", sa.Integer,
                  sa.ForeignKey("accounts.id"), nullable=True, index=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_archived", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_table(
        "purchase_returns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("supplier_id", sa.Integer,
                  sa.ForeignKey("suppliers.id"), nullable=False, index=True),
        sa.Column("invoice_id", sa.Integer,
                  sa.ForeignKey("purchase_invoices.id"), nullable=True, index=True),
        sa.Column("return_date", sa.Date, nullable=False, index=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("mode", sa.String(10), nullable=False, server_default="credit"),
        sa.Column("treasury_account_id", sa.Integer,
                  sa.ForeignKey("accounts.id"), nullable=True, index=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_archived", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade():
    op.drop_table("purchase_returns")
    op.drop_table("sales_returns")
