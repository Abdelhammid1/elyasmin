"""FEED-TANK: separate feed production from feed consumption

Adds feed_tanks + feed_tank_movements, then backfills every existing
non-archived feed run as a production movement PLUS a matching withdrawal on the
same date, at the same quantity and cost.

Why backfill both sides rather than starting the tanks from zero: the milk-cost
report now reads withdrawals instead of runs. With no backfill every historical
period would report zero feed cost, which reads as data loss in the client's
most important financial report. Booking production and withdrawal together
leaves the tanks empty (correct — that feed was eaten) while every historical
figure stays identical to what it was before this migration.

Revision ID: b8e2f0c94d17
Revises: c7d1e5a03f84
Create Date: 2026-08-04 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b8e2f0c94d17'
down_revision = 'c7d1e5a03f84'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feed_tanks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('current_qty', sa.Numeric(precision=14, scale=3), nullable=False,
                  server_default='0'),
        sa.Column('avg_cost_per_kg', sa.Numeric(precision=12, scale=3), nullable=False,
                  server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['cattle_groups.id'], name='fk_feedtank_group'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', name='uq_feedtank_group'),
    )
    with op.batch_alter_table('feed_tanks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feed_tanks_group_id'), ['group_id'], unique=True)

    op.create_table(
        'feed_tank_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tank_id', sa.Integer(), nullable=False),
        sa.Column('movement_type', sa.String(length=20), nullable=False),
        sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column('unit_cost', sa.Numeric(precision=12, scale=3), nullable=False,
                  server_default='0'),
        sa.Column('total_cost', sa.Numeric(precision=14, scale=2), nullable=False,
                  server_default='0'),
        sa.Column('ref_feed_run_id', sa.Integer(), nullable=True),
        sa.Column('moved_on', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['tank_id'], ['feed_tanks.id'], name='fk_tankmv_tank'),
        sa.ForeignKeyConstraint(['ref_feed_run_id'], ['feed_runs.id'], name='fk_tankmv_run'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_tankmv_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('feed_tank_movements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feed_tank_movements_tank_id'), ['tank_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_feed_tank_movements_movement_type'), ['movement_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_feed_tank_movements_moved_on'), ['moved_on'], unique=False)
        batch_op.create_index(batch_op.f('ix_feed_tank_movements_ref_feed_run_id'), ['ref_feed_run_id'], unique=False)

    # ------------------------------------------------------------------
    # BACKFILL — one tank per group that ever ran feed, then production +
    # withdrawal per historical run so the tanks end at zero.
    # ------------------------------------------------------------------
    conn = op.get_bind()

    conn.execute(sa.text("""
        INSERT INTO feed_tanks (group_id, current_qty, avg_cost_per_kg, updated_at)
        SELECT DISTINCT r.group_id, 0, 0, CURRENT_TIMESTAMP
        FROM feed_runs r
        WHERE r.is_archived = false
          AND NOT EXISTS (SELECT 1 FROM feed_tanks t WHERE t.group_id = r.group_id)
    """))

    conn.execute(sa.text("""
        INSERT INTO feed_tank_movements
            (tank_id, movement_type, qty, unit_cost, total_cost,
             ref_feed_run_id, moved_on, notes, created_at, created_by_id)
        SELECT t.id, 'production', r.total_weight_kg, r.cost_per_kg, r.total_cost,
               r.id, r.run_date, 'ترحيل تلقائي — تشغيل قديم قبل نظام الخزان',
               CURRENT_TIMESTAMP, r.created_by_id
        FROM feed_runs r
        JOIN feed_tanks t ON t.group_id = r.group_id
        WHERE r.is_archived = false
    """))

    conn.execute(sa.text("""
        INSERT INTO feed_tank_movements
            (tank_id, movement_type, qty, unit_cost, total_cost,
             ref_feed_run_id, moved_on, notes, created_at, created_by_id)
        SELECT t.id, 'withdrawal', -r.total_weight_kg, r.cost_per_kg, -r.total_cost,
               r.id, r.run_date, 'ترحيل تلقائي — استهلاك التشغيل القديم بالكامل',
               CURRENT_TIMESTAMP, r.created_by_id
        FROM feed_runs r
        JOIN feed_tanks t ON t.group_id = r.group_id
        WHERE r.is_archived = false
    """))


def downgrade():
    with op.batch_alter_table('feed_tank_movements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feed_tank_movements_ref_feed_run_id'))
        batch_op.drop_index(batch_op.f('ix_feed_tank_movements_moved_on'))
        batch_op.drop_index(batch_op.f('ix_feed_tank_movements_movement_type'))
        batch_op.drop_index(batch_op.f('ix_feed_tank_movements_tank_id'))
    op.drop_table('feed_tank_movements')

    with op.batch_alter_table('feed_tanks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feed_tanks_group_id'))
    op.drop_table('feed_tanks')
