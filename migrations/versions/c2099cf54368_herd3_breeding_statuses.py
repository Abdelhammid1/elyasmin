"""HERD-3 — breeding_statuses table + remap to the client's list

Revision ID: c2099cf54368
Revises: b181bacc1e0a
Create Date: 2026-10-18 20:00:00.000000

The reproductive status used to be 9 hard-coded values on Cow.
The client sent his own list (STATUS.txt, 12 statuses) and we add
"milking" for a cow that has calved. This migration:

  1. Creates `breeding_statuses` and seeds the 13 rows.
  2. Remaps every stored old code on cows + breeding_status_changes.
  3. Wipes and reseeds `event_status_suggestions` with the new codes
     (an in-place remap would collide on its unique constraint —
     e.g. inseminated and heifer_insem both become insem).

Postgres-safe: booleans bound as Python True/False, no `IS :param`.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c2099cf54368'
down_revision = 'b181bacc1e0a'
branch_labels = None
depends_on = None


_STATUSES = [
    # (code, name, sort_order)
    ("calf",      "عجلة رضيعة", 1),
    ("b_calf",    "عجل رضيع",   2),
    ("weaning",   "فطام",       3),
    ("heifer",    "عجلة نامية", 4),
    ("fattening", "تسمين",      5),
    ("insem",     "ملقحة",      6),
    ("preg",      "عشار",       7),
    ("preg2",     "عشار مؤكد",  8),
    ("dry",       "جفاف",       9),
    ("waiting",   "انتظار",     10),
    ("milking",   "حليب",       11),
    ("abort",     "طراحة",      12),
    ("open",      "فارغة",      13),
]

# old code → new code ("nursing" is handled separately for cows
# because it depends on gender).
_REMAP = {
    "inseminated":  "insem",
    "pregnant":     "preg",
    "heifer_small": "calf",
    "heifer_grown": "heifer",
    "heifer_insem": "insem",
    "heifer_preg":  "preg",
    "nursing":      "calf",
    # "dry" and "waiting" keep the same code.
}

# (event_type, event_result-or-None, suggested_status, is_default, sort_order)
_SUGGESTIONS = [
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

_OLD_SUGGESTIONS = [
    ("insemination",    None,           "inseminated",  True,  0),
    ("insemination",    None,           "heifer_insem", False, 1),
    ("pregnancy_check", "pregnant",     "pregnant",     True,  0),
    ("pregnancy_check", "pregnant",     "heifer_preg",  False, 1),
    ("pregnancy_check", "not_pregnant", "waiting",      True,  0),
    ("pregnancy_check", "not_pregnant", "inseminated",  False, 1),
    ("drying",          None,           "dry",          True,  0),
    ("waiting",         None,           "waiting",      True,  0),
    ("waiting",         None,           "inseminated",  False, 1),
    ("calving",         None,           "nursing",      True,  0),
    ("calving",         None,           "waiting",      False, 1),
    ("weaning",         None,           "dry",          True,  0),
    ("weaning",         None,           "inseminated",  False, 1),
]


def _seed_suggestions(bind, rows):
    stmt = sa.text("""
        INSERT INTO event_status_suggestions
            (event_type, event_result, suggested_status, is_default, sort_order)
        VALUES (:event_type, :event_result, :suggested, :is_default, :sort_order)
    """)
    for event_type, event_result, suggested, is_default, sort_order in rows:
        bind.execute(stmt, {
            "event_type": event_type,
            "event_result": event_result,
            "suggested": suggested,
            "is_default": is_default,
            "sort_order": sort_order,
        })


def upgrade():
    op.create_table(
        'breeding_statuses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('breeding_statuses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_breeding_statuses_code'),
                              ['code'], unique=True)

    bind = op.get_bind()

    seed = sa.text("""
        INSERT INTO breeding_statuses (code, name, sort_order, is_active, created_at)
        SELECT :code, :name, :sort_order, :active, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (SELECT 1 FROM breeding_statuses WHERE code = :code)
    """)
    for code, name, sort_order in _STATUSES:
        bind.execute(seed, {"code": code, "name": name,
                            "sort_order": sort_order, "active": True})

    # Cows: nursing splits by gender (male → b_calf).
    bind.execute(sa.text(
        "UPDATE cows SET breeding_status = 'b_calf' "
        "WHERE breeding_status = 'nursing' AND gender = 'male'"
    ))
    upd_cow = sa.text("UPDATE cows SET breeding_status = :new "
                      "WHERE breeding_status = :old")
    upd_from = sa.text("UPDATE breeding_status_changes SET from_status = :new "
                       "WHERE from_status = :old")
    upd_to = sa.text("UPDATE breeding_status_changes SET to_status = :new "
                     "WHERE to_status = :old")
    for old, new in _REMAP.items():
        params = {"old": old, "new": new}
        bind.execute(upd_cow, params)
        bind.execute(upd_from, params)
        bind.execute(upd_to, params)

    bind.execute(sa.text("DELETE FROM event_status_suggestions"))
    _seed_suggestions(bind, _SUGGESTIONS)


def downgrade():
    bind = op.get_bind()
    back = {
        "insem": "inseminated", "preg": "pregnant", "preg2": "pregnant",
        "calf": "heifer_small", "b_calf": "nursing", "heifer": "heifer_grown",
    }
    # Codes that didn't exist before have nowhere to go.
    gone = ["weaning", "fattening", "milking", "abort", "open"]
    for new, old in back.items():
        p = {"old": old, "new": new}
        bind.execute(sa.text("UPDATE cows SET breeding_status = :old "
                             "WHERE breeding_status = :new"), p)
        bind.execute(sa.text("UPDATE breeding_status_changes SET from_status = :old "
                             "WHERE from_status = :new"), p)
        bind.execute(sa.text("UPDATE breeding_status_changes SET to_status = :old "
                             "WHERE to_status = :new"), p)
    for code in gone:
        bind.execute(sa.text("UPDATE cows SET breeding_status = NULL "
                             "WHERE breeding_status = :c"), {"c": code})

    bind.execute(sa.text("DELETE FROM event_status_suggestions"))
    _seed_suggestions(bind, _OLD_SUGGESTIONS)

    with op.batch_alter_table('breeding_statuses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_breeding_statuses_code'))
    op.drop_table('breeding_statuses')
