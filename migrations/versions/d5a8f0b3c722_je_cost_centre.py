"""PHASE 2 — cost centre tag on journal lines

Revision ID: d5a8f0b3c722
Revises: c1f2d3e40a11
Create Date: 2026-09-03

Nullable FK cattle_groups(id) on journal_lines. No backfill — every line
that landed under phase 1's backfill was posted before the concept existed,
and there's no reliable way to guess a herd group from an old JE. New
autoposts tag going forward; the un-tagged bucket is a legitimate slice
of its own on the milk-cost-by-group report.
"""
import sqlalchemy as sa
from alembic import op

revision = "d5a8f0b3c722"
down_revision = "c1f2d3e40a11"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("journal_lines") as b:
        b.add_column(sa.Column("cost_center_id", sa.Integer, nullable=True))
        b.create_foreign_key(
            "fk_journal_lines_cost_center",
            "cattle_groups", ["cost_center_id"], ["id"],
        )
        b.create_index("ix_journal_lines_cost_center", ["cost_center_id"])


def downgrade():
    with op.batch_alter_table("journal_lines") as b:
        b.drop_index("ix_journal_lines_cost_center")
        b.drop_constraint("fk_journal_lines_cost_center", type_="foreignkey")
        b.drop_column("cost_center_id")
