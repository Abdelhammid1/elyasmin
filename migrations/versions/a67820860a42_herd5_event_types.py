"""HERD-5 — breeding_event_types (group + status per event type)

Revision ID: a67820860a42
Revises: c2099cf54368
Create Date: 2026-10-18 22:00:00.000000

Event types become an editable table. Each type says which group
the cow moves to and which status she gets; the event form
pre-fills both. Seeds the 8 types from the client meeting and drops
`event_status_suggestions`, which these settings replace.

Postgres-safe: booleans bound as Python True/False, no `IS :param`.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a67820860a42'
down_revision = 'c2099cf54368'
branch_labels = None
depends_on = None


# (code, name, sort, group_type, target_status, has_result, positive, negative)
_TYPES = [
    ("insemination",      "تلقيح",        1, None,        "insem",   False, None,    None),
    ("pregnancy_check",   "جس",           2, None,        None,      True,  "preg",  "open"),
    ("pregnancy_confirm", "جس تأكيد",     3, None,        None,      True,  "preg2", "open"),
    ("drying",            "تجفيف",        4, "dry",       "dry",     False, None,    None),
    ("waiting",           "انتظار ولادة", 5, "pre_birth", "waiting", False, None,    None),
    ("calving",           "ولادة",        6, "milk",      "milking", False, None,    None),
    ("abortion",          "طراحة",        7, None,        "abort",   False, None,    None),
    ("weaning",           "فطام",         8, None,        "weaning", False, None,    None),
]

# HERD-3's suggestion rows, restored on downgrade.
_OLD_SUGGESTIONS = [
    ("insemination",    None,           "insem",   True,  0),
    ("pregnancy_check", "pregnant",     "preg",    True,  0),
    ("pregnancy_check", "pregnant",     "preg2",   False, 1),
    ("pregnancy_check", "not_pregnant", "open",    True,  0),
    ("pregnancy_check", "not_pregnant", "insem",   False, 1),
    ("drying",          None,           "dry",     True,  0),
    ("waiting",         None,           "waiting", True,  0),
    ("calving",         None,           "milking", True,  0),
    ("weaning",         None,           "weaning", True,  0),
    ("weaning",         None,           "heifer",  False, 1),
]


def upgrade():
    op.create_table(
        'breeding_event_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),
        sa.Column('is_system', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('target_group_id', sa.Integer(), nullable=True),
        sa.Column('target_status', sa.String(length=30), nullable=True),
        sa.Column('has_result', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('status_if_positive', sa.String(length=30), nullable=True),
        sa.Column('status_if_negative', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['target_group_id'], ['cattle_groups.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('breeding_event_types', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_breeding_event_types_code'),
                              ['code'], unique=True)

    bind = op.get_bind()
    insert = sa.text("""
        INSERT INTO breeding_event_types
            (code, name, sort_order, is_active, is_system, target_status,
             has_result, status_if_positive, status_if_negative, created_at)
        SELECT :code, :name, :sort, :active, :system, :status,
               :has_result, :pos, :neg, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (SELECT 1 FROM breeding_event_types WHERE code = :code)
    """)
    set_group = sa.text("""
        UPDATE breeding_event_types
        SET target_group_id = (
            SELECT id FROM cattle_groups
            WHERE type = :gtype AND is_archived = :archived
            ORDER BY id LIMIT 1
        )
        WHERE code = :code
    """)
    for code, name, sort, gtype, status, has_result, pos, neg in _TYPES:
        bind.execute(insert, {
            "code": code, "name": name, "sort": sort,
            "active": True, "system": True, "status": status,
            "has_result": has_result, "pos": pos, "neg": neg,
        })
        if gtype:
            bind.execute(set_group, {"gtype": gtype, "archived": False,
                                     "code": code})

    with op.batch_alter_table('event_status_suggestions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_status_suggestions_event_type'))
        batch_op.drop_index(batch_op.f('ix_event_status_suggestions_event_result'))
    op.drop_table('event_status_suggestions')


def downgrade():
    op.create_table(
        'event_status_suggestions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=30), nullable=False),
        sa.Column('event_result', sa.String(length=20), nullable=True),
        sa.Column('suggested_status', sa.String(length=30), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'),
                  nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_type', 'event_result', 'suggested_status',
                            name='uq_esm_event_status'),
    )
    with op.batch_alter_table('event_status_suggestions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_status_suggestions_event_result'),
                              ['event_result'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_status_suggestions_event_type'),
                              ['event_type'], unique=False)
    bind = op.get_bind()
    stmt = sa.text("""
        INSERT INTO event_status_suggestions
            (event_type, event_result, suggested_status, is_default, sort_order)
        VALUES (:t, :r, :s, :d, :o)
    """)
    for t, r, s, d, o in _OLD_SUGGESTIONS:
        bind.execute(stmt, {"t": t, "r": r, "s": s, "d": d, "o": o})

    with op.batch_alter_table('breeding_event_types', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_breeding_event_types_code'))
    op.drop_table('breeding_event_types')
