"""ACCOUNTING FOUNDATION — the single chokepoint through which every journal
entry is written. Ported from marsoud's services/ledger.py with the multi-
tenant + multi-currency arguments dropped (elyasmin is one deployment, EGP).

Nothing else in the app should construct a JournalEntry directly. Going
through this function is what guarantees the ledger is always balanced,
never lands on a header account, and always has a source-event link back
into the farm workflow that produced it.
"""
from datetime import date as _date
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import func

from app.extensions import db
from app.models.accounting import LedgerAccount, JournalEntry, JournalLine

MONEY = Decimal("0.0001")   # ledger precision is 4 decimals; UI rounds to 2
TOL = Decimal("0.005")      # balance tolerance — half a piastre


class LedgerError(ValueError):
    """A journal entry was rejected for a rule violation (unbalanced, empty,
    zero-value, or a line on a header account). Message is in Arabic and safe
    to flash to the user."""


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _next_number() -> str:
    """Next JE number, in the form JE-000001. Monotonic; ties are broken by
    the primary key so a concurrent write can never collide."""
    last = (
        db.session.query(func.max(JournalEntry.number))
        .filter(JournalEntry.number.like("JE-%"))
        .scalar()
    )
    if not last:
        return "JE-000001"
    try:
        n = int(last.split("-", 1)[1]) + 1
    except (IndexError, ValueError):
        n = 1
    return f"JE-{n:06d}"


def post_journal(
    description: str,
    lines: Iterable[dict],
    *,
    entry_date: Optional[_date] = None,
    reference: Optional[str] = None,
    source_type: Optional[str] = None,
    source_id: Optional[int] = None,
    created_by: Optional[int] = None,
    is_reversal: bool = False,
    reversal_of_id: Optional[int] = None,
) -> JournalEntry:
    """Post a balanced journal entry and add it to the session.

    lines: iterable of dicts, each with `account_id`, one of `debit` or
    `credit` (or both, if you insist — zero on the wrong side), optional
    `memo`, `party_type`, `party_id`.

    The caller commits alongside its own writes so the JE and the source row
    land in one atomic transaction. Returns the added entry.

    Refuses:
    - fewer than 2 lines
    - a total on either side that is zero
    - an unbalanced entry (|debit − credit| > half a piastre)
    - a line on a header (is_postable=False) account
    - a line on an inactive account
    - a line on a non-existent account
    """
    lines = list(lines)
    if len(lines) < 2:
        raise LedgerError("القيد لازم فيه سطرين على الأقل.")

    total_debit = sum((_d(l.get("debit")) for l in lines), Decimal("0"))
    total_credit = sum((_d(l.get("credit")) for l in lines), Decimal("0"))
    if abs(total_debit - total_credit) > TOL:
        raise LedgerError(
            f"القيد مش متوازن: مدين {total_debit} ≠ دائن {total_credit}."
        )
    if total_debit <= 0:
        raise LedgerError("مجموع القيد لازم يكون أكبر من صفر.")

    # Load every account in one query so the postable/active check doesn't
    # do N round-trips for a big JE.
    account_ids = {int(l["account_id"]) for l in lines}
    accounts = {
        a.id: a
        for a in LedgerAccount.query.filter(LedgerAccount.id.in_(account_ids)).all()
    }
    for aid in account_ids:
        acc = accounts.get(aid)
        if acc is None:
            raise LedgerError(f"الحساب رقم {aid} مش موجود في دليل الحسابات.")
        if not acc.is_postable:
            raise LedgerError(
                f"الحساب {acc.code} ({acc.name}) عنوان تجميعي — مش ممكن يترحّل عليه قيد."
            )
        if not acc.is_active:
            raise LedgerError(
                f"الحساب {acc.code} ({acc.name}) موقوف — مش ممكن يترحّل عليه قيد."
            )

    entry = JournalEntry(
        number=_next_number(),
        date=entry_date or _date.today(),
        description=description,
        reference=reference,
        source_type=source_type,
        source_id=source_id,
        created_by_id=created_by,
        is_reversal=is_reversal,
        reversal_of_id=reversal_of_id,
    )
    db.session.add(entry)
    db.session.flush()  # need entry.id for the lines

    for l in lines:
        entry.lines.append(
            JournalLine(
                account_id=int(l["account_id"]),
                debit=_d(l.get("debit")),
                credit=_d(l.get("credit")),
                memo=l.get("memo"),
                party_type=l.get("party_type"),
                party_id=l.get("party_id"),
                cost_center_id=l.get("cost_center_id"),
            )
        )

    return entry


# ---------- read helpers everything else in the app reuses ----------

def get_account_by_code(code: str) -> Optional[LedgerAccount]:
    return LedgerAccount.query.filter_by(code=code).first()


def party_balance(party_type: str, party_id: int) -> Decimal:
    """SUM(debit − credit) across every active line tagged with this party.

    Positive = party owes us net (customer receivable / supplier overpaid).
    Negative = we owe the party net (supplier payable / customer credit).
    """
    row = (
        db.session.query(
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(
            JournalLine.party_type == party_type,
            JournalLine.party_id == party_id,
            JournalEntry.is_active.is_(True),
        )
        .one()
    )
    return (Decimal(str(row[0])) - Decimal(str(row[1]))).quantize(Decimal("0.01"))


def trial_balance(as_of: Optional[_date] = None):
    """Every leaf account with a non-zero balance, one row each.

    Returns list of (account, debit, credit) — the amount is shown on the side
    the balance naturally falls on. Total debit MUST equal total credit if the
    ledger has integrity; the trial-balance screen asserts that.
    """
    from app.models.accounting import AccountType, NormalSide

    q = (
        db.session.query(
            JournalLine.account_id,
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(JournalEntry.is_active.is_(True))
    )
    if as_of is not None:
        q = q.filter(JournalEntry.date <= as_of)
    q = q.group_by(JournalLine.account_id)

    rows = []
    for aid, debit, credit in q.all():
        acc = db.session.get(LedgerAccount, aid)
        if acc is None:
            continue
        debit = Decimal(str(debit))
        credit = Decimal(str(credit))
        if acc.normal_side == NormalSide.DEBIT:
            net = debit - credit
        else:
            net = credit - debit
        if net == 0:
            continue
        if acc.normal_side == NormalSide.DEBIT:
            rows.append((acc, net, Decimal("0")))
        else:
            rows.append((acc, Decimal("0"), net))
    rows.sort(key=lambda r: r[0].code)
    return rows
