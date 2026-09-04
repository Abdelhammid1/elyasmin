import os
from datetime import datetime, timedelta

from flask import Flask, render_template

from config import configs
from app.extensions import csrf, db, limiter, login_manager, migrate


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.getenv("FLASK_ENV", "development")
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(configs[config_name])

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)  # SEC-4 (PHASE 29)

    from app.models import auth as _auth_models  # noqa: F401
    from app.models import herd as _herd_models  # noqa: F401
    from app.models import audit as _audit_models  # noqa: F401
    from app.models import inventory as _inv_models  # noqa: F401
    from app.models import suppliers as _sup_models  # noqa: F401
    from app.models import feed as _feed_models  # noqa: F401
    from app.models import sales as _sales_models  # noqa: F401
    from app.models import finance as _fin_models  # noqa: F401
    from app.models import labor as _labor_models  # noqa: F401
    from app.models import accounting as _acct_models  # noqa: F401
    from app.models import checks as _checks_models  # noqa: F401
    from app.models import assets as _assets_models  # noqa: F401

    from app.models.auth import User

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    from app.blueprints.auth.routes import bp as auth_bp
    from app.blueprints.users.routes import bp as users_bp
    from app.blueprints.herd.routes import bp as herd_bp
    from app.blueprints.dashboard.routes import bp as dashboard_bp
    from app.blueprints.landing.routes import bp as landing_bp
    from app.blueprints.suppliers.routes import bp as suppliers_bp
    from app.blueprints.inventory.routes import bp as inventory_bp
    from app.blueprints.purchases.routes import bp as purchases_bp
    from app.blueprints.feed.routes import bp as feed_bp
    from app.blueprints.medicine.routes import bp as medicine_bp
    from app.blueprints.customers.routes import bp as customers_bp
    from app.blueprints.milk.routes import bp as milk_bp
    from app.blueprints.finance.routes import bp as finance_bp
    from app.blueprints.labor.routes import bp as labor_bp
    from app.blueprints.help.routes import bp as help_bp
    from app.blueprints.accounts.routes import bp as accounts_bp
    from app.blueprints.assistant.routes import bp as assistant_bp
    from app.blueprints.accounting.routes import bp as accounting_bp
    from app.blueprints.returns.routes import bp as returns_bp
    from app.blueprints.reports.routes import bp as reports_bp
    from app.blueprints.checks.routes import bp as checks_bp
    from app.blueprints.assets.routes import bp as assets_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(users_bp, url_prefix="/users")
    app.register_blueprint(herd_bp, url_prefix="/herd")
    app.register_blueprint(dashboard_bp)
    # The public marketing page at `/`. Must be registered AFTER dashboard so it
    # is clear at a glance the two don't collide on the same URL any more.
    app.register_blueprint(landing_bp)
    app.register_blueprint(suppliers_bp, url_prefix="/suppliers")
    app.register_blueprint(inventory_bp, url_prefix="/inventory")
    app.register_blueprint(purchases_bp, url_prefix="/purchases")
    app.register_blueprint(feed_bp, url_prefix="/feed")
    app.register_blueprint(medicine_bp, url_prefix="/medicine")
    app.register_blueprint(customers_bp, url_prefix="/customers")
    app.register_blueprint(milk_bp, url_prefix="/milk")
    app.register_blueprint(finance_bp, url_prefix="/finance")
    app.register_blueprint(accounts_bp, url_prefix="/accounts")
    app.register_blueprint(assistant_bp, url_prefix="/help/assistant")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(labor_bp, url_prefix="/labor")
    app.register_blueprint(help_bp, url_prefix="/help")
    app.register_blueprint(accounting_bp, url_prefix="/accounting")
    app.register_blueprint(returns_bp, url_prefix="/returns")
    app.register_blueprint(checks_bp, url_prefix="/checks")
    app.register_blueprint(assets_bp, url_prefix="/assets")

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_):
        return render_template("errors/500.html"), 500

    @app.errorhandler(413)
    def payload_too_large(_):
        # SEC-3 (PHASE 29): Werkzeug fires this before the view when
        # the request body exceeds MAX_CONTENT_LENGTH.
        return render_template("errors/413.html"), 413

    @app.errorhandler(429)
    def too_many_requests(_):
        # SEC-4 (PHASE 29): Flask-Limiter's default 429; also fired
        # from /auth/login when the per-email lockout kicks in.
        return render_template("errors/429.html"), 429

    # PHASE 10 (YAS-ACC-1): expose the invoice→JE reverse-lookup helper
    # as a jinja global so any invoice-like template can pull the JE
    # without route glue.
    from app.services.accounting_links import find_journal_entry_for
    app.jinja_env.globals["find_journal_entry_for"] = find_journal_entry_for

    # PHASE 18 (PDF): `currency_ar` filter — maps ISO currency codes to
    # their Arabic name for the printed invoice ("EGP" → "جنيه مصري").
    # Ported from marsoud's convention.
    _CURRENCY_AR = {
        "EGP": "جنيه مصري", "SAR": "ريال سعودي",
        "USD": "دولار أمريكي", "EUR": "يورو",
    }
    app.jinja_env.filters["currency_ar"] = lambda c: _CURRENCY_AR.get(
        (c or "").upper(), c or "")

    @app.context_processor
    def inject_company_profile():
        # PHASE 11 (YAS-SET-3): every logged-in page can read the current
        # company profile without route glue. The `.current()` helper
        # auto-creates the singleton row on first access, so this never
        # returns None on a booted app. Guarded to skip the auth pages
        # where the DB session may not be ready.
        from flask_login import current_user
        try:
            if not current_user.is_authenticated:
                return {"company": None}
            from app.models.finance import CompanyProfile
            return {"company": CompanyProfile.current()}
        except Exception:  # noqa: BLE001
            return {"company": None}

    @app.context_processor
    def inject_globals():
        # BRAND: the four values are env-driven so a deployment can rebrand or hide
        # the attribution without a code change. BRAND_SHOW=0 removes it everywhere.
        return {
            "now": datetime.utcnow,
            "app_name": "مزرعة الياسمين",
            "timedelta": timedelta,
            "brand": {
                "text": os.getenv("BRAND_TEXT", "Developed by"),
                "name": os.getenv("BRAND_NAME", "Manasety"),
                "url":  os.getenv("BRAND_URL",  "https://manasety.ai"),
                "show": os.getenv("BRAND_SHOW", "1") != "0",
            },
        }

    @app.context_processor
    def inject_role_helpers():
        """PHASE 27 (SEC-2): expose the current user's write permission
        to every template so `{% if can_write %}` can wrap write buttons.
        Backend already blocks the write via @write_required — this just
        hides the button so viewers don't see a link that would 403.
        `user_role` is exposed for the top-bar viewer chip."""
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            return {"can_write": False, "user_role": None}
        return {
            "can_write": bool(getattr(current_user, "can_write", False)),
            "user_role": getattr(current_user, "role", None),
        }

    @app.context_processor
    def inject_pending_leaves():
        """PHASE 15 (YAS-HR-1): live badge on the sidebar's leave link for
        admins. Cheap COUNT with a status index, one per request."""
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            return {"pending_leaves_total": 0}
        if not getattr(current_user, "is_admin", False):
            return {"pending_leaves_total": 0}
        from app.models.labor import LeaveRequest
        n = LeaveRequest.query.filter_by(
            status=LeaveRequest.STATUS_PENDING).count()
        return {"pending_leaves_total": n}

    @app.context_processor
    def inject_sidebar_badges():
        """M3 sidebar redesign: small real-count badges next to a couple
        of nav sections (herd size, active suppliers), matching the
        reference design's badge pattern. Same cheap-COUNT-per-request
        style as inject_pending_leaves above — no new query pattern."""
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            return {"herd_badge_count": 0, "suppliers_badge_count": 0}
        from app.models.herd import Cow
        from app.models.suppliers import Supplier
        herd_count = Cow.query.filter_by(is_archived=False).count()
        suppliers_count = Supplier.query.filter_by(is_archived=False).count()
        return {
            "herd_badge_count": herd_count,
            "suppliers_badge_count": suppliers_count,
        }

    @app.before_request
    def track_activity_and_timeout():
        """Enforce rolling inactivity timeout regardless of remember_me cookie.

        TC-1.4 fix: previously Flask-Login's remember_me kept users signed in for
        weeks. Now every request checks last activity and forces logout after
        SESSION_LIFETIME_MINUTES of inactivity.
        """
        from flask import request, session
        from flask_login import current_user, logout_user

        session.permanent = True

        # Skip enforcement on auth + static endpoints so a fresh login can succeed
        if request.endpoint in {"static", None} or (request.endpoint or "").startswith("auth."):
            session["last_activity"] = datetime.utcnow().timestamp()
            return

        if current_user.is_authenticated:
            timeout = app.config["PERMANENT_SESSION_LIFETIME"]
            last = session.get("last_activity")
            now = datetime.utcnow().timestamp()
            if last is not None and (now - last) > timeout.total_seconds():
                logout_user()
                session.clear()
                from flask import flash, redirect, url_for

                flash("انتهت جلستك بسبب عدم النشاط، من فضلك سجّل الدخول مجدداً.", "warning")
                return redirect(url_for("auth.login"))
            session["last_activity"] = now

    @app.before_request
    def enforce_viewer_read_only():
        """TC-9.6 fix: viewer role cannot use POST / PUT / DELETE / PATCH.

        Applied globally so URL-manipulation attempts fail hard with 403.
        Auth endpoints (login/logout/change_password) are always allowed.
        """
        from flask import abort, request
        from flask_login import current_user

        if not current_user.is_authenticated:
            return
        if current_user.role != "viewer":
            return
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        # Allow the viewer to logout + change their own password
        if (request.endpoint or "") in {"auth.logout", "auth.change_password"}:
            return
        abort(403)

    @app.before_request
    def force_password_change():
        """TC-1.5 fix: users with must_change_password=True are redirected to the
        password change page for every request except that page + logout + static.
        """
        from flask import redirect, request, url_for
        from flask_login import current_user

        if not current_user.is_authenticated:
            return
        if not getattr(current_user, "must_change_password", False):
            return

        allowed = {
            "auth.change_password",
            "auth.logout",
            "static",
        }
        if request.endpoint in allowed or request.endpoint is None:
            return
        return redirect(url_for("auth.change_password"))

    return app
