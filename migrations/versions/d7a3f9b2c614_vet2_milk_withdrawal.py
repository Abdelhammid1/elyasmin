"""VET-2 — vet_records.withdrawal_until

Revision ID: d7a3f9b2c614
Revises: c5d2e8f1a7b3
Create Date: 2026-10-19 22:00:00.000000

Last day a treated / vaccinated cow's milk has to be thrown away.
Existing rows stay NULL (no withdrawal).
"""
from alembic import op
import sqlalchemy as sa


revision = 'd7a3f9b2c614'
down_revision = 'c5d2e8f1a7b3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vet_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('withdrawal_until', sa.Date(), nullable=True))
        batch_op.create_index(batch_op.f('ix_vet_records_withdrawal_until'),
                              ['withdrawal_until'], unique=False)


def downgrade():
    with op.batch_alter_table('vet_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vet_records_withdrawal_until'))
        batch_op.drop_column('withdrawal_until')
