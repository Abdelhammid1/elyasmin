from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    ROLE_ADMIN = "admin"
    ROLE_MANAGER = "manager"
    ROLE_VIEWER = "viewer"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_MANAGER)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)

    reset_token = db.Column(db.String(255), nullable=True, index=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)

    must_change_password = db.Column(db.Boolean, nullable=False, default=False)

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    @property
    def is_admin(self) -> bool:
        return self.role == self.ROLE_ADMIN

    @property
    def can_write(self) -> bool:
        """False for viewer, True for admin and manager."""
        return self.role in (self.ROLE_ADMIN, self.ROLE_MANAGER)

    @property
    def is_first_admin(self) -> bool:
        return self.id == 1

    @property
    def role_label(self) -> str:
        return {
            self.ROLE_ADMIN: "مدير النظام",
            self.ROLE_MANAGER: "مدير مزرعة",
            self.ROLE_VIEWER: "مشاهد فقط",
        }.get(self.role, self.role)

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class LoginAttempt(db.Model):
    __tablename__ = "login_attempts"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    ip_address = db.Column(db.String(45), nullable=True)
    success = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
