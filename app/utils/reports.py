"""Report export helpers.

- Excel via openpyxl (writes multi-column workbooks).
- PDF via server-side Playwright (Chromium headless). Downloads a real PDF
  file so the user doesn't need to use browser print → save-as-PDF.
"""
from io import BytesIO
from urllib.parse import urlsplit

from flask import Response, current_app, request
from openpyxl import Workbook

# Text starting with one of these is run as a formula by Excel /
# LibreOffice (e.g. an ear tag typed as "=HYPERLINK(...)").
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value):
    """Neutralise spreadsheet formula injection: text that would be read
    as a formula gets a leading apostrophe so it stays plain text.
    Numbers, dates and None pass through unchanged."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def excel_response(sheet_name: str, headers: list[str], rows: list[list], filename: str) -> Response:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:30]
    ws.sheet_view.rightToLeft = True
    ws.append([_safe_cell(h) for h in headers])
    for r in rows:
        ws.append([_safe_cell(v) for v in r])
    # Basic column width
    for i, _ in enumerate(headers, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 20

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def local_render_url(target_url: str) -> tuple[str, str]:
    """(url, cookie_domain): `target_url`'s path + query on the fixed
    PDF_RENDER_BASE_URL. Scheme and host of `target_url` are dropped."""
    parts = urlsplit(target_url)
    base = current_app.config.get("PDF_RENDER_BASE_URL", "http://127.0.0.1:5001").rstrip("/")
    url = base + (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return url, urlsplit(base).hostname or "127.0.0.1"


def pdf_from_current_page(target_url: str, filename: str) -> Response:
    """Render an authenticated in-app page to PDF via headless Chromium.

    The route calling this must pass a signed URL back to itself; the PDF
    renderer needs to authenticate via the same session cookie.

    Only the path + query of `target_url` are used. Callers build it with
    `url_for(..., _external=True)`, whose host comes from the request's
    Host header — a spoofed Host would otherwise make Chromium fetch an
    arbitrary URL (SSRF) and hand the result back as a PDF. The page is
    always loaded from the fixed PDF_RENDER_BASE_URL instead.
    """
    from playwright.sync_api import sync_playwright

    target_url, cookie_domain = local_render_url(target_url)

    # Copy the caller's session cookie so Playwright can log in
    session_cookie = request.cookies.get("session")
    remember_cookie = request.cookies.get("remember_token")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1200, "height": 900}, locale="ar-EG"
        )
        cookies = []
        if session_cookie:
            cookies.append({"name": "session", "value": session_cookie,
                            "domain": cookie_domain, "path": "/"})
        if remember_cookie:
            cookies.append({"name": "remember_token", "value": remember_cookie,
                            "domain": cookie_domain, "path": "/"})
        if cookies:
            ctx.add_cookies(cookies)

        page = ctx.new_page()
        # 15s was too tight: the FIRST export after a server restart has to pay
        # for a cold Chromium launch on top of the page's CDN assets (Bootstrap,
        # icons, fonts) all settling for "networkidle" — and it timed out, so the
        # user's first PDF of the day failed and the second worked. Pre-existing;
        # it affects the P&L and milk-cost exports the same way.
        page.goto(target_url, wait_until="networkidle", timeout=45_000)
        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
        )
        browser.close()

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
