"""PHASE 33 regression suite for the login post-redirect fix.

Zakaria reported that after login the browser landed on `/` (the
marketing landing page) instead of `/dashboard`. Cause:
`auth.login` used to redirect to `request.args.get("next") or
url_for("dashboard.index")` and Flask-Login had been stamping
`next=/` when the visitor first hit the landing page. The
`_safe_next` helper (app/blueprints/auth/routes.py) now filters
that away.

Also verifies the open-redirect defence — `?next=https://evil.com`
must not leave the site.
"""
from __future__ import annotations

import re

import pytest

from app.blueprints.auth.routes import _safe_next


# ---------- Pure-function unit tests on _safe_next ----------

@pytest.mark.parametrize("raw,expected", [
    (None,                                    None),
    ("",                                      None),
    ("/",                                     None),   # landing itself
    ("/?foo=bar",                             None),   # landing w/ query
    ("/#top",                                 None),   # landing w/ fragment
    ("https://evil.example/steal",            None),   # open-redirect
    ("http://evil.example/steal",             None),
    ("//evil.example",                        None),   # scheme-relative
    ("customers/",                            None),   # relative, no leading /
    ("/customers/",                           "/customers/"),
    ("/dashboard",                            "/dashboard"),
    ("/accounting/coa",                       "/accounting/coa"),
])
def test_safe_next(raw, expected):
    assert _safe_next(raw) == expected


# ---------- End-to-end login redirect tests ----------

ADMIN_EMAIL = "admin@yasmin-farm.com"
ADMIN_PASS = "Admin@12345"


def _login(client, next_param: str | None = None):
    """Log in as admin, optionally with a ?next=... on the URL.
    Returns the response of the POST (no follow_redirects)."""
    login_url = "/auth/login"
    if next_param is not None:
        login_url = f"/auth/login?next={next_param}"
    # Pull CSRF from the GET (test config disables CSRF but this
    # keeps the login form's other-fields validators happy).
    r = client.get(login_url)
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', r.data)
    csrf = m.group(1).decode() if m else ""
    return client.post(
        login_url,
        data={"csrf_token": csrf,
              "email": ADMIN_EMAIL, "password": ADMIN_PASS},
        follow_redirects=False,
    )


def test_login_without_next_goes_to_dashboard(client):
    r = _login(client)
    assert r.status_code in (302, 303)
    assert r.headers["Location"].endswith("/dashboard"), r.headers["Location"]


def test_login_with_landing_next_goes_to_dashboard(client):
    """The observed bug: next=/ used to send you back to the landing."""
    r = _login(client, next_param="/")
    assert r.status_code in (302, 303)
    assert r.headers["Location"].endswith("/dashboard"), r.headers["Location"]


def test_login_with_internal_next_honors_it(client):
    r = _login(client, next_param="/customers/")
    assert r.status_code in (302, 303)
    assert r.headers["Location"].endswith("/customers/"), r.headers["Location"]


def test_login_with_open_redirect_next_falls_back(client):
    """Open-redirect defence: an absolute external URL must not win."""
    r = _login(client, next_param="https://evil.example/x")
    assert r.status_code in (302, 303)
    loc = r.headers["Location"]
    assert "evil.example" not in loc
    assert loc.endswith("/dashboard"), loc
