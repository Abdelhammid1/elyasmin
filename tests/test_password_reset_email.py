"""PHASE 28 (SEC-1): regression suite for the password-reset email.

Three scenarios, all with `smtplib.SMTP` monkey-patched so no real
network call ever leaves the test:

1.  ``test_happy_path_sends_email`` — SMTP_HOST is set, POST to
    /auth/forgot-password for a real user → FakeSMTP.sendmail
    receives one message containing the user's reset_token, DB has
    a fresh token + 2-hour expiry, flash matches the generic
    enumeration-safe message.

2.  ``test_log_only_fallback_when_smtp_host_blank`` — SMTP_HOST blank
    → no SMTP class is instantiated, generic flash still shown, no
    audit-log failure row is written (log-only isn't a failure).

3.  ``test_smtp_failure_records_audit_log`` — FakeSMTP.sendmail raises
    smtplib.SMTPException → generic flash still shown, AuditLog has
    a `password_reset_email_failed` row for that user.

These lock the SEC-1 contract: the enumeration-safe flow never
diverges based on delivery outcome, and every send failure lands
in the audit log without user-visible noise.
"""
from __future__ import annotations

import smtplib
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.audit import AuditLog
from app.models.auth import User


# ---------------------------------------------------------------------------
# One canonical seeded user for the whole file — admin@yasmin-farm.com
# is always present (per conftest.py).
# ---------------------------------------------------------------------------

TARGET_EMAIL = "admin@yasmin-farm.com"

GENERIC_FLASH = (
    "لو الإيميل مسجّل عندنا، هيتبعت رابط استرجاع كلمة المرور خلال دقيقة."
)


class FakeSMTP:
    """Drop-in replacement for smtplib.SMTP that records every call
    instead of touching the network. Instances register themselves on
    the class-level `instances` list so a test can inspect what was
    sent."""

    instances: list["FakeSMTP"] = []
    raise_on_send: bool = False  # test-level knob

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_calls: list[tuple[str, str]] = []
        self.sendmail_calls: list[tuple[str, list[str], str]] = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_calls.append((user, password))

    def sendmail(self, from_addr, to_addrs, msg_string):
        if FakeSMTP.raise_on_send:
            raise smtplib.SMTPException("simulated failure")
        self.sendmail_calls.append((from_addr, to_addrs, msg_string))


@pytest.fixture
def fake_smtp(monkeypatch):
    """Isolate every test's FakeSMTP state, then monkey-patch smtplib.SMTP
    inside `app.services.mail` (the sender imports it at module load,
    so we patch it there)."""
    FakeSMTP.instances = []
    FakeSMTP.raise_on_send = False
    import app.services.mail as mail_module
    monkeypatch.setattr(mail_module.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def _post_forgot(client, email=TARGET_EMAIL):
    """Shared helper — CSRF is off in test config (Phase 27)."""
    return client.post(
        "/auth/forgot-password",
        data={"email": email},
        follow_redirects=False,
    )


def _fresh_user_token(app):
    with app.app_context():
        return User.query.filter_by(email=TARGET_EMAIL).first().reset_token


# ---------------------------------------------------------------------------


def test_happy_path_sends_email(app, client, fake_smtp):
    """Real SMTP config → one email leaves the sender containing the
    reset URL, and the flash message stays generic."""
    app.config["SMTP_HOST"] = "smtp.test.local"
    app.config["SMTP_USER"] = "resend"
    app.config["SMTP_PASSWORD"] = "test-key"
    app.config["SMTP_USE_TLS"] = True

    r = _post_forgot(client)
    assert r.status_code in (302, 303)

    # Exactly one SMTP session, one sendmail
    assert len(fake_smtp.instances) == 1
    inst = fake_smtp.instances[0]
    assert inst.host == "smtp.test.local"
    assert inst.starttls_called is True
    assert inst.login_calls == [("resend", "test-key")]
    assert len(inst.sendmail_calls) == 1
    from_addr, to_addrs, msg_string = inst.sendmail_calls[0]
    assert TARGET_EMAIL in to_addrs

    # The message carries the user's reset_token (a live URL). MIME
    # parts are base64-encoded by MIMEText, so we walk the message and
    # inspect the decoded payloads rather than the raw wire bytes.
    import email as _email
    parsed = _email.message_from_string(msg_string)
    decoded_bodies = [
        p.get_payload(decode=True).decode("utf-8")
        for p in parsed.walk() if p.get_content_type() in ("text/plain", "text/html")
    ]
    token = _fresh_user_token(app)
    assert token is not None
    assert any(token in body for body in decoded_bodies), (
        "The generated reset URL — which embeds user.reset_token — "
        "was not in the email body"
    )

    # Token expiry is roughly two hours out (matches auth/routes.py:86).
    with app.app_context():
        u = User.query.filter_by(email=TARGET_EMAIL).first()
        delta = u.reset_token_expires - datetime.utcnow()
        assert timedelta(minutes=115) < delta < timedelta(minutes=125)


def test_log_only_fallback_when_smtp_host_blank(app, client, fake_smtp):
    """SMTP_HOST blank → sender takes the log-only path; no SMTP
    instantiation, no audit-log failure, and the flash still generic."""
    app.config["SMTP_HOST"] = ""

    with app.app_context():
        before = AuditLog.query.filter_by(action="password_reset_email_failed").count()
    r = _post_forgot(client)
    assert r.status_code in (302, 303)

    # Never touched smtplib
    assert fake_smtp.instances == []

    # Log-only is a success, not a failure — no audit row
    with app.app_context():
        after = AuditLog.query.filter_by(action="password_reset_email_failed").count()
    assert after == before


def test_smtp_failure_records_audit_log(app, client, fake_smtp):
    """SMTP session raises → the sender swallows it (returns False),
    the route records `password_reset_email_failed` in the audit log,
    and the user-facing flash still doesn't mention it."""
    app.config["SMTP_HOST"] = "smtp.test.local"
    app.config["SMTP_USER"] = ""  # skip login branch
    app.config["SMTP_USE_TLS"] = False
    FakeSMTP.raise_on_send = True

    with app.app_context():
        before = AuditLog.query.filter_by(action="password_reset_email_failed").count()
    r = _post_forgot(client)
    assert r.status_code in (302, 303)

    # We tried to send, but it raised — no sendmail_calls succeeded
    assert len(fake_smtp.instances) == 1
    assert fake_smtp.instances[0].sendmail_calls == []

    # Audit row was written, and its details carry the reset URL
    # for ops triage.
    with app.app_context():
        after = AuditLog.query.filter_by(action="password_reset_email_failed").count()
        assert after == before + 1
        row = (
            AuditLog.query.filter_by(action="password_reset_email_failed")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert row.details and "reset_url=" in row.details
