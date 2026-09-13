"""PHASE 39 (EXP-CAT) regression suite.

Locks the dynamic-category contract:

  1. Adding an ExpenseCategory row makes its label appear in
     the /finance/expenses/new picker on the next request.
  2. Posting an expense with a __custom__ label creates the
     matching ExpenseCategory row AND stores the Expense with
     category = "custom:<label>".
  3. Posting the same __custom__ label twice deduplicates to
     one ExpenseCategory row.
  4. Toggling a system row (e.g. electricity) is refused.
  5. expense_category_label() handles the three input shapes
     (built-in code, custom:X, unknown, None).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.finance import (
    Expense, ExpenseCategory, TreasuryAccount,
    expense_category_label,
)


_TEST_LABEL = "EXP-CAT-TEST-تجريب"  # "EXP-CAT-TEST-تجريب"


def _first_active_treasury(app):
    with app.app_context():
        t = TreasuryAccount.query.filter_by(is_archived=False).first()
        assert t is not None, "test DB needs at least one treasury"
        return t.id


def _cleanup(app):
    with app.app_context():
        # Kill any test-tagged Expense rows first (they hold FK
        # to the category by string).
        Expense.query.filter(
            Expense.category.like(f"custom:{_TEST_LABEL}%")
        ).delete(synchronize_session=False)
        ExpenseCategory.query.filter(
            ExpenseCategory.name.like(f"custom:{_TEST_LABEL}%")
        ).delete(synchronize_session=False)
        db.session.commit()


def test_expense_category_label_helper(app):
    """Pure-Python — no DB touch beyond the mapper registry."""
    with app.app_context():
        assert expense_category_label(None) is None
        assert expense_category_label("electricity") == "كهرباء"
        assert expense_category_label("custom:X") == "X"
        assert expense_category_label("foo_unknown") == "foo_unknown"


def test_category_form_choices_are_dynamic(admin_client, app):
    """Add an ExpenseCategory row → GET /finance/expenses/new
    → the picker HTML now carries the new option."""
    try:
        with app.app_context():
            db.session.add(ExpenseCategory(
                name=f"custom:{_TEST_LABEL}",
                display_label=_TEST_LABEL,
                is_system=False,
                is_active=True,
            ))
            db.session.commit()

        r = admin_client.get("/finance/expenses/new")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert _TEST_LABEL in body
    finally:
        _cleanup(app)


def test_create_expense_with_new_custom_persists_row(admin_client, app):
    """POST /finance/expenses/new with __custom__ + label →
    one Expense row + one ExpenseCategory row."""
    treasury_id = _first_active_treasury(app)
    try:
        r = admin_client.post(
            "/finance/expenses/new",
            data={
                "category": "__custom__",
                "custom_category": _TEST_LABEL,
                "amount": "50",
                "expense_date": date.today().isoformat(),
                "account_id": str(treasury_id),
                "description": "test row",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.data[:400]
        with app.app_context():
            cat = ExpenseCategory.query.filter_by(
                name=f"custom:{_TEST_LABEL}"
            ).first()
            assert cat is not None
            assert cat.display_label == _TEST_LABEL
            assert cat.is_system is False

            e = (
                Expense.query
                .filter_by(category=f"custom:{_TEST_LABEL}")
                .order_by(Expense.id.desc())
                .first()
            )
            assert e is not None
            assert e.amount == Decimal("50.00")
            assert e.category_label == _TEST_LABEL
    finally:
        _cleanup(app)


def test_duplicate_custom_dedupes(admin_client, app):
    """Posting the same __custom__ label twice creates only one
    ExpenseCategory row."""
    treasury_id = _first_active_treasury(app)
    try:
        for _ in range(2):
            r = admin_client.post(
                "/finance/expenses/new",
                data={
                    "category": "__custom__",
                    "custom_category": _TEST_LABEL,
                    "amount": "10",
                    "expense_date": date.today().isoformat(),
                    "account_id": str(treasury_id),
                    "description": "",
                },
                follow_redirects=False,
            )
            assert r.status_code in (302, 303)
        with app.app_context():
            count = ExpenseCategory.query.filter_by(
                name=f"custom:{_TEST_LABEL}"
            ).count()
            assert count == 1
            # Two expenses landed on the same category name
            e_count = Expense.query.filter_by(
                category=f"custom:{_TEST_LABEL}"
            ).count()
            assert e_count == 2
    finally:
        _cleanup(app)


def test_system_row_cannot_be_disabled(admin_client, app):
    """POST toggle on a system row is refused (302 back with a
    flash), and is_active stays True."""
    with app.app_context():
        sys_row = ExpenseCategory.query.filter_by(
            name="electricity"
        ).first()
        assert sys_row is not None
        assert sys_row.is_system is True
        assert sys_row.is_active is True
        sys_id = sys_row.id

    r = admin_client.post(
        f"/finance/expense-categories/{sys_id}/toggle",
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    with app.app_context():
        sys_row = db.session.get(ExpenseCategory, sys_id)
        assert sys_row.is_active is True   # unchanged
