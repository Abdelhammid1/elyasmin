import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from app.extensions import db
from app.forms.auth import ForgotPasswordForm, LoginForm, ResetPasswordForm
from app.models.auth import LoginAttempt, User
from app.utils.audit import log_action

bp = Blueprint("auth", __name__, template_folder="../../templates/auth")


def _recent_failed_attempts(email: str) -> int:
    window_start = datetime.utcnow() - timedelta(hours=1)
    return (
        db.session.query(func.count(LoginAttempt.id))
        .filter(
            LoginAttempt.email == email.lower(),
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at >= window_start,
        )
        .scalar()
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        max_attempts = current_app.config["MAX_LOGIN_ATTEMPTS"]

        if _recent_failed_attempts(email) >= max_attempts:
            flash(
                f"الحساب متوقف مؤقتاً بسبب {max_attempts} محاولات دخول فاشلة. جرّب بعد ساعة.",
                "error",
            )
            return render_template("auth/login.html", form=form), 429

        user = User.query.filter(func.lower(User.email) == email).first()
        attempt = LoginAttempt(email=email, ip_address=request.remote_addr, success=False)

        if user and user.check_password(form.password.data) and user.is_active and not user.is_archived:
            attempt.success = True
            db.session.add(attempt)
            user.last_login_at = datetime.utcnow()
            log_action("login", "User", user.id)
            db.session.commit()
            login_user(user, remember=form.remember_me.data)
            flash(f"أهلاً بيك يا {user.full_name}", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))

        db.session.add(attempt)
        db.session.commit()
        flash("بيانات الدخول غير صحيحة. راجع الإيميل وكلمة المرور.", "error")

    return render_template("auth/login.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    log_action("logout", "User", current_user.id)
    db.session.commit()
    logout_user()
    flash("تم تسجيل الخروج بنجاح.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter(func.lower(User.email) == email).first()

        if user and user.is_active and not user.is_archived:
            user.reset_token = secrets.token_urlsafe(32)
            user.reset_token_expires = datetime.utcnow() + timedelta(hours=2)
            db.session.commit()
            reset_url = url_for("auth.reset_password", token=user.reset_token, _external=True)
            current_app.logger.info("Password reset link for %s: %s", email, reset_url)

        flash(
            "لو الإيميل مسجّل عندنا، هيتبعت رابط استرجاع كلمة المرور خلال دقيقة.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html", form=form)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    user = User.query.filter_by(reset_token=token).first()

    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        flash("رابط استرجاع كلمة المرور غير صالح أو منتهي الصلاحية.", "error")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.reset_token = None
        user.reset_token_expires = None
        log_action("password_reset", "User", user.id)
        db.session.commit()
        flash("تم تحديث كلمة المرور. تقدر تسجّل الدخول دلوقتي.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form, token=token)
