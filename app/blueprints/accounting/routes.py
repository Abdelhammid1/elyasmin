"""ACCOUNTING FOUNDATION — the four read-only screens.

- /accounting/          index (menu of the four screens)
- /accounting/coa       chart of accounts tree
- /accounting/journal   date-ranged list of journal entries
- /accounting/journal/<id>  one JE and its lines
- /accounting/party/<type>/<id>  a customer's or supplier's party ledger
- /accounting/trial-balance  as-of trial balance

Nothing writes here — every JE in the system is produced by the autoposting
service in response to a farm event. A "manual JE" screen is a later phase.
"""
from datetime import date as _date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func, or_

from app.extensions import db
from app.models.accounting import (
    AccountType, JournalEntry, JournalLine, LedgerAccount, NormalSide,
)
from app.models.sales import Customer
from app.models.suppliers import Supplier
from app.services.ledger import party_balance, trial_balance
from app.services.statements import (
    balance_sheet, cash_flow, income_statement, milk_cost_by_group,
)

bp = Blueprint("accounting", __name__, template_folder="../../templates/accounting")


# ---------- index ----------
@bp.route("/")
@login_required
def index():
    return render_template("accounting/index.html")


# ---------- Chart of Accounts ----------
@bp.route("/coa")
@login_required
def chart_of_accounts():
    """Every root account and its descendants, top-down. Each account's
    displayed balance is the LedgerAccount.balance property (recursive on
    headers, live sum on leaves)."""
    roots = (
        LedgerAccount.query
        .filter(LedgerAccount.parent_id.is_(None))
        .order_by(LedgerAccount.code)
        .all()
    )
    return render_template("accounting/coa.html", roots=roots)


# ---------- Journal listing ----------
def _period():
    """Filters: date_from / date_to / source_type / q (free text on
    description). Defaults to the last 30 days."""
    today = _date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = _date.fromisoformat(fm) if fm else today - timedelta(days=30)
    d_to = _date.fromisoformat(to) if to else today
    return d_from, d_to


@bp.route("/journal")
@login_required
def journal_list():
    d_from, d_to = _period()
    source_type = request.args.get("source_type") or ""
    q = (request.args.get("q") or "").strip()

    query = JournalEntry.query.filter(
        JournalEntry.date >= d_from,
        JournalEntry.date <= d_to,
        JournalEntry.is_active.is_(True),
    )
    if source_type:
        query = query.filter(JournalEntry.source_type == source_type)
    if q:
        query = query.filter(JournalEntry.description.ilike(f"%{q}%"))

    entries = query.order_by(
        JournalEntry.date.desc(), JournalEntry.id.desc()
    ).limit(500).all()

    # Distinct source types in the current window, for the filter dropdown
    source_choices = (
        db.session.query(JournalEntry.source_type)
        .filter(JournalEntry.source_type.isnot(None))
        .distinct().order_by(JournalEntry.source_type).all()
    )
    source_choices = [s[0] for s in source_choices]

    # Totals for the visible slice
    total_debit = sum(je.total_debit for je in entries)
    total_credit = sum(je.total_credit for je in entries)

    return render_template(
        "accounting/journal_list.html",
        entries=entries, source_choices=source_choices,
        date_from=d_from, date_to=d_to,
        source_type=source_type, q=q,
        total_debit=total_debit, total_credit=total_credit,
    )


@bp.route("/journal/<int:entry_id>")
@login_required
def journal_detail(entry_id):
    je = db.session.get(JournalEntry, entry_id)
    if je is None:
        abort(404)
    # Cheap trace back to the source screen where possible
    source_url = _source_url(je.source_type, je.source_id)
    return render_template("accounting/journal_detail.html",
                           entry=je, source_url=source_url)


def _source_url(source_type, source_id):
    """The farm screen that produced this JE, so the JE detail can link back."""
    if not source_type or not source_id:
        return None
    if source_type == "SupplierPayment":
        from app.models.suppliers import SupplierPayment
        p = db.session.get(SupplierPayment, source_id)
        return url_for("suppliers.supplier_detail", supplier_id=p.supplier_id) if p else None
    if source_type == "CustomerPayment":
        from app.models.sales import CustomerPayment
        p = db.session.get(CustomerPayment, source_id)
        return url_for("customers.customer_detail", customer_id=p.customer_id) if p else None
    if source_type == "MilkDelivery":
        return url_for("milk.edit_delivery", delivery_id=source_id)
    if source_type == "PurchaseInvoice":
        return url_for("purchases.view_invoice", invoice_id=source_id)
    if source_type == "AccountTransfer":
        return url_for("accounts.list_accounts")
    if source_type.startswith("OpeningBalance:"):
        return None  # openings don't have their own screen
    return None


# ---------- Party ledger ----------
@bp.route("/party/<party_type>/<int:party_id>")
@login_required
def party_ledger(party_type, party_id):
    if party_type not in ("customer", "supplier"):
        abort(404)

    party = (
        db.session.get(Customer, party_id) if party_type == "customer"
        else db.session.get(Supplier, party_id)
    )
    if party is None:
        abort(404)

    lines = (
        JournalLine.query
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(
            JournalLine.party_type == party_type,
            JournalLine.party_id == party_id,
            JournalEntry.is_active.is_(True),
        )
        .order_by(JournalEntry.date, JournalEntry.id, JournalLine.id)
        .all()
    )

    # Running balance, one row at a time
    rows = []
    running = Decimal("0")
    for l in lines:
        delta = Decimal(str(l.debit or 0)) - Decimal(str(l.credit or 0))
        running += delta
        rows.append({"line": l, "delta": delta.quantize(Decimal("0.01")),
                     "running": running.quantize(Decimal("0.01"))})

    return render_template(
        "accounting/party_ledger.html",
        party=party, party_type=party_type, rows=rows,
        balance=party_balance(party_type, party_id),
    )


# ---------- Trial Balance ----------
@bp.route("/trial-balance")
@login_required
def trial_balance_view():
    as_of_str = request.args.get("as_of")
    as_of = _date.fromisoformat(as_of_str) if as_of_str else _date.today()

    rows = trial_balance(as_of=as_of)
    total_debit = sum((r[1] for r in rows), Decimal("0"))
    total_credit = sum((r[2] for r in rows), Decimal("0"))

    return render_template(
        "accounting/trial_balance.html",
        rows=rows, as_of=as_of,
        total_debit=total_debit, total_credit=total_credit,
        is_balanced=abs(total_debit - total_credit) < Decimal("0.05"),
    )


# ==================== PHASE 2 — the three statements ====================

def _report_dates():
    today = _date.today()
    fm = request.args.get("date_from")
    to = request.args.get("date_to")
    d_from = _date.fromisoformat(fm) if fm else today.replace(day=1)
    d_to = _date.fromisoformat(to) if to else today
    return d_from, d_to


@bp.route("/income-statement")
@login_required
def income_statement_view():
    d_from, d_to = _report_dates()
    data = income_statement(d_from, d_to)
    return render_template(
        "accounting/income_statement.html",
        date_from=d_from, date_to=d_to, **data,
    )


@bp.route("/balance-sheet")
@login_required
def balance_sheet_view():
    as_of_str = request.args.get("as_of")
    as_of = _date.fromisoformat(as_of_str) if as_of_str else _date.today()
    data = balance_sheet(as_of)
    return render_template(
        "accounting/balance_sheet.html", as_of=as_of, **data,
    )


@bp.route("/cash-flow")
@login_required
def cash_flow_view():
    d_from, d_to = _report_dates()
    data = cash_flow(d_from, d_to)
    return render_template(
        "accounting/cash_flow.html",
        date_from=d_from, date_to=d_to, **data,
    )


@bp.route("/milk-cost-by-group")
@login_required
def milk_cost_by_group_view():
    d_from, d_to = _report_dates()
    data = milk_cost_by_group(d_from, d_to)
    return render_template(
        "accounting/milk_cost_by_group.html",
        date_from=d_from, date_to=d_to, **data,
    )
