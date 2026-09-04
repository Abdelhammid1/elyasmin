"""Public landing page — the front door for anyone who does not have an account.

The `app/static/landing/index.html` file IS the page — a self-contained
RTL Arabic static HTML (Tailwind via CDN, external fonts + images).
Not `@login_required`: first-time visitors see what the system does
before being asked to sign in.

To update the landing: replace `app/static/landing/index.html`.
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
