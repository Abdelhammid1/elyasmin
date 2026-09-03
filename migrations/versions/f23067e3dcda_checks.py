"""PHASE 8a — checks table + 1130 شيكات تحت التحصيل + 2110 شيكات تحت الدفع

Revision ID: f23067e3dcda
Revises: 6a5c06020e6e
Create Date: 2026-09-03

The 1130/2110 numbering fits our current scheme. Phase 8c renumbers
both alongside every other code to match Ibrahim's 1030/2020.
"""
import sqlalchemy as sa
from alembic import op

revision = "f23067e3dcda"
down_revision = "6a5c06020e6e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "checks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("customer_id", sa.Integer,
                  sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("supplier_id", sa.Integer,
                  sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("check_number", sa.String(60), nullable=False),
        sa.Column("bank_name", sa.String(120), nullable=False),
        sa.Column("drawer_name", sa.String(120), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("issue_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(10), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("cleared_on", sa.Date, nullable=True),
        sa.Column("bounced_on", sa.Date, nullable=True),
        sa.Column("treasury_account_id", sa.Integer,
                  sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("related_ref", sa.String(120), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_archived", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint(
            "(customer_id IS NOT NULL AND supplier_id IS NULL) OR "
            "(customer_id IS NULL AND supplier_id IS NOT NULL)",
            name="ck_check_one_party",
        ),
        sa.CheckConstraint(
            "direction IN ('received', 'issued')",
            name="ck_check_direction",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'cleared', 'bounced')",
            name="ck_check_status",
        ),
    )
    with op.batch_alter_table("checks") as batch:
        batch.create_index("ix_checks_direction", ["direction"])
        batch.create_index("ix_checks_customer_id", ["customer_id"])
        batch.create_index("ix_checks_supplier_id", ["supplier_id"])
        batch.create_index("ix_checks_due_date", ["due_date"])
        batch.create_index("ix_checks_status", ["status"])
        batch.create_index("ix_checks_treasury_account_id",
                           ["treasury_account_id"])

    # Seed the two new COA leaves. Deferred to the seed service so the
    # migration doesn't need to duplicate the (code, name, type, parent)
    # tuple format.
    from app.services.coa_seed import ensure_check_accounts
    try:
        from flask import current_app
        current_app.name  # noqa: B018
        ensure_check_accounts()
    except RuntimeError:
        # No app context — nothing to seed (the boot-time coa_seed run
        # will pick these up too).
        pass


def downgrade():
    with op.batch_alter_table("checks") as batch:
        for idx in ("ix_checks_treasury_account_id", "ix_checks_status",
                    "ix_checks_due_date", "ix_checks_supplier_id",
                    "ix_checks_customer_id", "ix_checks_direction"):
            batch.drop_index(idx)
    op.drop_table("checks")
    # We leave 1130/2110 in the COA — dropping them would need a JE-lines
    # scan to protect posted rows, and their presence is harmless.
