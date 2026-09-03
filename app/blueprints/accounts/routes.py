"""TREASURY: manage cash/bank accounts, transfer between them, read a statement."""
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.finance import AccountForm, AccountTransferForm
from app.models.finance import TreasuryAccount, AccountMovement, AccountTransfer
from app.utils import accounts as acc
from app.utils.audit import log_action

bp = Blueprint("accounts", __name__, template_folder="../../templates/accounts")


def _account_choices():
    rows = TreasuryAccount.query.filter_by(is_archived=False).order_by(TreasuryAccount.name).all()
    return [(a.id, f"{a.display_name} ({a.current_balance})") for a in rows]


@bp.route("/")
@login_required
def list_accounts():
    rows = TreasuryAccount.query.filter_by(is_archived=False).order_by(
        TreasuryAccount.account_type, TreasuryAccount.name
    ).all()
    total = sum((Decimal(str(a.current_balance)) for a in rows), Decimal("0"))
    cash_total = sum(
        (Decimal(str(a.current_balance)) for a in rows if a.account_type == TreasuryAccount.TYPE_CASH),
        Decimal("0"),
    )
    bank_total = total - cash_total
    return render_template(
        "accounts/list.html", accounts=rows, total=total,
        cash_total=cash_total, bank_total=bank_total,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_account():
    form = AccountForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if TreasuryAccount.query.filter(func.lower(TreasuryAccount.name) == name.lower()).first():
            flash("فيه حساب بنفس الاسم مسجّل قبل كده.", "error")
            return render_template("accounts/form.html", form=form, mode="create")

        opening = Decimal(str(form.opening_balance.data or 0))
        account = TreasuryAccount(
            name=name,
            account_type=form.account_type.data,
            bank_name=(form.bank_name.data or "").strip() or None,
            account_number=(form.account_number.data or "").strip() or None,
            opening_balance=opening,
            # Starts at the opening balance — no historical movements are linked,
            # so this is the figure the client counted on activation day.
            current_balance=opening,
            created_by_id=current_user.id,
        )
        db.session.add(account)
        db.session.flush()

        # ACCOUNTING: wire the new treasury row onto its own COA leaf so any
        # payment/transfer routed through it finds a JE destination. Also
        # post an opening-balance JE if the client seeded one.
        from app.services.coa_seed import wire_treasury_accounts
        from app.services.ledger import get_account_by_code, post_journal
        from app.services.autoposting import CODE_OPENING_EQUITY
        wire_treasury_accounts()
        if opening > 0:
            from app.models.accounting import LedgerAccount
            leaf = LedgerAccount.query.filter_by(treasury_account_id=account.id).first()
            equity = get_account_by_code(CODE_OPENING_EQUITY)
            if leaf and equity:
                post_journal(
                    description=f"رصيد افتتاحي — {account.display_name}",
                    lines=[
                        {"account_id": leaf.id, "debit": opening, "memo": "افتتاحي"},
                        {"account_id": equity.id, "credit": opening,
                         "memo": f"مقابل افتتاحي {account.name}"},
                    ],
                    source_type="OpeningBalance:TreasuryAccount",
                    source_id=account.id,
                    created_by=current_user.id,
                )

        log_action("account_created", "TreasuryAccount", account.id,
                   details=f"type={account.account_type} opening={opening}")
        db.session.commit()
        flash(f"تم إضافة الحساب {account.display_name} برصيد افتتاحي {opening} جنيه.", "success")
        return redirect(url_for("accounts.list_accounts"))

    return render_template("accounts/form.html", form=form, mode="create")


@bp.route("/<int:account_id>/edit", methods=["GET", "POST"])
@login_required
def edit_account(account_id: int):
    account = db.session.get(TreasuryAccount, account_id)
    if not account or account.is_archived:
        abort(404)

    form = AccountForm(obj=account)
    if form.validate_on_submit():
        name = form.name.data.strip()
        clash = TreasuryAccount.query.filter(
            func.lower(TreasuryAccount.name) == name.lower(), TreasuryAccount.id != account.id
        ).first()
        if clash:
            flash("فيه حساب بنفس الاسم مسجّل قبل كده.", "error")
            return render_template("accounts/form.html", form=form, mode="edit", account=account)

        account.name = name
        account.account_type = form.account_type.data
        account.bank_name = (form.bank_name.data or "").strip() or None
        account.account_number = (form.account_number.data or "").strip() or None

        # Changing the opening balance shifts the whole account, so re-derive the
        # current balance from the ledger rather than nudging it.
        new_opening = Decimal(str(form.opening_balance.data or 0))
        if new_opening != Decimal(str(account.opening_balance)):
            old = account.opening_balance
            account.opening_balance = new_opening
            acc.recompute_balance(account)
            log_action("account_opening_balance_changed", "TreasuryAccount", account.id,
                       details=f"{old} -> {new_opening}")

        log_action("account_updated", "TreasuryAccount", account.id)
        db.session.commit()
        flash("تم تحديث بيانات الحساب.", "success")
        return redirect(url_for("accounts.list_accounts"))

    return render_template("accounts/form.html", form=form, mode="edit", account=account)


@bp.route("/<int:account_id>/archive", methods=["POST"])
@login_required
def archive_account(account_id: int):
    account = db.session.get(TreasuryAccount, account_id)
    if not account or account.is_archived:
        abort(404)
    if Decimal(str(account.current_balance)) != 0:
        flash(
            f"مش ممكن تأرشف {account.name} ورصيده {account.current_balance} جنيه — "
            "حوّل الرصيد لحساب تاني الأول.",
            "error",
        )
        return redirect(url_for("accounts.list_accounts"))

    account.is_archived = True
    log_action("account_archived", "TreasuryAccount", account.id)
    db.session.commit()
    flash(f"تم أرشفة الحساب {account.name}.", "success")
    return redirect(url_for("accounts.list_accounts"))


@bp.route("/<int:account_id>/statement")
@login_required
def statement(account_id: int):
    """Movements in date order with a running balance — the same shape as the
    feed tank statement."""
    account = db.session.get(TreasuryAccount, account_id)
    if not account:
        abort(404)

    rows = []
    running = Decimal(str(account.opening_balance))
    for mv in account.movements:
        running += Decimal(str(mv.amount))
        rows.append({"mv": mv, "balance": running})
    rows.reverse()  # newest first to read, balances already computed forward

    return render_template(
        "accounts/statement.html", account=account, rows=rows, closing=running
    )


@bp.route("/transfer", methods=["GET", "POST"])
@login_required
def transfer():
    form = AccountTransferForm()
    choices = _account_choices()
    form.from_account_id.choices = choices
    form.to_account_id.choices = choices

    if len(choices) < 2:
        flash("محتاج حسابين على الأقل عشان تعمل تحويل.", "warning")
        return redirect(url_for("accounts.list_accounts"))

    if form.validate_on_submit():
        src = db.session.get(TreasuryAccount, form.from_account_id.data)
        dst = db.session.get(TreasuryAccount, form.to_account_id.data)
        if not src or not dst or src.is_archived or dst.is_archived:
            flash("حساب غير صالح.", "error")
            return render_template("accounts/transfer.html", form=form)

        try:
            tr = acc.transfer(
                src, dst, form.amount.data, form.transfer_date.data,
                notes=form.notes.data, user_id=current_user.id,
            )
        except ValueError as exc:
            form.amount.errors.append(str(exc))
            flash(str(exc), "error")
            return render_template("accounts/transfer.html", form=form)

        # ACCOUNTING: mirror the transfer as a DR/CR between the two treasury leaves.
        from app.services import autoposting
        autoposting.on_treasury_transfer(src, dst, tr, created_by=current_user.id)

        log_action("account_transfer", "AccountTransfer", tr.id,
                   details=f"{src.id}->{dst.id} amount={tr.amount}")
        db.session.commit()
        flash(
            f"تم تحويل {tr.amount} جنيه من {src.name} إلى {dst.name}. "
            f"رصيد {src.name} بقى {src.current_balance} و {dst.name} بقى {dst.current_balance}.",
            "success",
        )
        return redirect(url_for("accounts.list_accounts"))

    return render_template("accounts/transfer.html", form=form)
