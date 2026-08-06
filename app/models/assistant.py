"""ASSISTANT: usage + cost log for the in-app AI helper.

Every question is logged, successful or not, so the daily per-user limit and the
monthly budget kill-switch have something to count, and so anyone can see what
the assistant is costing without opening the DeepSeek dashboard.
"""
from datetime import date, datetime
from decimal import Decimal

from app.extensions import db


class AIUsageLog(db.Model):
    __tablename__ = "ai_usage_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    cost_usd = db.Column(db.Numeric(10, 6), nullable=False, default=Decimal("0"))

    model = db.Column(db.String(50), nullable=False)
    success = db.Column(db.Boolean, nullable=False, default=True)
    error_message = db.Column(db.Text, nullable=True)

    asked_on = db.Column(db.Date, nullable=False, default=date.today, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship("User")

    @staticmethod
    def cost_for(input_tokens: int, output_tokens: int,
                 price_in: float, price_out: float) -> Decimal:
        """Cost in USD for one call, at the configured per-million prices."""
        million = Decimal(1_000_000)
        return (
            Decimal(int(input_tokens or 0)) / million * Decimal(str(price_in))
            + Decimal(int(output_tokens or 0)) / million * Decimal(str(price_out))
        )
