# Heaven & Angel Scents — Inventory & Distribution System

A Flask + MySQL web app for managing a perfume brand's HQ warehouse and
retail branches: production, requisitions, receiving, point-of-sale, and
a full audit ledger — with a Chart.js reporting dashboard for HQ.

## Stack
- **Backend:** Flask 3 (blueprints, session auth, Flask-WTF CSRF protection)
- **Database:** MySQL / MariaDB (built for XAMPP's default MySQL service)
- **Frontend:** Server-rendered Jinja templates, vanilla CSS, vanilla JS, Chart.js (CDN)

## 1. Start MySQL in XAMPP
Open the XAMPP Control Panel and start **MySQL** (and **Apache** if you'll
use phpMyAdmin). You don't need Apache to run this app — Flask runs its own
dev server on port 5000.

## 2. Create the database
Open **phpMyAdmin** (`http://localhost/phpmyadmin`) → *Import* → choose
`schema.sql` → *Go*.

Or from a terminal:
```bash
mysql -u root -p < schema.sql
```
(XAMPP's default root password is empty — just press Enter.)

This creates the `heaven_and_angel_scents` database with every table,
seeds three branches (HQ + Manila + Cebu) and a starter product catalog.

## 3. Set up the Python environment
```bash
cd heaven_angel_scents
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

If your XAMPP MySQL uses a different host/port/user/password, copy
`.env.example` to `.env` and adjust the values. `config.py` loads this file
automatically; do not commit the real `.env` file.

## 4. Create login accounts
```bash
python seed.py
```
This creates:
| Username | Password    | Role   | Branch          |
|----------|-------------|--------|------------------|
| admin    | admin123    | Admin  | — (HQ) —         |
| manila   | branch123   | Branch | Manila Branch    |
| cebu     | branch123   | Branch | Cebu Branch      |

**Change these passwords after first login** — this is a starter seed for
local development, not production credentials.

## 5. Run the app locally
```bash
python app.py
```
Visit **http://localhost:5000** and sign in.

## 6. Run in production
Install the dependencies, set a strong secret, and run behind HTTPS:

```powershell
$env:APP_ENV = "production"
$env:SECRET_KEY = "replace-with-a-long-random-secret"
waitress-serve --listen=*:8000 wsgi:app
```

`APP_ENV=production` disables debug mode, enables secure HTTP-only session
cookies, and makes the application refuse to start without `SECRET_KEY`.
Terminate HTTPS at a reverse proxy such as IIS or nginx before forwarding to
Waitress.

## What's included

**Admin (HQ):**
- Dashboard with live KPIs, low-stock alerts, and recent activity
- Product catalog with soft-delete (discontinue instead of hard delete)
- Production logging with optional batch codes (FIFO-friendly)
- Branch management and staff account creation
- Stock requisition queue — dispatch or reject branch requests
- Full stock movement ledger (every addition/deduction, filterable by branch)
- Reports page with Chart.js: sales by variant, sales by branch, stock
  distribution, and a 14-day movement trend

**Branch:**
- Dashboard scoped to that branch only
- Inventory view with a low-stock "fill" indicator
- Request stock from HQ
- Receive shipments — confirm actual vs. dispatched quantity, report
  damaged units (shortfalls are auto-flagged in the ledger)
- Record a sale (point-of-sale) — deducts stock and logs revenue
- Sales history with running totals

## Security notes
- Passwords are hashed with Werkzeug's `generate_password_hash` (never stored in plain text)
- All state-changing routes are POST-only and CSRF-protected (Flask-WTF)
- `@admin_required` / `@branch_required` decorators enforce role-based access
  on every route — a Branch session can never reach `/admin/*`
- Branch routes always scope queries to `session['branch_id']`, so one
  branch can never see or modify another branch's data
- All SQL uses parameterized queries — no string-built SQL anywhere

## Project structure
```
heaven_angel_scents/
├── app.py                 # app factory, error handlers, template filters
├── wsgi.py                 # production WSGI entry point
├── config.py               # DB credentials & app config
├── db.py                    # MySQL connection + query/execute helpers
├── decorators.py            # login_required / admin_required / branch_required
├── routes/
│   ├── __init__.py
│   ├── auth.py              # login / logout blueprint
│   ├── admin.py             # HQ blueprint
│   └── branch.py            # Branch blueprint
├── schema.sql                # full DDL + seed data
├── seed.py                    # creates starter login accounts
├── requirements.txt
├── static/
│   ├── css/style.css        # design system (tokens, layout, components)
│   └── js/main.js
└── templates/
    ├── base.html, login.html, _macros.html
    ├── admin/  (dashboard, products, production, branches, users, requests, movement_logs, reports)
    └── branch/ (dashboard, inventory, request_stock, receive_stock, record_sale, sales_history)
```
