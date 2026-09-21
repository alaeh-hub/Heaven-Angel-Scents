# Heaven & Angel Scents

A Flask-based operations platform for a perfume brand with a central warehouse, multiple retail branches, resale partners, and internal staff users. This is the current system used to manage inventory, sales, production, stock requests, partner leads, audit history, and reporting in one place.

## Current project status

This repository is an active operational application rather than a starter template or demo. The system currently includes:

- HQ administration for catalog, product management, production, suppliers, packages, partners, customers, users, and reports
- Branch-side workflows for inventory, sales, refills, requests, receiving, and credit purchases
- Public partner portal for browsing packages and submitting inquiries without a login
- Role-based access control for Admin and Branch accounts
- Real-time updates with Socket.IO for shared operational views
- AI-powered internal assistant for scoped business questions over live data
- Audit trail and movement logging for operational accountability
- Receipt generation and QR-code verification for branch sales

---

## What the system does

Heaven & Angel Scents runs as a single operating layer for the business across a few distinct roles:

| Role | Primary access | Main responsibilities |
| --- | --- | --- |
| Admin / HQ | `/admin/*` | Product catalog, production, stock dispatch, partner management, user management, financial/reporting views, audit log review |
| Branch staff | `/branch/*` | Inventory checks, branch sales, stock requests, receiving shipments, customer records, discrepancies, branch reports |
| Partner / reseller | `/partner-portal/<slug>/*` | Browse package offerings and submit inquiries through a public-facing portal |
| Signed-in users | `/ai/*` | Ask business questions using a read-only AI assistant scoped to the user’s data and role |

The system is built around a single source of truth in MySQL, with append-only movement logs and admin audit records so internal actions remain traceable.

---

## Product and business model

The app is designed around a retail and wholesale perfume operation:

- Products are tracked by SKU, with base code + unit size forming the unique identifier.
- Inventory is maintained per branch, not just globally.
- HQ manages production and warehouse stock.
- Branches place stock requests to HQ and receive fulfilled deliveries.
- Sales and refills are recorded per branch with payment method tracking.
- Packages can be bundled and sold to partners or resellers.
- Partner inquiries are kept as a separate lead and opportunity workflow.
- Financial reporting combines branch sales, partner orders, COGS, and operational stock movement history.

---

## High-level architecture

The app is a Flask application with several route blueprints, a shared database layer, and realtime notification support.

- `app.py` builds the application and registers the main blueprints
- `config.py` holds environment-based configuration for security, database, mail, AI, and limits
- `routes/admin.py` contains HQ operations screens and logic
- `routes/branch.py` contains branch management views and workflows
- `routes/portal.py` hosts the public partner portal
- `routes/ai.py` powers the read-only AI assistant
- `routes/auth.py` handles login, logout, and password-change flows
- `schema.sql` defines the operational database schema and business records
- `db.py` centralizes MySQL query execution and transaction safety
- `sockets.py` manages Socket.IO room membership and scoped updates
- `utils.py` contains shared validators, constants, formatting helpers, and business logic helpers

The platform uses:

- Flask 3 for application logic and routing
- MySQL as the system database
- Socket.IO for live page refreshes and notifications
- Flask-Talisman for transport hardening and security headers
- Flask-WTF for CSRF protection
- Flask-Limiter for request throttling
- reportlab and openpyxl for printable/exportable reports
- Gemini API for the internal AI assistant

---

## Core workflows

### 1. Authentication and access control

Users log in through a role-aware flow. Admin and Branch accounts are validated against the database on every authenticated request, not only at login. This keeps account deactivation or password resets effective immediately.

Key behaviors:

- Admin and Branch login are handled in the same auth system with role checks
- Inactive accounts are blocked
- Password change enforcement is supported for fresh or reset accounts
- Session state is kept lightweight but revalidated against live DB state on each request

### 2. Warehouse and branch inventory flow

The system supports the movement of stock between HQ and branches:

1. A branch creates a stock request for one or more SKUs.
2. HQ reviews and dispatches the request.
3. HQ stock is reduced and a movement log is recorded.
4. The branch later receives the shipment and confirms the received/damaged quantities.
5. The request is marked fulfilled and the final receipt is preserved for traceability.

This process is designed to be auditable and transactional, so stock counts and movement history remain consistent.

### 3. Production tracking

HQ can log production batches for finished goods. This updates inventory for the warehouse and records the production event in movement history. Production is tied to formula-driven cost logic for operational planning and profit analysis.

### 4. Sales and refills

Branches can record both regular sales and refills:

- Sale: customer takes the product and stock decreases
- Refill: customer uses their own container; stock is not reduced
- Payment methods include cash and credit entries
- Each sale is recorded in a way that supports branch reporting and receipt verification

### 5. Partner portal

The public partner portal gives distributors and resellers access to package offerings without requiring an authenticated account. It uses a deployment-specific slug to protect the portal route instead of app login.

The flow includes:

- browsing partner package listings
- viewing package details
- submitting inquiries with validated contact information
- server-side price/order recalculation
- stored inquiry history and status progression
- real-time HQ notification of new partner leads

### 6. AI assistant

The AI assistant is intentionally read-only and scoped to the signed-in user’s role and branch. It builds a server-side snapshot of live business data and sends it to Gemini with strict rules:

- use only returned data
- never invent inventory or prices
- never perform writes
- answer only in plain text
- suggest the relevant operational page when an action is requested

This keeps the AI practical without allowing unsafe operation of the business system.

### 7. Audit and movement tracking

The system keeps two separate but complementary records:

- `admin_actions`: who changed what in configuration or operational metadata
- `stock_movement_logs`: what happened to stock quantities over time

Together they make it possible to answer both operational and administrative questions responsibly.

---

## Security and operational safeguards

The application is designed with a production-minded security posture:

- HTTPS enforcement and hardened headers in production via Flask-Talisman
- CSRF protection on state-changing forms
- Session cookies set with security-friendly defaults
- Role-based access control enforced through shared route decorators
- Rate limiting on high-risk endpoints such as login and AI use
- Public portal isolation behind a deployment slug rather than a signed-in session
- Atomic multi-table writes through a transaction layer to prevent partial updates
- Best-effort audit logging and email notifications that do not block the main business action

---

## Configuration and environment

The app reads configuration from environment variables and a local `.env` file when present. Core settings include:

- `APP_ENV` = `development` or `production`
- `SECRET_KEY` = application secret, required in non-debug environments
- `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`
- `PARTNER_PORTAL_SLUG` = stable public portal slug for partner access
- `GEMINI_API_KEY` and `GEMINI_MODEL` for the AI assistant
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`, `PARTNER_INQUIRY_NOTIFY_EMAIL`
- `NUM_PROXIES` for reverse-proxy deployments
- `RATELIMIT_ENABLED` and rate-limit-related environment settings

The app is intentionally strict in production mode and will warn or fail early if required configuration is missing.

---

## Local development workflow

1. Create and activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Ensure MySQL is running and reachable.
4. Load the schema in `schema.sql` into your database.
5. Set required environment variables in a `.env` file or shell config.
6. Start the app with:

```bash
python app.py
```

For production-style WSGI serving, the repo also includes `wsgi.py` and the required Gunicorn + gevent stack in `requirements.txt`.

---

## Project structure

```text
.
├── app.py
├── wsgi.py
├── config.py
├── db.py
├── decorators.py
├── utils.py
├── audit.py
├── login_activity.py
├── mailer.py
├── receipts.py
├── reports.py
├── schema.sql
├── seed.py
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── routes/
│   ├── admin.py
│   ├── auth.py
│   ├── branch.py
│   ├── portal.py
│   ├── ai.py
│   ├── ai_tools.py
│   └── scan.py
├── static/
│   ├── css/
│   ├── js/
│   ├── img/
│   └── uploads/
├── templates/
│   ├── admin/
│   ├── branch/
│   ├── public/
│   ├── ai/
│   ├── scan/
│   ├── base.html
│   ├── login.html
│   └── errors/
├── tests/
├── fonts/
└── vid/
```

---

## Business notes and current characteristics

The current implementation is optimized for a real operating environment, not for a purely academic prototype. In practice, that means:

- most workflows are server-rendered and database-backed
- stock and revenue reporting are tied to live transactional data
- branch, admin, and partner access are intentionally segmented
- audit visibility is part of the expected user experience
- realtime updates are built into the app rather than bolted on later
- the system expects a reliable MySQL environment and proper deployment configuration

This is a serious internal operations platform with clear business workflows, accounting logic, and operational visibility built into the core application design.

---

## Summary

Heaven & Angel Scents is a full inventory and branch operations system for a fragrance brand. It combines warehouse management, branch retail operations, partner lead handling, reporting, operational audit logs, and AI-assisted business support into a single Flask application.

It is currently structured as a real-world internal business system with strong operational guardrails, role isolation, and traceable inventory and finance workflows.


Build and reconcile Jan–Jul 2026 Sales/COGS/Profit workbook for Heaven and Angel Scents

- Extracted per-month SALE/REFILL tables from raw SALES WITH DETAILS,
  split by branch (Lipa City vs. Balayan) with subtotals + grand totals
- Built COGS calculations from scratch (materials → per-bottle/per-ml
  rates → Business 1 & 2 cost), cross-checked against each month's own
  Monthly Report tab
- Discovered source files use two disagreeing rate methodologies:
  split tabs (₱95/₱6/₱0.82-per-ml + container costs, used in Jan) vs.
  combined "MONTHLY REPORT" tabs (₱85/₱67-flat/₱10/₱1.00-per-ml, no
  container line, used Feb–Jul); switched Feb–Jul to match the
  combined tab per instruction, left Jan on the original rates
- Found and fixed: February missing Address column (rebuilt from raw
  file), missed online Shopee orders (+₱1,569.04), March's profit
  formula bug (referenced prior month's sales), free/promotional
  bottles inconsistently excluded from COGS (now included everywhere
  at full cost — Jan +₱570, Feb +₱85)
- Merged branch-split file + COGS file into one MASTER workbook:
  added cross-file SUMMARY (financials + branch totals) with 3 charts,
  a PRICING CHANGES tab documenting the Feb rate change, and a DATA
  QUALITY NOTES tab compiling every inconsistency found
- Applied black-and-gold theme across all 25 sheets (Cambria/Calibri
  fonts, gold headers/totals, restyled charts) to match the site's
  branding
- Investigated the Supplier tab's ₱90 rate vs. ₱85/₱95 in use;
  built a materials-cost reconstruction (~₱83.71/bottle) as a
  standalone PDF, including a January-specific check (materials
  prices unchanged from July, scent mix not the cause of the gap)
- Identified 85ml-refill bottle-count errors in the combined tab for
  Feb (22 vs. actual 20), Mar (16 vs. actual 31 — the big one), and
  Apr (26 vs. actual 27); corrections proposed but not yet applied
- The workbook should not be treated as final until these refill counts
  are corrected and the affected COGS and profit totals are revalidated

Files: Heaven_and_Angel_Scents_MASTER_Jan-Jul2026.xlsx,
85ml_Bottle_Cost_Estimate.pdf