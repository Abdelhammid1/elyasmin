"""End-to-end Playwright test with screenshots + HTML report.

Run with the Flask server already running on :5001:
    python tests/e2e.py

Outputs:
    tests/screenshots/*.png
    tests/report.html
"""
import os
import re
import sys
import time
import traceback
from datetime import datetime
from html import escape
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeoutError, sync_playwright

BASE = os.getenv("FARM_BASE", "http://127.0.0.1:5001")
ADMIN_EMAIL = "admin@yasmin-farm.com"
ADMIN_PASS = "Admin@12345"

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "screenshots"
SHOTS.mkdir(exist_ok=True)
REPORT = ROOT / "report.html"

steps: list[dict] = []


def snap(page: Page, slug: str, title: str, desc: str = "", status: str = "OK") -> None:
    """Take a full-page screenshot and log the step."""
    fname = f"{len(steps):02d}_{slug}.png"
    path = SHOTS / fname
    page.screenshot(path=str(path), full_page=True)
    steps.append(
        {
            "slug": slug,
            "title": title,
            "desc": desc,
            "img": f"screenshots/{fname}",
            "status": status,
            "url": page.url,
        }
    )
    print(f"  [{status}] {slug} — {title}")


def fail(page: Page, slug: str, title: str, err: str) -> None:
    """Capture a screenshot in failure state and log."""
    fname = f"{len(steps):02d}_{slug}_FAIL.png"
    path = SHOTS / fname
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:  # noqa: BLE001
        path.write_bytes(b"")
    steps.append(
        {
            "slug": slug,
            "title": title,
            "desc": err[:1000],
            "img": f"screenshots/{fname}",
            "status": "FAIL",
            "url": page.url if page else "-",
        }
    )
    print(f"  [FAIL] {slug} — {title}: {err[:200]}")


def login(page: Page) -> None:
    page.goto(f"{BASE}/auth/login")
    page.fill('input[name="email"]', ADMIN_EMAIL)
    page.fill('input[name="password"]', ADMIN_PASS)
    page.click('input[type="submit"]')
    page.wait_for_url(re.compile(r"/(?!auth/login)"), timeout=10_000)


def assert_arabic(page: Page, needle: str) -> None:
    content = page.content()
    if needle not in content:
        raise AssertionError(f"expected Arabic text '{needle}' on page")


# ---------- desktop flows ----------


def run_desktop(page: Page) -> None:
    # ---- Auth ----
    page.goto(f"{BASE}/auth/login")
    assert_arabic(page, "مزرعة الياسمين")
    snap(page, "login_empty", "صفحة تسجيل الدخول", "الصفحة الأولى قبل الدخول — واجهة RTL كاملة.")

    # Invalid login
    page.fill('input[name="email"]', ADMIN_EMAIL)
    page.fill('input[name="password"]', "wrong")
    page.click('input[type="submit"]')
    page.wait_for_selector(".alert-danger", timeout=5_000)
    assert_arabic(page, "بيانات الدخول غير صحيحة")
    snap(page, "login_invalid", "دخول غير صالح", "رسالة خطأ بالعربية عند إدخال بيانات خطأ.")

    # Valid login
    page.goto(f"{BASE}/auth/login")
    page.fill('input[name="email"]', ADMIN_EMAIL)
    page.fill('input[name="password"]', ADMIN_PASS)
    page.click('input[type="submit"]')
    page.wait_for_url(re.compile(r"/$"), timeout=10_000)
    assert_arabic(page, "لوحة التحكم")
    snap(page, "dashboard", "لوحة التحكم", "KPIs، توزيع المجموعات، آخر الأنشطة، مواد تحت الحد الأدنى، موردون.")

    # Forgot password page
    page.goto(f"{BASE}/auth/forgot-password")
    assert_arabic(page, "استرجاع كلمة المرور")
    snap(page, "forgot_password", "استرجاع كلمة المرور", "صفحة طلب رابط إعادة تعيين كلمة المرور.")

    # ---- Herd ----
    page.goto(f"{BASE}/herd/")
    assert_arabic(page, "القطيع")
    snap(page, "herd_list", "قائمة القطيع", "قائمة الأبقار مع بحث وفلتر مجموعة/حالة.")

    page.goto(f"{BASE}/herd/new")
    snap(page, "herd_new", "إضافة بقرة", "نموذج إدخال بقرة جديدة برقم أذن فريد.")

    # Actually create a new cow to enrich the demo (unique tag per run)
    unique_tag = "E2E-" + str(int(time.time()) % 100000)
    page.fill('input[name="ear_tag"]', unique_tag)
    page.fill('input[name="name"]', "بقرة اختبار")
    page.fill('input[name="date_of_birth"]', "2024-01-01")
    page.select_option('select[name="gender"]', "female")
    # first group in the choices (whatever it is)
    page.select_option('select[name="group_id"]', index=0)
    page.fill('textarea[name="notes"]', "تم إنشاؤها بواسطة اختبار Playwright")
    page.click('input[type="submit"]')
    try:
        page.wait_for_selector(".alert-success", timeout=5_000)
    except PWTimeoutError:
        pass  # cow may already exist from a previous run — screenshot anyway
    snap(page, "herd_created", "بعد إنشاء بقرة", "شاشة تفاصيل البقرة الجديدة مع سجل النقلات.")

    # Move that cow
    detail_url = page.url
    match = re.search(r"/herd/(\d+)", detail_url)
    if match:
        new_cow_id = match.group(1)
        page.goto(f"{BASE}/herd/{new_cow_id}/move")
        snap(page, "herd_move_form", "نقل بقرة", "نموذج نقل البقرة إلى مجموعة مختلفة.")
        page.select_option('select[name="to_group_id"]', index=1)
        page.fill('input[name="reason"]', "نقل تجريبي")
        page.click('input[type="submit"]')
        try:
            page.wait_for_selector(".alert-success", timeout=5_000)
        except PWTimeoutError:
            pass
        snap(page, "herd_after_move", "بعد النقل", "المجموعة الجديدة وسجل النقلات محدث.")

    page.goto(f"{BASE}/herd/groups")
    snap(page, "herd_groups", "مجموعات القطيع", "الخمس مجموعات مع عدد الرؤوس النشطة في كل مجموعة.")

    page.goto(f"{BASE}/herd/births")
    snap(page, "herd_births", "المواليد", "سجل المواليد المسجّلة.")

    page.goto(f"{BASE}/herd/births/new")
    snap(page, "herd_birth_form", "تسجيل ولادة", "نموذج تسجيل ولادة يدعم عدة مواليد وتحديد جنس كل مولود.")

    page.goto(f"{BASE}/herd/sales")
    snap(page, "herd_sales", "مبيعات الحيوانات", "سجل بيع الحيوانات (مصدر إيراد).")

    # First cow detail (with medicine history)
    page.goto(f"{BASE}/herd/1")
    snap(page, "cow_detail", "تفاصيل البقرة", "شاشة البقرة مع سجل النقلات وسجل الأدوية.")

    page.goto(f"{BASE}/herd/1/sell")
    snap(page, "herd_sell_form", "نموذج بيع", "نموذج بيع بقرة يسجّل كإيراد ويخرج البقرة من القطيع.")

    page.goto(f"{BASE}/herd/1/death")
    snap(page, "herd_death_form", "نموذج نفوق", "نموذج تسجيل نفوق — بيانات لا تُحذف، تحفظ في السجل التاريخي.")

    # ---- Users (admin) ----
    page.goto(f"{BASE}/users/")
    assert_arabic(page, "المستخدمون")
    snap(page, "users_list", "المستخدمون", "قائمة المستخدمين — مدير النظام الأول محمي من الحذف.")

    page.goto(f"{BASE}/users/new")
    snap(page, "users_new", "إضافة مستخدم", "نموذج إضافة مستخدم — بيانات + دور + كلمة مرور مؤقتة.")

    # ---- Inventory ----
    page.goto(f"{BASE}/inventory/")
    assert_arabic(page, "المخزون")
    snap(page, "inventory_list", "قائمة المخزون", "المواد الخام والأدوية — المواد تحت الحد الأدنى بلون أحمر.")

    page.goto(f"{BASE}/inventory/new")
    snap(page, "inventory_new", "إضافة مادة", "نموذج إضافة مادة جديدة — علف أو دواء.")

    page.goto(f"{BASE}/inventory/1")
    snap(page, "inventory_detail", "تفاصيل مادة", "شاشة المادة مع سجل الحركة الكامل وتعديل جرد يدوي.")

    page.goto(f"{BASE}/inventory/movements")
    snap(page, "inventory_movements", "سجل حركات المخزون", "كل عمليات الدخول والخروج على كل المواد.")

    # ---- Suppliers ----
    page.goto(f"{BASE}/suppliers/")
    assert_arabic(page, "الموردون")
    snap(page, "suppliers_list", "قائمة الموردين", "الموردون مع إجمالي الرصيد المستحق للمزرعة.")

    page.goto(f"{BASE}/suppliers/new")
    snap(page, "suppliers_new", "إضافة مورد", "نموذج مورد جديد — الاسم + التليفون + أنواع المواد.")

    page.goto(f"{BASE}/suppliers/1")
    snap(
        page,
        "supplier_detail",
        "تفاصيل مورد",
        "الفواتير والدفعات والرصيد + نموذج تسجيل دفعة على اليمين مع حماية من الدفع الزيادة.",
    )

    # ---- Purchases ----
    page.goto(f"{BASE}/purchases/")
    snap(page, "purchases_list", "فواتير الشراء", "قائمة الفواتير — نقدي/آجل + المتبقي.")

    page.goto(f"{BASE}/purchases/new")
    snap(page, "purchases_new", "فاتورة شراء جديدة", "نموذج فاتورة بعدة بنود — يحسب الإجمالي أوتوماتيك.")

    page.goto(f"{BASE}/purchases/1")
    snap(page, "purchase_view", "عرض فاتورة", "تفاصيل الفاتورة والبنود والإجمالي والمتبقي.")

    # ---- Feed ----
    page.goto(f"{BASE}/feed/recipes")
    snap(
        page,
        "feed_recipes",
        "وصفات العلف",
        "وصفة كل مجموعة مع تكلفة الخلطة وتكلفة الكيلو المحسوبة أوتوماتيك.",
    )

    page.goto(f"{BASE}/feed/recipes/1/edit")
    snap(
        page,
        "feed_recipe_edit",
        "تعديل وصفة",
        "محرر الوصفة مع حساب التكلفة الحية أثناء الكتابة.",
    )

    page.goto(f"{BASE}/feed/runs")
    snap(page, "feed_runs_list", "سجل تشغيلات العلف", "كل التشغيلات مع وزن الخلطات والتكلفة.")

    page.goto(f"{BASE}/feed/runs/new")
    snap(
        page,
        "feed_run_form",
        "تشغيل علف جديد",
        "شاشة التشغيل اليومي — الأكثر استخداماً — مع أزرار زيادة/نقصان عدد الخلطات.",
    )

    page.goto(f"{BASE}/feed/runs/1")
    snap(
        page,
        "feed_run_view",
        "تفاصيل تشغيل",
        "الوزن الإجمالي، التكلفة الكلية، وتكلفة الكيلو، مع لقطة أسعار وقت التشغيل.",
    )

    # ---- Medicine ----
    page.goto(f"{BASE}/medicine/")
    snap(page, "medicine_list", "صرف الأدوية", "سجل صرف الأدوية للبقر أو للمجموعات.")

    page.goto(f"{BASE}/medicine/new")
    snap(page, "medicine_new", "صرف دواء جديد", "نموذج صرف دواء — بقرة معينة أو مجموعة كاملة.")

    # ---- Sprint 4: Customers + Milk ----
    page.goto(f"{BASE}/customers/")
    snap(page, "customers_list", "العملاء", "قائمة العملاء مع أرصدتهم وطريقة تسعير كل واحد.")

    page.goto(f"{BASE}/customers/new")
    snap(page, "customers_new", "إضافة عميل", "نموذج عميل — تسعير ثابت أو بالتحليل.")

    page.goto(f"{BASE}/customers/1")
    snap(page, "customer_detail", "تفاصيل عميل", "التوريدات + الدفعات + نموذج تسجيل دفعة.")

    page.goto(f"{BASE}/customers/settlement")
    snap(page, "settlement", "التسوية الأسبوعية", "تقرير أسبوعي بالكيلوات والقيمة والرصيد لكل عميل.")

    page.goto(f"{BASE}/milk/deliveries")
    snap(page, "milk_deliveries", "توريدات اللبن", "توريدات اليوم مع الفاقد.")

    page.goto(f"{BASE}/milk/deliveries/new")
    snap(page, "milk_delivery_new", "تسجيل توريد", "النموذج يدعم تسعير ثابت وتسعير بالجودة.")

    page.goto(f"{BASE}/milk/production")
    snap(page, "milk_production", "الإنتاج والفاقد", "تسجيل الإنتاج اليومي وحساب الفاقد الشهري.")

    # ---- Sprint 5: Finance ----
    page.goto(f"{BASE}/finance/milk-cost")
    snap(page, "milk_cost", "تكلفة كيلو اللبن", "الحساب الرئيسي — تكلفة العلف + 80% غير مباشرة ÷ الكيلوات.")

    page.goto(f"{BASE}/finance/pnl")
    snap(page, "pnl", "الأرباح والخسائر", "الإيرادات والمصروفات وصافي الربح — قابل للتصدير PDF و Excel.")

    page.goto(f"{BASE}/finance/expenses")
    snap(page, "expenses", "المصروفات", "قائمة المصروفات مع فلتر تاريخ وتصدير Excel.")

    page.goto(f"{BASE}/finance/expenses/new")
    snap(page, "expense_new", "إضافة مصروف", "تسجيل مصروف عام يدوي.")

    page.goto(f"{BASE}/finance/settings")
    snap(page, "settings", "إعدادات النظام", "نسبة توزيع التكاليف 80/20 ومعادلة سعر التحليل — قابلة للتعديل.")

    # ---- Sprint 6: Labor + Help ----
    page.goto(f"{BASE}/labor/")
    snap(page, "labor_list", "العمالة", "قائمة العمال مع رصيد كل عامل ومستحقات الشهر.")

    page.goto(f"{BASE}/labor/new")
    snap(page, "labor_new", "إضافة عامل", "نموذج عامل — بالحلبة أو يومي.")

    page.goto(f"{BASE}/labor/1")
    snap(page, "worker_detail", "تفاصيل عامل", "الحضور الشهري + الدفعات + نموذج دفع.")

    page.goto(f"{BASE}/labor/attendance")
    snap(page, "attendance", "حضور اليوم", "شبكة تسجيل حضور كل العمال في يوم واحد.")

    page.goto(f"{BASE}/suppliers/report")
    snap(page, "suppliers_report", "تقرير الموردين", "فواتير الفترة + كشف حساب — طباعة PDF + Excel.")

    page.goto(f"{BASE}/help/")
    snap(page, "help", "مركز المساعدة", "دليل مختصر بالعربي لكل مواضيع النظام.")

    page.goto(f"{BASE}/help/cost")
    snap(page, "help_cost", "مساعدة — التكلفة", "شرح خطوة بخطوة لتكلفة كيلو اللبن.")

    # ---- Error page ----
    page.goto(f"{BASE}/does-not-exist")
    snap(page, "error_404", "صفحة 404", "رسالة صفحة غير موجودة بالعربية.")


# ---------- mobile flow (375px per NFR) ----------


def run_mobile(context) -> None:
    """Same login on a 375px viewport, then screenshot the mobile-critical screens."""
    page = context.new_page()
    login(page)

    page.goto(f"{BASE}/")
    snap(page, "mobile_dashboard", "الداشبورد على الموبايل (375px)", "التصميم متجاوب.")

    page.goto(f"{BASE}/feed/runs/new")
    snap(page, "mobile_feed_run", "تشغيل العلف على الموبايل", "الشاشة الأكثر استخداماً في الحقل.")

    page.goto(f"{BASE}/inventory/")
    snap(page, "mobile_inventory", "المخزون على الموبايل", "جدول متجاوب مع تنبيه المواد الناقصة.")

    page.goto(f"{BASE}/herd/")
    snap(page, "mobile_herd", "القطيع على الموبايل", "قائمة الأبقار على شاشة صغيرة.")

    page.goto(f"{BASE}/milk/deliveries/new")
    snap(page, "mobile_milk_delivery", "توريد لبن على الموبايل", "شاشة يومية تُستخدم في المزرعة.")

    page.goto(f"{BASE}/labor/attendance")
    snap(page, "mobile_attendance", "حضور العمال على الموبايل", "شبكة الحضور اليومي على شاشة صغيرة.")

    page.goto(f"{BASE}/finance/milk-cost")
    snap(page, "mobile_milk_cost", "تكلفة الكيلو على الموبايل", "التقرير المالي الأهم على الموبايل.")

    page.close()


# ---------- HTML report ----------


def write_report(desktop_count: int, mobile_count: int, elapsed: float) -> None:
    ok = sum(1 for s in steps if s["status"] == "OK")
    fails = len(steps) - ok
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    cards = []
    for s in steps:
        color = "ok" if s["status"] == "OK" else "fail"
        cards.append(
            f"""
    <article class="card {color}">
      <header>
        <span class="pill">{s['status']}</span>
        <h3>{escape(s['title'])}</h3>
      </header>
      <p class="desc">{escape(s['desc'])}</p>
      <div class="url" dir="ltr">{escape(s['url'])}</div>
      <a class="thumb" href="{escape(s['img'])}" target="_blank">
        <img loading="lazy" src="{escape(s['img'])}" alt="{escape(s['title'])}">
      </a>
    </article>
"""
        )

    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <title>تقرير اختبار مزرعة الياسمين — {ts}</title>
  <style>
    :root {{
      --primary: #0f2c4a;
      --ok: #10b981;
      --fail: #ef4444;
      --bg: #f4f6fa;
      --border: #e5e9f0;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "Tajawal", "Segoe UI", sans-serif;
      background: var(--bg);
      margin: 0;
      color: #1f2937;
    }}
    header.page-head {{
      background: var(--primary);
      color: #fff;
      padding: 2rem 1.5rem;
    }}
    header.page-head h1 {{ margin: 0 0 .5rem; font-size: 1.75rem; }}
    header.page-head .meta {{ opacity: .85; font-size: .95rem; }}
    .stats {{
      display: flex;
      gap: 1rem;
      padding: 1.5rem;
      flex-wrap: wrap;
      max-width: 1400px;
      margin: 0 auto;
    }}
    .stat {{
      background: #fff;
      border-radius: 12px;
      padding: 1rem 1.5rem;
      border: 1px solid var(--border);
      flex: 1;
      min-width: 180px;
    }}
    .stat .n {{ font-size: 2rem; font-weight: 700; }}
    .stat.ok .n {{ color: var(--ok); }}
    .stat.fail .n {{ color: var(--fail); }}
    .stat.primary .n {{ color: var(--primary); }}
    main {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 1.5rem;
      padding: 0 1.5rem 3rem;
      max-width: 1400px;
      margin: 0 auto;
    }}
    .card {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,.04);
      display: flex;
      flex-direction: column;
    }}
    .card header {{
      padding: 1rem 1.25rem .5rem;
      display: flex;
      align-items: center;
      gap: .75rem;
    }}
    .card h3 {{ margin: 0; font-size: 1.05rem; }}
    .card .desc {{
      padding: 0 1.25rem;
      color: #6b7280;
      font-size: .9rem;
      min-height: 3rem;
      margin: 0 0 .5rem;
    }}
    .card .url {{
      padding: 0 1.25rem;
      font-family: monospace;
      font-size: .75rem;
      color: #9ca3af;
      margin-bottom: .5rem;
      overflow-wrap: anywhere;
    }}
    .thumb {{
      display: block;
      background: #f9fafb;
      border-top: 1px solid var(--border);
    }}
    .thumb img {{
      width: 100%;
      height: 240px;
      object-fit: cover;
      object-position: top;
      display: block;
    }}
    .pill {{
      padding: .25rem .75rem;
      border-radius: 999px;
      font-size: .75rem;
      font-weight: 700;
      color: #fff;
    }}
    .card.ok .pill {{ background: var(--ok); }}
    .card.fail .pill {{ background: var(--fail); }}
    .card.fail {{ border-color: var(--fail); }}
    footer {{
      text-align: center;
      padding: 2rem;
      color: #6b7280;
      font-size: .85rem;
    }}
  </style>
</head>
<body>
  <header class="page-head">
    <h1>🐄 تقرير اختبار مزرعة الياسمين — Playwright E2E</h1>
    <div class="meta">
      وقت التنفيذ: {ts} · مدة الاختبار: {elapsed:.1f} ثانية
    </div>
  </header>

  <section class="stats">
    <div class="stat primary"><div class="n">{len(steps)}</div><div>إجمالي اللقطات</div></div>
    <div class="stat ok"><div class="n">{ok}</div><div>ناجحة</div></div>
    <div class="stat fail"><div class="n">{fails}</div><div>فاشلة</div></div>
    <div class="stat primary"><div class="n">{desktop_count}</div><div>لقطات سطح المكتب</div></div>
    <div class="stat primary"><div class="n">{mobile_count}</div><div>لقطات الموبايل (375px)</div></div>
  </section>

  <main>
{"".join(cards)}
  </main>

  <footer>
    كل اللقطات full-page — اضغط على أي صورة لفتحها كاملة.
  </footer>
</body>
</html>
"""
    REPORT.write_text(html, encoding="utf-8")
    print(f"\nReport written: {REPORT}")
    print(f"Open with: open '{REPORT}'")


def main() -> int:
    start = time.time()
    print(f"E2E starting against {BASE}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # Desktop context
        desktop_ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ar-EG")
        desktop_page = desktop_ctx.new_page()

        desktop_start = len(steps)
        try:
            run_desktop(desktop_page)
        except AssertionError as e:
            fail(desktop_page, "assertion", "AssertionError", str(e))
        except PWTimeoutError as e:
            fail(desktop_page, "pw_timeout", "Playwright timeout", str(e))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            fail(desktop_page, "unexpected", "Unexpected error", f"{type(e).__name__}: {e}")

        desktop_count = len(steps) - desktop_start
        desktop_ctx.close()

        # Mobile context (375x812 = iPhone-ish per NFR "شاشة 375px")
        mobile_ctx = browser.new_context(
            viewport={"width": 375, "height": 812}, locale="ar-EG", is_mobile=True
        )
        mobile_start = len(steps)
        try:
            run_mobile(mobile_ctx)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            fail(None, "mobile_unexpected", "Mobile flow error", f"{type(e).__name__}: {e}")
        mobile_count = len(steps) - mobile_start
        mobile_ctx.close()

        browser.close()

    elapsed = time.time() - start
    write_report(desktop_count, mobile_count, elapsed)
    return 0 if all(s["status"] == "OK" for s in steps) else 1


if __name__ == "__main__":
    sys.exit(main())
