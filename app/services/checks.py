"""PHASE 8a — checks: one autoposter per status transition.

Received (customer gave us a check):

    on receipt:   DR 1130 شيكات تحت التحصيل   /   CR 1300 ذمم العملاء
    on clear:     DR treasury                  /   CR 1130
    on bounce:    DR 1300 ذمم العملاء (customer) /   CR 1130

Issued (we gave a check to a supplier):

    on issue:     DR 2100 ذمم الموردين (supplier) /   CR 2110 شيكات تحت الدفع
    on settle:    DR 2110                        /   CR treasury
    on bounce:    DR 2110                        /   CR 2100 ذمم الموردين

Each transition posts its OWN JE — the check history stays intact.
`source_type` disambiguates ('Check:receive', 'Check:clear', etc.),
`source_id` = check.id. Idempotent: replaying a transition deletes its
prior JE and posts fresh. That's what lets an admin fix a bank_name
typo on a cleared check without rebooking the whole trail.
"""
from decimal import Decimal

from app.models.checks import Check
from app.services.autoposting import (
    CODE_TRADE_PAYABLE,
    CODE_TRADE_RECEIVABLE,
    _code,
    _delete_prior_je,
    _treasury_leaf,
)
from app.services.ledger import LedgerError, post_journal

# Deliberately kept as module-level constants so P8c can flip them
# alongside the autoposting ones with a single find/replace.
# PHASE 8c — codes match Ibrahim's spec: 1030/2020.
CODE_CHECKS_RECEIVABLE = "1030"    # شيكات تحت التحصيل
CODE_CHECKS_PAYABLE    = "2020"    # شيكات تحت الدفع


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


# ==================== RECEIVED ====================

def on_check_received(check: Check, *, created_by=None):
    """Customer hands us a check. Their receivable drops, checks-in-transit
    grows by the same amount."""
    if check.direction != Check.DIRECTION_RECEIVED:
        raise LedgerError("الشيك ده مش شيك وارد.")

    _delete_prior_je("Check:receive", check.id)
    amount = _d(check.amount)
    if amount <= 0:
        return None

    checks_recv = _code(CODE_CHECKS_RECEIVABLE)
    receivable = _code(CODE_TRADE_RECEIVABLE)

    return post_journal(
        description=f"شيك وارد #{check.check_number} من {check.party_name}",
        lines=[
            {"account_id": checks_recv.id, "debit": amount,
             "memo": f"شيك #{check.check_number} — {check.bank_name}"},
            {"account_id": receivable.id, "credit": amount,
             "party_type": "customer", "party_id": check.customer_id,
             "memo": f"سداد بشيك — {check.customer.name}"},
        ],
        entry_date=check.issue_date,
        source_type="Check:receive",
        source_id=check.id,
        created_by=created_by,
    )


def on_check_cleared_received(check: Check, *, created_by=None):
    """The bank cleared the received check. Money actually arrives —
    treasury grows, checks-in-transit shrinks by the same amount."""
    if check.direction != Check.DIRECTION_RECEIVED:
        raise LedgerError("الشيك ده مش شيك وارد.")
    if check.treasury_account is None:
        raise LedgerError("لازم تختار حساب البنك اللي الشيك اتصرف عليه.")

    _delete_prior_je("Check:clear", check.id)
    amount = _d(check.amount)
    if amount <= 0:
        return None

    treasury = _treasury_leaf(check.treasury_account)
    checks_recv = _code(CODE_CHECKS_RECEIVABLE)

    return post_journal(
        description=f"تحصيل شيك #{check.check_number}",
        lines=[
            {"account_id": treasury.id, "debit": amount,
             "memo": f"إلى {check.treasury_account.name}"},
            {"account_id": checks_recv.id, "credit": amount,
             "memo": f"شيك #{check.check_number} — {check.party_name}"},
        ],
        entry_date=check.cleared_on or check.due_date,
        source_type="Check:clear",
        source_id=check.id,
        created_by=created_by,
    )


def on_check_bounced_received(check: Check, *, created_by=None):
    """Bank returned the received check. Customer owes us again;
    checks-in-transit shrinks."""
    if check.direction != Check.DIRECTION_RECEIVED:
        raise LedgerError("الشيك ده مش شيك وارد.")

    _delete_prior_je("Check:bounce", check.id)
    amount = _d(check.amount)
    if amount <= 0:
        return None

    receivable = _code(CODE_TRADE_RECEIVABLE)
    checks_recv = _code(CODE_CHECKS_RECEIVABLE)

    return post_journal(
        description=f"ارتداد شيك #{check.check_number}",
        lines=[
            {"account_id": receivable.id, "debit": amount,
             "party_type": "customer", "party_id": check.customer_id,
             "memo": f"ارتداد — {check.customer.name}"},
            {"account_id": checks_recv.id, "credit": amount,
             "memo": f"شيك #{check.check_number} — ارتد"},
        ],
        entry_date=check.bounced_on or check.due_date,
        source_type="Check:bounce",
        source_id=check.id,
        created_by=created_by,
    )


# ==================== ISSUED ====================

def on_check_issued(check: Check, *, created_by=None):
    """We handed a check to a supplier. Their payable drops now (the
    obligation moved to the bank), our checks-payable liability grows."""
    if check.direction != Check.DIRECTION_ISSUED:
        raise LedgerError("الشيك ده مش شيك صادر.")

    _delete_prior_je("Check:receive", check.id)  # issued uses same key namespace
    amount = _d(check.amount)
    if amount <= 0:
        return None

    payable = _code(CODE_TRADE_PAYABLE)
    checks_pay = _code(CODE_CHECKS_PAYABLE)

    return post_journal(
        description=f"شيك صادر #{check.check_number} إلى {check.party_name}",
        lines=[
            {"account_id": payable.id, "debit": amount,
             "party_type": "supplier", "party_id": check.supplier_id,
             "memo": f"سداد بشيك — {check.supplier.name}"},
            {"account_id": checks_pay.id, "credit": amount,
             "memo": f"شيك #{check.check_number} — {check.bank_name}"},
        ],
        entry_date=check.issue_date,
        source_type="Check:receive",
        source_id=check.id,
        created_by=created_by,
    )


def on_check_settled_issued(check: Check, *, created_by=None):
    """The supplier deposited our check. Money leaves treasury,
    checks-payable liability clears."""
    if check.direction != Check.DIRECTION_ISSUED:
        raise LedgerError("الشيك ده مش شيك صادر.")
    if check.treasury_account is None:
        raise LedgerError("لازم تختار حساب البنك اللي الشيك اتصرف منه.")

    _delete_prior_je("Check:clear", check.id)
    amount = _d(check.amount)
    if amount <= 0:
        return None

    treasury = _treasury_leaf(check.treasury_account)
    checks_pay = _code(CODE_CHECKS_PAYABLE)

    return post_journal(
        description=f"صرف شيك صادر #{check.check_number}",
        lines=[
            {"account_id": checks_pay.id, "debit": amount,
             "memo": f"شيك #{check.check_number} — {check.party_name}"},
            {"account_id": treasury.id, "credit": amount,
             "memo": f"من {check.treasury_account.name}"},
        ],
        entry_date=check.cleared_on or check.due_date,
        source_type="Check:clear",
        source_id=check.id,
        created_by=created_by,
    )


def on_check_bounced_issued(check: Check, *, created_by=None):
    """Rare — our issued check was rejected (insufficient funds). The
    supplier's payable rebounds; checks-payable clears."""
    if check.direction != Check.DIRECTION_ISSUED:
        raise LedgerError("الشيك ده مش شيك صادر.")

    _delete_prior_je("Check:bounce", check.id)
    amount = _d(check.amount)
    if amount <= 0:
        return None

    payable = _code(CODE_TRADE_PAYABLE)
    checks_pay = _code(CODE_CHECKS_PAYABLE)

    return post_journal(
        description=f"ارتداد شيك صادر #{check.check_number}",
        lines=[
            {"account_id": checks_pay.id, "debit": amount,
             "memo": f"ارتداد — شيك #{check.check_number}"},
            {"account_id": payable.id, "credit": amount,
             "party_type": "supplier", "party_id": check.supplier_id,
             "memo": f"رجوع دين — {check.supplier.name}"},
        ],
        entry_date=check.bounced_on or check.due_date,
        source_type="Check:bounce",
        source_id=check.id,
        created_by=created_by,
    )


# ==================== VOID ====================

def void_check(check: Check):
    """Admin-only: wipe the entire JE chain for this check (receive +
    clear + bounce, whichever were posted). The check row itself stays
    archived so audit still shows it existed."""
    for suffix in ("receive", "clear", "bounce"):
        _delete_prior_je(f"Check:{suffix}", check.id)
    check.is_archived = True
    check.status = Check.STATUS_PENDING
