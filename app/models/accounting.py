"""ACCOUNTING FOUNDATION — Chart of Accounts + double-entry journals.

Ported from marsoud's models/account.py + models/journal.py with the
multi-tenant (company_id) and multi-currency columns dropped: elyasmin is one
deployment, one currency (EGP).

Every money event in the app auto-posts one balanced entry through
`app.services.ledger.post_journal`. The old rows (Supplier.balance_due,
Customer.balance, TreasuryAccount.current_balance, Expense) keep working;
new screens read from these journals.
"""
import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db


class AccountType(enum.Enum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class NormalSide(enum.Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


# Which side a type's balance sits on when positive. Assets and expenses grow
# with a debit; the other three grow with a credit.
NORMAL_SIDE_FOR_TYPE = {
    AccountType.ASSET: NormalSide.DEBIT,
    AccountType.EXPENSE: NormalSide.DEBIT,
    AccountType.LIABILITY: NormalSide.CREDIT,
    AccountType.EQUITY: NormalSide.CREDIT,
    AccountType.REVENUE: NormalSide.CREDIT,
}


class LedgerAccount(db.Model):
    """One row in the Chart of Accounts.

    Hierarchical via `parent_id`. Headers (`is_postable=False`) exist for
    grouping and reporting only; `post_journal()` refuses any line that lands
    on them. Leaves accept journals as normal.
    """

    __tablename__ = "coa_accounts"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False, unique=True, index=True)
    name = db.Column(db.String(150), nullable=False)         # عربي primary label
    name_en = db.Column(db.String(150), nullable=True)       # optional English
    type = db.Column(db.Enum(AccountType), nullable=False)
    normal_side = db.Column(db.Enum(NormalSide), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("coa_accounts.id"), nullable=True, index=True)
    is_postable = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")

    # Optional link back to a TreasuryAccount (app.models.finance.TreasuryAccount)
    # — one COA leaf per real cash/bank drawer, so treasury movements know
    # which leaf to hit. Nullable so non-treasury accounts have nothing to link.
    treasury_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    parent = db.relationship("LedgerAccount", remote_side=[id], backref="children")

    def __repr__(self) -> str:
        return f"<LedgerAccount {self.code} {self.name}>"

    @property
    def display_name(self) -> str:
        return f"{self.code} — {self.name}"

    def descendants(self):
        """Every account reachable through parent → child, including self."""
        yield self
        for c in self.children:
            yield from c.descendants()

    @property
    def balance(self) -> Decimal:
        """Net balance. For a leaf, the sum of its lines expressed on the
        account's own normal side. For a header, the sum of its descendants."""
        if self.is_postable:
            row = (
                db.session.query(
                    func.coalesce(func.sum(JournalLine.debit), 0),
                    func.coalesce(func.sum(JournalLine.credit), 0),
                )
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .filter(JournalLine.account_id == self.id, JournalEntry.is_active.is_(True))
                .one()
            )
            debit, credit = Decimal(str(row[0])), Decimal(str(row[1]))
            if self.normal_side == NormalSide.DEBIT:
                return (debit - credit).quantize(Decimal("0.01"))
            return (credit - debit).quantize(Decimal("0.01"))

        # header — sum of children on this same normal side
        total = Decimal("0")
        for c in self.children:
            total += c.balance
        return total.quantize(Decimal("0.01"))


class JournalEntry(db.Model):
    """One balanced double-entry post. Every money event in the app produces
    exactly one of these (or, for a reversal, one more with is_reversal=True)."""

    __tablename__ = "journal_entries"

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), nullable=False, unique=True, index=True)  # JE-000001
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    description = db.Column(db.Text, nullable=False)
    reference = db.Column(db.String(50), nullable=True)   # e.g. cheque number

    # The row that produced this entry — an inline audit trail from the JE
    # back to the farm event. e.g. (source_type='SupplierPayment', source_id=42)
    source_type = db.Column(db.String(40), nullable=True, index=True)
    source_id = db.Column(db.Integer, nullable=True, index=True)

    # Paused entries are excluded from reports without being deleted; the
    # audit fields say who paused, why, and when it was reactivated.
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default="1", index=True)
    pause_reason = db.Column(db.Text, nullable=True)
    paused_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    paused_at = db.Column(db.DateTime, nullable=True)
    reactivate_reason = db.Column(db.Text, nullable=True)
    reactivated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reactivated_at = db.Column(db.DateTime, nullable=True)

    is_reversal = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    reversal_of_id = db.Column(db.Integer, db.ForeignKey("journal_entries.id"), nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    lines = db.relationship(
        "JournalLine", back_populates="entry",
        cascade="all, delete-orphan",
        order_by="JournalLine.id",
    )

    @property
    def total_debit(self) -> Decimal:
        return sum((Decimal(str(l.debit or 0)) for l in self.lines), Decimal("0"))

    @property
    def total_credit(self) -> Decimal:
        return sum((Decimal(str(l.credit or 0)) for l in self.lines), Decimal("0"))

    @property
    def is_balanced(self) -> bool:
        return abs(self.total_debit - self.total_credit) < Decimal("0.005")


class JournalLine(db.Model):
    """One side of one journal entry — a debit OR credit on one account.

    Exactly one of `debit` or `credit` should be non-zero on each line. The
    party columns (nullable) let the same line appear on a customer's or a
    supplier's party ledger without a separate posting.
    """

    __tablename__ = "journal_lines"

    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(
        db.Integer, db.ForeignKey("journal_entries.id"),
        nullable=False, index=True,
    )
    account_id = db.Column(
        db.Integer, db.ForeignKey("coa_accounts.id"),
        nullable=False, index=True,
    )
    debit = db.Column(db.Numeric(15, 4), nullable=False, default=Decimal("0"), server_default="0")
    credit = db.Column(db.Numeric(15, 4), nullable=False, default=Decimal("0"), server_default="0")
    memo = db.Column(db.Text, nullable=True)

    # Party tagging — a line posted against a supplier or customer carries the
    # party info so the party ledger is one filter, not a scan.
    party_type = db.Column(db.String(20), nullable=True, index=True)   # 'customer' | 'supplier'
    party_id = db.Column(db.Integer, nullable=True, index=True)

    # PHASE 2 — cost centre. Points at a herd group (cattle_groups.id) so
    # feed cost, milk revenue and any manually-tagged JE line can be sliced
    # by group without a shadow calculation. Nullable — a line that isn't
    # meaningfully tied to a group (e.g. an owner's capital injection, a
    # bank-to-bank transfer, a rent expense that covers the whole farm)
    # stays untagged.
    cost_center_id = db.Column(
        db.Integer, db.ForeignKey("cattle_groups.id"),
        nullable=True, index=True,
    )

    entry = db.relationship("JournalEntry", back_populates="lines")
    account = db.relationship("LedgerAccount", backref=db.backref("lines", lazy="dynamic"))
    cost_center = db.relationship("CattleGroup", foreign_keys=[cost_center_id])
