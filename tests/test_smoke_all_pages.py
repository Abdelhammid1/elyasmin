"""Smoke test: every GET route in the app returns non-500.

Enumerates ``app.url_map``, substitutes an entity id for
``<int:xxx_id>`` parameters using a lookup table, and asserts each
response is 200 / 302 / 404 — but NEVER 500. If a route can't be
tested (no matching row in the dev DB), it's reported as SKIP.

Runs in seconds against the dev SQLite; catches BuildError,
TemplateSyntaxError, NameError, and every unhandled exception before
the user does. Motivated by the returns/purchases/1 500 that shipped
to production because no dev navigated there with real data.
"""
import pytest


# ---------------------------------------------------------------
# Sample-id lookup: for every <int:xxx_id> in the url_map, find one
# id from the model that owns it. Extend when a new blueprint lands.
# ---------------------------------------------------------------
def _sample_ids(app):
    from app.models.suppliers import Supplier, PurchaseInvoice, PurchaseReturn, SupplierPayment
    from app.models.sales import (
        Customer, MilkInvoice, SalesReturn, MilkDelivery, CustomerPayment,
    )
    from app.models.herd import Cow, CattleGroup, Birth
    from app.models.inventory import Ingredient
    from app.models.feed import FeedRecipe, FeedRun, FeedTank, FeedingSession
    from app.models.labor import Worker, LeaveRequest
    from app.models.finance import TreasuryAccount, Expense
    from app.models.accounting import JournalEntry
    from app.models.checks import Check
    from app.models.assets import FixedAsset
    from app.models.auth import User

    def first(m):
        with app.app_context():
            try:
                row = m.query.first()
            except Exception:
                return None
            return row.id if row else None

    return {
        "supplier_id":     first(Supplier),
        "invoice_id":      first(PurchaseInvoice) or first(MilkInvoice),
        "milk_invoice_id": first(MilkInvoice),
        "ret_id":          first(PurchaseReturn) or first(SalesReturn),
        "return_id":       first(PurchaseReturn) or first(SalesReturn),
        "customer_id":     first(Customer),
        "cow_id":          first(Cow),
        "group_id":        first(CattleGroup),
        "birth_id":        first(Birth),
        "ingredient_id":   first(Ingredient),
        "recipe_id":       first(FeedRecipe),
        "run_id":          first(FeedRun),
        "tank_id":         first(FeedTank),
        "session_id":      first(FeedingSession),
        "worker_id":       first(Worker),
        "leave_id":        first(LeaveRequest),
        "account_id":      first(TreasuryAccount),
        "expense_id":      first(Expense),
        "entry_id":        first(JournalEntry),
        "check_id":        first(Check),
        "asset_id":        first(FixedAsset),
        "user_id":         first(User),
        "delivery_id":     first(MilkDelivery),
        "payment_id":      first(CustomerPayment) or first(SupplierPayment),
        # non-int params (party ledger)
        "party_type":      "customer",
        "party_id":        first(Customer),
        "topic":           "dashboard",     # for /help/<topic>
    }


def _testable_urls(app):
    """Build a list of (endpoint, concrete_url) for every GET rule
    whose parameters we can satisfy. Return skipped rules too."""
    ids = _sample_ids(app)
    urls, skipped = [], []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods:
            continue
        if rule.endpoint in ("static",):
            continue
        values = {}
        missing = False
        for arg in rule.arguments:
            v = ids.get(arg)
            if v is None:
                missing = True
                break
            values[arg] = v
        if missing:
            skipped.append((rule.endpoint, rule.rule))
            continue
        # Fill the rule's placeholders by string replacement — handles
        # both <int:x> and <x> shapes uniformly.
        url = rule.rule
        for k, v in values.items():
            url = url.replace(f"<int:{k}>", str(v)) \
                     .replace(f"<string:{k}>", str(v)) \
                     .replace(f"<{k}>", str(v))
        urls.append((rule.endpoint, url))
    return urls, skipped


@pytest.fixture(scope="module")
def route_map(app):
    return _testable_urls(app)


def test_smoke_no_500(admin_client, app, capsys):
    """The main safety net: every reachable GET route → not 500."""
    urls, skipped = _testable_urls(app)
    with capsys.disabled():
        print(f"\n[smoke] testing {len(urls)} routes "
              f"({len(skipped)} skipped for missing dev-DB rows)")

    failures = []
    for endpoint, url in urls:
        # /auth/logout would clear the session mid-test; skip.
        # *_pdf endpoints spin up Chromium and hit their own /print
        # sibling over the network — needs a live server, not
        # test_client. Skip in this smoke; e2e.py exercises them.
        if endpoint == "auth.logout":
            continue
        if endpoint.endswith("_pdf") or endpoint.endswith(".invoice_pdf"):
            continue
        try:
            r = admin_client.get(url, follow_redirects=False)
        except Exception as e:
            failures.append((endpoint, url,
                             f"raised {type(e).__name__}: {e}"))
            continue
        if r.status_code >= 500:
            failures.append((endpoint, url,
                             f"HTTP {r.status_code}"))

    if failures:
        msg = "\n".join(
            f"  {ep:45s}  {u:55s}  → {why}"
            for ep, u, why in failures
        )
        pytest.fail(
            f"{len(failures)} route(s) errored:\n{msg}"
        )


def test_smoke_expected_200(admin_client):
    """Spot-check the highest-traffic pages actually render (200 —
    not redirect, not 404). Complements the no-500 check by proving
    the pages the user opens daily actively work."""
    core = [
        "/dashboard",
        "/customers/", "/suppliers/", "/inventory/",
        "/herd/", "/milk/deliveries", "/milk/invoices", "/purchases/",
        "/customers/settlement",
        "/feed/runs/new", "/feed/recipes", "/feed/runs", "/feed/tanks",
        "/accounting/", "/accounting/coa", "/accounting/journal",
        "/accounts/", "/reports/", "/labor/", "/checks/",
        "/assets/", "/finance/pnl", "/finance/milk-cost",
    ]
    failures = []
    for u in core:
        r = admin_client.get(u)
        if r.status_code != 200:
            failures.append(f"{u} → {r.status_code}")
    assert not failures, (
        f"{len(failures)} core routes not returning 200:\n  "
        + "\n  ".join(failures)
    )
