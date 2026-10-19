"""HERD-10 — cow_movements.event_id

Revision ID: 71ff6318bcba
Revises: 52757ed612ee
Create Date: 2026-10-19 15:00:00.000000

Links a group move to the breeding event that made it, so deleting
the event can undo the move. Existing rows stay NULL.
"""
from alembic import op
import sqlalchemy as sa


revision = '71ff6318bcba'
down_revision = '52757ed612ee'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cow_movements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('event_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_cow_movements_event_id'),
                              ['event_id'], unique=False)
        batch_op.create_foreign_key('fk_cow_movements_event',
                                    'breeding_events', ['event_id'], ['id'])


def downgrade():
    with op.batch_alter_table('cow_movements', schema=None) as batch_op:
        batch_op.drop_constraint('fk_cow_movements_event', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_cow_movements_event_id'))
        batch_op.drop_column('event_id')
