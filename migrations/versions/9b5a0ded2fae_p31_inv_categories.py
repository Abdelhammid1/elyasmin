"""P31 INV categories

Revision ID: 9b5a0ded2fae
Revises: dc01d57b81e6
Create Date: 2026-09-05 06:03:25.477123

New `ingredient_categories` table + a data-seed step that populates
it from every distinct value already sitting on `ingredients.category`
(both the built-in "feed"/"medicine" and every "custom:..." string).
This way the new /inventory/categories report shows every existing
category on the first load.

Autogen also noticed a spurious index-rename on depreciation_postings
(two indexes had been created under slightly different names in a
prior migration) — that's intentional cleanup and stays in the diff.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9b5a0ded2fae'
down_revision = 'dc01d57b81e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ingredient_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('ingredient_categories', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_ingredient_categories_name'),
            ['name'], unique=True,
        )

    # Seed the new table with every distinct category already used
    # by an ingredient row. `created_at` defaults are set via
    # CURRENT_TIMESTAMP so downgrade → upgrade cycles stay stable.
    op.execute("""
        INSERT INTO ingredient_categories (name, is_active, created_at)
        SELECT DISTINCT category, true, CURRENT_TIMESTAMP
        FROM ingredients
        WHERE category IS NOT NULL
    """)

    # Unrelated index-name cleanup that autogen flagged.
    with op.batch_alter_table('depreciation_postings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dep_postings_asset_id'))
        batch_op.drop_index(batch_op.f('ix_dep_postings_period_month'))
        batch_op.create_index(
            batch_op.f('ix_depreciation_postings_asset_id'),
            ['asset_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_depreciation_postings_period_month'),
            ['period_month'], unique=False,
        )


def downgrade():
    with op.batch_alter_table('depreciation_postings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_depreciation_postings_period_month'))
        batch_op.drop_index(batch_op.f('ix_depreciation_postings_asset_id'))
        batch_op.create_index(
            batch_op.f('ix_dep_postings_period_month'),
            ['period_month'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_dep_postings_asset_id'),
            ['asset_id'], unique=False,
        )

    with op.batch_alter_table('ingredient_categories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ingredient_categories_name'))

    op.drop_table('ingredient_categories')
