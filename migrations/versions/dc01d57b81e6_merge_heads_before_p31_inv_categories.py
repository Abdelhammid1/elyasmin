"""merge heads before P31 INV categories

Revision ID: dc01d57b81e6
Revises: 6a581acceefb, c4ac9623c865
Create Date: 2026-09-05 06:03:22.087711

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dc01d57b81e6'
down_revision = ('6a581acceefb', 'c4ac9623c865')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
