"""Report export helpers.

- Excel via openpyxl (writes multi-column workbooks).
- PDF via server-side Playwright (Chromium headless). Downloads a real PDF
  file so the user doesn't need to use browser print → save-as-PDF.
"""
from io import BytesIO

from flask import Response, request
from openpyxl import Workbook


def excel_response(sheet_name: str, headers: list[str], rows: list[list], filename: str) -> Response:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:30]
    ws.sheet_view.rightToLeft = True
    ws.append(headers)
    for r in rows:
        ws.append(r)
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


def pdf_from_current_page(target_url: str, filename: str) -> Response:
    """Render an authenticated in-app page to PDF via headless Chromium.

    The route calling this must pass a signed URL back to itself; the PDF
    renderer needs to authenticate via the same session cookie.
    """
    from playwright.sync_api import sync_playwright

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
                            "domain": "127.0.0.1", "path": "/"})
        if remember_cookie:
            cookies.append({"name": "remember_token", "value": remember_cookie,
                            "domain": "127.0.0.1", "path": "/"})
        if cookies:
            ctx.add_cookies(cookies)

        page = ctx.new_page()
        page.goto(target_url, wait_until="networkidle", timeout=15_000)
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
