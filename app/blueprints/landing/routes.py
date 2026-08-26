"""Public landing page — the front door for anyone who does not have an account
yet, and the marketing surface for logged-in users to share.

Deliberately not `@login_required`: the whole point is that a first-time visitor
sees what the system does before being asked to sign in. The one CTA button
adapts to the visitor's session via current_user.is_authenticated.
"""
from flask import Blueprint, render_template

bp = Blueprint("landing", __name__, template_folder="../../templates/landing")


@bp.route("/")
def home():
    return render_template("landing/home.html")
