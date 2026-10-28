"""Public landing page — the front door for anyone who does not have an account.

`app/templates/landing/index.html` IS the page: an exported RTL Arabic
design (Tailwind via CDN, Google fonts, Material Symbols icons). Not
`@login_required`: first-time visitors see what the farm offers before
being asked to sign in.

LANDING-1: it's rendered as a template (not served as a static file) so
the title / Open Graph links, the year and the sales phone / WhatsApp /
address come from the app instead of placeholders. When replacing the
design, keep the `{{ … }}` / `{% if … %}` bits in the new file.
"""
from datetime import date
from urllib.parse import quote

from flask import Blueprint, current_app, render_template

from app.models.finance import CompanyProfile, Setting

bp = Blueprint("landing", __name__)

_AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

# Opening line of every WhatsApp chat started from a button.
_WA_GREETING = "السلام عليكم، أرغب في الاستفسار عن توريد اللبن الخام من مزرعة الياسمين."


@bp.route("/")
def home():
    whatsapp = "".join(ch for ch in Setting.get(Setting.KEY_LANDING_WHATSAPP) if ch.isdigit())
    phone = Setting.get(Setting.KEY_LANDING_PHONE).strip()
    profile = CompanyProfile.current()
    wa_base = f"https://wa.me/{whatsapp}" if whatsapp else ""
    return render_template(
        "landing/index.html",
        # Absolute links only from config, never from the request's Host
        # header (a spoofed Host would end up in canonical / og tags and
        # could be cached for everyone). Unset → those tags are left out.
        site_url=current_app.config.get("SITE_URL") or "",
        phone=phone,
        phone_tel="".join(ch for ch in phone if ch.isdigit() or ch == "+"),
        whatsapp=whatsapp,
        wa_base=wa_base,
        wa_link=f"{wa_base}?text={quote(_WA_GREETING)}" if wa_base else "",
        address=(profile.address or "").strip() if profile else "",
        year=str(date.today().year).translate(_AR_DIGITS),
    )
