"""merge company_profile branch with leave_requests branch

Revision ID: 6a581acceefb
Revises: 09a754998ae0, 6d2848a312d1
Create Date: 2026-09-04 12:33:27.701593

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a581acceefb'
down_revision = ('09a754998ae0', '6d2848a312d1')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
