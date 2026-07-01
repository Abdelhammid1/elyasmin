from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db
from app.forms.users import UserCreateForm, UserEditForm
from app.models.auth import User
from app.utils.audit import log_action
from app.utils.decorators import admin_required

bp = Blueprint("users", __name__, template_folder="../../templates/users")


@bp.route("/")
@login_required
@admin_required
def list_users():
    users = User.query.filter_by(is_archived=False).order_by(User.id).all()
    return render_template("users/list.html", users=users)


@bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_required
def create_user():
    form = UserCreateForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        existing = User.query.filter(func.lower(User.email) == email).first()
        if existing:
            flash("الإيميل ده مسجّل قبل كده.", "error")
        else:
            user = User(
                email=email,
                full_name=form.full_name.data.strip(),
                role=form.role.data,
                is_active=form.is_active.data,
                created_by_id=current_user.id,
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.flush()
            log_action("user_created", "User", user.id, details=f"role={user.role}")
            db.session.commit()
            flash(f"تم إضافة المستخدم {user.full_name} بنجاح.", "success")
            return redirect(url_for("users.list_users"))

    return render_template("users/form.html", form=form, mode="create")


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_user(user_id: int):
    user = db.session.get(User, user_id)
    if not user or user.is_archived:
        abort(404)

    form = UserEditForm(obj=user)
    if request.method == "GET":
        form.full_name.data = user.full_name
        form.role.data = user.role
        form.is_active.data = user.is_active

    if form.validate_on_submit():
        user.full_name = form.full_name.data.strip()

        # Business rule: the first admin cannot have their role changed
        if user.is_first_admin and form.role.data != User.ROLE_ADMIN:
            flash("مينفعش تغيّر صلاحية مدير النظام الأول.", "error")
            return render_template("users/form.html", form=form, mode="edit", user=user)

        user.role = form.role.data
        user.is_active = form.is_active.data

        if form.password.data:
            user.set_password(form.password.data)
            log_action("user_password_changed", "User", user.id)

        log_action("user_updated", "User", user.id)
        db.session.commit()
        flash("تم تحديث بيانات المستخدم.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/form.html", form=form, mode="edit", user=user)


@bp.route("/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id: int):
    user = db.session.get(User, user_id)
    if not user or user.is_archived:
        abort(404)

    # Business rule: the first admin cannot be deleted
    if user.is_first_admin:
        flash("مينفعش تحذف مدير النظام الأول.", "error")
        return redirect(url_for("users.list_users"))

    if user.id == current_user.id:
        flash("مينفعش تحذف حسابك بنفسك.", "error")
        return redirect(url_for("users.list_users"))

    user.is_archived = True
    user.is_active = False
    log_action("user_archived", "User", user.id)
    db.session.commit()
    flash(f"تم أرشفة المستخدم {user.full_name}.", "info")
    return redirect(url_for("users.list_users"))
