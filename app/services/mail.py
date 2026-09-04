"""SMTP email sender — SEC-1 (PHASE 28).

Ported (trimmed) from the sibling marsoud project's `services/email.py`.
Same principles:

  - Pure stdlib (`smtplib` + `email.mime`) — no new dependency.
  - Never raises. Every path returns bool.
  - Log-only fallback when `SMTP_HOST` is blank — keeps dev functional
    without needing real SMTP credentials.
  - Generic config vars (`SMTP_HOST` etc.) so we're not vendor-locked
    to Resend; a Resend deployment fills in
        SMTP_HOST     = smtp.resend.com
        SMTP_PORT     = 587
        SMTP_USER     = resend
        SMTP_PASSWORD = <api key>
        SMTP_USE_TLS  = true
    and it works.

Public API:
    send_email(to, subject, html_body, text_body=None) -> bool
    send_password_reset_email(user, reset_url)         -> bool

New wrappers for other transactional emails (invoice-sent, payment,
etc.) go here as ~10-line functions calling `send_email` — same
pattern as marsoud's `send_invoice_email`, `send_payment_received_email`
and friends.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from flask import current_app, render_template

_LOG = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    """SEC-1: True only when the operator has actually set SMTP_HOST.
    Blank is the dev default and switches us to log-only mode."""
    return bool(current_app.config.get("SMTP_HOST"))


def send_email(
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
) -> bool:
    """Deliver one email over SMTP. Returns True on success (or on the
    log-only fallback path in dev), False on any SMTP failure.

    NEVER raises. Any exception during the SMTP session is caught,
    logged with host/port/user/to/exception-class/message at ERROR,
    and swallowed — the caller is expected to check the bool and,
    where a paper trail matters, write its own audit-log entry.

    `text_body` defaults to the HTML body with tags stripped-ish (a
    naïve fallback — good enough for a reset link, which is basically
    one URL). Callers who care can pass a hand-written plain-text
    version.
    """
    cfg = current_app.config
    from_addr = cfg.get("SMTP_FROM", "no-reply@example.com")
    from_name = cfg.get("SMTP_FROM_NAME", "")
    from_header = formataddr((from_name, from_addr)) if from_name else from_addr

    if not _smtp_configured():
        # Dev mode. Log the full envelope so a developer can grab the
        # reset link straight out of the Flask log without needing a
        # real inbox. The prefix `[MAIL log-only]` makes it easy to
        # filter.
        _LOG.info(
            "[MAIL log-only] To: %s | From: %s | Subject: %s\n---\n%s\n---",
            to, from_header, subject,
            text_body or html_body,
        )
        return True

    if text_body is None:
        text_body = html_body

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to
    # Plain first, HTML second — per RFC 2046, the LAST part is the
    # preferred one for clients that can render it.
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    host = cfg["SMTP_HOST"]
    port = int(cfg.get("SMTP_PORT", 587))
    user = cfg.get("SMTP_USER", "")
    password = cfg.get("SMTP_PASSWORD", "")
    use_tls = bool(cfg.get("SMTP_USE_TLS", True))

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            if use_tls:
                server.starttls()
            if user:
                server.login(user, password)
            server.sendmail(from_addr, [to], msg.as_string())
        _LOG.info("[MAIL sent] To: %s | Subject: %s", to, subject)
        return True
    except Exception as e:  # noqa: BLE001 — intentional: never re-raise
        _LOG.error(
            "[MAIL failed] host=%s port=%s user=%s to=%s exc=%s: %s",
            host, port, user, to, type(e).__name__, e,
        )
        return False


def send_password_reset_email(user, reset_url: str) -> bool:
    """SEC-1: send the password-reset email built by
    `auth.forgot_password`. Returns whatever `send_email` returns —
    the caller (currently `forgot_password`) records a failure in the
    audit log without changing the enumeration-safe flash message.

    `user` is an `app.models.auth.User` — we use `.full_name` and
    `.email`. `reset_url` is the `_external=True` URL built with
    `url_for("auth.reset_password", token=...)`.
    """
    app_name = current_app.config.get("APP_NAME", "مزرعة الياسمين")
    subject = f"إعادة تعيين كلمة المرور — {app_name}"

    html = render_template(
        "emails/password_reset.html",
        user=user,
        reset_url=reset_url,
        app_name=app_name,
    )
    # Plain-text fallback: mail clients that can't render HTML still
    # get the link.
    text = (
        f"مرحبًا {user.full_name or ''},\n\n"
        f"وصلنا طلب إعادة تعيين كلمة المرور لحسابك على {app_name}.\n"
        f"افتح الرابط التالي خلال ساعتين لتعيين كلمة مرور جديدة:\n\n"
        f"{reset_url}\n\n"
        f"لو مطلبتش ده، تجاهل الرسالة — كلمة المرور الحالية هتفضل زي ما هي.\n"
    )
    return send_email(user.email, subject, html, text)
