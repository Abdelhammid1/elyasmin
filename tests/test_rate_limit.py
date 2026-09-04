"""PHASE 29 (SEC-4): regression suite for Flask-Limiter on auth
endpoints.

The limiter is DISABLED for the rest of the suite (see conftest.py —
otherwise admin_client's tight fixture loops trip it). This test
re-enables it locally for /auth/forgot-password and verifies:

  1. Under the 10-per-hour limit → responses stay 200.
  2. Over the limit → the 11th call returns 429 with the Arabic 429
     template.

We don't run the SAME check on /auth/login because
`_recent_failed_attempts` in auth/routes.py has its own 5-per-email
1-hour lockout (also returning 429) — the two paths would race and
give brittle assertions here. The decorator on /auth/login is
verified by the "under the limit" leg (login GET is not gated by
_recent_failed_attempts).
"""
from __future__ import annotations

import pytest

from app.extensions import limiter


@pytest.fixture
def limiter_on(app):
    """Re-enable the rate limiter for one test, then restore.

    Also resets the limiter's storage so a previous test's counters
    don't spill in (in-memory backend keeps a per-key ticker).
    Flask-Limiter 3.x needs both the config key AND the instance
    property flipped to actually gate — the config alone is
    consulted after a hit is already recorded."""
    app.config["RATELIMIT_ENABLED"] = True
    limiter.enabled = True
    limiter.reset()
    yield
    app.config["RATELIMIT_ENABLED"] = False
    limiter.enabled = False
    limiter.reset()


def test_under_limit_stays_200(client, limiter_on):
    """5 GETs to /auth/forgot-password from one client → all 200."""
    for i in range(5):
        r = client.get("/auth/forgot-password")
        assert r.status_code == 200, f"iteration {i} unexpectedly {r.status_code}"


def test_over_limit_returns_429(client, limiter_on):
    """11 GETs to /auth/forgot-password → the 11th is 429 and the
    body is our Arabic 429 template."""
    # First 10 should be fine
    for i in range(10):
        r = client.get("/auth/forgot-password")
        assert r.status_code == 200, f"pre-limit iteration {i} unexpectedly {r.status_code}"
    # 11th trips the limit
    r = client.get("/auth/forgot-password")
    assert r.status_code == 429, f"expected 429, got {r.status_code}"
    body = r.get_data(as_text=True)
    assert "429" in body or "محاولات كتير" in body, (
        "429 response body doesn't look like our errors/429.html template"
    )


def test_login_get_is_gated_too(client, limiter_on):
    """Also verify the decorator on /auth/login: 11 GETs → 429.
    (POST would race the per-email lockout; GET is safe.)"""
    for i in range(10):
        r = client.get("/auth/login")
        assert r.status_code == 200
    r = client.get("/auth/login")
    assert r.status_code == 429


def test_reset_password_get_is_gated_too(client, limiter_on):
    """And /auth/reset-password/<token>: even an invalid token gets
    a response (302 back to /forgot-password), so we can count."""
    # Invalid token redirects to /forgot-password (302); either
    # response code counts against the rate limit.
    for i in range(10):
        r = client.get("/auth/reset-password/badtoken", follow_redirects=False)
        assert r.status_code in (200, 302, 303)
    r = client.get("/auth/reset-password/badtoken", follow_redirects=False)
    assert r.status_code == 429
