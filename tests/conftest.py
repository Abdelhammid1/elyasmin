"""Shared pytest fixtures. Runs against the current dev DB — no
teardown, no migrations — so the smoke reflects reality.

session-scope `app` also seeds one minimum-viable row of any entity
that's empty, so the smoke can render every detail template (and
catch BuildError / TemplateSyntaxError bugs the way the returns/
detail 500 would have been caught).
"""
from datetime import date
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db
from app.models.auth import User


def _seed_missing_entities(app):
    """For each detail-page entity, make sure at least one row exists —
    otherwise the route aborts 404 without ever rendering the template
    where the actual template bug would live."""
    with app.app_context():
        # Purchase return — the exact class of bug that shipped
        from app.models.suppliers import PurchaseReturn, Supplier
        if PurchaseReturn.query.first() is None:
            sup = Supplier.query.first()
            if sup is not None:
                db.session.add(PurchaseReturn(
                    supplier_id=sup.id,
                    return_date=date.today(),
                    amount=Decimal("100"),
                    reason="smoke-test seed",
                    mode="credit",
                ))
                db.session.commit()

        # Sales return
        from app.models.sales import SalesReturn, Customer
        if SalesReturn.query.first() is None:
            cus = Customer.query.first()
            if cus is not None:
                db.session.add(SalesReturn(
                    customer_id=cus.id,
                    return_date=date.today(),
                    amount=Decimal("100"),
                    reason="smoke-test seed",
                    mode="credit",
                ))
                db.session.commit()


@pytest.fixture(scope="session")
def app():
    a = create_app("development")
    _seed_missing_entities(a)
    return a


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


ADMIN_EMAIL = "admin@yasmin-farm.com"
ADMIN_PASS = "Admin@12345"


@pytest.fixture
def admin_client(app, client):
    """Test client logged in as admin via the actual login form.

    Setting the session dict directly kept getting stripped by
    Flask-Login's session-protection (the `_id` hash check fails
    for cookie-less test_client requests), so we just do a real
    login the way a browser would."""
    r = client.get("/auth/login")
    import re
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', r.data)
    csrf = m.group(1).decode() if m else ""
    r = client.post(
        "/auth/login",
        data={
            "csrf_token": csrf,
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASS,
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), (
        f"login failed: HTTP {r.status_code} — is admin@yasmin-farm.com "
        "still the seeded admin with password Admin@12345?"
    )
    return client
