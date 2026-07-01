# 🐄 دليل النشر — مزرعة الياسمين

> نظام الإدارة المتكامل لمزرعة الياسمين
> واجهة عربية RTL بالكامل · Flask 3 · SQLite (تطوير) / PostgreSQL (إنتاج)

---

## 📋 المتطلبات الأساسية

- **Python 3.10 أو أحدث** (يُفضّل 3.12)
- **Git** لسحب الكود
- **متصفح حديث** (Chrome / Edge / Safari)
- **قاعدة بيانات**: SQLite (يجي مع Python) للتطوير، PostgreSQL 14+ للإنتاج
- **نظام تشغيل**: Linux / macOS / Windows (WSL2 مفضّل على Windows)

---

## 🚀 التنصيب السريع (تطوير محلي)

### 1. سحب الكود

```bash
git clone https://github.com/Abdelhammid1/elyasmin.git
cd elyasmin
```

### 2. إنشاء البيئة الافتراضية وتنصيب المكتبات

```bash
python3.12 -m venv .venv
source .venv/bin/activate     # على Linux/macOS
# .venv\Scripts\activate      # على Windows
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. إعداد ملف البيئة `.env`

```bash
cp .env.example .env
```

افتح `.env` وحدّث القيم:

```ini
FLASK_APP=flask_app.py
FLASK_ENV=development
SECRET_KEY=غيّر-هذا-المفتاح-إلى-قيمة-عشوائية-طويلة
SESSION_LIFETIME_MINUTES=30
MAX_LOGIN_ATTEMPTS=5
LOCKOUT_MINUTES=60
```

> ⚠️ **مهم جداً**: لا تشغل النظام على السيرفر الحقيقي بدون تغيير `SECRET_KEY` لقيمة عشوائية طويلة (32 حرف أو أكتر).
> يمكنك توليدها بالأمر:
> ```bash
> python -c "import secrets; print(secrets.token_urlsafe(48))"
> ```

### 4. إنشاء قاعدة البيانات وتنفيذ الـ migrations

```bash
flask --app flask_app.py db upgrade
```

هذا الأمر سينشئ ملف `instance/farm.db` (SQLite) بكل الجداول جاهزة.

### 5. تجهيز البيانات الأساسية (Seed)

```bash
python seed.py
```

هذا السكربت هيعمل الآتي:

- ✅ إنشاء الـ **5 مجموعات الأساسية** للقطيع:
  - مجموعة الحليب
  - مجموعة الجفاف
  - مجموعة انتظار الولادة
  - مجموعة الرضاعة
  - مجموعة التسمين

- ✅ إنشاء **حساب المدير الأساسي (Admin)**:
  - **الإيميل**: `admin@yasmin-farm.com`
  - **كلمة المرور المؤقتة**: `Admin@12345`

- ✅ ضبط **الإعدادات الافتراضية**:
  - نسبة توزيع التكاليف: 80% حليب / 20% باقي المجموعات
  - سعر أساس اللبن بالتحليل: 6 جنيه/كيلو
  - زيادة السعر لكل +1% بروتين: 0.5 جنيه
  - خصم لكل +100 ألف بكتيريا: 0.25 جنيه

> 🔒 **خطوة أمان مهمة**: بعد أول تسجيل دخول، غيّر كلمة المرور الافتراضية فوراً:
> 1. سجّل الدخول بالحساب الافتراضي
> 2. من قائمة "المستخدمون" اضغط على تعديل حسابك
> 3. أدخل كلمة مرور جديدة قوية (8 أحرف على الأقل)

### 6. تشغيل السيرفر

```bash
python flask_app.py
```

افتح المتصفح على:

```
http://127.0.0.1:5001
```

سجّل الدخول بالحساب اللي طلع من `seed.py`.

---

## 🔧 تخصيص حساب الـ Admin عند الـ Seed

لو عايز تسمي المدير باسم مختلف أو تحدد كلمة مرور مختلفة من البداية:

```bash
SEED_ADMIN_EMAIL="mymail@example.com" \
SEED_ADMIN_PASSWORD="MyStr0ng-Pass!" \
SEED_ADMIN_NAME="أحمد المدير" \
python seed.py
```

---

## 💾 النسخ الاحتياطي التلقائي

النظام يجي مع سكربت نسخ احتياطي يومي:

```bash
scripts/backup.sh
```

يعمل نسخة من `instance/farm.db` في مجلد `instance/backups/` مع طابع تاريخ ووقت، ويحذف النسخ الأقدم من 30 يوم أوتوماتيك.

### جدولته يومياً عبر cron

```bash
crontab -e
```

أضف السطر ده (يشتغل كل يوم الساعة 2 صباحاً):

```
0 2 * * *  /المسار/الكامل/إلى/elyasmin/scripts/backup.sh >> /المسار/الكامل/backup.log 2>&1
```

---

## 🏭 النشر على الإنتاج (Production)

### 1. استخدم PostgreSQL بدل SQLite

في ملف `.env`:

```ini
DATABASE_URL=postgresql://user:password@localhost:5432/yasmin_farm
```

ثم:

```bash
pip install psycopg2-binary
flask --app flask_app.py db upgrade
python seed.py
```

### 2. تشغيل بـ Gunicorn (مش `python flask_app.py`)

```bash
pip install gunicorn
gunicorn --bind 0.0.0.0:5001 --workers 3 --timeout 60 flask_app:app
```

### 3. Nginx كـ Reverse Proxy مع HTTPS

مثال ملف `/etc/nginx/sites-available/yasmin`:

```nginx
server {
    server_name yasmin-farm.example.com;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 443 ssl;
    ssl_certificate     /etc/letsencrypt/live/yasmin-farm.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yasmin-farm.example.com/privkey.pem;
}

server {
    listen 80;
    server_name yasmin-farm.example.com;
    return 301 https://$server_name$request_uri;
}
```

### 4. شهادة HTTPS مجانية (Let's Encrypt)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yasmin-farm.example.com
```

### 5. تشغيل كخدمة دائمة (systemd)

ملف `/etc/systemd/system/yasmin.service`:

```ini
[Unit]
Description=Yasmin Farm
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/elyasmin
Environment="PATH=/opt/elyasmin/.venv/bin"
ExecStart=/opt/elyasmin/.venv/bin/gunicorn --bind 127.0.0.1:5001 --workers 3 flask_app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

ثم:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now yasmin
sudo systemctl status yasmin
```

### 6. لتفعيل الإعدادات في الإنتاج، عدّل `.env`:

```ini
FLASK_ENV=production
```

هذا يفعّل `SESSION_COOKIE_SECURE=True` (المتصفح يبعت الكوكيز فقط عبر HTTPS).

---

## 🧪 اختبار النظام (E2E)

قبل التسليم للعميل، شغّل الاختبار الشامل:

```bash
# السيرفر لازم يشتغل في terminal تاني
python flask_app.py &

# ثم:
python tests/e2e.py
```

هذا هيعمل الآتي:

- ✅ 63 لقطة شاشة لكل صفحة في النظام (Desktop + Mobile)
- ✅ اختبار كل عمليات الإدخال (بقر، عملاء، فواتير، تشغيل علف، إلخ)
- ✅ اختبار الحسابات المالية بأرقام حقيقية
- ✅ توليد تقرير HTML قابل للاستعراض

بعد الاختبار:

```bash
open tests/report.html
```

لو عايز التقرير كملف PDF واحد:

```bash
python tests/render_pdf.py
open tests/yasmin_farm_e2e_report.pdf
```

---

## ⚙️ إعدادات النظام (بعد التنصيب)

من داخل التطبيق، اذهب إلى **المالية → الإعدادات** لتعديل:

| الإعداد | القيمة الافتراضية | الوصف |
|---------|-------------------|--------|
| نسبة تحميل التكاليف على الحليب | 80% | جزء التكاليف غير المباشرة اللي بيتحسب على مجموعة الحليب |
| نسبة باقي المجموعات | 20% | الباقي على مجموعات الجفاف والرضاعة والتسمين |
| سعر أساس اللبن بالتحليل | 6.000 جنيه/kg | نقطة بداية معادلة تسعير جهينة/بيتي |
| زيادة لكل +1% بروتين فوق 3.0 | 0.500 جنيه/kg | مكافأة الجودة |
| خصم لكل +100 ألف بكتيريا فوق 100 ألف | 0.250 جنيه/kg | عقوبة التلوث |

كل هذه القيم يمكن تعديلها **بدون تغيير كود** — الحفظ يسري فوراً على كل الحسابات القادمة.

---

## 🛠️ استكشاف الأخطاء

### "Address already in use" على المنفذ 5001

على macOS، الـ AirPlay Receiver بيستخدم منفذ 5000. النظام مضبوط على 5001 افتراضياً. لتغييره:

```bash
PORT=8080 python flask_app.py
```

أو أغلق العملية اللي شاغلة المنفذ:

```bash
lsof -ti:5001 | xargs kill -9
```

### "unable to open database file"

تأكد من:
1. مجلد `instance/` موجود ولديك صلاحية الكتابة عليه
2. `SQLALCHEMY_DATABASE_URI` في `config.py` يشير لمسار صالح

### الرسائل تظهر بحروف غريبة (Encoding)

تأكد من:
- ملف قاعدة البيانات UTF-8 (SQLite يستخدم UTF-8 افتراضياً)
- لو PostgreSQL: `CREATE DATABASE yasmin_farm ENCODING 'UTF8';`

### نسيت كلمة مرور الـ Admin

فيه طريقتين:

**الأولى**: من صفحة "نسيت كلمة المرور" في تسجيل الدخول (يبعت رابط للإيميل).

**الثانية** (لو الإيميل مش شغّال بعد): تعديل مباشر من الـ Python shell:

```bash
python
>>> from app import create_app
>>> from app.extensions import db
>>> from app.models import User
>>> app = create_app()
>>> with app.app_context():
...     u = User.query.filter_by(email="admin@yasmin-farm.com").first()
...     u.set_password("كلمة-مرور-جديدة-قوية")
...     db.session.commit()
```

### `pip install` بيفشل على openpyxl

نصّبها بشكل منفصل:

```bash
pip install openpyxl==3.1.5
```

---

## 📚 هيكل المشروع

```
elyasmin/
├── flask_app.py          # نقطة الدخول (تشغيل السيرفر)
├── config.py             # الإعدادات من متغيرات البيئة
├── seed.py               # تحضير البيانات الأساسية
├── requirements.txt      # مكتبات Python
├── .env.example          # مثال للـ environment variables
├── scripts/
│   └── backup.sh         # النسخ الاحتياطي اليومي
├── app/
│   ├── __init__.py       # App factory + تسجيل الـ blueprints
│   ├── extensions.py     # db, login, migrate, csrf
│   ├── models/           # جداول قاعدة البيانات (SQLAlchemy)
│   ├── forms/            # نماذج الإدخال (WTForms)
│   ├── blueprints/       # منطق كل صفحة
│   ├── templates/        # واجهات Jinja2 عربية RTL
│   ├── static/css/       # التصميم
│   └── utils/            # helpers (audit, reports)
├── migrations/           # سجل تعديلات قاعدة البيانات (Alembic)
├── instance/             # قاعدة البيانات المحلية + النسخ الاحتياطية
└── tests/
    ├── e2e.py            # 63 اختبار Playwright شامل
    ├── render_pdf.py     # توليد تقرير PDF من HTML
    ├── report.html       # التقرير التفاعلي
    └── screenshots/      # لقطات الشاشة
```

---

## 🔑 حسابات افتراضية للاختبار

بعد `python seed.py`:

| الدور | الإيميل | كلمة المرور |
|-------|---------|-------------|
| مدير النظام (Admin) | `admin@yasmin-farm.com` | `Admin@12345` |

> 🚨 **غيّر كلمة المرور دي قبل النشر على الإنتاج!**

---

## 📞 دعم فني

لو ظهرت مشكلة أثناء التنصيب:

1. راجع قسم "استكشاف الأخطاء" فوق
2. راجع `tests/report.html` لتشوف إن كل الوظائف بتشتغل
3. راجع `git log` لمعرفة آخر التغييرات

---

## 🎯 التحقق من نجاح التنصيب

بعد ما تنفّذ كل الخطوات:

- [ ] السيرفر يشتغل على `http://127.0.0.1:5001`
- [ ] تقدر تسجّل دخول بحساب Admin
- [ ] الداشبورد بيعرض 5 مجموعات (0 رأس لكل واحدة)
- [ ] القوائم كلها بتفتح بدون أخطاء
- [ ] الواجهة عربية بالكامل (RTL)
- [ ] الأرقام بالإنجليزي (0-9)
- [ ] التصميم يشتغل صح على الموبايل (استخدم DevTools → Toggle device)
- [ ] `scripts/backup.sh` يعمل نسخة احتياطية بنجاح

لو كل النقاط دي تشيك ✅، النظام جاهز للاستخدام.

---

**آخر تحديث**: يوليو 2026
