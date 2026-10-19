"""HERD-11 — cow_milk_records

Revision ID: b3e1c7d90a42
Revises: 71ff6318bcba
Create Date: 2026-10-19 18:00:00.000000

Per-cow daily milk, imported from the parlor's daily Excel. Also the
setting for how many days the herd list averages over (default 7 is
applied in code, so no seed row).
"""
from alembic import op
import sqlalchemy as sa


revision = 'b3e1c7d90a42'
down_revision = '71ff6318bcba'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cow_milk_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cow_id', sa.Integer(), nullable=False),
        sa.Column('record_date', sa.Date(), nullable=False),
        sa.Column('kg', sa.Numeric(8, 2), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['cow_id'], ['cows.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cow_id', 'record_date', name='uq_cow_milk_day'),
    )
    with op.batch_alter_table('cow_milk_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cow_milk_records_cow_id'), ['cow_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_cow_milk_records_record_date'), ['record_date'], unique=False)


def downgrade():
    with op.batch_alter_table('cow_milk_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cow_milk_records_record_date'))
        batch_op.drop_index(batch_op.f('ix_cow_milk_records_cow_id'))
    op.drop_table('cow_milk_records')
