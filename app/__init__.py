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
    def make_session_permanent():
        from flask import session

        session.permanent = True

    return app
