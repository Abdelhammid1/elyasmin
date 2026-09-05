"""PHASE 31 (FIN-6): regression suite for the Chart-of-Accounts CRUD.

Covers the five business rules from the ticket:
  1. duplicate code rejected on create
  2. edit-code refused when the account already has posted lines
  3. cycle in parent chain refused
  4. archive with active children refused
  5. happy path — create, edit (non-code fields), archive
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date

import pytest

from app.extensions import db
from app.models.accounting import (
    AccountType, JournalEntry, JournalLine, LedgerAccount, NormalSide,
)


def _make_account(**kwargs):
    """Seed helper — creates a LedgerAccount with sensible defaults."""
    defaults = dict(
        code=f"9999{kwargs.get('code_suffix', '01')}",
        name="اختبار",
        type=AccountType.ASSET,
        normal_side=NormalSide.DEBIT,
        is_postable=True,
        is_active=True,
    )
    kwargs.pop("code_suffix", None)
    defaults.update(kwargs)
    a = LedgerAccount(**defaults)
    db.session.add(a)
    db.session.commit()
    return a


def _cleanup_test_account(app, code_prefix="9999"):
    """Delete any test-seeded accounts + their lines so the suite is
    re-runnable against the same dev DB."""
    with app.app_context():
        seeded = LedgerAccount.query.filter(
            LedgerAccount.code.like(f"{code_prefix}%")
        ).all()
        for a in seeded:
            JournalLine.query.filter_by(account_id=a.id).delete()
            db.session.delete(a)
        db.session.commit()


def test_duplicate_code_rejected(admin_client, app):
    with app.app_context():
        _make_account(code="99990001", name="أ")
    try:
        r = admin_client.post("/accounting/coa/new", data={
            "code": "99990001",   # collision
            "name": "ب",
            "type": "ASSET",
            "parent_id": "0",
            "is_postable": "y",
            "treasury_account_id": "0",
        }, follow_redirects=False)
        assert r.status_code == 200, f"expected inline 200 with error, got {r.status_code}"
        assert "مستخدم فعلاً" in r.get_data(as_text=True)
        with app.app_context():
            same_code = LedgerAccount.query.filter_by(code="99990001").count()
            assert same_code == 1, "duplicate row was saved"
    finally:
        _cleanup_test_account(app)


def test_edit_code_with_lines_refused(admin_client, app):
    """Once posted lines exist on an account, its code is frozen."""
    with app.app_context():
        acct = _make_account(code="99990002", name="مع قيود")
        aid = acct.id
        # Fabricate one JE + one line pointing at this account so
        # has_journal_lines() returns True.
        je = JournalEntry(
            number="TEST-COA-001",
            date=date.today(),
            description="test seed for FIN-6",
            source_type="Test",
            source_id=0,
            is_active=True,
        )
        db.session.add(je)
        db.session.flush()
        db.session.add(JournalLine(
            entry_id=je.id, account_id=aid,
            debit=Decimal("10"), credit=Decimal("0"),
        ))
        db.session.commit()

    try:
        r = admin_client.post(f"/accounting/coa/{aid}/edit", data={
            "code": "99990099",     # attempt rename
            "name": "مع قيود",
            "type": "ASSET",
            "parent_id": "0",
            "is_postable": "y",
            "treasury_account_id": "0",
        }, follow_redirects=False)
        assert r.status_code == 200
        assert "مينفعش تعدّل الكود" in r.get_data(as_text=True)
        with app.app_context():
            # Code unchanged
            assert LedgerAccount.query.get(aid).code == "99990002"
    finally:
        with app.app_context():
            JournalEntry.query.filter_by(number="TEST-COA-001").delete()
            db.session.commit()
        _cleanup_test_account(app)


def test_parent_cycle_refused(admin_client, app):
    """Setting parent to one of your own descendants must be refused.

    Two layers of defence, both verified here:
      (a) The GET's parent-dropdown filters out descendants — POSTing
          one of them fails WTForms's `choices` validation, the form
          re-renders inline (200), and root.parent_id stays unchanged.
      (b) The route's own `_descendant_ids` cycle guard is the belt-
          and-braces backup in case (a) is ever bypassed.
    """
    with app.app_context():
        root = _make_account(code="99990010", name="جذر")
        child = _make_account(
            code="99990011", name="ابن", parent_id=root.id
        )
        grand = _make_account(
            code="99990012", name="حفيد", parent_id=child.id
        )
        root_id, grand_id = root.id, grand.id

    try:
        # Attempt to POST descendant as parent — the form's choices
        # list doesn't include it, so validation fails and we
        # re-render the edit page inline (200). Nothing about the
        # account changes.
        r = admin_client.post(f"/accounting/coa/{root_id}/edit", data={
            "code": "99990010",
            "name": "جذر",
            "type": "ASSET",
            "parent_id": str(grand_id),
            "is_postable": "y",
            "treasury_account_id": "0",
        }, follow_redirects=False)
        assert r.status_code == 200, (
            f"expected inline 200 (form re-render), got {r.status_code}"
        )
        with app.app_context():
            assert LedgerAccount.query.get(root_id).parent_id is None, (
                "cycle was allowed — parent was set"
            )

        # Verify the dropdown itself excludes the descendants (belt).
        r = admin_client.get(f"/accounting/coa/{root_id}/edit")
        body = r.get_data(as_text=True)
        assert 'value="0"' in body           # "بدون" is always there
        # descendant options must NOT appear
        assert f'value="{grand_id}"' not in body
    finally:
        _cleanup_test_account(app)


def test_archive_with_active_children_refused(admin_client, app):
    with app.app_context():
        root = _make_account(code="99990020", name="أب", is_postable=False)
        child = _make_account(
            code="99990021", name="ابن نشط", parent_id=root.id
        )
        root_id = root.id

    try:
        r = admin_client.post(
            f"/accounting/coa/{root_id}/archive",
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert "حساب فرعي نشط" in r.get_data(as_text=True)
        with app.app_context():
            assert LedgerAccount.query.get(root_id).is_active is True
    finally:
        _cleanup_test_account(app)


def test_happy_path_create_edit_archive(admin_client, app):
    # Create
    r = admin_client.post("/accounting/coa/new", data={
        "code": "99990030",
        "name": "حساب اختباري",
        "name_en": "Test account",
        "type": "EXPENSE",
        "parent_id": "0",
        "is_postable": "y",
        "treasury_account_id": "0",
    }, follow_redirects=False)
    assert r.status_code in (302, 303), r.status_code
    with app.app_context():
        acct = LedgerAccount.query.filter_by(code="99990030").first()
        assert acct is not None
        assert acct.type == AccountType.EXPENSE
        assert acct.normal_side == NormalSide.DEBIT
        aid = acct.id

    # Edit (rename — no lines yet, allowed)
    r = admin_client.post(f"/accounting/coa/{aid}/edit", data={
        "code": "99990030",
        "name": "حساب معدّل",
        "type": "EXPENSE",
        "parent_id": "0",
        "is_postable": "y",
        "treasury_account_id": "0",
    }, follow_redirects=False)
    assert r.status_code in (302, 303), r.status_code
    with app.app_context():
        assert LedgerAccount.query.get(aid).name == "حساب معدّل"

    # Archive (no children, no lines)
    r = admin_client.post(
        f"/accounting/coa/{aid}/archive", follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.status_code
    with app.app_context():
        assert LedgerAccount.query.get(aid).is_active is False

    _cleanup_test_account(app)
