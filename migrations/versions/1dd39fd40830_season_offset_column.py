"""SEASON-OFFSET — cows.season_count_offset

Revision ID: 1dd39fd40830
Revises: 21df6569fbff
Create Date: 2026-09-12 16:30:00.000000

Adds a per-cow integer offset so pre-system calvings can be
backfilled without inventing Birth rows. `Cow.season_count` now
returns `offset + COUNT(births)`; every existing row auto-gets
0 via the DDL server_default (no explicit UPDATE needed).

Postgres-safe (per postgres-safe-migration-seeds memory): plain
integer server_default="0" parses on both SQLite and Postgres
without boolean gymnastics; no `IS :param` bindings in this
migration.
"""
from alembic import op
import sqlalchemy as sa


revision = '1dd39fd40830'
down_revision = '21df6569fbff'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cows', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'season_count_offset', sa.Integer(), nullable=False,
            server_default='0',
        ))


def downgrade():
    with op.batch_alter_table('cows', schema=None) as batch_op:
        batch_op.drop_column('season_count_offset')
