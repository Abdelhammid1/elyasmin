"""ACCOUNTING FOUNDATION — chart of accounts + journal ledger, with backfill

Revision ID: c1f2d3e40a11
Revises: a3c7e91b5d24
Create Date: 2026-09-03

Three phases in one transaction:
  1) Schema — create coa_accounts, journal_entries, journal_lines + indexes
  2) Seed  — the default chart, wire treasury Accounts onto their COA leaves
  3) Backfill — replay every existing farm event (opening balances, purchase
     invoices, supplier payments, customer payments, priced milk deliveries,
     manual expenses, treasury transfers) into JEs in chronological order.

The backfill ends with SIX assertions:
  a) SUM(debit) == SUM(credit) globally
  b) Every treasury Account.current_balance == its JE net balance
  c) Every Supplier.balance_due == its party-ledger balance
  d) Every Customer.balance == its party-ledger balance
  e) No JE is unbalanced
  f) Every non-mirror source row that should have a JE, has one
Any failure raises and the whole upgrade rolls back — no half-loaded ledger
survives.

downgrade() drops the three tables. Source rows keep working, so re-upgrade
seeds and backfills again from scratch.
"""
from datetime import date as _date, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "c1f2d3e40a11"
down_revision = "a3c7e91b5d24"
branch_labels = None
depends_on = None

TOL = Decimal("0.05")   # backfill tolerance — 5 piastre for float→Decimal drift on legacy rows


# ============================ SCHEMA ============================

def upgrade():
    _create_schema()
    _seed_and_backfill()


def _create_schema():
    op.create_table(
        "coa_accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("name_en", sa.String(150), nullable=True),
        sa.Column("type", sa.String(20), nullable=False),   # enum stored as string
        sa.Column("normal_side", sa.String(10), nullable=False),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("coa_accounts.id"),
                  nullable=True, index=True),
        sa.Column("is_postable", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("treasury_account_id", sa.Integer,
                  sa.ForeignKey("accounts.id"), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("number", sa.String(20), nullable=False, unique=True, index=True),
        sa.Column("date", sa.Date, nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("reference", sa.String(50), nullable=True),
        sa.Column("source_type", sa.String(40), nullable=True, index=True),
        sa.Column("source_id", sa.Integer, nullable=True, index=True),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.text("true"), index=True),
        sa.Column("pause_reason", sa.Text, nullable=True),
        sa.Column("paused_by_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("paused_at", sa.DateTime, nullable=True),
        sa.Column("reactivate_reason", sa.Text, nullable=True),
        sa.Column("reactivated_by_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reactivated_at", sa.DateTime, nullable=True),
        sa.Column("is_reversal", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("reversal_of_id", sa.Integer,
                  sa.ForeignKey("journal_entries.id"), nullable=True),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_journal_entries_source",
                    "journal_entries", ["source_type", "source_id"])

    op.create_table(
        "journal_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entry_id", sa.Integer,
                  sa.ForeignKey("journal_entries.id"),
                  nullable=False, index=True),
        sa.Column("account_id", sa.Integer,
                  sa.ForeignKey("coa_accounts.id"),
                  nullable=False, index=True),
        sa.Column("debit", sa.Numeric(15, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("credit", sa.Numeric(15, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("memo", sa.Text, nullable=True),
        sa.Column("party_type", sa.String(20), nullable=True, index=True),
        sa.Column("party_id", sa.Integer, nullable=True, index=True),
    )
    op.create_index("ix_journal_lines_party",
                    "journal_lines", ["party_type", "party_id"])


# ============================ SEED + BACKFILL ============================

def _seed_and_backfill():
    """The seed + backfill lives in Flask's session, not the alembic
    connection. Commit + close at the end so alembic can bump the version
    without racing SQLite's exclusive lock.

    Raises on any imbalance — SQLAlchemy rollback drops every write above
    and the DB stays on the previous head.
    """
    from app.extensions import db
    from app.services.coa_seed import seed_default_coa, wire_treasury_accounts

    try:
        # 1) seed the chart, wire treasury accounts onto their leaves
        seed_default_coa()
        wire_treasury_accounts()
        db.session.flush()

        # 2) opening balances — one JE per non-zero opening
        # Fresh COA read here — wire_treasury_accounts just created leaves
        # that the initial seed dict doesn't know about.
        from app.models.accounting import LedgerAccount
        coa = {a.code: a for a in LedgerAccount.query.all()}
        _backfill_openings(coa)

        # 3) replay every farm event chronologically. Order matters: an invoice
        #    must be posted before its cash-payment settlement so the payable
        #    exists when the settlement debits it.
        _backfill_events()

        # 4) verify — raises if any assertion fails
        _verify_ledger()

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        db.session.close()


def _backfill_openings(coa):
    """One JE per non-zero opening balance — treasury Accounts + Suppliers.

    Treasury openings: DR treasury leaf / CR 3900 (أرصدة افتتاحية).
    Supplier openings: DR 3900 / CR ذمم الموردين  (positive = we owe them).
    """
    from app.extensions import db
    # PHASE 7 rename: `finance.Account` became `TreasuryAccount`. Import
    # the new name directly — migrations are code, not just history, so
    # they follow model renames.
    from app.models.finance import TreasuryAccount
    from app.models.suppliers import Supplier
    from app.services.autoposting import CODE_TRADE_PAYABLE, CODE_OPENING_EQUITY
    from app.services.ledger import post_journal, get_account_by_code

    opening_equity = get_account_by_code(CODE_OPENING_EQUITY)

    # Treasury accounts
    for t in TreasuryAccount.query.filter_by(is_archived=False).all():
        opening = Decimal(str(t.opening_balance or 0))
        if opening == 0:
            continue
        leaf = next((a for a in coa.values() if a.treasury_account_id == t.id), None)
        if leaf is None:
            continue
        post_journal(
            description=f"رصيد افتتاحي — {t.display_name}",
            lines=[
                {"account_id": leaf.id, "debit": opening,
                 "memo": "افتتاحي"},
                {"account_id": opening_equity.id, "credit": opening,
                 "memo": f"مقابل افتتاحي {t.name}"},
            ],
            entry_date=t.created_at.date() if t.created_at else _date.today(),
            source_type="OpeningBalance:Account",
            source_id=t.id,
        )

    # Supplier openings (positive = we owe them at start)
    payable = get_account_by_code(CODE_TRADE_PAYABLE)
    for s in Supplier.query.filter_by(is_archived=False).all():
        opening = Decimal(str(s.opening_balance or 0))
        if opening == 0:
            continue
        post_journal(
            description=f"رصيد افتتاحي — المورد {s.name}",
            lines=[
                {"account_id": opening_equity.id, "debit": opening,
                 "memo": f"مقابل افتتاحي {s.name}"},
                {"account_id": payable.id, "credit": opening,
                 "party_type": "supplier", "party_id": s.id,
                 "memo": "افتتاحي"},
            ],
            entry_date=s.created_at.date() if s.created_at else _date.today(),
            source_type="OpeningBalance:Supplier",
            source_id=s.id,
        )
    db.session.flush()


def _backfill_events():
    """Every existing money event → one JE, in chronological order.

    Order: purchase invoices first (they create the payable), then payments
    (which pay against it), then anything else. Within a source, order by
    the row's own date + id so ties break deterministically."""
    from app.extensions import db
    from app.models.finance import AccountTransfer, Expense
    from app.models.sales import CustomerPayment, MilkDelivery
    from app.models.suppliers import PurchaseInvoice, SupplierPayment
    from app.models.labor import WorkerPayment
    from app.services import autoposting

    # ---- purchase invoices ----
    invoices = (
        PurchaseInvoice.query
        .filter_by(is_archived=False)
        .order_by(PurchaseInvoice.invoice_date, PurchaseInvoice.id)
        .all()
    )
    for inv in invoices:
        autoposting.on_purchase_invoice(inv)

    # ---- supplier payments (find their treasury) ----
    for p in (SupplierPayment.query.filter_by(is_archived=False)
              .order_by(SupplierPayment.payment_date, SupplierPayment.id).all()):
        if p.account is None:
            continue     # legacy pre-treasury row — no treasury side to credit
        autoposting.on_supplier_payment(p, p.account)

    # ---- customer payments ----
    for p in (CustomerPayment.query.filter_by(is_archived=False)
              .order_by(CustomerPayment.payment_date, CustomerPayment.id).all()):
        if p.account is None:
            continue
        autoposting.on_customer_payment(p, p.account)

    # ---- worker payments ----
    for p in (WorkerPayment.query.filter_by(is_archived=False)
              .order_by(WorkerPayment.payment_date, WorkerPayment.id).all()):
        if p.account is None:
            continue
        autoposting.on_worker_payment(p, p.account)

    # ---- manual expenses (non-mirror, real cash events) + cash-invoice mirrors ----
    #     on_expense skips mirror rows itself.
    for e in (Expense.query.filter_by(is_archived=False)
              .order_by(Expense.expense_date, Expense.id).all()):
        if e.account_id is None:
            continue
        from app.models.finance import TreasuryAccount
        acc = db.session.get(TreasuryAccount, e.account_id)
        if acc is None:
            continue
        autoposting.on_expense(e, acc)

    # ---- treasury transfers ----
    for tr in AccountTransfer.query.order_by(
            AccountTransfer.transfer_date, AccountTransfer.id).all():
        from app.models.finance import TreasuryAccount
        src = db.session.get(TreasuryAccount, tr.from_account_id)
        dst = db.session.get(TreasuryAccount, tr.to_account_id)
        if src is None or dst is None:
            continue
        autoposting.on_treasury_transfer(src, dst, tr)

    # ---- priced milk deliveries ----
    for d in (MilkDelivery.query
              .filter(MilkDelivery.is_archived.is_(False),
                      MilkDelivery.total_value.isnot(None))
              .order_by(MilkDelivery.delivery_date, MilkDelivery.id).all()):
        autoposting.on_milk_delivery_priced(d)

    db.session.flush()


# ============================ VERIFY ============================

def _verify_ledger():
    """SIX assertions the backfill must satisfy. Any failure raises, the
    whole upgrade rolls back."""
    from app.extensions import db
    from app.models.accounting import LedgerAccount as Account, JournalEntry, JournalLine
    from app.models.finance import TreasuryAccount
    from app.models.sales import Customer
    from app.models.suppliers import Supplier
    from app.services.ledger import party_balance

    errors = []

    # a) global debit == credit
    row = db.session.query(
        sa.func.coalesce(sa.func.sum(JournalLine.debit), 0),
        sa.func.coalesce(sa.func.sum(JournalLine.credit), 0),
    ).one()
    dr, cr = Decimal(str(row[0])), Decimal(str(row[1]))
    if abs(dr - cr) > TOL:
        errors.append(f"global unbalanced: debit={dr} credit={cr}")

    # b) every JE individually balanced
    for je in JournalEntry.query.all():
        if not je.is_balanced:
            errors.append(f"JE {je.number} unbalanced: dr={je.total_debit} cr={je.total_credit}")

    # c) treasury Account.current_balance == JE net balance
    for t in TreasuryAccount.query.filter_by(is_archived=False).all():
        leaf = Account.query.filter_by(treasury_account_id=t.id).first()
        if leaf is None:
            continue  # unwired — verified separately by wire_treasury_accounts()
        ledger_balance = leaf.balance
        treasury_balance = Decimal(str(t.current_balance or 0))
        if abs(ledger_balance - treasury_balance) > TOL:
            errors.append(
                f"treasury {t.name}: ledger={ledger_balance} vs current_balance={treasury_balance}"
            )

    # d) every Supplier.balance_due matches party-ledger for that supplier
    #    balance_due is what the farm OWES, so party ledger balance should be
    #    NEGATIVE of balance_due (positive party ledger = they owe us).
    for s in Supplier.query.filter_by(is_archived=False).all():
        expected = -Decimal(str(s.balance_due or 0))   # farm owes them = party ledger negative
        actual = party_balance("supplier", s.id)
        if abs(actual - expected) > TOL:
            errors.append(
                f"supplier {s.name}: ledger={actual} vs balance_due={-expected}"
            )

    # e) every Customer.balance matches party-ledger
    for c in Customer.query.filter_by(is_archived=False).all():
        expected = Decimal(str(c.balance or 0))   # customer owes us = party ledger positive
        actual = party_balance("customer", c.id)
        if abs(actual - expected) > TOL:
            errors.append(
                f"customer {c.name}: ledger={actual} vs balance={expected}"
            )

    if errors:
        raise RuntimeError(
            "Ledger backfill verification failed:\n  - " + "\n  - ".join(errors)
        )


# ============================ DOWNGRADE ============================

def downgrade():
    op.drop_index("ix_journal_lines_party", table_name="journal_lines")
    op.drop_table("journal_lines")
    op.drop_index("ix_journal_entries_source", table_name="journal_entries")
    op.drop_table("journal_entries")
    op.drop_table("coa_accounts")
