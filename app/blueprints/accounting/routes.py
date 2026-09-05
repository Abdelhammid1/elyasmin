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

from app.extensions import db
from app.models.accounting import (
    JournalEntry, JournalLine, LedgerAccount,
)
from app.models.sales import Customer
from app.models.suppliers import Supplier
from app.services.ledger import party_balance, trial_balance
from app.services.statements import (
    balance_sheet, cash_flow, income_statement, milk_cost_by_group,
)
from app.utils.reports import excel_response, pdf_from_current_page
from app.utils.decorators import write_required

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
        .filter(LedgerAccount.is_active.is_(True))
        .order_by(LedgerAccount.code)
        .all()
    )
    return render_template("accounting/coa.html", roots=roots)


# ---------- PHASE 31 (FIN-6): CoA CRUD ----------

def _parent_choices(exclude_id: int | None = None):
    """Every active account, flat + sorted by code, for the parent
    dropdown. On edit, `exclude_id` filters out the account itself
    AND every one of its descendants — you can't make a subtree its
    own ancestor. Includes headers (is_postable=False) so a user can
    park a leaf under a header."""
    q = LedgerAccount.query.filter(LedgerAccount.is_active.is_(True))
    all_accounts = q.order_by(LedgerAccount.code).all()
    if exclude_id is not None:
        excluded_ids = _descendant_ids(exclude_id)
        all_accounts = [a for a in all_accounts if a.id not in excluded_ids]
    choices = [(0, "— بدون (حساب رئيسي) —")]
    choices.extend((a.id, a.display_name) for a in all_accounts)
    return choices


def _descendant_ids(root_id: int) -> set[int]:
    """Every id in the subtree rooted at `root_id` (inclusive). Used
    to guard against cycles on parent re-assignment."""
    root = db.session.get(LedgerAccount, root_id)
    if root is None:
        return set()
    return {a.id for a in root.descendants()}


def _treasury_choices(exclude_treasury_id: int | None = None):
    """Every un-claimed TreasuryAccount (no LedgerAccount points at it
    yet), plus `exclude_treasury_id` when editing (so the current
    account's own treasury stays selectable)."""
    from app.models.finance import TreasuryAccount
    claimed = {
        row[0] for row in
        db.session.query(LedgerAccount.treasury_account_id)
        .filter(LedgerAccount.treasury_account_id.isnot(None))
        .all()
    }
    if exclude_treasury_id is not None:
        claimed.discard(exclude_treasury_id)
    free = (
        TreasuryAccount.query
        .filter(TreasuryAccount.is_archived.is_(False))
        .order_by(TreasuryAccount.name)
        .all()
    )
    free = [t for t in free if t.id not in claimed]
    return [(0, "— بدون —")] + [(t.id, t.name) for t in free]


@bp.route("/coa/new", methods=["GET", "POST"])
@login_required
@write_required
def create_ledger_account():
    _admin_only()
    from flask import flash
    from app.forms.accounting import LedgerAccountForm
    from app.models.accounting import AccountType, NORMAL_SIDE_FOR_TYPE
    from app.utils.audit import log_action

    form = LedgerAccountForm()
    form.parent_id.choices = _parent_choices()
    form.treasury_account_id.choices = _treasury_choices()

    if form.validate_on_submit():
        code = form.code.data.strip()
        parent_id = form.parent_id.data or None
        treasury_id = form.treasury_account_id.data or None

        # Unique code
        if LedgerAccount.query.filter_by(code=code).first():
            flash(f"الكود {code} مستخدم فعلاً على حساب تاني.", "error")
            return render_template("accounting/coa_form.html",
                                   form=form, mode="new", account=None)

        # Type inheritance from parent
        if parent_id:
            parent = db.session.get(LedgerAccount, parent_id)
            if parent is None or not parent.is_active:
                flash("الحساب الأب مش موجود أو مؤرشف.", "error")
                return render_template("accounting/coa_form.html",
                                       form=form, mode="new", account=None)
            atype = parent.type
        else:
            atype = AccountType(form.type.data)

        normal_side = NORMAL_SIDE_FOR_TYPE[atype]

        acct = LedgerAccount(
            code=code,
            name=form.name.data.strip(),
            name_en=(form.name_en.data or "").strip() or None,
            type=atype,
            normal_side=normal_side,
            parent_id=parent_id,
            is_postable=form.is_postable.data,
            treasury_account_id=treasury_id,
            is_active=True,
        )
        db.session.add(acct)
        log_action("ledger_account_created", "LedgerAccount", 0,
                   details=f"code={code}")
        db.session.commit()
        flash(f"تم إنشاء الحساب {acct.display_name}.", "success")
        return redirect(url_for("accounting.chart_of_accounts"))

    return render_template("accounting/coa_form.html",
                           form=form, mode="new", account=None)


@bp.route("/coa/<int:account_id>/edit", methods=["GET", "POST"])
@login_required
@write_required
def edit_ledger_account(account_id: int):
    _admin_only()
    from flask import flash
    from app.forms.accounting import LedgerAccountForm
    from app.models.accounting import AccountType, NORMAL_SIDE_FOR_TYPE
    from app.utils.audit import log_action

    acct = db.session.get(LedgerAccount, account_id)
    if acct is None or not acct.is_active:
        abort(404)

    form = LedgerAccountForm(obj=acct)
    if request.method == "GET":
        form.parent_id.data = acct.parent_id or 0
        form.treasury_account_id.data = acct.treasury_account_id or 0
        form.type.data = acct.type.value

    form.parent_id.choices = _parent_choices(exclude_id=acct.id)
    form.treasury_account_id.choices = _treasury_choices(
        exclude_treasury_id=acct.treasury_account_id
    )

    has_lines = acct.has_journal_lines()

    if form.validate_on_submit():
        new_code = form.code.data.strip()
        new_parent_id = form.parent_id.data or None
        new_treasury_id = form.treasury_account_id.data or None

        # Code-change guard — refuse if account has lines
        if new_code != acct.code:
            if has_lines:
                flash("الحساب عليه قيود مرحّلة — مينفعش تعدّل الكود.", "error")
                return render_template("accounting/coa_form.html",
                                       form=form, mode="edit", account=acct)
            if LedgerAccount.query.filter(
                LedgerAccount.code == new_code,
                LedgerAccount.id != acct.id,
            ).first():
                flash(f"الكود {new_code} مستخدم فعلاً على حساب تاني.", "error")
                return render_template("accounting/coa_form.html",
                                       form=form, mode="edit", account=acct)

        # Cycle detection — parent cannot be self or a descendant of self
        if new_parent_id and new_parent_id in _descendant_ids(acct.id):
            flash("مينفعش يبقى الحساب أب لنفسه عبر مسار غير مباشر.", "error")
            return render_template("accounting/coa_form.html",
                                   form=form, mode="edit", account=acct)

        # Type inheritance
        if new_parent_id:
            parent = db.session.get(LedgerAccount, new_parent_id)
            if parent is None or not parent.is_active:
                flash("الحساب الأب مش موجود أو مؤرشف.", "error")
                return render_template("accounting/coa_form.html",
                                       form=form, mode="edit", account=acct)
            atype = parent.type
        else:
            atype = AccountType(form.type.data)

        # is_postable toggle guard: if flipping to header, refuse if lines exist
        new_postable = form.is_postable.data
        if not new_postable and has_lines:
            flash(
                "الحساب عليه قيود مرحّلة — مينفعش تحوّله لتجميعي (هيتم يتيم القيود).",
                "error",
            )
            return render_template("accounting/coa_form.html",
                                   form=form, mode="edit", account=acct)

        acct.code = new_code
        acct.name = form.name.data.strip()
        acct.name_en = (form.name_en.data or "").strip() or None
        acct.type = atype
        acct.normal_side = NORMAL_SIDE_FOR_TYPE[atype]
        acct.parent_id = new_parent_id
        acct.is_postable = new_postable
        acct.treasury_account_id = new_treasury_id

        log_action("ledger_account_updated", "LedgerAccount", acct.id,
                   details=f"code={acct.code}")
        db.session.commit()
        flash(f"تم حفظ التعديلات على {acct.display_name}.", "success")
        return redirect(url_for("accounting.chart_of_accounts"))

    return render_template("accounting/coa_form.html",
                           form=form, mode="edit", account=acct,
                           has_lines=has_lines)


@bp.route("/coa/<int:account_id>/archive", methods=["POST"])
@login_required
@write_required
def archive_ledger_account(account_id: int):
    _admin_only()
    from flask import flash
    from app.utils.audit import log_action

    acct = db.session.get(LedgerAccount, account_id)
    if acct is None or not acct.is_active:
        abort(404)

    # Refuse if it still has active children
    active_children = [c for c in acct.children if c.is_active]
    if active_children:
        flash(
            f"مينفعش تأرشف {acct.display_name} — عنده "
            f"{len(active_children)} حساب فرعي نشط. أرشف الأبناء الأول.",
            "error",
        )
        return redirect(url_for("accounting.chart_of_accounts"))

    # Refuse if it has any posted lines (paused or not — reversible)
    if acct.has_journal_lines():
        flash(
            f"مينفعش تأرشف {acct.display_name} — عليه قيود مرحّلة. "
            "لو محتاج توقفه، ما تستخدمهوش في قيود جديدة بس.",
            "error",
        )
        return redirect(url_for("accounting.chart_of_accounts"))

    acct.is_active = False
    log_action("ledger_account_archived", "LedgerAccount", acct.id,
               details=f"code={acct.code}")
    db.session.commit()
    flash(f"تم أرشفة الحساب {acct.display_name}.", "info")
    return redirect(url_for("accounting.chart_of_accounts"))


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
def _party_or_404(party_type, party_id):
    if party_type not in ("customer", "supplier"):
        abort(404)
    party = (
        db.session.get(Customer, party_id) if party_type == "customer"
        else db.session.get(Supplier, party_id)
    )
    if party is None:
        abort(404)
    return party


def _party_statement_rows(party_type, party_id, d_from, d_to):
    """Build a chronological statement — one row per journal line tagged
    to the party. Returns (opening, rows, closing) where opening is the
    running balance carried in from before d_from and each row carries the
    balance *after* that movement was applied. Rows include a source_url
    so the template can link back to the invoice/payment that produced
    the JE."""
    Q = (
        JournalLine.query
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(
            JournalLine.party_type == party_type,
            JournalLine.party_id == party_id,
            JournalEntry.is_active.is_(True),
        )
    )
    # Opening = fold every line strictly before the window
    before = Q.filter(JournalEntry.date < d_from).all()
    opening = sum(
        (Decimal(str(l.debit or 0)) - Decimal(str(l.credit or 0)) for l in before),
        Decimal("0"),
    )
    # In-period lines
    lines = (
        Q.filter(JournalEntry.date >= d_from, JournalEntry.date <= d_to)
         .order_by(JournalEntry.date, JournalEntry.id, JournalLine.id)
         .all()
    )
    running = opening
    rows = []
    for l in lines:
        delta = Decimal(str(l.debit or 0)) - Decimal(str(l.credit or 0))
        running += delta
        rows.append({
            "line": l,
            "delta": delta.quantize(Decimal("0.01")),
            "running": running.quantize(Decimal("0.01")),
            "source_url": _source_url(l.entry.source_type, l.entry.source_id),
        })
    return (
        opening.quantize(Decimal("0.01")),
        rows,
        running.quantize(Decimal("0.01")),
    )


@bp.route("/party/<party_type>/<int:party_id>")
@login_required
def party_ledger(party_type, party_id):
    party = _party_or_404(party_type, party_id)
    d_from, d_to = _period()
    opening, rows, closing = _party_statement_rows(party_type, party_id, d_from, d_to)
    return render_template(
        "accounting/party_ledger.html",
        party=party, party_type=party_type,
        rows=rows, opening=opening, closing=closing,
        d_from=d_from, d_to=d_to,
        balance=party_balance(party_type, party_id),
    )


@bp.route("/party/<party_type>/<int:party_id>/statement.xlsx")
@login_required
def party_ledger_excel(party_type, party_id):
    party = _party_or_404(party_type, party_id)
    d_from, d_to = _period()
    opening, rows, closing = _party_statement_rows(party_type, party_id, d_from, d_to)

    # Emit an opening row, then one row per movement, then a closing row —
    # matches the on-screen table so the file reads the same as the page.
    xrows = [[d_from.isoformat(), "", "رصيد افتتاحي", "", "", float(opening)]]
    for r in rows:
        xrows.append([
            r["line"].entry.date.isoformat(),
            r["line"].entry.number or "",
            (r["line"].entry.description or "") + (
                f" — {r['line'].memo}" if r["line"].memo else ""
            ),
            float(r["line"].debit or 0),
            float(r["line"].credit or 0),
            float(r["running"]),
        ])
    xrows.append([d_to.isoformat(), "", "رصيد ختامي", "", "", float(closing)])

    fname = f"statement_{party_type}_{party.id}_{d_from}_{d_to}.xlsx"
    return excel_response(
        f"Statement {party.name}"[:30],
        ["التاريخ", "قيد", "البيان", "مدين", "دائن", "الرصيد"],
        xrows,
        fname,
    )


@bp.route("/party/<party_type>/<int:party_id>/statement.pdf")
@login_required
def party_ledger_pdf(party_type, party_id):
    party = _party_or_404(party_type, party_id)
    d_from, d_to = _period()
    target = url_for(
        "accounting.party_ledger",
        party_type=party_type, party_id=party_id,
        date_from=d_from.isoformat(), date_to=d_to.isoformat(),
        _external=True,
    )
    fname = f"statement_{party_type}_{party.id}_{d_from}_{d_to}.pdf"
    return pdf_from_current_page(target, fname)


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


# ==================== PHASE 2 — manual JE + admin actions ====================

def _admin_only():
    """Every write route in this blueprint is admin-only. Non-admins can
    read everything but the "moves money on the ledger" side is gated
    the same way finance.settings is."""
    from flask_login import current_user
    if not current_user.is_admin:
        abort(403)


@bp.route("/journal/new", methods=["GET", "POST"])
@login_required
@write_required
def journal_new():
    _admin_only()
    from decimal import Decimal, InvalidOperation
    from flask import flash
    from flask_login import current_user
    from app.forms.accounting import ManualJournalForm
    from app.services.ledger import post_journal, LedgerError
    from app.models.herd import CattleGroup

    form = ManualJournalForm()
    accounts = (
        LedgerAccount.query
        .filter(LedgerAccount.is_postable.is_(True), LedgerAccount.is_active.is_(True))
        .order_by(LedgerAccount.code)
        .all()
    )
    groups = CattleGroup.query.filter_by(is_archived=False).order_by(CattleGroup.name).all()
    customers = Customer.query.filter_by(is_archived=False).order_by(Customer.name).all()
    suppliers = Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()

    if form.validate_on_submit():
        lines = []
        idx = 0
        while True:
            key = f"line_account_{idx}"
            if key not in request.form:
                break
            aid = request.form.get(key)
            if not aid:
                idx += 1
                continue
            try:
                dr = Decimal(request.form.get(f"line_debit_{idx}") or "0")
                cr = Decimal(request.form.get(f"line_credit_{idx}") or "0")
            except InvalidOperation:
                flash(f"مبلغ غير صالح في السطر {idx + 1}.", "error")
                idx += 1
                continue
            if dr == 0 and cr == 0:
                idx += 1
                continue

            party_raw = request.form.get(f"line_party_{idx}") or ""
            party_type, party_id = None, None
            if party_raw:
                # value shape: "customer:42" or "supplier:7"
                if ":" in party_raw:
                    party_type, pid = party_raw.split(":", 1)
                    try:
                        party_id = int(pid)
                    except ValueError:
                        party_type = None

            cc_raw = request.form.get(f"line_cc_{idx}") or ""
            cc = int(cc_raw) if cc_raw.isdigit() else None

            lines.append({
                "account_id": int(aid),
                "debit": dr, "credit": cr,
                "memo": (request.form.get(f"line_memo_{idx}") or "").strip() or None,
                "party_type": party_type, "party_id": party_id,
                "cost_center_id": cc,
            })
            idx += 1

        try:
            je = post_journal(
                description=form.description.data.strip(),
                lines=lines,
                entry_date=form.entry_date.data,
                reference=(form.reference.data or "").strip() or None,
                created_by=current_user.id,
            )
            db.session.commit()
            flash(f"تم حفظ القيد {je.number} — إجمالي {je.total_debit} جنيه.", "success")
            return redirect(url_for("accounting.journal_detail", entry_id=je.id))
        except LedgerError as e:
            flash(str(e), "error")

    return render_template(
        "accounting/journal_form.html", form=form,
        accounts=accounts, groups=groups,
        customers=customers, suppliers=suppliers,
    )


@bp.route("/journal/<int:entry_id>/reverse", methods=["POST"])
@login_required
@write_required
def journal_reverse(entry_id):
    _admin_only()
    from flask import flash
    from flask_login import current_user
    from app.services.ledger import post_journal, LedgerError

    je = db.session.get(JournalEntry, entry_id)
    if je is None:
        abort(404)
    if je.is_reversal or je.reversal_of_id:
        flash("مش ممكن تعكس قيد عكسي.", "error")
        return redirect(url_for("accounting.journal_detail", entry_id=entry_id))

    # flip debit/credit on every line
    lines = [
        {"account_id": l.account_id,
         "debit": l.credit, "credit": l.debit,
         "memo": f"عكس {je.number}: {l.memo or ''}".strip(),
         "party_type": l.party_type, "party_id": l.party_id,
         "cost_center_id": l.cost_center_id}
        for l in je.lines
    ]
    try:
        rev = post_journal(
            description=f"عكس القيد {je.number} — {je.description}",
            lines=lines,
            entry_date=_date.today(),
            reference=je.reference,
            is_reversal=True,
            reversal_of_id=je.id,
            source_type="Reversal",
            source_id=je.id,
            created_by=current_user.id,
        )
        db.session.commit()
        from flask import flash
        flash(f"تم عكس القيد — قيد جديد {rev.number}.", "success")
        return redirect(url_for("accounting.journal_detail", entry_id=rev.id))
    except LedgerError as e:
        flash(str(e), "error")
        return redirect(url_for("accounting.journal_detail", entry_id=entry_id))


@bp.route("/journal/<int:entry_id>/pause", methods=["POST"])
@login_required
@write_required
def journal_pause(entry_id):
    _admin_only()
    from datetime import datetime
    from flask import flash
    from flask_login import current_user

    je = db.session.get(JournalEntry, entry_id)
    if je is None:
        abort(404)
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("لازم تكتب سبب الإيقاف.", "error")
        return redirect(url_for("accounting.journal_detail", entry_id=entry_id))
    je.is_active = False
    je.pause_reason = reason
    je.paused_by_id = current_user.id
    je.paused_at = datetime.utcnow()
    db.session.commit()
    flash("تم إيقاف القيد — مش هيظهر في التقارير.", "warning")
    return redirect(url_for("accounting.journal_detail", entry_id=entry_id))


@bp.route("/journal/<int:entry_id>/reactivate", methods=["POST"])
@login_required
@write_required
def journal_reactivate(entry_id):
    _admin_only()
    from datetime import datetime
    from flask import flash
    from flask_login import current_user

    je = db.session.get(JournalEntry, entry_id)
    if je is None:
        abort(404)
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("لازم تكتب سبب إعادة التنشيط.", "error")
        return redirect(url_for("accounting.journal_detail", entry_id=entry_id))
    je.is_active = True
    je.reactivate_reason = reason
    je.reactivated_by_id = current_user.id
    je.reactivated_at = datetime.utcnow()
    db.session.commit()
    flash("تم إعادة تنشيط القيد.", "success")
    return redirect(url_for("accounting.journal_detail", entry_id=entry_id))


# ==================== FIN-7 (PHASE 32): quick-entry templates ====================

def _treasury_leaf(treasury_id: int):
    """Look up the CoA leaf wired to this TreasuryAccount."""
    from app.services.ledger import LedgerError
    leaf = LedgerAccount.query.filter_by(
        treasury_account_id=treasury_id, is_active=True,
    ).first()
    if leaf is None:
        raise LedgerError(
            f"الخزنة #{treasury_id} مش مربوطة بحساب في دليل الحسابات."
        )
    return leaf


def _coa_by_code(code: str):
    """Fetch a required CoA leaf by code."""
    from app.services.ledger import LedgerError
    acc = LedgerAccount.query.filter_by(code=code, is_active=True).first()
    if acc is None:
        raise LedgerError(f"حساب {code} مش موجود في دليل الحسابات.")
    return acc


# ---- Per-kind lines builders — each returns (description, lines_list) ----

def _lines_opening(form):
    """DR <picked account> / CR 3090 opening balances."""
    from decimal import Decimal
    amt = Decimal(str(form.amount.data))
    picked = db.session.get(LedgerAccount, form.account_id.data)
    if picked is None or not picked.is_active or not picked.is_postable:
        from app.services.ledger import LedgerError
        raise LedgerError("اختار حساب أصل/خصم من القائمة.")
    opening = _coa_by_code("3090")
    memo = form.memo.data or f"رصيد افتتاحي لحساب {picked.display_name}"
    return (
        f"رصيد افتتاحي — {picked.display_name}",
        [
            {"account_id": picked.id,  "debit": amt, "credit": 0, "memo": memo},
            {"account_id": opening.id, "debit": 0,   "credit": amt, "memo": memo},
        ],
    )


def _lines_capital(form):
    """DR <treasury leaf> / CR 3010 owner's capital."""
    from decimal import Decimal
    amt = Decimal(str(form.amount.data))
    t_leaf = _treasury_leaf(form.treasury_account_id.data)
    capital = _coa_by_code("3010")
    memo = form.memo.data or "إضافة رأس مال"
    return (
        f"إضافة رأس مال — {t_leaf.name}",
        [
            {"account_id": t_leaf.id,  "debit": amt, "credit": 0, "memo": memo},
            {"account_id": capital.id, "debit": 0,   "credit": amt, "memo": memo},
        ],
    )


def _lines_deposit_in(form):
    """DR <treasury leaf> / CR 2050 deposits received. Party name in memo."""
    from decimal import Decimal
    amt = Decimal(str(form.amount.data))
    t_leaf = _treasury_leaf(form.treasury_account_id.data)
    dep = _coa_by_code("2050")
    party = (form.party_name.data or "").strip()
    if not party:
        from app.services.ledger import LedgerError
        raise LedgerError("اسم الطرف مطلوب لأمانة مستلمة.")
    memo = form.memo.data or f"أمانة من {party}"
    return (
        f"استلام أمانة من {party}",
        [
            {"account_id": t_leaf.id, "debit": amt, "credit": 0, "memo": memo,
             "party_type": "other"},
            {"account_id": dep.id,    "debit": 0,   "credit": amt, "memo": memo,
             "party_type": "other"},
        ],
    )


def _lines_deposit_out(form):
    """DR 2050 deposits received / CR <treasury leaf>. Party in memo."""
    from decimal import Decimal
    amt = Decimal(str(form.amount.data))
    t_leaf = _treasury_leaf(form.treasury_account_id.data)
    dep = _coa_by_code("2050")
    party = (form.party_name.data or "").strip()
    if not party:
        from app.services.ledger import LedgerError
        raise LedgerError("اسم الطرف مطلوب لرد الأمانة.")
    memo = form.memo.data or f"رد أمانة إلى {party}"
    return (
        f"رد أمانة إلى {party}",
        [
            {"account_id": dep.id,    "debit": amt, "credit": 0, "memo": memo,
             "party_type": "other"},
            {"account_id": t_leaf.id, "debit": 0,   "credit": amt, "memo": memo,
             "party_type": "other"},
        ],
    )


def _lines_drawings(form):
    """DR 3030 owner draws / CR <treasury leaf>."""
    from decimal import Decimal
    amt = Decimal(str(form.amount.data))
    t_leaf = _treasury_leaf(form.treasury_account_id.data)
    draws = _coa_by_code("3030")
    memo = form.memo.data or "مسحوبات المالك"
    return (
        f"مسحوبات المالك — {t_leaf.name}",
        [
            {"account_id": draws.id,  "debit": amt, "credit": 0, "memo": memo},
            {"account_id": t_leaf.id, "debit": 0,   "credit": amt, "memo": memo},
        ],
    )


def _lines_loan_received(form):
    """DR <treasury leaf> / CR 2041 short-term OR 2042 long-term."""
    from decimal import Decimal
    amt = Decimal(str(form.amount.data))
    t_leaf = _treasury_leaf(form.treasury_account_id.data)
    code = "2041" if form.loan_kind.data == "short" else "2042"
    loan = _coa_by_code(code)
    memo = form.memo.data or f"استلام {loan.name}"
    return (
        f"استلام {loan.name}",
        [
            {"account_id": t_leaf.id, "debit": amt, "credit": 0, "memo": memo},
            {"account_id": loan.id,   "debit": 0,   "credit": amt, "memo": memo},
        ],
    )


def _lines_loan_repaid(form):
    """DR <picked loan account> / CR <treasury leaf>."""
    from decimal import Decimal
    amt = Decimal(str(form.amount.data))
    t_leaf = _treasury_leaf(form.treasury_account_id.data)
    loan = db.session.get(LedgerAccount, form.loan_account_id.data)
    if loan is None or loan.code not in ("2041", "2042"):
        from app.services.ledger import LedgerError
        raise LedgerError("اختار قرض من القائمة.")
    memo = form.memo.data or f"سداد قسط من {loan.name}"
    return (
        f"سداد قسط قرض — {loan.name}",
        [
            {"account_id": loan.id,   "debit": amt, "credit": 0, "memo": memo},
            {"account_id": t_leaf.id, "debit": 0,   "credit": amt, "memo": memo},
        ],
    )


_QUICK_KINDS = {
    "opening":       _lines_opening,
    "capital":       _lines_capital,
    "deposit_in":    _lines_deposit_in,
    "deposit_out":   _lines_deposit_out,
    "drawings":      _lines_drawings,
    "loan_received": _lines_loan_received,
    "loan_repaid":   _lines_loan_repaid,
}


@bp.route("/quick-entry")
@login_required
def quick_entry_index():
    """FIN-7 landing page: 7 cards, one per template."""
    _admin_only()
    from app.forms.accounting import QUICK_ENTRY_KINDS
    return render_template(
        "accounting/quick_entry.html", kinds=QUICK_ENTRY_KINDS,
    )


@bp.route("/quick-entry/<kind>", methods=["GET", "POST"])
@login_required
@write_required
def quick_entry(kind: str):
    """FIN-7 per-kind form. On POST builds JE lines + calls post_journal."""
    _admin_only()
    from flask import flash
    from flask_login import current_user
    from app.forms.accounting import QUICK_ENTRY_KINDS, QuickEntryForm
    from app.models.finance import TreasuryAccount
    from app.services.ledger import LedgerError, post_journal

    if kind not in _QUICK_KINDS:
        abort(404)
    label = dict(QUICK_ENTRY_KINDS)[kind]

    form = QuickEntryForm()
    form.kind.data = kind

    treasuries = (
        TreasuryAccount.query
        .filter_by(is_archived=False).order_by(TreasuryAccount.name).all()
    )
    form.treasury_account_id.choices = [
        (t.id, t.display_name) for t in treasuries
    ]
    postable_accts = (
        LedgerAccount.query
        .filter(LedgerAccount.is_active.is_(True),
                LedgerAccount.is_postable.is_(True))
        .order_by(LedgerAccount.code).all()
    )
    form.account_id.choices = [(a.id, a.display_name) for a in postable_accts]
    loan_accts = [a for a in postable_accts if a.code in ("2041", "2042")]
    form.loan_account_id.choices = [
        (a.id, a.display_name) for a in loan_accts
    ]

    if request.method == "POST":
        if not form.validate_on_submit():
            for _, errors in form.errors.items():
                for e in errors:
                    flash(e, "error")
            return render_template(
                "accounting/quick_entry_form.html",
                form=form, kind=kind, label=label,
                treasuries=treasuries, loan_accts=loan_accts,
            )
        try:
            desc, lines = _QUICK_KINDS[kind](form)
            je = post_journal(
                description=desc,
                lines=lines,
                entry_date=form.entry_date.data,
                created_by=current_user.id,
                source_type="QuickEntry",
                source_id=0,
            )
            db.session.commit()
            flash(
                f"تم حفظ القيد {je.number} — {label} — "
                f"إجمالي {je.total_debit} جنيه.",
                "success",
            )
            return redirect(url_for("accounting.journal_detail",
                                    entry_id=je.id))
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")

    return render_template(
        "accounting/quick_entry_form.html",
        form=form, kind=kind, label=label,
        treasuries=treasuries, loan_accts=loan_accts,
    )
