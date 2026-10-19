"""VET-1 — vet_catalog_items + vet_records

Revision ID: c5d2e8f1a7b3
Revises: b3e1c7d90a42
Create Date: 2026-10-19 20:00:00.000000

The editable vaccine / disease lists (seeded with common ones the
client can rename or disable) and one vet record per cow per entry.
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = 'c5d2e8f1a7b3'
down_revision = 'b3e1c7d90a42'
branch_labels = None
depends_on = None

_SEED = [
    ("vaccine", "حمى قلاعية", 180),
    ("vaccine", "جلد عقدي", 365),
    ("vaccine", "تسمم معوي (كلوستريديا)", 180),
    ("vaccine", "حمى الوادي المتصدع", 365),
    ("vaccine", "بروسيلا", None),
    ("disease", "التهاب ضرع", None),
    ("disease", "عرج", None),
    ("disease", "حمى اللبن", None),
    ("disease", "احتباس مشيمة", None),
    ("disease", "التهاب رحم", None),
    ("disease", "إسهال", None),
    ("disease", "التهاب رئوي", None),
]


def upgrade():
    catalog = op.create_table(
        'vet_catalog_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('repeat_days', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kind', 'name', name='uq_vet_catalog_kind_name'),
    )
    with op.batch_alter_table('vet_catalog_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vet_catalog_items_kind'), ['kind'], unique=False)

    op.create_table(
        'vet_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cow_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=True),
        sa.Column('record_date', sa.Date(), nullable=False),
        sa.Column('next_due', sa.Date(), nullable=True),
        sa.Column('dispense_id', sa.Integer(), nullable=True),
        sa.Column('batch', sa.String(length=32), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['cow_id'], ['cows.id']),
        sa.ForeignKeyConstraint(['item_id'], ['vet_catalog_items.id']),
        sa.ForeignKeyConstraint(['dispense_id'], ['medicine_dispenses.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('vet_records', schema=None) as batch_op:
        for col in ('cow_id', 'kind', 'item_id', 'record_date', 'next_due', 'dispense_id', 'batch'):
            batch_op.create_index(batch_op.f(f'ix_vet_records_{col}'), [col], unique=False)

    now = datetime.utcnow()
    # Python True, never 1 — Postgres booleans.
    op.bulk_insert(catalog, [
        {"kind": k, "name": n, "repeat_days": d, "is_active": True, "created_at": now}
        for k, n, d in _SEED
    ])


def downgrade():
    with op.batch_alter_table('vet_records', schema=None) as batch_op:
        for col in ('batch', 'dispense_id', 'next_due', 'record_date', 'item_id', 'kind', 'cow_id'):
            batch_op.drop_index(batch_op.f(f'ix_vet_records_{col}'))
    op.drop_table('vet_records')
    with op.batch_alter_table('vet_catalog_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vet_catalog_items_kind'))
    op.drop_table('vet_catalog_items')
