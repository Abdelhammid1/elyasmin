"""PHASE 10 (YAS-ACC-1): reverse lookup from a source row to its JE.

The forward direction (JE → source) already lives in
`accounting/routes.py::_source_url`. This module adds the reverse so
any invoice / payment / dispense view can render a "عرض القيد
المحاسبي" button without route glue.

Registered as a jinja global in `app/__init__.py` so templates can
call it directly:
    {% set je = find_journal_entry_for('PurchaseInvoice', inv.id) %}
    {% if je %}<a href="…/{{ je.id }}">القيد</a>{% endif %}
"""
from typing import Optional


def find_journal_entry_for(source_type: str, source_id: int):
    """Return the most-recent active JE for this source, or None.

    Multiple JEs can share (source_type, source_id) if the autoposter
    re-ran (e.g. an invoice edit) — we return the newest one by id.
    """
    if not source_type or source_id is None:
        return None
    from app.models.accounting import JournalEntry
    return (
        JournalEntry.query
        .filter_by(source_type=source_type,
                   source_id=source_id,
                   is_active=True)
        .order_by(JournalEntry.id.desc())
        .first()
    )
