# مزرعة الياسمين — نظام الإدارة المتكامل

Yasmin Farm Integrated Management System (Arabic RTL, Flask).

## Sprint 1 — Foundation & Herd

Delivered:
- Auth (login, session timeout, forgot/reset password, brute-force lockout)
- User management (admin CRUD, protected first admin)
- Herd management (add/edit/search cows, group transfers)
- Birth registration (auto calf allocation to groups)
- Death and animal sale flows (archive, not delete)
- Dashboard (herd totals, per-group counts, monthly counters)
- 5 seeded cattle groups + 1 admin user

## Setup

```bash
cd "farm"
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env to set SECRET_KEY

# initialize database
flask --app flask_app.py db init
flask --app flask_app.py db migrate -m "sprint 1 schema"
flask --app flask_app.py db upgrade

# seed groups + admin
python seed.py

# run
# kill any process on 5000 first (per CLAUDE.md guidance)
lsof -ti:5000 | xargs kill -9 2>/dev/null
python flask_app.py
```

Default admin (change immediately in prod):
- Email: `admin@yasmin-farm.com`
- Password: `Admin@12345`

## Structure

```
farm/
├── flask_app.py           # entry point
├── config.py              # env-driven config
├── seed.py                # groups + admin bootstrap
├── app/
│   ├── __init__.py        # app factory
│   ├── extensions.py      # db, login, migrate, csrf
│   ├── models/            # SQLAlchemy models
│   ├── blueprints/        # auth, users, herd, dashboard
│   ├── forms/             # WTForms
│   ├── templates/         # Jinja2 (RTL)
│   ├── static/css/app.css
│   └── utils/             # audit, decorators
├── migrations/            # Alembic (created by flask db init)
└── instance/farm.db       # SQLite (dev)
```
