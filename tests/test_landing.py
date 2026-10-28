"""LANDING-1 regression suite — public landing page.

  1. Title, description, Open Graph (absolute image), local logo, icon
     guard; no template placeholders (Saudi address / phone, empty
     WhatsApp links, hard-coded year, unbacked numbers).
  2. Phone / WhatsApp / address come from settings and are hidden when
     empty; the inquiry form only exists with a WhatsApp number.
  3. Settings screen saves the two numbers (WhatsApp digits only).
"""
from __future__ import annotations

from datetime import date

import pytest

from app.extensions import db
from app.models.finance import CompanyProfile, Setting

_KEYS = (Setting.KEY_LANDING_PHONE, Setting.KEY_LANDING_WHATSAPP)


@pytest.fixture
def landing_settings(app):
    """Snapshot the landing settings + company address, restore after."""
    with app.app_context():
        before = {k: getattr(db.session.get(Setting, k), "value", None) for k in _KEYS}
        address = CompanyProfile.current().address

    def apply(phone="", whatsapp="", addr=None):
        with app.app_context():
            Setting.set(Setting.KEY_LANDING_PHONE, phone)
            Setting.set(Setting.KEY_LANDING_WHATSAPP, whatsapp)
            CompanyProfile.current().address = addr
            db.session.commit()

    yield apply
    with app.app_context():
        for k, v in before.items():
            row = db.session.get(Setting, k)
            if v is None and row is not None:
                db.session.delete(row)
            elif v is not None:
                Setting.set(k, v)
        CompanyProfile.current().address = address
        db.session.commit()


def test_metadata_logo_and_no_placeholders(client, landing_settings):
    landing_settings()
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/html; charset=utf-8")
    body = r.get_data(as_text=True)
    assert "<title>مزرعة الياسمين |" in body
    assert '<meta name="description"' in body
    assert '<meta property="og:title"' in body
    assert 'property="og:image" content="http://localhost/static/img/logo-512.png"' in body
    assert '<link rel="canonical" href="http://localhost/"' in body
    assert "/static/img/logo-256.png" in body
    assert "googleusercontent.com/aida/" not in body          # dead logo URL
    assert "&amp;display=block" in body                         # icon font
    assert "html:not(.icons-ready) .material-symbols-outlined" in body
    for placeholder in ("المملكة", "920000000", "٩٢٠٠٠٠٠٠٠", 'href="https://wa.me/"',
                        "05xxxxxxxx", "٢٠٢٥", "+١٥,٠٠٠", "+٦٠٠", "خلال ساعتين", "مجانية"):
        assert placeholder not in body, placeholder
    year = str(date.today().year).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    assert f"© {year} مزرعة الياسمين" in body


def test_contact_hidden_when_not_set(client, landing_settings):
    landing_settings()
    body = client.get("/").get_data(as_text=True)
    assert "wa.me" not in body
    assert 'href="tel:' not in body
    assert 'id="inquiry-form"' not in body
    assert "location_on" not in body
    assert "لطلب تسعيرة أو شحنة تجريبية تواصل مع مكتب التوريد" in body


def test_contact_from_settings(client, landing_settings):
    landing_settings(phone="0100 123 4567", whatsapp="201001234567",
                     addr="طريق مصر إسكندرية الصحراوي")
    body = client.get("/").get_data(as_text=True)
    assert "https://wa.me/201001234567?text=" in body
    assert 'href="tel:01001234567"' in body
    assert "0100 123 4567" in body
    assert "طريق مصر إسكندرية الصحراوي" in body
    assert 'id="inquiry-form"' in body and "function sendInquiry" in body
    assert '"https://wa.me/201001234567"' in body              # form target, JSON-escaped


def test_site_url_config(client, app, landing_settings):
    landing_settings()
    app.config["SITE_URL"] = "https://yasmin-farm.example"
    try:
        body = client.get("/").get_data(as_text=True)
        assert 'content="https://yasmin-farm.example/static/img/logo-512.png"' in body
    finally:
        app.config["SITE_URL"] = ""


def test_settings_save_numbers(admin_client, app, landing_settings):
    landing_settings()
    with app.app_context():
        p = CompanyProfile.current()
        data = {"_tab": "company", "name": p.name, "base_currency": p.base_currency,
                "tax_rate_pct": str(p.tax_rate_pct), "invoice_number_prefix_sale": "INV",
                "invoice_number_prefix_purchase": "PUR", "reminder_days_before_due": "3",
                "landing_phone": "01001234567", "landing_whatsapp": "+20 100 123 4567"}
    r = admin_client.post("/finance/settings?tab=company", data=data)
    assert r.status_code in (302, 303), r.get_data(as_text=True)[:400]
    with app.app_context():
        assert Setting.get(Setting.KEY_LANDING_PHONE) == "01001234567"
        assert Setting.get(Setting.KEY_LANDING_WHATSAPP) == "201001234567"
