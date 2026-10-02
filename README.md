# Heaven & Angel Scents

A Flask-based operations platform for a perfume brand with a central warehouse, multiple retail branches, resale partners, and internal staff users. This is the system actually used day-to-day to manage inventory, production, branch sales, stock requests, partner leads, receipts, and reporting in one place — not a demo or starter template.

## Current project status

This repository is an active operational application. The system currently includes:

- HQ administration for catalog, product management, production, suppliers, packages, partners, customers, users, and reports
- Branch-side workflows for inventory, sales, refills, requests, receiving, and credit purchases
- Public partner portal for browsing packages and submitting inquiries without a login
- Role-based access control for Admin and Branch accounts
- Real-time updates with Socket.IO for shared operational views
- AI-powered internal assistant for scoped business questions over live data
- Audit trail and movement logging for operational accountability
- Receipt generation and QR-code scan verification for branch sales
- Bulk sales import/export via Excel, with the same stock and validation rules as manual entry

---

## What the system does

Heaven & Angel Scents runs as a single operating layer for the business across a few distinct roles:

| Role | Primary access | Main responsibilities |
| --- | --- | --- |
| Admin / HQ | `/admin/*` | Product catalog, production, stock dispatch, partner management, user management, financial/reporting views, audit log review |
| Branch staff | `/branch/*` | Inventory checks, branch sales, stock requests, receiving shipments, customer records, discrepancies, branch reports |
| Partner / reseller | `/partner-portal/<slug>/*` | Browse package offerings and submit inquiries through a public-facing portal, no account needed |
| Signed-in users | `/ai/*` | Ask business questions using a read-only AI assistant scoped to the user's data and role |
| Anyone with a receipt | `/scan/*` | Verify a printed/shared sale receipt is genuinely on file, by scanning or typing its QR code |

The system is built around a single source of truth in MySQL, with append-only movement logs and admin audit records so internal actions remain traceable after the fact.

---

## Product and business model

The app is designed around a retail and wholesale perfume operation:

- Products are tracked by SKU, with base code + unit size forming the unique identifier (e.g. `A1-50ML`, with a corresponding `A1-BULK` for the scent's unpackaged stock).
- Inventory is maintained per branch, not just globally — HQ (branch ID 1) holds bulk and warehouse stock; retail branches hold bottled stock only.
- HQ manages production batches, turning raw materials into bottled and bulk stock, with cost-of-goods tracked per batch.
- Branches place stock requests to HQ and receive fulfilled deliveries, with received/damaged quantities reconciled on arrival.
- Sales and refills are recorded per branch with payment method tracking (cash and credit).
- A refill draws from the scent's *bulk* stock rather than decrementing a bottled SKU, and is HQ-only since branches don't hold bulk.
- Packages (bundles of products) can be sold to partners or resellers at wholesale pricing.
- Partner inquiries are kept as a separate lead/opportunity workflow, distinct from branch retail sales.
- Financial reporting combines branch sales, partner orders, COGS, and operational stock movement history.

---

## High-level architecture

The app is a single Flask application with several route blueprints, a shared database layer, and realtime notification support via Socket.IO.

| File | Responsibility |
| --- | --- |
| `app.py` | Builds the Flask app, configures security headers (Talisman), CSP, logging, and registers all blueprints |
| `config.py` | Environment-based configuration: database, security, mail, AI, rate limits, partner portal slug |
| `extensions.py` | Shared extension instances (Flask-Limiter, Flask-SocketIO) — single-process/single-worker by design |
| `db.py` | MySQL connection pooling and a `transaction()` helper for atomic, all-or-nothing multi-table writes |
| `decorators.py` | Shared route decorators for login/role/branch access control |
| `utils.py` | Form-parsing helpers, constants (payment methods, sale types), validation, receipt-code signing |
| `sale_stock.py` | Shared stock logic for recording and voiding a sale — used by both manual entry and Excel import, so every path moves stock identically |
| `sales_import.py` | Builds/parses the Excel bulk sales import template and inserts an entire file as one atomic transaction |
| `audit.py` | Admin action audit log (`admin_actions` table) |
| `login_activity.py` | Login/logout activity tracking (`login_activity` table) |
| `mailer.py` | Outbound SMTP notifications (currently: new partner-inquiry alerts to HQ) |
| `receipts.py` | PDF sale receipt generation, including the signed QR verification code |
| `brand_assets.py` | Shared reportlab-drawn brand mark used in generated PDFs (reports, receipts) |
| `reports.py` | Financial/operational report building (branch, HQ, partner) and Excel export |
| `sockets.py` | Socket.IO room membership and role/branch-scoped realtime event broadcasting |
| `seed.py` | Initial/demo data seeding |
| `schema.sql` | Full operational database schema, including guarded migrations for existing installs |
| `routes/auth.py` | Login, logout, and forced password-change flows |
| `routes/admin.py` | HQ operations screens and logic (largest blueprint — catalog, production, partners, users, reports) |
| `routes/branch.py` | Branch-side inventory, sales, requests, receiving, and customer workflows |
| `routes/portal.py` | Public partner portal API, slug-gated, serves the built `public-site/` app |
| `routes/ai.py` | Chat endpoint and session handling for the read-only AI assistant |
| `routes/ai_tools.py` | The data-fetching "tools" the AI assistant is allowed to call, scoped by role/branch |
| `routes/scan.py` | QR receipt verification — decodes and checks a signed receipt code against the database |

The platform uses:

- Flask 3 for application logic and routing
- MySQL (via `mysql-connector-python`) as the system database, with a pooled connection layer
- Flask-SocketIO for live page refreshes and notifications (threading mode in dev, gevent in production)
- Flask-Talisman for HTTPS enforcement, CSP, and security headers
- Flask-WTF for CSRF protection
- Flask-Limiter for request throttling (in-memory store, single-worker deployment)
- reportlab and openpyxl for printable/exportable PDF and Excel reports
- Pillow for image handling (product photos, uploads)
- Google's Gemini API for the internal AI assistant
- A separate React + Vite front end (`public-site/`) for the public partner portal

---

## Core workflows

### 1. Authentication and access control

Users log in through a role-aware flow. Admin and Branch accounts are validated against the database on every authenticated request, not only at login — so deactivating an account or forcing a password reset takes effect immediately, not just on next login.

Key behaviors:

- Admin and Branch login are handled in the same auth system with role checks
- Inactive accounts are blocked outright
- Password-change enforcement is supported for fresh or admin-reset accounts
- Session state is kept lightweight but revalidated against live DB state on each request
- Login/logout events are recorded in `login_activity` for visibility into account usage

### 2. Warehouse and branch inventory flow

The system supports the movement of stock between HQ and branches:

1. A branch creates a stock request for one or more SKUs.
2. HQ reviews and dispatches the request.
3. HQ stock is reduced and a movement log is recorded.
4. The branch later receives the shipment and confirms the received/damaged quantities.
5. The request is marked fulfilled and the final receipt is preserved for traceability.

This process is transactional end-to-end, so stock counts and movement history never drift out of sync even if a step fails partway through.

### 3. Production tracking

HQ logs production batches that convert raw materials into finished (bottled) and bulk stock. Each batch is costed against unit formulas and bulk rate settings, updating warehouse inventory and recording the production event in movement history — tying production directly into COGS and profit reporting.

### 4. Sales, refills, and voids

Branches (and HQ) record sales through `sale_stock.py`'s shared logic, so manual entry and Excel bulk import both move stock identically:

- **Sale / Freebie** — the product's own stock at that location decreases.
- **Refill** — the customer brings their own bottle; no bottled unit leaves stock, but the scent's *bulk* product is drawn down instead (HQ-only, since branches don't stock bulk).
- Payment methods include cash and credit entries.
- Every sale produces a PDF receipt with a signed, scannable QR code (see `receipts.py` / `routes/scan.py`).
- A sale can be **voided**: stock is restored, a permanent snapshot is kept in `sale_voids` (who voided it and why), and the sale row is removed so revenue/units/customer/credit totals self-correct without every query having to filter voids out.

### 5. Bulk sales import

HQ or a branch can download an Excel template, fill in a batch of sales offline, and re-upload it. The whole file is validated and inserted as **one atomic transaction** — either every row is applied, or none are — with every problem reported at once rather than failing row-by-row partway through an import.

### 6. Partner portal

The public partner portal gives distributors and resellers access to package offerings without requiring an authenticated account. It's protected by a deployment-specific slug instead of a login — the link is only ever shared directly by HQ, never linked from the signed-in app.

Its pages are a separate React app in `public-site/` (Vite + React Router + Motion, Geist type, Phosphor icons), with its own design tokens in `public-site/src/styles/tokens.css` (light and dark, following the visitor's system setting). Flask checks the slug, serves the built app at `/partner-portal/<slug>/packages[/<id>]`, and exposes the JSON API it uses under `/partner-portal/<slug>/api/` (see `routes/portal.py`). Product photos, bottle renders, and video are still served from Flask's `static/`.

The flow includes:

- browsing partner package listings and viewing package details
- submitting inquiries with validated contact information
- server-side price/order recalculation (never trusting client-submitted totals)
- stored inquiry history and status progression
- real-time HQ notification of new partner leads over Socket.IO

### 7. Receipt verification (QR scan)

Every sale receipt PDF carries a short, HMAC-signed code (not the sale's actual data) rendered as a QR code. `/scan/*` lets anyone with the receipt — staff or customer — scan that QR (webcam) or upload a photo of it, decode it client-side, and verify server-side that the sale it points to genuinely exists, pulling the real details from the database rather than trusting anything printed on the receipt itself. Manual code entry is available as a fallback. Branch staff can only verify sales recorded at their own branch; Admin can verify any sale.

### 8. AI assistant

The AI assistant is intentionally read-only and scoped to the signed-in user's role and branch. It builds a server-side snapshot of live business data (via `routes/ai_tools.py`) and sends it to Gemini with strict rules:

- use only the data it was given
- never invent inventory, prices, or history
- never perform writes
- answer only in plain text
- point the user to the relevant operational page when they ask for an action

This keeps the assistant genuinely useful for business questions without allowing it to operate the system unsupervised. Chat usage is rate-limited per user to control API cost.

### 9. Audit and movement tracking

The system keeps two separate but complementary records:

- `admin_actions` — who changed what in configuration or operational metadata
- `stock_movement_logs` — what happened to stock quantities over time, and why (sale, refill, production, dispatch, receipt, void, etc.)

Together they make it possible to answer both operational and administrative "what happened and who did it" questions after the fact.

---

## Security and operational safeguards

The application is built with a production-minded security posture:

- HTTPS enforcement and hardened security headers in production via Flask-Talisman, including a locked-down Content-Security-Policy
- CSRF protection on all state-changing forms (Flask-WTF)
- Session cookies set with security-friendly defaults (`HttpOnly`, `SameSite=Lax`, `Secure` in production)
- Role-based access control enforced through shared route decorators, re-checked against the database on every request
- Rate limiting on high-risk endpoints such as login and AI chat usage
- A hard cap on request body size (`MAX_CONTENT_LENGTH`) so no endpoint can be forced to buffer unbounded uploads
- Public portal isolation behind a long, random deployment slug rather than a signed-in session
- Receipt QR codes are HMAC-signed, not raw sale data — a garbled scan or hand-typed guess is rejected rather than trusted
- Atomic multi-table writes through a shared transaction layer (`db.transaction()`) to prevent partial updates on failure
- Best-effort audit logging and email notifications that never block the underlying business action if they fail
- The app refuses to start in a non-debug run with a default/insecure `SECRET_KEY`

---

## Configuration and environment

The app reads configuration from environment variables and a local `.env` file when present. Core settings include:

- `APP_ENV` = `development` or `production`
- `FLASK_DEBUG` = enables debug mode locally
- `SECRET_KEY` = application secret, required to be set explicitly for any non-debug run
- `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`, `MYSQL_POOL_SIZE`
- `BUSINESS_TIMEZONE_OFFSET` = the UTC offset used for "business day" boundaries in reporting (defaults to `+08:00`, Philippines)
- `PARTNER_PORTAL_SLUG` = stable public portal slug for partner access (auto-generated and logged if unset in dev — unsuitable for production, since it changes every restart)
- `GEMINI_API_KEY` and `GEMINI_MODEL` for the AI assistant, plus `AI_CHAT_RATE_LIMIT`
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`, `PARTNER_INQUIRY_NOTIFY_EMAIL`
- `PORTAL_CONTACT_PHONE`, `PORTAL_CONTACT_EMAIL`, `PORTAL_CONTACT_VIBER`, `PORTAL_CONTACT_MESSENGER_URL`, `PORTAL_REPLY_TIME` — optional contact details shown on the public portal
- `RATELIMIT_ENABLED` and `MAX_CONTENT_LENGTH`
- `SOCKETIO_CORS_ALLOWED_ORIGINS` = origins allowed to connect over Socket.IO (defaults to any origin; should be locked down in production)
- `ALLOW_DATA_RESET` = enables the dev-only "clear all data" admin action; hard-disabled in production regardless of this setting

The app is intentionally strict in production mode and will warn, or fail to start, if required configuration is missing or insecure.

---

## Local development workflow

1. Create and activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Ensure MySQL is running and reachable (defaults match a stock XAMPP install: `localhost:3306`, user `root`, empty password).
4. Load the schema in `schema.sql` into your database.
5. Set required environment variables in a `.env` file or shell config (see above).
6. Start the app with:

```bash
python app.py
```

For the partner portal front end (`public-site/`), in a second terminal:

```bash
cd public-site
npm install
npm run dev      # http://localhost:5173/partner-portal/<slug>/packages, hot reload,
                 # proxies the API and /static to Flask on :5000
npm run build    # or build once and use Flask's own :5000 URL
```

(The npm scripts call Vite through `node` directly because npm's Windows shims break on the `&` in this folder's name.)

For production-style WSGI serving, the repo also includes `wsgi.py` and the required Gunicorn + gevent stack in `requirements.txt`. The app is designed to run as a **single worker process** — rate limiting and Socket.IO both use in-memory state rather than a shared store like Redis.

### Running tests

The test suite (`tests/`) covers RBAC, sales/voids, stock requests, bulk batches, formulas, reports, partner portal API, dashboard trends, and more, using `pytest` against disposable fixtures/factories:

```bash
pytest
```

---

## Project structure

```text
.
├── app.py
├── wsgi.py
├── config.py
├── extensions.py
├── db.py
├── decorators.py
├── utils.py
├── audit.py
├── login_activity.py
├── mailer.py
├── receipts.py
├── brand_assets.py
├── reports.py
├── sale_stock.py
├── sales_import.py
├── sockets.py
├── seed.py
├── schema.sql
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── DEPLOY.md
├── public-site/          # partner portal front end (React + Vite)
│   └── src/
│       ├── components/
│       ├── content/
│       ├── hooks/
│       ├── pages/
│       └── styles/
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
│   ├── video/
│   ├── uploads/
│   └── public-site/      # build output of public-site/ (git-ignored)
├── templates/
│   ├── admin/
│   ├── branch/
│   ├── ai/
│   ├── scan/
│   ├── base.html
│   ├── login.html
│   └── errors/
├── tests/
└── fonts/
```

---

## Business notes and current characteristics

The current implementation is optimized for a real operating environment, not a purely academic prototype. In practice, that means:

- most workflows are server-rendered and database-backed, not a decoupled SPA (the partner portal is the one exception, by design)
- stock and revenue reporting are tied to live transactional data, not periodic snapshots
- branch, admin, and partner access are intentionally segmented at the route and data level
- audit visibility and movement history are part of the expected user experience, not an afterthought
- realtime updates are built into core workflows (new leads, stock changes) rather than bolted on later
- the system expects a reliable MySQL environment and correct production configuration (secret key, slug, mail, CORS) to run safely

This is a serious internal operations platform with real business workflows, accounting logic, and operational visibility built into the core application design.

---

## Summary

Heaven & Angel Scents is a full inventory and branch operations system for a fragrance brand. It combines warehouse management, production and COGS tracking, branch retail operations (sales, refills, voids, bulk import), partner lead handling through a public portal, receipt generation with QR verification, financial and operational reporting, audit logging, and AI-assisted business support into a single Flask application.

It is currently structured as a real-world internal business system with strong operational guardrails, role isolation, and traceable inventory and finance workflows.
