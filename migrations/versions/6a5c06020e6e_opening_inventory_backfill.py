"""PHASE 6 (3/3) — backfill opening-inventory JEs for existing stock

Every ingredient with `current_qty > 0` and no existing opening JE gets
one posted here: DR (category leaf) / CR 3900 أرصدة افتتاحية, valued at
`current_qty * avg_cost`. Idempotent — a re-run skips ingredients that
already have an entry.

Runs via the Flask app so it can call `post_journal`, which needs the
model registry + `db.session`. Alembic supplies the connection but the
model layer is what we want here — running raw SQL for a double-entry
posting would duplicate the ledger service.

Revision ID: 6a5c06020e6e
Revises: a9771d70c528
Create Date: 2026-09-03
"""
from alembic import op  # noqa: F401  (imported for symmetry / IDE)

revision = "6a5c06020e6e"
down_revision = "a9771d70c528"
branch_labels = None
depends_on = None


def upgrade():
    # Deferred imports: alembic loads this module before flask app context
    # exists, and we want the model registry only when the DDL step runs.
    from flask import current_app
    from app.extensions import db
    from app.services.opening_inventory import backfill_missing

    try:
        # Under `flask db upgrade`, an app context is already active. If the
        # migration is being applied from a raw alembic invocation (rare
        # here), fall through — the ledger service needs the app.
        current_app.name  # noqa: B018
    except RuntimeError:
        return  # nothing safe to do without an app context

    posted, total = backfill_missing()
    db.session.commit()
    print(f"[opening-inventory backfill] posted {posted} JEs, total {total} EGP")


def downgrade():
    # Reversing a data backfill safely means removing exactly the JEs the
    # upgrade created — identified by source_type — and letting the JE
    # service handle the delete cascade of lines.
    from app.extensions import db
    from app.models.accounting import JournalEntry
    from app.services.opening_inventory import SOURCE_TYPE

    for je in JournalEntry.query.filter_by(source_type=SOURCE_TYPE).all():
        db.session.delete(je)
    db.session.commit()
