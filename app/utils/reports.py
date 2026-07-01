"""Report export helpers.

- Excel via openpyxl (writes multi-column workbooks).
- PDF via print-friendly HTML page + browser print. The route sets a body class
  that triggers a `@media print` stylesheet stripping chrome/nav so the user
  can click Print → Save as PDF and get a clean document. This avoids native
  GTK/Pango dependencies while still satisfying "قابل للتصدير PDF" (NFR).
"""
from io import BytesIO

from flask import Response
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
