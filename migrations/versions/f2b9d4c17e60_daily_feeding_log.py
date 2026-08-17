"""TICKET-3: daily feeding log — recipe feed vs additions from stores

Separates what the tank supplies (the vet's recipe, already mixed) from what the
worker tips in at the trough straight from general inventory (سيلاج، تبن، دريس،
قش). Both are deducted from their own stock; they meet only in the meal's cost.

No backfill: there is no historical record of which additions went in with which
meal — that information was never captured, which is the whole reason for the
ticket. Feeding sessions start from the day this ships. Past feed withdrawals
remain in feed_tank_movements and still count toward cost as before.

Revision ID: f2b9d4c17e60
Revises: e6f3c81a09d2
Create Date: 2026-08-17 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f2b9d4c17e60'
down_revision = 'e6f3c81a09d2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'group_feed_allowances',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('ingredient_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['cattle_groups.id'], name='fk_allowance_group'),
        sa.ForeignKeyConstraint(['ingredient_id'], ['ingredients.id'], name='fk_allowance_ingredient'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'ingredient_id', name='uq_group_allowance'),
    )
    with op.batch_alter_table('group_feed_allowances', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_group_feed_allowances_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_group_feed_allowances_ingredient_id'), ['ingredient_id'], unique=False)

    op.create_table(
        'feeding_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('session_date', sa.Date(), nullable=False),
        sa.Column('meal', sa.String(length=20), nullable=False),
        sa.Column('feed_qty', sa.Numeric(precision=14, scale=3), nullable=False, server_default='0'),
        sa.Column('feed_unit_cost', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0'),
        sa.Column('feed_cost', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'),
        sa.Column('additions_cost', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'),
        sa.Column('total_cost', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['cattle_groups.id'], name='fk_feeding_group'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_feeding_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('feeding_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feeding_sessions_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_feeding_sessions_session_date'), ['session_date'], unique=False)

    op.create_table(
        'feeding_additions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('ingredient_id', sa.Integer(), nullable=False),
        sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column('unit_cost', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0'),
        sa.Column('total_cost', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['session_id'], ['feeding_sessions.id'], name='fk_addition_session'),
        sa.ForeignKeyConstraint(['ingredient_id'], ['ingredients.id'], name='fk_addition_ingredient'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('feeding_additions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feeding_additions_session_id'), ['session_id'], unique=False)


def downgrade():
    with op.batch_alter_table('feeding_additions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feeding_additions_session_id'))
    op.drop_table('feeding_additions')

    with op.batch_alter_table('feeding_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feeding_sessions_session_date'))
        batch_op.drop_index(batch_op.f('ix_feeding_sessions_group_id'))
    op.drop_table('feeding_sessions')

    with op.batch_alter_table('group_feed_allowances', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_group_feed_allowances_ingredient_id'))
        batch_op.drop_index(batch_op.f('ix_group_feed_allowances_group_id'))
    op.drop_table('group_feed_allowances')
