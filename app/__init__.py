import os
from datetime import datetime, timedelta

from flask import Flask, render_template

from config import configs
from app.extensions import csrf, db, login_manager, migrate


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.getenv("FLASK_ENV", "development")
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(configs[config_name])

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.models import auth as _auth_models  # noqa: F401
    from app.models import herd as _herd_models  # noqa: F401
    from app.models import audit as _audit_models  # noqa: F401
    from app.models import inventory as _inv_models  # noqa: F401
    from app.models import suppliers as _sup_models  # noqa: F401
    from app.models import feed as _feed_models  # noqa: F401
    from app.models import sales as _sales_models  # noqa: F401
    from app.models import finance as _fin_models  # noqa: F401
    from app.models import labor as _labor_models  # noqa: F401

    from app.models.auth import User

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    from app.blueprints.auth.routes import bp as auth_bp
    from app.blueprints.users.routes import bp as users_bp
    from app.blueprints.herd.routes import bp as herd_bp
    from app.blueprints.dashboard.routes import bp as dashboard_bp
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

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(users_bp, url_prefix="/users")
    app.register_blueprint(herd_bp, url_prefix="/herd")
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(suppliers_bp, url_prefix="/suppliers")
    app.register_blueprint(inventory_bp, url_prefix="/inventory")
    app.register_blueprint(purchases_bp, url_prefix="/purchases")
    app.register_blueprint(feed_bp, url_prefix="/feed")
    app.register_blueprint(medicine_bp, url_prefix="/medicine")
    app.register_blueprint(customers_bp, url_prefix="/customers")
    app.register_blueprint(milk_bp, url_prefix="/milk")
    app.register_blueprint(finance_bp, url_prefix="/finance")
    app.register_blueprint(labor_bp, url_prefix="/labor")
    app.register_blueprint(help_bp, url_prefix="/help")

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_):
        return render_template("errors/500.html"), 500

    @app.context_processor
    def inject_globals():
        return {"now": datetime.utcnow, "app_name": "مزرعة الياسمين", "timedelta": timedelta}

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
