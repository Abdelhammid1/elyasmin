from functools import wraps

from flask import abort
from flask_login import current_user


def admin_required(fn):
    """Admin-only endpoint. Auth stricter than write_required."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.is_admin:
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


def write_required(fn):
    """PHASE 27 (SEC-2): block viewer role from any write endpoint.

    Auth guard hierarchy:
      @login_required   — everyone signed in
      @write_required   — admin + manager (not viewer)  ← this
      @admin_required   — admin only (superset — never stack)

    Apply to every POST route that creates / edits / deletes /
    archives data. Skip:
      - Auth POSTs (login / forgot / reset / logout) — everyone
        needs those regardless of role
      - Routes already guarded by @admin_required — admin is
        strictly stronger, don't double-decorate
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.can_write:
            abort(403)
        return fn(*args, **kwargs)

    return wrapper
