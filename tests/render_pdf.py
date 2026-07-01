"""Render tests/report.html to a single PDF with all screenshots embedded."""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
REPORT_HTML = ROOT / "report.html"
OUT_PDF = ROOT / "yasmin_farm_e2e_report.pdf"


def main() -> None:
    if not REPORT_HTML.exists():
        raise SystemExit(f"missing: {REPORT_HTML} — run tests/e2e.py first")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.goto(f"file://{REPORT_HTML.as_posix()}")
        # give lazy-loaded images time to appear
        page.wait_for_load_state("networkidle")
        # force any lazy images to load
        page.evaluate(
            "Array.from(document.querySelectorAll('img')).forEach(i => { i.loading='eager'; })"
        )
        page.evaluate(
            "window.scrollTo(0, document.body.scrollHeight)"
        )
        page.wait_for_timeout(1200)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)

        page.pdf(
            path=str(OUT_PDF),
            format="A4",
            print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
            prefer_css_page_size=False,
        )
        browser.close()

    print(f"PDF written: {OUT_PDF} ({OUT_PDF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
