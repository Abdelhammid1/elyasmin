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
    # LANDING: `/` is now a public marketing page, so login lands on /dashboard
    page.wait_for_url(re.compile(r"/dashboard$"), timeout=10_000)
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
    snap(page, "suppliers_new", "إضافة مورد", "نموذج مورد جديد — الاسم + التليفون + الرصيد الافتتاحي + أنواع المواد.")

    # TICKET-1: a supplier created with an opening balance must show that balance
    # as owed straight away, before any invoice exists.
    unique_supplier = "مورد اختبار " + str(int(time.time()) % 100000)
    page.fill('input[name="name"]', unique_supplier)
    page.fill('input[name="opening_balance"]', "5000")
    page.check('input[name="supplied_categories"] >> nth=0')
    page.click('input[type="submit"]')
    try:
        page.wait_for_selector(".alert-success", timeout=5_000)
    except PWTimeoutError:
        pass
    if "5000" in page.content():
        snap(
            page,
            "supplier_opening_balance",
            "مورد برصيد افتتاحي",
            "TICKET-1: الرصيد الافتتاحي (5000) بيظهر كرصيد مستحق للمورد من غير أي فاتورة.",
        )
    else:
        fail(page, "supplier_opening_balance", "مورد برصيد افتتاحي",
             "الرصيد الافتتاحي 5000 لم يظهر في صفحة المورد")

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

    # FEED-TANK: build a real recipe and run it, so the tank actually holds a
    # balance to withdraw from. A fresh seed has no ingredients at all, so the
    # raw material has to be created first or the recipe dropdown is empty.
    feed_ing = "ذرة اختبار " + str(int(time.time()) % 100000)
    page.goto(f"{BASE}/inventory/new")
    page.fill('input[name="name"]', feed_ing)
    page.select_option('select[name="category"]', "feed")
    page.select_option('select[name="unit"]', "kg")
    page.fill('input[name="initial_qty"]', "5000")
    page.fill('input[name="initial_price"]', "5")
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")

    page.goto(f"{BASE}/feed/recipes")
    first_recipe_edit = page.locator('a[href*="/recipes/"][href$="/edit"]').first
    if first_recipe_edit.count() > 0:
        first_recipe_edit.click()
        page.wait_for_selector("#addLine", timeout=10_000)
        if page.locator('select[name="line_ingredient_0"]').count() == 0:
            page.click("#addLine")
            page.wait_for_selector('select[name="line_ingredient_0"]', timeout=5_000)
        page.select_option('select[name="line_ingredient_0"]', label=feed_ing)
        page.fill('input[name="line_kg_0"]', "100")
        page.click('input[type="submit"]')
        page.wait_for_load_state("networkidle")

        page.goto(f"{BASE}/feed/runs/new")
        page.fill('input[name="batches_count"]', "2")
        page.click('input[type="submit"]')
        page.wait_for_load_state("networkidle")
        if "اتضافت للخزان" in page.content():
            snap(page, "feed_run_to_tank", "FEED-TANK: التشغيل بيروح للخزان",
                 "التشغيل بقى بيضيف رصيد في الخزان بدل ما يتحسب استهلاك في نفس اليوم.")
        else:
            fail(page, "feed_run_to_tank", "FEED-TANK: التشغيل بيروح للخزان",
                 "رسالة النجاح ماقالتش إن الكمية اتضافت للخزان")

    page.goto(f"{BASE}/feed/tanks")
    snap(page, "feed_tanks", "خزانات العلف",
         "رصيد كل مجموعة من الخلطة المخزّنة، متوسط تكلفة الكيلو، وقيمة الرصيد.")

    # TICKET-3: feeding replaced the bare withdrawal screen. The old steps kept
    # "passing" by falling through to an else-branch once the button changed, so
    # they were testing nothing — these drive the real flow instead.
    # scoped to the table: the same href also sits in the collapsed العلف nav
    # dropdown, which is not visible, so an unscoped .first waits forever
    feeding_link = page.locator('table a[href*="/feeding/new"]').first
    if feeding_link.count() > 0:
        feeding_link.click()
        page.wait_for_selector('input[name="feed_qty"]', timeout=10_000)
        snap(page, "feeding_form", "TICKET-3: تسجيل تغذية",
             "علف من خزان المجموعة + إضافات من المخزن العام، والتكلفة بتتحسب للاتنين.")

        # feed from the tank AND an addition from general stores, in one meal
        page.fill('input[name="feed_qty"]', "100")
        if page.locator('select[name="addition_ingredient_0"]').count() > 0:
            page.select_option('select[name="addition_ingredient_0"]', index=0)
            page.fill('input[name="addition_qty_0"]', "50")
        page.click('input[type="submit"]')
        page.wait_for_load_state("networkidle")
        body = page.content()
        if "تم تسجيل تغذية" in body and "إضافات" in body:
            snap(page, "feeding_recorded", "TICKET-3: الوجبة اتسجلت بتكلفتها",
                 "الرسالة بتفصل تكلفة العلف عن تكلفة الإضافات وبتجمعهم.")
        else:
            fail(page, "feeding_recorded", "TICKET-3: الوجبة اتسجلت بتكلفتها",
                 "مفيش رسالة نجاح فيها تفصيل العلف والإضافات")

        # over-drawing the tank must still be refused, with the balance named
        page.goto(f"{BASE}/feed/feeding/new")
        page.wait_for_selector('input[name="feed_qty"]', timeout=10_000)
        page.fill('input[name="feed_qty"]', "999999")
        page.click('input[type="submit"]')
        page.wait_for_load_state("networkidle")
        if "مش كفاية" in page.content():
            snap(page, "feeding_overdraw", "TICKET-3: منع السحب الزيادة",
                 "السحب أكبر من رصيد الخزان اتمنع، والرسالة بتقول المتاح كام.")
        else:
            fail(page, "feeding_overdraw", "TICKET-3: منع السحب الزيادة",
                 "السحب الزيادة عدّى من غير رسالة")
    else:
        fail(page, "feeding_form", "TICKET-3: تسجيل تغذية",
             "مفيش زرار تغذية في شاشة الخزانات")

    page.goto(f"{BASE}/feed/feeding")
    snap(page, "feedings_list", "TICKET-3: سجل التغذية اليومي",
         "كل مجموعة بوجباتها وتكلفة اليوم — علف + إضافات.")

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

    # Create a quality-priced customer — a fresh seed has none, and the milk
    # delivery form (and TICKET-3 below) needs one to render at all.
    unique_customer = "عميل تحليل " + str(int(time.time()) % 100000)
    page.fill('input[name="name"]', unique_customer)
    page.select_option('select[name="pricing_type"]', "quality")
    page.click('input[type="submit"]')
    try:
        page.wait_for_selector(".alert-success", timeout=5_000)
    except PWTimeoutError:
        pass
    snap(page, "customer_quality_created", "عميل بتسعير التحليل",
         "عميل تسعيره على أساس تحليل الجودة — لازم بروتين + بكتيريا عشان يتحسب سعره.")

    page.goto(f"{BASE}/customers/1")
    snap(page, "customer_detail", "تفاصيل عميل", "التوريدات + الدفعات + نموذج تسجيل دفعة.")

    page.goto(f"{BASE}/customers/settlement")
    snap(page, "settlement", "التسوية الأسبوعية", "تقرير أسبوعي بالكيلوات والقيمة والرصيد لكل عميل.")

    page.goto(f"{BASE}/milk/deliveries")
    snap(page, "milk_deliveries", "توريدات اللبن", "توريدات اليوم مع الفاقد.")

    page.goto(f"{BASE}/milk/deliveries/new")
    snap(page, "milk_delivery_new", "تسجيل توريد", "النموذج يدعم تسعير ثابت وتسعير بالجودة.")

    # TICKET-3: the original repro — a quality-priced customer, protein filled but
    # bacteria left empty, used to fail silently with no message at all.
    page.select_option('select[name="customer_id"]',
                       label=f"{unique_customer} (على أساس التحليل)")
    page.fill('input[name="qty_kg"]', "100")
    page.fill('input[name="protein_pct"]', "3.5%")   # the % must be typeable
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")
    body = page.content()
    # The bug was a blank re-render with zero feedback. Assert on the inline error
    # text itself — it only ever appears as a field error, never as static markup.
    if "مطلوب لأن تسعير العميل على أساس التحليل" in body and "3.5%" in body:
        snap(
            page,
            "milk_delivery_feedback",
            "TICKET-3: رسالة واضحة بدل الفشل الصامت",
            "النموذج بقى يقول للمستخدم إيه الناقص بالظبط، وبيحتفظ بـ 3.5% في الخانة "
            "(الـ number input القديم كان بيمسحها).",
        )
    else:
        fail(page, "milk_delivery_feedback", "TICKET-3: رسالة واضحة بدل الفشل الصامت",
             "رسالة الخطأ المضمّنة لم تظهر أو الخانة فقدت قيمة 3.5% — الفشل الصامت لسه موجود")

    # TICKET-4: record a delivery with no price, then price it from the list.
    # The client enters these in stages, sometimes days apart.
    page.goto(f"{BASE}/milk/deliveries/new")
    page.select_option('select[name="customer_id"]',
                       label=f"{unique_customer} (على أساس التحليل)")
    page.fill('input[name="qty_kg"]', "250")
    page.click('button[name="save_unpriced"]')
    # wait for the redirect to land before reading content — networkidle alone
    # can return mid-navigation
    page.wait_for_url(re.compile(r"/milk/deliveries"), timeout=10_000)
    page.wait_for_selector("table, .alert", timeout=10_000)
    if "بانتظار التسعير" in page.content():
        snap(page, "milk_delivery_unpriced", "TICKET-4: توريد بدون سعر",
             "التوريد اتسجل بحالة «بانتظار التسعير» — بره حساب العميل وبره الفواتير لحد ما يتسعّر.")
    else:
        fail(page, "milk_delivery_unpriced", "TICKET-4: توريد بدون سعر",
             "التوريد غير المسعّر لم يظهر بحالة بانتظار التسعير")

    # now price it — the edit route is the second half of the ticket.
    # Target the edit link inside an UNPRICED row (tr.table-warning); the list
    # may hold priced rows too, so .first over all links is not deterministic.
    unpriced_rows_before = page.locator("tr.table-warning").count()
    edit_link = page.locator('tr.table-warning a[href*="/edit"]').first
    if edit_link.count() > 0:
        edit_link.click()
        page.wait_for_selector('input[name="protein_pct"]', timeout=10_000)
        page.fill('input[name="protein_pct"]', "3.5")
        page.fill('input[name="bacteria_count"]', "100000")
        page.click('input[type="submit"]')
        page.wait_for_url(re.compile(r"/milk/deliveries"), timeout=10_000)
        page.wait_for_selector("table, .alert", timeout=10_000)
        unpriced_rows_after = page.locator("tr.table-warning").count()
        if unpriced_rows_after == unpriced_rows_before - 1:
            snap(page, "milk_delivery_priced_later", "TICKET-4: تسعير التوريد بعدين",
                 "نفس التوريد بعد ما اتسعّر من صفحة التعديل — دخل حساب العميل وطلع من قائمة الانتظار.")
        else:
            fail(page, "milk_delivery_priced_later", "TICKET-4: تسعير التوريد بعدين",
                 f"عدد التوريدات غير المسعّرة لم ينقص: {unpriced_rows_before} -> {unpriced_rows_after}")
    else:
        fail(page, "milk_delivery_priced_later", "TICKET-4: تسعير التوريد بعدين",
             "مفيش زرار تعديل في صف توريد غير مسعّر")

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

    # TREASURY: cash drawer + any number of bank accounts, with live balances.
    page.goto(f"{BASE}/accounts/new")
    page.fill('input[name="name"]', "الخزنة الرئيسية")
    page.select_option('select[name="account_type"]', "cash")
    page.fill('input[name="opening_balance"]', "10000")
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")

    page.goto(f"{BASE}/accounts/new")
    page.fill('input[name="name"]', "البنك الأهلي")
    page.select_option('select[name="account_type"]', "bank")
    page.fill('input[name="bank_name"]', "البنك الأهلي المصري")
    page.fill('input[name="opening_balance"]', "50000")
    page.click('input[type="submit"]')
    page.wait_for_url(re.compile(r"/accounts"), timeout=10_000)
    page.wait_for_selector("table, .alert", timeout=10_000)
    if "10000" in page.content() and "50000" in page.content():
        snap(page, "accounts_list", "الخزنة والحسابات",
             "TREASURY: خزنة نقدية + أي عدد حسابات بنكية، وكل واحد برصيده الحالي.")
    else:
        fail(page, "accounts_list", "الخزنة والحسابات", "أرصدة الحسابات لم تظهر")

    # transfer moves both sides
    page.goto(f"{BASE}/accounts/transfer")
    page.select_option('select[name="from_account_id"]', index=0)
    page.select_option('select[name="to_account_id"]', index=1)
    page.fill('input[name="amount"]', "1000")
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")
    if "تم تحويل" in page.content():
        snap(page, "accounts_transfer", "TREASURY: تحويل بين حسابين",
             "التحويل بيخصم من حساب ويضيف للتاني، والرصيدين بيتحدثوا مع بعض.")
    else:
        fail(page, "accounts_transfer", "TREASURY: تحويل بين حسابين",
             "التحويل لم يتم أو لم تظهر رسالة نجاح")

    page.goto(f"{BASE}/accounts/1/statement")
    snap(page, "account_statement", "كشف حساب",
         "كل الحركات بالترتيب مع رصيد جاري ينتهي عند الرصيد الحالي بالظبط.")

    page.goto(f"{BASE}/finance/settings")
    snap(page, "settings", "إعدادات النظام", "نسبة توزيع التكاليف 80/20 ومعادلة سعر التحليل — قابلة للتعديل.")

    # ASSISTANT: the floating helper is on every page, not just /help.
    page.goto(f"{BASE}/help/assistant/usage")
    snap(page, "assistant_usage", "استهلاك المساعد الذكي",
         "توكنز وتكلفة اليوم والشهر مع شريط الميزانية وقفل الأمان.")

    page.goto(f"{BASE}/herd/")
    page.wait_for_selector("#assistantOpen", timeout=10_000)
    page.click("#assistantOpen")
    page.wait_for_selector("#assistant-input", state="visible", timeout=10_000)
    if page.locator("#assistant-reset").is_visible():
        snap(page, "assistant_panel", "المساعد الذكي جوه الصفحة",
             "اللوحة بتفتح من أي صفحة في النظام، وفيها زرار «محادثة جديدة» عشان التكلفة ماتكبرش.")
    else:
        fail(page, "assistant_panel", "المساعد الذكي جوه الصفحة",
             "اللوحة اتفتحت من غير زرار محادثة جديدة")

    # ASSISTANT FIX: the history used to live in a plain JS variable, so every
    # navigation wiped it and the chat felt like it kept closing. It now lives in
    # sessionStorage. Seeded directly here because a real exchange needs an API key.
    uid = page.evaluate("document.body.dataset.userId")
    page.evaluate(
        """(uid) => sessionStorage.setItem('assistant_history_' + uid, JSON.stringify([
            {role: 'user', content: 'سؤال محفوظ قبل التنقل'},
            {role: 'assistant', content: 'إجابة محفوظة قبل التنقل'}
        ]))""",
        uid,
    )
    # navigate to a DIFFERENT page — this is the exact reported bug
    page.goto(f"{BASE}/inventory/")
    page.wait_for_selector("#assistantOpen", timeout=10_000)
    page.click("#assistantOpen")
    page.wait_for_selector("#assistant-input", state="visible", timeout=10_000)
    body = page.content()
    if "سؤال محفوظ قبل التنقل" in body and "إجابة محفوظة قبل التنقل" in body:
        snap(page, "assistant_history_kept", "المحادثة بتفضل بعد التنقل",
             "الهيستوري اتخزن في sessionStorage، فالمحادثة بتفضل زي ما هي لما تنتقل لصفحة تانية.")
    else:
        fail(page, "assistant_history_kept", "المحادثة بتفضل بعد التنقل",
             "المحادثة اتمسحت بعد الانتقال لصفحة تانية")

    # "محادثة جديدة" must clear the stored key too, not just the screen
    page.click("#assistant-reset")
    left = page.evaluate("(uid) => sessionStorage.getItem('assistant_history_' + uid)", uid)
    still_shown = "سؤال محفوظ قبل التنقل" in page.content()
    if left is None and not still_shown:
        snap(page, "assistant_history_reset", "«محادثة جديدة» بتمسح فعلاً",
             "الزرار بيمسح المحادثة من الشاشة ومن الـ sessionStorage مع بعض.")
    else:
        fail(page, "assistant_history_reset", "«محادثة جديدة» بتمسح فعلاً",
             f"الهيستوري لسه موجود (storage={left!r}, على الشاشة={still_shown})")

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
