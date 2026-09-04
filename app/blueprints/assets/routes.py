"""PHASE 8b — fixed-assets routes."""
from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.forms.assets import DisposeForm, FixedAssetForm, PostMonthForm
from app.models.assets import DepreciationPosting, FixedAsset
from app.models.finance import TreasuryAccount
from app.models.suppliers import Supplier
from app.services import assets as svc
from app.services.ledger import LedgerError
from app.utils import accounts as acc
from app.utils.audit import log_action
from app.utils.decorators import write_required

bp = Blueprint("assets", __name__, template_folder="../../templates/assets")


def _load_choices(form):
    form.treasury_account_id.choices = (
        [("", "— اختر الحساب —")] + [(str(id_), lbl) for id_, lbl in acc.active_choices()]
    )
    form.supplier_id.choices = (
        [("", "— اختر المورد —")]
        + [(str(s.id), s.name) for s in
           Supplier.query.filter_by(is_archived=False).order_by(Supplier.name).all()]
    )


# ---------- list ----------
@bp.route("/")
@login_required
def list_assets():
    active = (
        FixedAsset.query.filter_by(status=FixedAsset.STATUS_ACTIVE)
        .order_by(FixedAsset.purchase_date.desc(), FixedAsset.id.desc())
        .all()
    )
    disposed = (
        FixedAsset.query.filter_by(status=FixedAsset.STATUS_DISPOSED)
        .order_by(FixedAsset.disposed_on.desc())
        .limit(20)
        .all()
    )
    # For the "post depreciation" bulk button, count how many active
    # assets are missing a posting for the current month.
    today = date.today()
    first = today.replace(day=1)
    pending = 0
    for a in active:
        if a.is_fully_depreciated:
            continue
        exists = DepreciationPosting.query.filter_by(
            asset_id=a.id, period_month=first,
        ).first()
        if not exists:
            pending += 1
    total_cost = sum((Decimal(str(a.purchase_cost)) for a in active), Decimal("0"))
    total_book = sum((Decimal(str(a.book_value)) for a in active), Decimal("0"))
    return render_template(
        "assets/list.html",
        active=active, disposed=disposed,
        pending_month=first, pending_count=pending,
        total_cost=total_cost, total_book=total_book,
        post_form=PostMonthForm(),
    )


# ---------- new (purchase) ----------
@bp.route("/new", methods=["GET", "POST"])
@login_required
@write_required
def new_asset():
    form = FixedAssetForm()
    _load_choices(form)

    if form.validate_on_submit():
        if form.payment_type.data == "cash" and not form.treasury_account_id.data:
            form.treasury_account_id.errors.append(
                "لازم تختار الحساب اللي الفلوس هتطلع منه.",
            )
            return render_template("assets/form.html", form=form)
        if form.payment_type.data == "credit" and not form.supplier_id.data:
            form.supplier_id.errors.append("لازم تختار المورد.")
            return render_template("assets/form.html", form=form)

        asset = FixedAsset(
            name=form.name.data.strip(),
            category=form.category.data,
            purchase_date=form.purchase_date.data,
            purchase_cost=Decimal(str(form.purchase_cost.data)),
            salvage_value=Decimal(str(form.salvage_value.data or 0)),
            useful_life_months=form.useful_life_months.data,
            treasury_account_id=(
                form.treasury_account_id.data if form.payment_type.data == "cash" else None
            ),
            supplier_id=(
                form.supplier_id.data if form.payment_type.data == "credit" else None
            ),
            notes=(form.notes.data or "").strip() or None,
            created_by_id=current_user.id,
        )
        db.session.add(asset); db.session.flush()
        try:
            svc.record_asset_purchase(asset, created_by=current_user.id)
        except LedgerError as e:
            db.session.rollback()
            flash(str(e), "error")
            return render_template("assets/form.html", form=form)
        log_action("asset_purchased", "FixedAsset", asset.id,
                   details=f"cost={asset.purchase_cost}")
        db.session.commit()
        flash(f"تم إضافة الأصل {asset.name} بتكلفة {asset.purchase_cost} جنيه.", "success")
        return redirect(url_for("assets.asset_detail", asset_id=asset.id))

    return render_template("assets/form.html", form=form)


# ---------- detail ----------
@bp.route("/<int:asset_id>")
@login_required
def asset_detail(asset_id):
    asset = db.session.get(FixedAsset, asset_id)
    if asset is None:
        abort(404)
    dispose_form = DisposeForm()
    dispose_form.sale_treasury_id.choices = (
        [("", "— اختر الحساب —")] + [(str(id_), lbl) for id_, lbl in acc.active_choices()]
    )
    from app.models.accounting import JournalEntry
    jes = JournalEntry.query.filter(
        JournalEntry.source_type.in_(
            ["FixedAsset:purchase", "FixedAsset:dispose"]),
        JournalEntry.source_id == asset.id,
        JournalEntry.is_active.is_(True),
    ).order_by(JournalEntry.date, JournalEntry.id).all()
    return render_template(
        "assets/detail.html",
        asset=asset, jes=jes, dispose_form=dispose_form,
    )


# ---------- post monthly depreciation (one asset) ----------
@bp.route("/<int:asset_id>/post-depreciation", methods=["POST"])
@login_required
@write_required
def post_depreciation(asset_id):
    asset = db.session.get(FixedAsset, asset_id)
    if asset is None:
        abort(404)
    form = PostMonthForm()
    if not form.validate_on_submit():
        flash("الشهر غير صالح.", "error")
        return redirect(url_for("assets.asset_detail", asset_id=asset.id))
    try:
        posting = svc.post_monthly_depreciation(
            asset, form.period_month.data, created_by=current_user.id,
        )
    except IntegrityError:
        db.session.rollback()
        flash("الشهر ده اترحّل قبل كده.", "warning")
        return redirect(url_for("assets.asset_detail", asset_id=asset.id))
    except LedgerError as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("assets.asset_detail", asset_id=asset.id))

    if posting is None:
        flash("الأصل مخرّد أو استهلك بالكامل — مفيش إهلاك للترحيل.", "info")
    else:
        log_action("asset_depreciated", "FixedAsset", asset.id,
                   details=f"period={form.period_month.data} amount={posting.amount}")
        db.session.commit()
        flash(f"تم ترحيل إهلاك {posting.amount} جنيه للشهر.", "success")
    return redirect(url_for("assets.asset_detail", asset_id=asset.id))


# ---------- bulk-post depreciation for a month ----------
@bp.route("/post-monthly", methods=["POST"])
@login_required
@write_required
def post_monthly_all():
    if not current_user.is_admin:
        abort(403)
    form = PostMonthForm()
    if not form.validate_on_submit():
        flash("الشهر غير صالح.", "error")
        return redirect(url_for("assets.list_assets"))
    active = FixedAsset.query.filter_by(status=FixedAsset.STATUS_ACTIVE).all()
    posted = 0
    skipped = 0
    total = Decimal("0")
    for asset in active:
        try:
            posting = svc.post_monthly_depreciation(
                asset, form.period_month.data, created_by=current_user.id,
            )
        except IntegrityError:
            db.session.rollback()
            skipped += 1
            continue
        except LedgerError as e:
            db.session.rollback()
            flash(f"{asset.name}: {e}", "error")
            continue
        if posting is not None:
            posted += 1
            total += Decimal(str(posting.amount))
    db.session.commit()
    log_action("asset_bulk_depreciation", "FixedAsset", 0,
               details=f"period={form.period_month.data} posted={posted} skipped={skipped}")
    flash(
        f"تم ترحيل {posted} إهلاك بإجمالي {total} جنيه. "
        f"({skipped} أصل اترحّل الشهر ده من قبل.)",
        "success",
    )
    return redirect(url_for("assets.list_assets"))


# ---------- dispose (admin only) ----------
@bp.route("/<int:asset_id>/dispose", methods=["POST"])
@login_required
@write_required
def dispose(asset_id):
    if not current_user.is_admin:
        abort(403)
    asset = db.session.get(FixedAsset, asset_id)
    if asset is None:
        abort(404)
    if asset.status != FixedAsset.STATUS_ACTIVE:
        flash("الأصل ده اتخرد أو اتأرشف خلاص.", "error")
        return redirect(url_for("assets.asset_detail", asset_id=asset.id))

    form = DisposeForm()
    form.sale_treasury_id.choices = (
        [("", "— اختر —")] + [(str(id_), lbl) for id_, lbl in acc.active_choices()]
    )
    if not form.validate_on_submit():
        for _, errs in form.errors.items():
            for e in errs:
                flash(e, "error")
        return redirect(url_for("assets.asset_detail", asset_id=asset.id))

    sale_price = Decimal(str(form.sale_price.data or 0))
    sale_treasury = None
    if sale_price > 0:
        if not form.sale_treasury_id.data:
            flash("لازم تختار حساب استلام ثمن البيع.", "error")
            return redirect(url_for("assets.asset_detail", asset_id=asset.id))
        sale_treasury = db.session.get(TreasuryAccount, form.sale_treasury_id.data)

    try:
        svc.dispose_asset(
            asset,
            disposal_date=form.disposal_date.data,
            sale_price=sale_price,
            sale_treasury=sale_treasury,
            notes=(form.notes.data or "").strip(),
            created_by=current_user.id,
        )
    except LedgerError as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("assets.asset_detail", asset_id=asset.id))

    log_action("asset_disposed", "FixedAsset", asset.id,
               details=f"sale_price={sale_price}")
    db.session.commit()
    flash("تم تخريد الأصل — القيد المحاسبي اترحّل.", "success")
    return redirect(url_for("assets.asset_detail", asset_id=asset.id))
