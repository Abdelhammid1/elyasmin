"""PHASE 11 (YAS-SET-1) — CompanyProfile table + seed the singleton row

Revision ID: 09a754998ae0
Revises: d7a3e0f39ae1
Create Date: 2026-09-03
"""
import sqlalchemy as sa
from alembic import op

revision = "09a754998ae0"
down_revision = "d7a3e0f39ae1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "company_profile",
        sa.Column("id", sa.Integer, primary_key=True),
        # public identity
        sa.Column("name", sa.String(150), nullable=False,
                  server_default="مزرعة الياسمين"),
        sa.Column("logo_path", sa.String(255), nullable=True),
        sa.Column("base_currency", sa.String(10), nullable=False,
                  server_default="EGP"),
        sa.Column("tax_rate_pct", sa.Numeric(5, 2), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("region", sa.String(120), nullable=True),
        # legal identity
        sa.Column("legal_name", sa.String(200), nullable=True),
        sa.Column("commercial_register_no", sa.String(60), nullable=True),
        sa.Column("tax_registration_no", sa.String(60), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        # bank info
        sa.Column("bank_account_holder", sa.String(150), nullable=True),
        sa.Column("bank_name", sa.String(150), nullable=True),
        sa.Column("bank_account_no", sa.String(60), nullable=True),
        sa.Column("bank_iban", sa.String(60), nullable=True),
        # operational
        sa.Column("weekend_days", sa.String(20), nullable=False,
                  server_default="fri,sat"),
        sa.Column("reminder_days_before_due", sa.Integer, nullable=False,
                  server_default=sa.text("3")),
        # numbering prefixes
        sa.Column("invoice_number_prefix_sale", sa.String(20), nullable=False,
                  server_default="INV"),
        sa.Column("invoice_number_prefix_purchase", sa.String(20), nullable=False,
                  server_default="PUR"),
        # audit
        sa.Column("updated_at", sa.DateTime, nullable=True,
                  server_default=sa.func.now()),
        sa.Column("updated_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
    )

    # Seed the singleton row so `CompanyProfile.current()` never has to
    # create-and-flush on a live request. All the defaults kick in via
    # the server_default clauses above.
    op.execute("""
        INSERT INTO company_profile (id, name) VALUES (1, 'مزرعة الياسمين')
    """)


def downgrade():
    op.drop_table("company_profile")
