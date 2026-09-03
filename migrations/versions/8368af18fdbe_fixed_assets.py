"""PHASE 8b — fixed_assets + depreciation_postings tables + COA leaves

Revision ID: 8368af18fdbe
Revises: f23067e3dcda
Create Date: 2026-09-03

Numbering (1510, 1520, 5600) fits the current scheme; phase 8c
renumbers them to Ibrahim's 1310/1320/5070.
"""
import sqlalchemy as sa
from alembic import op

revision = "8368af18fdbe"
down_revision = "f23067e3dcda"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fixed_assets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("category", sa.String(20), nullable=False,
                  server_default=sa.text("'equipment'")),
        sa.Column("purchase_date", sa.Date, nullable=False),
        sa.Column("purchase_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("salvage_value", sa.Numeric(14, 2), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("useful_life_months", sa.Integer, nullable=False),
        sa.Column("accumulated_depreciation", sa.Numeric(14, 2), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("treasury_account_id", sa.Integer,
                  sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("supplier_id", sa.Integer,
                  sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'active'")),
        sa.Column("disposed_on", sa.Date, nullable=True),
        sa.Column("disposal_notes", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_archived", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint(
            "category IN ('equipment', 'machinery', 'other')",
            name="ck_asset_category",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disposed', 'archived')",
            name="ck_asset_status",
        ),
    )
    with op.batch_alter_table("fixed_assets") as batch:
        batch.create_index("ix_fixed_assets_purchase_date", ["purchase_date"])
        batch.create_index("ix_fixed_assets_status", ["status"])

    op.create_table(
        "depreciation_postings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("asset_id", sa.Integer,
                  sa.ForeignKey("fixed_assets.id"), nullable=False),
        sa.Column("period_month", sa.Date, nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("je_id", sa.Integer,
                  sa.ForeignKey("journal_entries.id"), nullable=True),
        sa.Column("posted_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("posted_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("asset_id", "period_month",
                            name="uq_dep_per_asset_month"),
    )
    with op.batch_alter_table("depreciation_postings") as batch:
        batch.create_index("ix_dep_postings_asset_id", ["asset_id"])
        batch.create_index("ix_dep_postings_period_month", ["period_month"])

    # Seed the three new COA leaves via the coa_seed service (idempotent).
    try:
        from flask import current_app
        current_app.name  # noqa: B018
        from app.services.coa_seed import seed_default_coa
        from app.extensions import db
        seed_default_coa()
        db.session.commit()
    except RuntimeError:
        pass


def downgrade():
    with op.batch_alter_table("depreciation_postings") as batch:
        batch.drop_index("ix_dep_postings_period_month")
        batch.drop_index("ix_dep_postings_asset_id")
    op.drop_table("depreciation_postings")
    with op.batch_alter_table("fixed_assets") as batch:
        batch.drop_index("ix_fixed_assets_status")
        batch.drop_index("ix_fixed_assets_purchase_date")
    op.drop_table("fixed_assets")
