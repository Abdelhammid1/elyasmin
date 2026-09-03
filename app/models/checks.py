"""PHASE 8a — checks (شيكات).

A check the farm receives from a customer or issues to a supplier.
Real-world flow:

    received: pending → cleared        (bank deposits it, funds arrive)
    received: pending → bounced        (bank returns it, customer still owes)
    issued:   pending → cleared        (supplier deposits it, funds leave)
    issued:   pending → bounced        (rare, but happens — reversed against supplier)

Every status transition posts one JE via `app/services/checks.py`.
The check row itself just carries the metadata; balances live in
the ledger.

Notes:
- Exactly one of `customer_id` / `supplier_id` is set. Enforced by a
  CHECK constraint at the DB level so the invariant never quietly rots.
- `treasury_account_id` is null until the check clears (received)
  or is drawn (issued) — that's the drawer the money moves in/out of.
- `related_ref` is free text: an invoice number the client wants to
  tie the check to. Not an FK because a check often covers a stack of
  invoices.
"""
from datetime import date, datetime
from decimal import Decimal

from app.extensions import db


class Check(db.Model):
    __tablename__ = "checks"

    DIRECTION_RECEIVED = "received"
    DIRECTION_ISSUED = "issued"

    STATUS_PENDING = "pending"
    STATUS_CLEARED = "cleared"
    STATUS_BOUNCED = "bounced"

    DIRECTION_LABELS = {
        DIRECTION_RECEIVED: "شيك وارد",
        DIRECTION_ISSUED: "شيك صادر",
    }
    STATUS_LABELS = {
        STATUS_PENDING: "معلّق",
        STATUS_CLEARED: "تم صرفه",
        STATUS_BOUNCED: "ارتد",
    }

    id = db.Column(db.Integer, primary_key=True)
    direction = db.Column(db.String(10), nullable=False, index=True)

    # Exactly one of these two is set — enforced by table CHECK below.
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"),
        nullable=True, index=True,
    )
    supplier_id = db.Column(
        db.Integer, db.ForeignKey("suppliers.id"),
        nullable=True, index=True,
    )

    check_number = db.Column(db.String(60), nullable=False)
    bank_name = db.Column(db.String(120), nullable=False)
    drawer_name = db.Column(db.String(120), nullable=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)

    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False, index=True)

    status = db.Column(
        db.String(10), nullable=False,
        default=STATUS_PENDING, server_default="pending", index=True,
    )
    cleared_on = db.Column(db.Date, nullable=True)
    bounced_on = db.Column(db.Date, nullable=True)

    # Only set on cleared checks — the drawer the money moves in/out of.
    treasury_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"),
        nullable=True, index=True,
    )
    related_ref = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    customer = db.relationship("Customer", foreign_keys=[customer_id],
                               backref=db.backref("checks", lazy="dynamic"))
    supplier = db.relationship("Supplier", foreign_keys=[supplier_id],
                               backref=db.backref("checks", lazy="dynamic"))
    treasury_account = db.relationship("TreasuryAccount")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    __table_args__ = (
        # Exactly one of customer_id / supplier_id is set.
        db.CheckConstraint(
            "(customer_id IS NOT NULL AND supplier_id IS NULL) OR "
            "(customer_id IS NULL AND supplier_id IS NOT NULL)",
            name="ck_check_one_party",
        ),
        db.CheckConstraint(
            "direction IN ('received', 'issued')",
            name="ck_check_direction",
        ),
        db.CheckConstraint(
            "status IN ('pending', 'cleared', 'bounced')",
            name="ck_check_status",
        ),
    )

    # ---------- display helpers ----------
    @property
    def direction_label(self) -> str:
        return self.DIRECTION_LABELS.get(self.direction, self.direction)

    @property
    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def party_name(self) -> str:
        if self.customer_id:
            return self.customer.name
        if self.supplier_id:
            return self.supplier.name
        return "—"

    def days_until_due(self) -> int:
        return (self.due_date - date.today()).days

    @property
    def is_due_soon(self) -> bool:
        """Pending checks with a due date in the next 7 days (or already past)."""
        if self.status != self.STATUS_PENDING:
            return False
        return self.days_until_due() <= 7
