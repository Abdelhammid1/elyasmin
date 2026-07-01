from flask import request
from flask_login import current_user

from app.extensions import db
from app.models.audit import AuditLog


def log_action(action: str, entity: str, entity_id: int | None = None, details: str | None = None) -> None:
    user_id = current_user.id if current_user.is_authenticated else None
    ip = request.remote_addr if request else None
    db.session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            details=details,
            ip_address=ip,
        )
    )
