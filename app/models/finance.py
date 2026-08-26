from datetime import date, datetime
from decimal import Decimal

from app.extensions import db


class Setting(db.Model):
    __tablename__ = "settings"

    KEY_COST_SPLIT_MILK_PCT = "cost_split_milk_pct"
    KEY_COST_SPLIT_OTHERS_PCT = "cost_split_others_pct"
    KEY_QUALITY_PRICE_BASE = "quality_price_base"           # base price EGP/kg
    KEY_QUALITY_PROTEIN_ADJ = "quality_protein_adj"         # + EGP per +1% protein above 3
    KEY_QUALITY_BACTERIA_PENALTY = "quality_bacteria_penalty"  # − EGP per 100k CFU above 100k
    # TICKET-A: fat joins the analysis the price is built from. Ships with
    # fat_adj = 0 so no existing price moves until the client sets his rate.
    KEY_QUALITY_FAT_REF = "quality_fat_ref"                 # fat % the bonus starts above
    KEY_QUALITY_FAT_ADJ = "quality_fat_adj"                 # + EGP per +1% fat above the ref

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        row = db.session.get(cls, key)
        return row.value if row else default

    @classmethod
    def get_decimal(cls, key: str, default: Decimal = Decimal("0")) -> Decimal:
        raw = cls.get(key, "")
        try:
            return Decimal(raw) if raw else default
        except Exception:  # noqa: BLE001
            return default

    @classmethod
    def set(cls, key: str, value: str, description: str | None = None) -> None:
        row = db.session.get(cls, key)
        if row:
            row.value = value
            if description is not None:
                row.description = description
        else:
            db.session.add(cls(key=key, value=value, description=description))


class Expense(db.Model):
    """Cash-outflow / expense record.

    Populated by:
      - supplier payments  (ref_type='supplier_payment')
      - worker payments    (ref_type='worker_payment')
      - manual entries     (ref_type=None)
    Purchase invoices themselves are NOT expenses at invoice time; they become
    expenses via supplier payments (or immediately for cash invoices).
    """

    __tablename__ = "expenses"

    CAT_ELECTRICITY = "electricity"
    CAT_MAINTENANCE = "maintenance"
    CAT_RENT = "rent"
    CAT_FEED_PURCHASE = "feed_purchase"
    CAT_MEDICINE_PURCHASE = "medicine_purchase"
    CAT_SUPPLIER_PAYMENT = "supplier_payment"
    CAT_WORKER_WAGE = "worker_wage"
    CAT_OTHER = "other"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(40), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    expense_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    description = db.Column(db.String(255), nullable=True)

    ref_type = db.Column(db.String(40), nullable=True)  # supplier_payment / worker_payment / manual
    ref_id = db.Column(db.Integer, nullable=True)

    # TREASURY: which account the money left. Nullable — rows created before the
    # accounts feature existed have no account and are excluded from balances.
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True)

    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    LABELS = {
        CAT_ELECTRICITY: "كهرباء",
        CAT_MAINTENANCE: "صيانة",
        CAT_RENT: "إيجار",
        CAT_FEED_PURCHASE: "شراء علف",
        CAT_MEDICINE_PURCHASE: "شراء أدوية",
        CAT_SUPPLIER_PAYMENT: "دفعة مورد",
        CAT_WORKER_WAGE: "أجور عمالة",
        CAT_OTHER: "أخرى",
    }

    @property
    def category_label(self) -> str:
        if self.category and self.category.startswith("custom:"):
            return self.category[len("custom:"):]
        return self.LABELS.get(self.category, self.category)


class Account(db.Model):
    """TREASURY: a real place money sits — the cash drawer or a bank account.

    Any number of bank accounts can exist; each is an ordinary row added from
    the accounts screen, exactly like adding a stock item. There is no fixed
    single bank.

    `current_balance` is always `opening_balance` plus the sum of this account's
    movements — see app/utils/accounts.py, which is the only place it changes.
    """

    __tablename__ = "accounts"

    TYPE_CASH = "cash"
    TYPE_BANK = "bank"

    TYPE_LABELS = {TYPE_CASH: "خزنة نقدية", TYPE_BANK: "حساب بنكي"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    account_type = db.Column(db.String(20), nullable=False, default=TYPE_CASH)
    bank_name = db.Column(db.String(120), nullable=True)
    account_number = db.Column(db.String(60), nullable=True)

    opening_balance = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))
    current_balance = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0"))

    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    movements = db.relationship(
        "AccountMovement",
        back_populates="account",
        order_by="AccountMovement.moved_on, AccountMovement.id",
    )

    @property
    def type_label(self) -> str:
        return self.TYPE_LABELS.get(self.account_type, self.account_type)

    @property
    def display_name(self) -> str:
        if self.account_type == self.TYPE_BANK and self.bank_name:
            return f"{self.name} — {self.bank_name}"
        return self.name


class AccountMovement(db.Model):
    """TREASURY: one row per real cash event on an account.

    SIGN CONVENTION: `amount` is signed — money in is positive, money out is
    negative. That makes a statement's running balance a plain cumulative sum
    and the account balance a single SUM.

    IMPORTANT — one movement per cash event. Expense rows that merely mirror a
    supplier or worker payment (ref_type 'supplier_payment' / 'worker_payment')
    must NOT write a movement: the payment itself already did, and posting both
    would debit the account twice. See app/utils/accounts.py:expense_moves_money.
    """

    __tablename__ = "account_movements"

    TYPE_IN = "in"
    TYPE_OUT = "out"
    TYPE_TRANSFER_IN = "transfer_in"
    TYPE_TRANSFER_OUT = "transfer_out"

    TYPE_LABELS = {
        TYPE_IN: "وارد",
        TYPE_OUT: "منصرف",
        TYPE_TRANSFER_IN: "تحويل وارد",
        TYPE_TRANSFER_OUT: "تحويل صادر",
    }

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    movement_type = db.Column(db.String(20), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)

    ref_type = db.Column(db.String(40), nullable=True)   # supplier_payment / customer_payment / ...
    ref_id = db.Column(db.Integer, nullable=True)

    moved_on = db.Column(db.Date, nullable=False, default=date.today, index=True)
    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    account = db.relationship("Account", back_populates="movements")

    @property
    def type_label(self) -> str:
        return self.TYPE_LABELS.get(self.movement_type, self.movement_type)

    @property
    def abs_amount(self) -> Decimal:
        return abs(Decimal(str(self.amount or 0)))

    @property
    def is_inflow(self) -> bool:
        return Decimal(str(self.amount or 0)) > 0


class AccountTransfer(db.Model):
    """TREASURY: moving money between two accounts (drawer -> bank, etc).

    Writes two AccountMovement rows, one per side, so both balances and both
    statements stay correct.
    """

    __tablename__ = "account_transfers"

    id = db.Column(db.Integer, primary_key=True)
    from_account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    to_account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    transfer_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    from_account = db.relationship("Account", foreign_keys=[from_account_id])
    to_account = db.relationship("Account", foreign_keys=[to_account_id])
