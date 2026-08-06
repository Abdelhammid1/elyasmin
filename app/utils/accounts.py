"""TREASURY: all account balance arithmetic lives here.

Five routes move money — supplier payments, customer collections, worker
payments, expenses and transfers — so the posting rules are kept in one place
rather than repeated in each.

`Account.current_balance` is always `opening_balance` plus the sum of that
account's movements. Nothing here commits; the calling route owns the
transaction.

Sign convention (see AccountMovement): `amount` is signed — in positive, out
negative.
"""
from decimal import Decimal

from app.extensions import db
from app.models.finance import Account, AccountMovement, AccountTransfer

MONEY = Decimal("0.01")

# Expense rows that merely MIRROR another payment. The payment already moved the
# account; posting the mirror too would debit it twice.
MIRROR_REF_TYPES = frozenset({"supplier_payment", "worker_payment"})


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def expense_moves_money(expense) -> bool:
    """True when this Expense is a cash event in its own right.

    A manual expense (ref_type None) and a cash purchase invoice
    (ref_type 'purchase_invoice_cash') really do take money out. A row mirroring
    a supplier or worker payment does not — that payment posted its own movement.
    """
    return expense.ref_type not in MIRROR_REF_TYPES


def post(account, amount, movement_type, moved_on, *, ref_type=None, ref_id=None,
         notes=None, user_id=None) -> AccountMovement:
    """Record one signed movement and move the balance with it.

    `amount` is signed by the caller: positive in, negative out.
    """
    amount = _d(amount).quantize(MONEY)
    if amount == 0:
        raise ValueError("قيمة الحركة لازم تكون أكبر من صفر.")

    mv = AccountMovement(
        account_id=account.id,
        movement_type=movement_type,
        amount=amount,
        ref_type=ref_type,
        ref_id=ref_id,
        moved_on=moved_on,
        notes=notes,
        created_by_id=user_id,
    )
    db.session.add(mv)
    account.current_balance = (_d(account.current_balance) + amount).quantize(MONEY)
    return mv


def money_in(account, amount, moved_on, **kw) -> AccountMovement:
    """A collection — money arriving in the account."""
    return post(account, abs(_d(amount)), AccountMovement.TYPE_IN, moved_on, **kw)


def money_out(account, amount, moved_on, **kw) -> AccountMovement:
    """A payment or expense — money leaving the account."""
    return post(account, -abs(_d(amount)), AccountMovement.TYPE_OUT, moved_on, **kw)


def transfer(from_account, to_account, amount, transfer_date, *, notes=None,
             user_id=None) -> AccountTransfer:
    """Move money between two accounts, writing one movement on each side.

    Raises ValueError (already in Arabic) when the transfer makes no sense or
    the source cannot cover it.
    """
    amount = _d(amount).quantize(MONEY)
    if amount <= 0:
        raise ValueError("المبلغ المحوَّل لازم يكون أكبر من صفر.")
    if from_account.id == to_account.id:
        raise ValueError("مش ممكن تحوّل من الحساب لنفسه — اختار حسابين مختلفين.")

    available = _d(from_account.current_balance)
    if amount > available:
        raise ValueError(
            f"رصيد {from_account.name} هو {available} جنيه بس — مش كفاية لتحويل {amount} جنيه."
        )

    tr = AccountTransfer(
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        amount=amount,
        transfer_date=transfer_date,
        notes=notes,
        created_by_id=user_id,
    )
    db.session.add(tr)
    db.session.flush()

    post(from_account, -amount, AccountMovement.TYPE_TRANSFER_OUT, transfer_date,
         ref_type="account_transfer", ref_id=tr.id, user_id=user_id,
         notes=f"تحويل إلى {to_account.name}")
    post(to_account, amount, AccountMovement.TYPE_TRANSFER_IN, transfer_date,
         ref_type="account_transfer", ref_id=tr.id, user_id=user_id,
         notes=f"تحويل من {from_account.name}")
    return tr


def recompute_balance(account) -> Decimal:
    """Rebuild current_balance from the ledger — a repair path, and what the
    tests assert against to prove the balance never drifts."""
    total = sum(
        (_d(m.amount) for m in account.movements), _d(account.opening_balance)
    )
    account.current_balance = total.quantize(MONEY)
    return account.current_balance


def active_choices():
    """(id, label) pairs for the account picker on every payment form."""
    accounts = (
        Account.query.filter_by(is_archived=False).order_by(Account.name).all()
    )
    return [(a.id, f"{a.display_name} ({a.current_balance})") for a in accounts]
