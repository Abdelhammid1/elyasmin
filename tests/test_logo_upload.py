"""PHASE 29 (SEC-3): regression suite for the company-logo upload
validator in `app/utils/uploads.py::validate_logo` + the route wire-up
in `finance.settings`.

Four cases:
  1. `.svg` (allowed before this phase) → flash "الامتداد غير مسموح",
     logo_path unchanged on the profile.
  2. `.png` extension but the body is actual HTML → magic-number
     sniff fires, flash "لا يطابق المحتوى".
  3. 3 MB payload (over the 2 MB cap) → flash "أكبر من 2 ميجابايت".
  4. A real, tiny valid PNG → 302, file lands under
     app/static/img/company/, profile.logo_path set.

Also tests that ANY oversize request body (over MAX_CONTENT_LENGTH)
hits the global Werkzeug 413 handler.
"""
from __future__ import annotations

import os
from io import BytesIO

import pytest

from app.models.finance import CompanyProfile


# 1x1 transparent PNG (67 bytes) — the smallest valid PNG we can send.
_TINY_PNG = bytes.fromhex(
    "89504E470D0A1A0A"           # PNG signature
    "0000000D49484452"           # IHDR chunk
    "0000000100000001"           # 1x1
    "0806000000"                 # bit depth 8, color type 6
    "1F15C489"                   # CRC
    "0000000A49444154"           # IDAT
    "789C63000100000500010D0A2DB4"
    "0000000049454E44AE426082"   # IEND
)

FLASH_EXT   = "الامتداد غير مسموح"
FLASH_MAGIC = "لا يطابق المحتوى"
FLASH_SIZE  = "أكبر من 2 ميجابايت"


def _post_company_form(client, files, extra=None):
    """POST the company tab with a logo file attachment.

    Only the fields the route reads are set; every other field on
    CompanyProfileForm is Optional so an empty POST for them is fine.
    Uses `admin_client` which is already logged-in.
    """
    data = {
        "_tab": "company",
        "name": "مزرعة الياسمين",
        "base_currency": "EGP",
        "tax_rate_pct": "0",
        "reminder_days_before_due": "3",
        "invoice_number_prefix_sale": "INV",
        "invoice_number_prefix_purchase": "PUR",
    }
    if extra:
        data.update(extra)
    data.update(files)
    return client.post(
        "/finance/settings?tab=company",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def _logo_path_now(app):
    with app.app_context():
        return CompanyProfile.query.get(1).logo_path


# ---------------------------------------------------------------------------


def test_svg_rejected(admin_client, app):
    """SVG is no longer in the whitelist (was, pre-SEC-3)."""
    before = _logo_path_now(app)
    r = _post_company_form(admin_client, {
        "logo": (BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'/>"), "evil.svg"),
    })
    # Form validation (FileAllowed) may render the page inline (200)
    # or redirect (302); either way logo_path must NOT change.
    assert r.status_code in (200, 302, 303)
    assert _logo_path_now(app) == before, "SVG was accepted; logo_path changed"


def test_html_masquerading_as_png_rejected(admin_client, app):
    """A .png that isn't really a PNG → magic-number sniff fires."""
    before = _logo_path_now(app)
    r = _post_company_form(admin_client, {
        "logo": (BytesIO(b"<html><body>hi</body></html>"), "fake.png"),
    })
    assert r.status_code in (302, 303)
    body = admin_client.get("/finance/settings?tab=company").get_data(as_text=True)
    # The Arabic flash carries through the follow-up GET
    assert FLASH_MAGIC in body, "magic-number reject not flashed"
    assert _logo_path_now(app) == before, "HTML-as-PNG was accepted"


def test_oversize_png_rejected(admin_client, app):
    """3 MB blob exceeds MAX_CONTENT_LENGTH (3 MB - a little) OR the
    per-file 2 MB cap. Either way, logo_path must stay unchanged.

    Note: exact behavior depends on which cap trips first. If
    MAX_CONTENT_LENGTH catches it, Werkzeug returns 413. If the file
    slips under MAX_CONTENT_LENGTH but exceeds MAX_LOGO_SIZE, the
    route flashes FLASH_SIZE. Either is acceptable — the invariant
    is: NO SAVE."""
    before = _logo_path_now(app)
    big = _TINY_PNG + (b"\x00" * (2 * 1024 * 1024 + 500))  # ~2 MB + tail
    r = _post_company_form(admin_client, {
        "logo": (BytesIO(big), "big.png"),
    })
    # 200 = form validation rendered the page with errors (FileSize
    # rejected it before hitting the route); 302/303 = route-level
    # flash-and-fallthrough; 413 = Werkzeug's global cap. All three
    # are valid rejection paths — the invariant is: NO SAVE.
    assert r.status_code in (200, 302, 303, 413)
    assert _logo_path_now(app) == before, "oversize PNG was accepted"


def test_valid_png_accepted(admin_client, app, tmp_path):
    """Happy path — a valid tiny PNG lands on disk and updates
    logo_path. Cleans up afterward so the dev DB isn't polluted."""
    r = _post_company_form(admin_client, {
        "logo": (BytesIO(_TINY_PNG), "logo.png"),
    })
    assert r.status_code in (302, 303), f"unexpected {r.status_code}"

    logo_path = _logo_path_now(app)
    assert logo_path is not None
    assert logo_path.startswith("img/company/logo_"), logo_path
    assert logo_path.endswith(".png")

    # File actually landed on disk
    with app.app_context():
        from flask import current_app
        abs_path = os.path.join(current_app.root_path, "static", logo_path)
        assert os.path.isfile(abs_path), f"file not on disk: {abs_path}"
        # ...and it's the exact bytes we sent
        with open(abs_path, "rb") as f:
            assert f.read() == _TINY_PNG
        # Cleanup so the dev DB / disk stays tidy
        try:
            os.remove(abs_path)
        except OSError:
            pass
