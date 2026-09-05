import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from app.extensions import db, limiter
from app.forms.auth import ChangePasswordForm, ForgotPasswordForm, LoginForm, ResetPasswordForm
from app.models.auth import LoginAttempt, User
from app.services.mail import send_password_reset_email
from app.utils.audit import log_action

bp = Blueprint("auth", __name__, template_folder="../../templates/auth")


def _safe_next(raw: str | None) -> str | None:
    """PHASE 33: only accept a ``next`` that is a SAME-ORIGIN, in-app
    path that is NOT the landing page. Otherwise the caller falls
    back to /dashboard.

    Two problems this closes:
      1. Open-redirect: `?next=https://evil.example` used to send
         the user to an outside URL after login.
      2. UX bug (Zakaria): landing on `/` after login instead of
         the dashboard, because Flask-Login had stamped `next=/`
         when the visitor first hit the landing page.
    """
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "//")):
        return None            # open-redirect defence
    if not raw.startswith("/"):
        return None            # relative-only
    if raw == "/" or raw.startswith("/?") or raw.startswith("/#"):
        return None            # landing itself is never post-login target
    return raw


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
@limiter.limit("10 per hour")  # SEC-4: per-IP ceiling (email-based
                                # lockout in `_recent_failed_attempts`
                                # still runs on top of this)
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
            # PHASE 33: guard `next` against open-redirect AND against
            # the observed "lands on /" UX bug.
            next_page = _safe_next(request.args.get("next"))
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
@limiter.limit("10 per hour")  # SEC-4
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
            # SEC-1 (PHASE 28): send the reset link over SMTP. Falls
            # back to logger-only when SMTP_HOST is blank (dev mode).
            sent = send_password_reset_email(user, reset_url)
            if not sent:
                # Never break the enumeration-safe flow — record the
                # SMTP failure in the audit log so ops can see it,
                # then keep going. `details=` on log_action was
                # unused elsewhere; this is its first caller.
                log_action(
                    "password_reset_email_failed", "User", user.id,
                    details=f"reset_url={reset_url}",
                )
                db.session.commit()
            # Fallback breadcrumb — cheap, and useful when SMTP is
            # blank AND a dev is watching the log.
            current_app.logger.info(
                "Password reset link for %s: %s (sent=%s)",
                email, reset_url, sent,
            )

        flash(
            "لو الإيميل مسجّل عندنا، هيتبعت رابط استرجاع كلمة المرور خلال دقيقة.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html", form=form)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour")  # SEC-4
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


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """TC-1.5: forced first-login password change; also usable voluntarily."""
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("كلمة المرور الحالية غير صحيحة.", "error")
        elif form.current_password.data == form.new_password.data:
            flash("كلمة المرور الجديدة لازم تكون مختلفة عن الحالية.", "error")
        else:
            current_user.set_password(form.new_password.data)
            current_user.must_change_password = False
            log_action("password_changed", "User", current_user.id)
            db.session.commit()
            flash("تم تحديث كلمة المرور.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/change_password.html", form=form)
