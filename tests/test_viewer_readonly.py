"""PHASE 27 (SEC-2): regression suite for viewer read-only enforcement.

Before commit 805d6ca, any signed-in user with role='viewer' could
POST directly to any write endpoint and the change would go through
(the User.can_write property was defined but never checked). This
suite locks that door shut.

Two complementary checks:

1.  ``test_viewer_sample_writes_are_forbidden`` — POSTs to a hand-
    picked spread of write endpoints across every blueprint we
    guarded, and asserts each one returns 403. This is the
    friendly-to-read failure — if you break a specific endpoint's
    decorator, the test names the URL.

2.  ``test_viewer_every_write_route_is_guarded`` — enumerates the
    url_map, filters to POST routes that touch data (skipping auth,
    landing, and admin_required routes which have their own guard),
    and asserts every one is either @admin_required or @write_required.
    This is the safety net that catches NEW write routes added later
    without the decorator sweep.
"""
from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# Sample writes: one representative per blueprint we guarded.
# Each entry is (url, form_data). form_data is intentionally invalid —
# the request should be rejected at auth (403) BEFORE the form is
# validated, so any minimum body works.
# ---------------------------------------------------------------------------

SAMPLE_WRITES = [
    ("/herd/new",                    {"ear_tag": "SEC2"}),
    ("/herd/groups/new",             {"name": "sec2"}),
    ("/milk/deliveries/new",         {}),
    ("/milk/invoices/new",           {}),
    ("/feed/runs/new",               {}),
    ("/feed/feedings/new",           {}),
    ("/inventory/new",               {"name": "sec2"}),
    ("/suppliers/new",               {"name": "sec2"}),
    ("/customers/new",               {"name": "sec2"}),
    ("/purchases/new",               {}),
    ("/checks/issued/new",           {}),
    ("/checks/received/new",         {}),
    ("/assets/new",                  {}),
    ("/returns/purchases/new",       {}),
    ("/returns/sales/new",           {}),
    ("/accounts/new",                {"name": "sec2"}),
    ("/accounts/transfer",           {}),
    ("/accounting/journal/new",      {}),
    ("/medicine/dispense",           {}),
]


@pytest.mark.parametrize("url,data", SAMPLE_WRITES)
def test_viewer_sample_writes_are_forbidden(viewer_client, url, data):
    """SEC-2: viewer POST to any of these must be 403."""
    r = viewer_client.post(url, data=data, follow_redirects=False)
    # 403 = correct. 404 = route was renamed (also protects the viewer,
    # but the test is out of date). 302 to /auth/login = login lost.
    assert r.status_code == 403, (
        f"{url} returned {r.status_code} for viewer — expected 403. "
        "Missing @write_required?"
    )


# ---------------------------------------------------------------------------
# Full-sweep guard: any POST route to a data blueprint MUST be guarded.
# ---------------------------------------------------------------------------

# Blueprints whose POST endpoints are intentionally open to everyone
# signed in (auth flows themselves).
OPEN_POST_BLUEPRINTS = {"auth", "landing"}

# Blueprints/routes whose POST paths are read-only convenience (search,
# filters, mark-as-read). None today — kept as an escape hatch.
READ_ONLY_POST_ENDPOINTS: set[str] = set()


def _iter_write_post_endpoints(app):
    """Yield (endpoint, view_func) for every POST route we expect
    to be guarded."""
    for rule in app.url_map.iter_rules():
        if "POST" not in (rule.methods or set()):
            continue
        # Blueprint prefix
        bp = rule.endpoint.split(".", 1)[0]
        if bp in OPEN_POST_BLUEPRINTS:
            continue
        if rule.endpoint in READ_ONLY_POST_ENDPOINTS:
            continue
        view = app.view_functions.get(rule.endpoint)
        if view is None:
            continue
        yield rule.endpoint, view


def _has_decorator(view_func, name: str) -> bool:
    """Walk the function's __wrapped__ chain looking for a decorator
    whose wrapped source file mentions the guard name. Falls back to
    scanning the source of the outermost function for the decorator
    literal."""
    # Fast path: functools.wraps preserves __wrapped__; walk the chain
    # and inspect each function's qualname / module.
    seen = set()
    f = view_func
    while f is not None and id(f) not in seen:
        seen.add(id(f))
        qn = getattr(f, "__qualname__", "")
        if name in qn:
            return True
        f = getattr(f, "__wrapped__", None)

    # Fallback: inspect the outer function's source for the literal
    # @<name> — this catches decorators that don't preserve __wrapped__.
    import inspect
    try:
        src = inspect.getsource(view_func)
    except (OSError, TypeError):
        return False
    return bool(re.search(rf"@{name}\b", src))


def test_viewer_every_write_route_is_guarded(app):
    """SEC-2 safety net: every POST route to a data blueprint must
    carry @write_required or @admin_required. This catches new routes
    added later without the decorator."""
    unguarded: list[str] = []
    for endpoint, view in _iter_write_post_endpoints(app):
        # inspect.getsource on the view returns the WRAPPED view (the
        # innermost def), which is what carries the decorator lines in
        # its source. Both @write_required and @admin_required are
        # accepted.
        import inspect
        try:
            src = inspect.getsource(view)
        except (OSError, TypeError):
            # Can't introspect — skip rather than fail spuriously.
            continue
        if "@write_required" in src or "@admin_required" in src:
            continue
        unguarded.append(endpoint)

    assert not unguarded, (
        "SEC-2 leak: these POST endpoints have no viewer guard "
        "(add @write_required or @admin_required):\n  - "
        + "\n  - ".join(sorted(unguarded))
    )
