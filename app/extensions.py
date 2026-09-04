from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

# SEC-4 (PHASE 29): per-IP rate limiter. No default limits — every
# guarded endpoint declares its own `@limiter.limit("...")`. In-memory
# storage is fine for the single-worker gunicorn deployment; for a
# future multi-worker setup, swap `storage_uri="memory://"` for
# `storage_uri="redis://…"` — Flask-Limiter picks the right backend
# from the URI scheme, no other code change needed.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)

login_manager.login_view = "auth.login"
login_manager.login_message = "من فضلك سجّل الدخول للوصول إلى هذه الصفحة."
login_manager.login_message_category = "warning"
