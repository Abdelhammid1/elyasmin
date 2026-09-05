"""PHASE 32 (FIN-7): regression suite for the quick-entry templates.

Each test POSTs to /accounting/quick-entry/<kind>, then asserts the
resulting JournalEntry has the exact double-entry structure the
ticket calls for. Uses `admin_client` — quick-entry is admin-only
(matches `journal_new`).

Four representative cases (of the 7 total templates):
  1. opening       — DR picked leaf / CR 3090
  2. capital       — DR treasury leaf / CR 3010
  3. deposit_in    — DR treasury leaf / CR 2050; party name in memo
  4. loan_received — DR treasury leaf / CR 2041 short-term
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.accounting import JournalEntry, JournalLine, LedgerAccount
from app.models.finance import TreasuryAccount


def _seed_treasury(app, name="TEST-QE-TREASURY"):
    """Create a treasury account + its CoA leaf wire-up. Idempotent."""
    with app.app_context():
        t = TreasuryAccount.query.filter_by(name=name).first()
        if t is None:
            t = TreasuryAccount(
                name=name,
                account_type=TreasuryAccount.TYPE_CASH,
                opening_balance=Decimal("0"),
                current_balance=Decimal("0"),
            )
            db.session.add(t)
            db.session.commit()
            # Wire up a CoA leaf pointing at it
            from app.services.coa_seed import wire_treasury_accounts
            wire_treasury_accounts()
            db.session.commit()
        return t.id


def _cleanup(app):
    """Remove test-created quick-entry JEs + the test treasury."""
    with app.app_context():
        je_ids = [
            j.id for j in JournalEntry.query.filter_by(
                source_type="QuickEntry"
            ).all()
        ]
        if je_ids:
            JournalLine.query.filter(
                JournalLine.entry_id.in_(je_ids)
            ).delete(synchronize_session=False)
            JournalEntry.query.filter(
                JournalEntry.id.in_(je_ids)
            ).delete(synchronize_session=False)
        # Drop the treasury's wired CoA leaf, then the treasury itself.
        t = TreasuryAccount.query.filter_by(name="TEST-QE-TREASURY").first()
        if t is not None:
            LedgerAccount.query.filter_by(
                treasury_account_id=t.id
            ).delete(synchronize_session=False)
            db.session.delete(t)
        db.session.commit()


def _last_je(app):
    with app.app_context():
        je = JournalEntry.query.filter_by(
            source_type="QuickEntry"
        ).order_by(JournalEntry.id.desc()).first()
        # Force-load lines while inside the app context
        _ = list(je.lines) if je else None
        return je


def test_opening_balance_produces_dr_account_cr_3090(admin_client, app):
    tid = _seed_treasury(app)
    try:
        # Pick any leaf — say 1010 (cash)
        with app.app_context():
            leaf = LedgerAccount.query.filter_by(code="1010").first()
            assert leaf is not None
            leaf_id = leaf.id

        r = admin_client.post(
            "/accounting/quick-entry/opening",
            data={
                "kind": "opening",
                "amount": "100",
                "entry_date": date.today().isoformat(),
                "account_id": str(leaf_id),
                "memo": "test opening",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code

        with app.app_context():
            je = JournalEntry.query.filter_by(
                source_type="QuickEntry"
            ).order_by(JournalEntry.id.desc()).first()
            assert je is not None
            lines = list(je.lines)
            assert len(lines) == 2
            # DR on the picked leaf, CR on 3090
            dr = [l for l in lines if l.debit > 0]
            cr = [l for l in lines if l.credit > 0]
            assert dr[0].account_id == leaf_id
            assert cr[0].account.code == "3090"
            assert je.total_debit == Decimal("100.00")
    finally:
        _cleanup(app)


def test_capital_produces_dr_treasury_cr_3010(admin_client, app):
    tid = _seed_treasury(app)
    try:
        r = admin_client.post(
            "/accounting/quick-entry/capital",
            data={
                "kind": "capital",
                "amount": "5000",
                "entry_date": date.today().isoformat(),
                "treasury_account_id": str(tid),
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code

        with app.app_context():
            je = JournalEntry.query.filter_by(
                source_type="QuickEntry"
            ).order_by(JournalEntry.id.desc()).first()
            lines = list(je.lines)
            dr = [l for l in lines if l.debit > 0][0]
            cr = [l for l in lines if l.credit > 0][0]
            # DR = treasury leaf; CR = 3010
            assert dr.account.treasury_account_id == tid
            assert cr.account.code == "3010"
            assert je.total_debit == Decimal("5000.00")
    finally:
        _cleanup(app)


def test_deposit_in_produces_dr_treasury_cr_2050_with_party_in_memo(admin_client, app):
    tid = _seed_treasury(app)
    party = "أحمد محمد الاختبار"
    try:
        r = admin_client.post(
            "/accounting/quick-entry/deposit_in",
            data={
                "kind": "deposit_in",
                "amount": "1500",
                "entry_date": date.today().isoformat(),
                "treasury_account_id": str(tid),
                "party_name": party,
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code

        with app.app_context():
            je = JournalEntry.query.filter_by(
                source_type="QuickEntry"
            ).order_by(JournalEntry.id.desc()).first()
            lines = list(je.lines)
            dr = [l for l in lines if l.debit > 0][0]
            cr = [l for l in lines if l.credit > 0][0]
            assert dr.account.treasury_account_id == tid
            assert cr.account.code == "2050"
            # Party name in memo + party_type = 'other'
            assert party in (dr.memo or "")
            assert party in (cr.memo or "")
            assert dr.party_type == "other"
            assert cr.party_type == "other"
            # Description also carries the party name
            assert party in je.description
    finally:
        _cleanup(app)


def test_loan_received_short_produces_dr_treasury_cr_2041(admin_client, app):
    tid = _seed_treasury(app)
    try:
        r = admin_client.post(
            "/accounting/quick-entry/loan_received",
            data={
                "kind": "loan_received",
                "amount": "25000",
                "entry_date": date.today().isoformat(),
                "treasury_account_id": str(tid),
                "loan_kind": "short",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303), r.status_code

        with app.app_context():
            je = JournalEntry.query.filter_by(
                source_type="QuickEntry"
            ).order_by(JournalEntry.id.desc()).first()
            lines = list(je.lines)
            dr = [l for l in lines if l.debit > 0][0]
            cr = [l for l in lines if l.credit > 0][0]
            assert dr.account.treasury_account_id == tid
            assert cr.account.code == "2041"   # short-term
            assert je.total_debit == Decimal("25000.00")
    finally:
        _cleanup(app)
