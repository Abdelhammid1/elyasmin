"""ACCOUNTING P2 — the three financial statements + milk-cost-by-group,
built entirely from journal entries.

Every function reads from the ledger, never from the source rows. That's
the whole point of the accounting foundation — a P&L is `SUM(revenue) −
SUM(expense)` from journals, not a rebuild of expense rows plus a
percentage split.

Nothing here writes to the DB.
"""
from datetime import date as _date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from app.extensions import db
from app.models.accounting import (
    AccountType, JournalEntry, JournalLine, LedgerAccount, NormalSide,
)
from app.models.herd import CattleGroup


def _pair(account_id, d_from, d_to):
    """(debit, credit) summed for one account over a date range,
    excluding paused entries."""
    q = (
        db.session.query(
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(
            JournalLine.account_id == account_id,
            JournalEntry.is_active.is_(True),
        )
    )
    if d_from is not None:
        q = q.filter(JournalEntry.date >= d_from)
    if d_to is not None:
        q = q.filter(JournalEntry.date <= d_to)
    d, c = q.one()
    return Decimal(str(d)), Decimal(str(c))


def _leaves_of_type(t):
    return (
        LedgerAccount.query
        .filter(LedgerAccount.type == t, LedgerAccount.is_postable.is_(True))
        .order_by(LedgerAccount.code)
        .all()
    )


# ============ INCOME STATEMENT ============

def income_statement(d_from: _date, d_to: _date):
    """قائمة الدخل — revenue and expense rows for the period, plus totals.

    Returns {revenues, expenses, total_revenue, total_expense, net} where
    revenues/expenses are lists of {account, amount}."""
    revenues = []
    total_rev = Decimal("0")
    for acc in _leaves_of_type(AccountType.REVENUE):
        d, c = _pair(acc.id, d_from, d_to)
        amt = (c - d).quantize(Decimal("0.01"))    # CR minus DR for a REVENUE
        if amt != 0:
            revenues.append({"account": acc, "amount": amt})
            total_rev += amt

    expenses = []
    total_exp = Decimal("0")
    for acc in _leaves_of_type(AccountType.EXPENSE):
        d, c = _pair(acc.id, d_from, d_to)
        amt = (d - c).quantize(Decimal("0.01"))    # DR minus CR for an EXPENSE
        if amt != 0:
            expenses.append({"account": acc, "amount": amt})
            total_exp += amt

    return {
        "revenues": revenues,
        "expenses": expenses,
        "total_revenue": total_rev,
        "total_expense": total_exp,
        "net": (total_rev - total_exp).quantize(Decimal("0.01")),
    }


# ============ BALANCE SHEET ============

def balance_sheet(as_of: _date):
    """الميزانية العمومية — assets / liabilities / equity as of a date.

    Retained earnings is derived on the fly: SUM(revenue) − SUM(expense) up
    to the as-of date. This is the standard "period income folded into
    equity" shape and is what makes the two sides add up.

    Returns {assets, liabilities, equity, total_assets, total_liab_equity,
    is_balanced, retained_earnings}."""
    assets = []
    total_assets = Decimal("0")
    for acc in _leaves_of_type(AccountType.ASSET):
        d, c = _pair(acc.id, None, as_of)
        amt = (d - c).quantize(Decimal("0.01"))
        if amt != 0:
            assets.append({"account": acc, "amount": amt})
            total_assets += amt

    liabilities = []
    total_liab = Decimal("0")
    for acc in _leaves_of_type(AccountType.LIABILITY):
        d, c = _pair(acc.id, None, as_of)
        amt = (c - d).quantize(Decimal("0.01"))
        if amt != 0:
            liabilities.append({"account": acc, "amount": amt})
            total_liab += amt

    equity = []
    total_eq_direct = Decimal("0")
    for acc in _leaves_of_type(AccountType.EQUITY):
        d, c = _pair(acc.id, None, as_of)
        amt = (c - d).quantize(Decimal("0.01"))
        if amt != 0:
            equity.append({"account": acc, "amount": amt})
            total_eq_direct += amt

    # Retained earnings = revenue − expense to date (period income folded in)
    total_rev = Decimal("0")
    for acc in _leaves_of_type(AccountType.REVENUE):
        d, c = _pair(acc.id, None, as_of)
        total_rev += (c - d)
    total_exp = Decimal("0")
    for acc in _leaves_of_type(AccountType.EXPENSE):
        d, c = _pair(acc.id, None, as_of)
        total_exp += (d - c)
    retained = (total_rev - total_exp).quantize(Decimal("0.01"))

    total_equity = (total_eq_direct + retained).quantize(Decimal("0.01"))
    total_liab_equity = (total_liab + total_equity).quantize(Decimal("0.01"))

    return {
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "retained_earnings": retained,
        "total_assets": total_assets.quantize(Decimal("0.01")),
        "total_liabilities": total_liab.quantize(Decimal("0.01")),
        "total_equity": total_equity,
        "total_liab_equity": total_liab_equity,
        "is_balanced": abs(total_assets - total_liab_equity) < Decimal("0.05"),
    }


# ============ CASH FLOW (direct method) ============

def _classify_cashflow(other_account: LedgerAccount) -> str:
    """The category a cash-touching line falls into, decided by the OTHER
    side of the entry. Direct-method categorisation.

    - INVESTING: assets under 1500 (fixed assets) — buying/selling equipment
    - FINANCING: equity or long-term liabilities — capital injections, loans
    - OPERATING: everything else touching a treasury account — day-to-day
      running of the farm, which is most of it.
    """
    code = other_account.code or ""
    if code.startswith("1500") or code.startswith("1510"):
        return "investing"
    if other_account.type == AccountType.EQUITY:
        return "financing"
    if other_account.type == AccountType.LIABILITY and code.startswith("29"):
        return "financing"   # long-term liabilities live under 2900+ in this chart
    return "operating"


def cash_flow(d_from: _date, d_to: _date):
    """قائمة التدفقات النقدية (direct method) — for each JE that touches
    a treasury leaf, one row per treasury line, classified by the OTHER
    side of the entry. Cash in = positive, cash out = negative.

    Returns {operating: [...], investing: [...], financing: [...],
    op_total, inv_total, fin_total, net_change, opening_cash, closing_cash}.
    """
    # Treasury leaves are the accounts under 1110 or 1120 with a treasury_account_id
    treasury_leaf_ids = [
        a.id for a in LedgerAccount.query.filter(
            LedgerAccount.treasury_account_id.isnot(None)
        ).all()
    ]

    if not treasury_leaf_ids:
        return {
            "operating": [], "investing": [], "financing": [],
            "op_total": Decimal("0"), "inv_total": Decimal("0"), "fin_total": Decimal("0"),
            "net_change": Decimal("0"),
            "opening_cash": Decimal("0"), "closing_cash": Decimal("0"),
        }

    # Every JE line that hits a treasury leaf in the period
    treasury_lines = (
        JournalLine.query
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(
            JournalLine.account_id.in_(treasury_leaf_ids),
            JournalEntry.is_active.is_(True),
            JournalEntry.date >= d_from,
            JournalEntry.date <= d_to,
        )
        .order_by(JournalEntry.date, JournalEntry.id)
        .all()
    )

    buckets = {"operating": [], "investing": [], "financing": []}
    op_t = inv_t = fin_t = Decimal("0")

    for tl in treasury_lines:
        cash_delta = Decimal(str(tl.debit or 0)) - Decimal(str(tl.credit or 0))
        # Find the OTHER lines in this entry that aren't treasury — those tell
        # us WHY cash moved.
        others = [
            l for l in tl.entry.lines
            if l.account_id not in treasury_leaf_ids
        ]
        # If there are multiple non-treasury lines, take the biggest one as the
        # classifier — reasonable heuristic for a mixed entry.
        others.sort(key=lambda l: abs(Decimal(str(l.debit or 0)) - Decimal(str(l.credit or 0))),
                    reverse=True)
        classifier = others[0].account if others else tl.account
        cat = _classify_cashflow(classifier)

        buckets[cat].append({
            "entry": tl.entry,
            "treasury_account": tl.account,
            "delta": cash_delta.quantize(Decimal("0.01")),
            "counterpart": classifier,
        })
        if cat == "operating": op_t += cash_delta
        elif cat == "investing": inv_t += cash_delta
        else: fin_t += cash_delta

    # Opening / closing cash — sum of every treasury leaf's balance at those dates
    opening_cash = Decimal("0")
    closing_cash = Decimal("0")
    for aid in treasury_leaf_ids:
        d1, c1 = _pair(aid, None, d_from)
        opening_cash += (d1 - c1)
        d2, c2 = _pair(aid, None, d_to)
        closing_cash += (d2 - c2)
    # opening_cash we want as of the day BEFORE the window — adjust
    # (subtract any activity on d_from itself so the row-total ties out)
    same_day_delta = Decimal("0")
    for tl in treasury_lines:
        if tl.entry.date == d_from:
            same_day_delta += Decimal(str(tl.debit or 0)) - Decimal(str(tl.credit or 0))
    opening_cash = (opening_cash - same_day_delta).quantize(Decimal("0.01"))

    net_change = (op_t + inv_t + fin_t).quantize(Decimal("0.01"))
    return {
        "operating": buckets["operating"],
        "investing": buckets["investing"],
        "financing": buckets["financing"],
        "op_total": op_t.quantize(Decimal("0.01")),
        "inv_total": inv_t.quantize(Decimal("0.01")),
        "fin_total": fin_t.quantize(Decimal("0.01")),
        "net_change": net_change,
        "opening_cash": opening_cash,
        "closing_cash": closing_cash.quantize(Decimal("0.01")),
    }


# ============ MILK COST BY GROUP ============

def milk_cost_by_group(d_from: _date, d_to: _date):
    """تكلفة الكيلو حسب المجموعة — per-group feed/labour/veterinary cost
    over milk delivered by the same group in the period.

    Reads from the ledger via the cost_center_id tag. Un-tagged expense
    lines fall into a "غير مخصّصة" row so the client can see how much of
    total farm cost hasn't been assigned to a group yet.

    Milk kg per group comes from MilkDelivery — no per-delivery group tag
    exists yet (the milk-group produces the milk that gets delivered to
    customers, not tied to a specific group), so kg is the total milk
    delivered in the period; the per-group cost gives cost/day, and cost/kg
    is TOTAL cost across all groups over TOTAL milk kg.
    """
    from app.models.sales import MilkDelivery

    # Every group with a cost tagged to it in the period, PLUS un-tagged
    rows_q = (
        db.session.query(
            JournalLine.cost_center_id,
            func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(LedgerAccount, LedgerAccount.id == JournalLine.account_id)
        .filter(
            LedgerAccount.type == AccountType.EXPENSE,
            JournalEntry.is_active.is_(True),
            JournalEntry.date >= d_from,
            JournalEntry.date <= d_to,
        )
        .group_by(JournalLine.cost_center_id)
        .all()
    )

    total_milk_kg = Decimal(str(
        db.session.query(func.coalesce(func.sum(MilkDelivery.qty_kg), 0))
        .filter(
            MilkDelivery.is_archived.is_(False),
            MilkDelivery.delivery_date >= d_from,
            MilkDelivery.delivery_date <= d_to,
        )
        .scalar() or 0
    ))

    groups = {g.id: g for g in CattleGroup.query.all()}
    rows = []
    total_cost = Decimal("0")
    for cc_id, amt in rows_q:
        amt = Decimal(str(amt)).quantize(Decimal("0.01"))
        rows.append({
            "group": groups.get(cc_id) if cc_id else None,
            "cost": amt,
        })
        total_cost += amt
    rows.sort(key=lambda r: (r["group"].name if r["group"] else "غير مخصّصة"))

    cost_per_kg = (
        (total_cost / total_milk_kg).quantize(Decimal("0.001"))
        if total_milk_kg > 0 else Decimal("0")
    )

    return {
        "rows": rows,
        "total_cost": total_cost.quantize(Decimal("0.01")),
        "total_milk_kg": total_milk_kg,
        "cost_per_kg": cost_per_kg,
    }
