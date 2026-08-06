"""ASSISTANT: add ai_usage_logs

Every AI question is logged (success or failure) so the daily per-user limit and
the monthly budget kill-switch have something to count against.

Revision ID: e6f3c81a09d2
Revises: d4a7b21c6e83
Create Date: 2026-08-06 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e6f3c81a09d2'
down_revision = 'd4a7b21c6e83'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ai_usage_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_usd', sa.Numeric(precision=10, scale=6), nullable=False,
                  server_default='0'),
        sa.Column('model', sa.String(length=50), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('asked_on', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_aiusage_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('ai_usage_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_usage_logs_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_usage_logs_asked_on'), ['asked_on'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_usage_logs_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('ai_usage_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_usage_logs_created_at'))
        batch_op.drop_index(batch_op.f('ix_ai_usage_logs_asked_on'))
        batch_op.drop_index(batch_op.f('ix_ai_usage_logs_user_id'))
    op.drop_table('ai_usage_logs')
