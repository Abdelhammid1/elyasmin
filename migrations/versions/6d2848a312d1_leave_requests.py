"""PHASE 15 (YAS-HR-1) — leave_requests table

Revision ID: 6d2848a312d1
Revises: b2a178b2c19f
Create Date: 2026-09-04
"""
import sqlalchemy as sa
from alembic import op

revision = "6d2848a312d1"
down_revision = "b2a178b2c19f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "leave_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("worker_id", sa.Integer,
                  sa.ForeignKey("workers.id"), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("status", sa.String(10), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("submitted_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("submitted_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("decided_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime, nullable=True),
        sa.Column("decision_note", sa.Text, nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_leave_status",
        ),
        sa.CheckConstraint(
            "end_date >= start_date",
            name="ck_leave_dates",
        ),
    )
    with op.batch_alter_table("leave_requests") as batch:
        batch.create_index("ix_leave_requests_worker_id", ["worker_id"])
        batch.create_index("ix_leave_requests_start_date", ["start_date"])
        batch.create_index("ix_leave_requests_status", ["status"])


def downgrade():
    with op.batch_alter_table("leave_requests") as batch:
        batch.drop_index("ix_leave_requests_status")
        batch.drop_index("ix_leave_requests_start_date")
        batch.drop_index("ix_leave_requests_worker_id")
    op.drop_table("leave_requests")
