"""Public landing page — the front door for anyone who does not have an account.

PHASE 26: replaced the old Jinja template with the React SPA from the
`Abdelhammid1/landing-yasmin` repo. The Vite production bundle is
checked into `app/static/landing/` with `base=/static/landing/` so all
asset URLs already point where Flask will serve them.

Not `@login_required`: first-time visitors see what the system does
before being asked to sign in.

To update: re-run `scripts/rebuild_landing.sh` (documented) after the
source repo is pushed to.
"""
from pathlib import Path

from flask import Blueprint, current_app, send_from_directory

bp = Blueprint("landing", __name__)


@bp.route("/")
def home():
    """Serve the built React SPA's entry point. All /static/landing/*
    assets it references get served automatically by Flask's static
    handler — no extra route needed."""
    landing_dir = Path(current_app.static_folder) / "landing"
    return send_from_directory(landing_dir, "index.html")
