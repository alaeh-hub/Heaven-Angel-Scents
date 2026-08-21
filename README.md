# Heaven & Angel Scents Inventory System

Heaven & Angel Scents is a Flask and MySQL web application for managing a perfume business with an HQ warehouse and multiple retail branches. The system covers product catalog management, HQ production, branch stock requests, shipment receiving, branch inventory, point-of-sale sales recording, sales reporting, and a read-only AI assistant.

The current application is operational up to the full sales lifecycle: HQ produces stock, dispatches approved requests, branches receive shipments, branches record sales, inventory is deducted automatically, and revenue reports are calculated from the actual selling price captured at the time of each sale.

## Current System State

### Admin / HQ

- Dashboard with active SKU count, branch count, pending request count, low-stock count, low-stock alerts, recent stock requests, and recent ledger activity.
- Product catalog with SKU, item name, variant, base HQ price, active status, and soft-discontinue support.
- HQ production logging that adds stock to the HQ warehouse and writes a `PRODUCTION` entry to the stock movement ledger.
- Branch management for adding retail branches.
- User account management for creating Admin and Branch users and activating or deactivating accounts.
- Branch Stock page for viewing total branch inventory and setting per-branch price overrides.
- Stock request queue where HQ can dispatch or reject branch requests.
- Movement ledger showing production, dispatch, receipt, sale, adjustment, and damage movements.
- Reports page powered by the bundled Chart.js file in `static/js/chart.umd.min.js`.
- API endpoint at `/admin/api/reports-data` for report data.

### Branch

- Branch dashboard scoped to the signed-in branch only.
- Inventory page showing stock quantity, reorder level, HQ price, branch override price, and effective selling price.
- Stock request form for asking HQ to dispatch inventory.
- Shipment receiving flow that confirms received quantity, records damaged quantity, updates branch stock, and writes receipt or damage ledger entries.
- Point-of-sale sale recording that validates available stock, records the sale, deducts inventory, and writes a sale movement.
- Sales history with branch-level units sold and revenue totals.

### AI Assistant

The app includes a read-only AI assistant under `/ai`.

- Uses the Gemini API through `requests`.
- Requires `GEMINI_API_KEY` in the environment.
- Uses `GEMINI_MODEL`, defaulting to `gemini-2.5-flash`.
- If no API key is configured, the assistant remains visible but tells the user it is not configured.
- Rebuilds a fresh, role-scoped data snapshot on each chat request.
- Admin users receive summary visibility across branches.
- Branch users receive only their own branch inventory, requests, today sales, and recent sales.
- The AI does not execute SQL, does not receive database credentials, has no tool-calling access, and cannot make changes such as recording sales, dispatching stock, changing prices, or creating accounts.

## Sales and Revenue Calculation

Sales are stored in the `sales` table with:

- `branch_id`
- `sku`
- `qty_sold`
- `unit_price`
- `sold_at`

When a branch records a sale:

1. The app reads the branch inventory row and product row.
2. The effective selling price is calculated as `COALESCE(branch_inventory.branch_price, products.price)`.
3. The sale is inserted with that effective price as `sales.unit_price`.
4. Branch inventory is deducted by the sold quantity.
5. A `SALE` entry is written to `stock_movement_logs`.

Revenue is calculated as:

```sql
SUM(qty_sold * unit_price)
```

Because `unit_price` is captured on the sale row at the moment of sale, historical revenue remains accurate even if HQ prices or branch override prices change later.

## Reporting

The Admin Reports page currently shows:

- Total units sold across all branches.
- Total revenue across all branches.
- Units sold by product variant.
- Units sold by branch.
- Revenue by branch.
- Current stock by branch.
- Average branch pricing difference versus HQ price.
- Stock movement trend for the last 14 days.

The reports page loads data from `/admin/api/reports-data` and renders charts with the local bundled Chart.js build.

## Tech Stack

- Backend: Flask 3, Flask blueprints, Flask-WTF CSRF protection
- Database: MySQL or MariaDB
- Database driver: `mysql-connector-python`
- Authentication: Flask sessions with Werkzeug password hashing
- Frontend: Server-rendered Jinja templates, vanilla CSS, vanilla JavaScript
- Charts: Bundled Chart.js
- Production server: Waitress
- AI integration: Gemini API over HTTPS through `requests`
- CI: GitHub Actions with Python checks and a MySQL schema smoke test

## Setup

### 1. Start MySQL

Start MySQL from XAMPP or another local MySQL/MariaDB installation.

The default configuration expects:

```text
Host: localhost
Port: 3306
User: root
Password: empty
Database: heaven_and_angel_scents
```

### 2. Create the Database

Import `schema.sql` through phpMyAdmin, or run:

```bash
mysql -u root -p < schema.sql
```

The schema creates the core tables and starter branches:

- HQ Main Warehouse
- Manila Branch
- Cebu Branch

### 3. Configure Environment Variables

Copy `env.example` to `.env` and update values as needed:

```bash
copy env.example .env
```

Important variables:

```text
SECRET_KEY=change-this-to-a-random-string
APP_ENV=development
FLASK_DEBUG=0

MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DB=heaven_and_angel_scents

GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
```

Leave `GEMINI_API_KEY` blank if the AI assistant should remain disabled.

### 4. Install Python Dependencies

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 5. Create Starter Accounts

Run:

```bash
python seed.py
```

Starter accounts:

| Username | Password | Role | Branch |
| --- | --- | --- | --- |
| admin | admin123 | Admin | HQ |
| manila | branch123 | Branch | Manila Branch |
| cebu | branch123 | Branch | Cebu Branch |

Change these credentials before using the system outside local development.

### 6. Run Locally

```bash
python app.py
```

Open:

```text
http://localhost:5000
```

## Production Run

Use a strong `SECRET_KEY`, set `APP_ENV=production`, and run behind HTTPS:

```powershell
$env:APP_ENV = "production"
$env:SECRET_KEY = "replace-with-a-long-random-secret"
waitress-serve --listen=*:8000 wsgi:app
```

Production mode disables debug mode, enables secure session cookies, and refuses to start without `SECRET_KEY`.

## Continuous Integration

The repository includes a GitHub Actions workflow at `.github/workflows/ci.yml`.

The CI pipeline runs on pushes to `main` or `master` and on pull requests. It installs Python dependencies, compiles the Python files, validates the Flask app factory, starts a MySQL service, loads `schema.sql`, and smoke-tests a database query.

## Database Notes

`schema.sql` is the complete database setup script for the current system. It includes the core tables, starter branches, branch price overrides, password-change enforcement, audit metadata columns, and reporting indexes.

Existing branch inventory rows with `branch_price = NULL` automatically fall back to the HQ product price.

## Security Model

- Passwords are hashed with Werkzeug and are not stored in plain text.
- State-changing routes use POST and are protected by Flask-WTF CSRF.
- Admin and Branch areas are protected by role-based decorators.
- Branch routes use `session["branch_id"]` to scope data access.
- SQL is parameterized through shared query and execute helpers.
- The AI assistant receives only a prepared JSON snapshot and cannot write to the database.

## Project Structure

```text
.
|-- .github/
|   `-- workflows/
|       `-- ci.yml
|-- app.py
|-- wsgi.py
|-- config.py
|-- db.py
|-- decorators.py
|-- schema.sql
|-- seed.py
|-- requirements.txt
|-- env.example
|-- routes/
|   |-- auth.py
|   |-- admin.py
|   |-- branch.py
|   `-- ai.py
|-- static/
|   |-- css/
|   |   `-- style.css
|   `-- js/
|       |-- main.js
|       `-- chart.umd.min.js
`-- templates/
    |-- base.html
    |-- login.html
    |-- change_password.html
    |-- _macros.html
    |-- errors/
    |-- admin/
    |-- branch/
    `-- ai/
```

## Known Operational Scope

The system currently supports the main inventory and sales workflow from production through reporting. It does not yet include purchase orders to suppliers, barcode scanning, refunds, customer records, payment processing, multi-currency accounting, or automated deployment scripts.
