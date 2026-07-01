# مزرعة الياسمين — نظام الإدارة المتكامل

Yasmin Farm Integrated Management System — a complete Arabic RTL Flask web app covering the full farm operation: herd, feed, inventory, purchases, suppliers, customers/milk, labor, finance, and reports.

**All 6 sprints delivered · 102/102 story points · verified end-to-end with 63 Playwright tests.**

## Highlights

- **Herd**: cow CRUD with unique ear tags, group transfers with history, births (auto-allocate calves to nursing/fattening), deaths, sales
- **Inventory**: raw materials + medicine, last-purchase-price costing, low-stock alerts, movement log
- **Suppliers & purchases**: multi-line invoices (cash/credit), supplier balance, payments, statement + Excel export
- **Feed**: versioned recipes per group, daily feed run with auto-deduct and cost per kg
- **Customers & milk**: fixed pricing OR quality-based formula (protein + bacteria), weekly settlement, daily production + waste
- **Finance**: **milk cost per kg with configurable 80/20 direct/indirect split**, P&L with PDF + Excel export, expenses
- **Labor**: workers (per-batch/daily), daily attendance grid, wages auto-recorded as expense
- **Arabic help center** with 11 topics + `?` icon in top nav

## Non-functional

- 100% Arabic RTL · Latin digits (0-9) · responsive down to 375px
- Session timeout 30 min · 5-attempt lockout · password hashing
- Archive-only deletes (no hard delete anywhere)
- All money-affecting settings editable without code (`/finance/settings`)
- Daily automated backup script (`scripts/backup.sh`)

## Quick start

See **[DEPLOY.md](DEPLOY.md)** for the full Arabic deployment guide.

Short version:
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then set SECRET_KEY
flask --app flask_app.py db upgrade
python seed.py        # creates 5 groups + admin@yasmin-farm.com / Admin@12345
python flask_app.py   # → http://127.0.0.1:5001
```

## Tests

```bash
python flask_app.py &                 # start server
python tests/e2e.py                    # 63 Playwright checks + screenshots
open tests/report.html                 # HTML audit
open tests/yasmin_farm_e2e_report.pdf  # single-file PDF audit
```

## Structure

```
farm/
├── flask_app.py, config.py, seed.py, requirements.txt
├── scripts/backup.sh                     # daily SQLite snapshot + 30-day retention
├── app/
│   ├── models/    (auth herd inventory suppliers feed sales finance labor audit)
│   ├── forms/     (WTForms — validated Arabic messages)
│   ├── blueprints/ auth users herd dashboard suppliers inventory purchases
│                   feed medicine customers milk finance labor help
│   ├── templates/ Jinja2 RTL Arabic
│   ├── static/css/app.css                # RTL + print @media for PDF export
│   └── utils/     audit, decorators, reports (Excel helper)
├── migrations/versions/                   # 4 Alembic revisions
└── tests/
    ├── e2e.py                             # 63-step Playwright suite
    ├── render_pdf.py                      # HTML report → PDF renderer
    ├── report.html                         # browsable audit
    └── yasmin_farm_e2e_report.pdf          # single-file audit
```

## License

Internal use — Yasmin Farm project.
