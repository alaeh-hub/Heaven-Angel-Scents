# Heaven & Angel Scents Inventory System

Heaven & Angel Scents is a Flask web application for managing perfume production, HQ warehouse stock, retail branches, stock transfers, sales, reporting, and operational audit trails. It provides separate Admin/HQ and Branch workspaces backed by MySQL or MariaDB.

## Local Setup

### Requirements

- Python 3.10 or newer
- MySQL or MariaDB with permission to create the application database
- The MySQL command-line client, MySQL Workbench, or another way to run `schema.sql`

### Install And Initialize

From the repository root, create a virtual environment and install the pinned dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then set the database values and replace the development `SECRET_KEY` with a private random value. Import `schema.sql` into the database configured by `MYSQL_DB`; for example, with the MySQL client:

```powershell
mysql -u root -p < schema.sql
```

The schema creates the database, HQ and starter branches, all application tables, and the `admin_actions` audit table. After the schema has been imported, run the development seed utility once:

```powershell
py seed.py
```

`seed.py` creates `admin`, `manila`, and `cebu` accounts with randomly generated temporary passwords unless `SEED_ADMIN_PASSWORD`, `SEED_MANILA_PASSWORD`, and `SEED_CEBU_PASSWORD` are set. It prints generated passwords once, requires a password change at first login, skips accounts that already exist, and refuses to run when `APP_ENV=production`.

### Run Locally

```powershell
py app.py
```

Open <http://127.0.0.1:5000>. Local development uses Flask-SocketIO's built-in server; `simple-websocket` is included so WebSocket upgrades can be used instead of long-polling when available. Set `FLASK_DEBUG=1` only for local debugging.

### Production Launch

For a deployment, set `APP_ENV=production`, provide a real `SECRET_KEY`, configure `SOCKETIO_CORS_ALLOWED_ORIGINS`, and use a shared `RATELIMIT_STORAGE_URI` such as Redis when running more than one worker. The bundled async Gunicorn worker supports Socket.IO:

```powershell
gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 wsgi:app
```

Production configuration forces `DEBUG=False`, secure session cookies, HTTPS, and HSTS. The application refuses to start without a real secret key in any non-debug run.

## System Lifecycle

1. HQ records production, increasing warehouse stock.
2. A branch requests stock from HQ.
3. HQ dispatches all or part of the request, reducing HQ stock and marking it `In Transit`.
4. The branch confirms the shipment, recording received and damaged quantities and increasing branch stock by the received quantity.
5. Branch staff record customer sales, which atomically reduce branch stock and create sales records.
6. Admin and branch reports read the resulting inventory, sales, request, production, and movement data.

## Roles And Access

### Admin

Admin accounts represent HQ or office staff. They can view cross-branch operations; manage products, branches, users, and branch pricing; record HQ production; dispatch or reject requests; review the movement ledger; download fulfilled-request receipts; and generate all Admin reports.

### Branch

Branch accounts are assigned to one retail branch. They can view their branch dashboard and inventory, submit stock requests, receive shipments, download their own fulfilled-request receipts, record sales, view sales history, and generate branch-scoped reports.

### Authentication

- Login requires selecting `Admin` or `Branch` account type.
- Passwords are stored as Werkzeug hashes.
- New and reset accounts must change their password on first use.
- Password changes require the current password and a new password of at least eight characters.
- Account status and forced-password state are rechecked from the database on protected requests.
- Deactivated accounts are signed out on their next protected request.
- Logout is available at `/logout`.

## Admin Workflows

| Area | Route | Function |
| --- | --- | --- |
| Dashboard | `/admin/` | Active SKU, branch, pending-request, and low-stock metrics; alerts; recent activity; top sellers. |
| Products | `/admin/products` | Create products and view the catalog with total stock. |
| Product status | `/admin/products/<sku>/toggle` | Toggle active/discontinued status without deleting the product. |
| Production | `/admin/production` | Record production, optional batch code, and HQ stock changes. |
| Branches | `/admin/branches` | Create retail branches and initialize product inventory rows. |
| Branch stock | `/admin/branch-stock` | View branch totals, stock, reorder levels, and prices. |
| Branch pricing | `/admin/branch-stock/price` | Set a branch price override or clear it to use the HQ price. |
| Users | `/admin/users` | Create accounts, toggle status, and reset passwords. |
| Requests | `/admin/requests` | Filter requests, dispatch stock, reject pending requests, and download fulfilled receipts. |
| Movement ledger | `/admin/movement-logs` | Review up to 200 recent movements and filter by branch. |
| Admin audit log | `/admin/audit-log` | Review up to 300 recent non-inventory administrative actions. |
| Reports | `/admin/reports` | Select and download Admin reports. |
| Report data | `/admin/api/reports-data` | Return JSON metrics for Admin charts. |

Products use the variants `Male`, `Female`, or `Unisex`. Discontinuing a product is a soft status change; historical records remain available.

## Branch Workflows

| Area | Route | Function |
| --- | --- | --- |
| Dashboard | `/branch/` | Branch inventory, low-stock items, open requests, and today's sales totals. |
| Inventory | `/branch/inventory` | Active products, stock, reorder level, HQ price, override price, and effective selling price. Discontinued products remain visible while the branch has stock, but cannot be requested or sold. |
| Request stock | `/branch/request-stock` | Submit requests for active products and view request history. |
| Receive stock | `/branch/receive-stock` | Confirm `In Transit` shipments and record received and damaged quantities. |
| Goods-received receipt | `/branch/receive-stock/<request_id>/receipt` | Download a PDF for the branch's own fulfilled request. |
| Record sale | `/branch/record-sale` | Record a sale when enough stock is available. |
| Sales history | `/branch/sales-history` | View up to 200 sales and all-time branch totals. |
| Reports | `/branch/reports` | Select and download branch-scoped reports. |

A shipment must satisfy `received_qty + damaged_qty <= dispatched_qty`. Any remaining shortfall is recorded as an `ADJUSTMENT` ledger entry for HQ follow-up.

## Pricing And Revenue

Each product has an HQ base price. A branch may optionally override that price. The effective selling price is `branch_price` when present, otherwise `products.price`.

When a sale is recorded, the effective price is copied into `sales.unit_price` in the same transaction that inserts the sale, deducts inventory, and writes the `SALE` movement. Revenue is calculated as:

```sql
SUM(qty_sold * unit_price)
```

Historical revenue remains accurate when prices change because each sale stores its price at the time of sale.

## Reports

`reports.py` provides these report types:

| Report | Admin | Branch | Time window |
| --- | --- | --- | --- |
| Product Catalog | Yes | No | No |
| HQ Production | Yes | No | Yes |
| Branch Stock / My Inventory | Yes | Yes | No |
| Stock Requests | Yes | Yes | Yes |
| Movement Ledger | Yes | Yes | Yes |
| Sales History | Yes | Yes | Yes |
| User Accounts | Yes | No | No |

Reports download as PDF or Excel (`.xlsx`). Filters can include recent 20, 50, 100, or 200 rows; date range or all-time selection; branch; request status; movement type; product variant; text search; active/discontinued status; low-stock-only inventory; and account role/status.

Results are capped at 1,000 rows and reports identify when they are capped. Branch reports always use the signed-in user's branch ID on the server, so a query-string branch override cannot expose another branch. PDF output uses bundled DejaVu fonts for Philippine peso values; Excel output preserves numeric/date values and includes filtering and frozen headers.

## Goods-Received Receipts

`receipts.py` generates fulfilled-request goods-received PDFs in memory. Receipts include request, dispatch, and receipt details; requested, dispatched, received, damaged, and unaccounted quantities; and available request and movement-ledger information.

Admin users can access any fulfilled request. Branch users can access only fulfilled requests belonging to their branch.

## AI Assistant

The read-only assistant is available at `/ai/` and `/ai/chat` and uses Gemini through `requests`.

- Admin snapshots contain summary data across retail branches.
- Branch snapshots contain only that branch's inventory, open requests, today's sales, and recent sales.
- A fresh role-scoped snapshot is built for every request.
- The model receives no SQL access, database credentials, or tool-calling capability.
- It cannot record sales, dispatch stock, change prices, create accounts, or edit application data.
- Messages are limited to 1,000 characters; only the latest ten supplied history turns are used.
- Requests are rate-limited per signed-in user.
- Without `GEMINI_API_KEY`, the endpoint returns a configuration message instead of calling Gemini.

Generated answers are model output and should be checked against the relevant application page when an operational decision depends on them.

## Realtime Updates

Flask-SocketIO sends authenticated browser notifications. Admin tabs join the `admin` room; Branch tabs join `branch:<branch_id>`.

Write operations publish small `data_changed` messages containing scope names such as `requests`, `inventory`, `production`, `sales`, `movement_logs`, `products`, `branches`, or `users`. The payload contains no business data. The frontend uses the scope to refresh affected page content or task-count badges. Unauthenticated connections are rejected, and branch notifications are limited to the session's branch room.

The shared top bar also provides a notification bell for human-readable alerts, such as product status changes. Notifications are scoped to the signed-in username, persist in browser local storage across tabs and restarts, show unread counts, and can be cleared from the bell panel. These alerts are separate from `data_changed` events: refresh events carry only scope names, while bell events carry the message displayed to the user.

## Data Model

The core schema is defined in `schema.sql`.

| Table | Purpose |
| --- | --- |
| `branches` | HQ and retail branch identity, location, and HQ flag. |
| `users` | Username, password hash, role, branch assignment, active status, and forced-password flag. |
| `products` | SKU, item name, variant, HQ price, and active status. |
| `branch_inventory` | One row per branch/SKU with stock, reorder threshold, and optional price override. |
| `production_logs` | HQ production quantity, optional batch code, and timestamp. |
| `stock_requests` | Requested, dispatched, received, and damaged quantities plus request status. |
| `sales` | Branch, SKU, quantity sold, sale-time unit price, and timestamp. |
| `stock_movement_logs` | Production, dispatch, receipt, sale, adjustment, and damage entries with actor, references, and stock levels. |
| `admin_actions` | Administrative audit entries created at application startup by `audit.py`. |

Stock request statuses are `Pending`, `In Transit`, `Fulfilled`, and `Rejected`. Stock movement types are `PRODUCTION`, `DISPATCH`, `RECEIPT`, `SALE`, `ADJUSTMENT`, and `DAMAGE`.

Production `batch_code` is stored for traceability in `production_logs`. The current sales flow does not associate sales with batches and does not implement FIFO batch allocation.

## Configuration Reference

Configuration is loaded from environment variables by `config.py`. Development defaults and production behavior are intentionally different: production requires an explicit `SECRET_KEY`, uses secure session cookies, and enables HTTPS/HSTS behavior.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | Selects development or production configuration. |
| `FLASK_DEBUG` | `0` | Enables Flask debug mode only when set to `1`. |
| `SECRET_KEY` | Development fallback | Flask session signing key; required in production. |
| `MYSQL_HOST` | `localhost` | MySQL/MariaDB host. |
| `MYSQL_PORT` | `3306` | Database port. |
| `MYSQL_USER` | `root` | Database user. |
| `MYSQL_PASSWORD` | Empty | Database password. |
| `MYSQL_DB` | `heaven_and_angel_scents` | Database name. |
| `GEMINI_API_KEY` | Empty | Enables Gemini requests for the AI assistant. |
| `GEMINI_MODEL` | `gemini-2.5-flash` in code; `gemini-3.5-flash` in `.env.example` | Gemini model name. |
| `AI_CHAT_RATE_LIMIT` | `15 per minute;150 per day` | Per-user AI chat limit. |
| `RATELIMIT_STORAGE_URI` | Flask-Limiter default | Optional limiter storage backend setting. |
| `SOCKETIO_CORS_ALLOWED_ORIGINS` | Extension default | Optional Socket.IO CORS setting. |

## Security And Integrity

- Admin and Branch decorators enforce authentication and role checks.
- Branch queries are scoped from `session["branch_id"]` in the backend.
- SQL uses parameterized database helpers.
- State-changing form operations use Flask-WTF CSRF protection. Logout is a GET endpoint that clears the session.
- Passwords are hashed and are not stored in plaintext.
- Inventory-changing workflows use transactions and row locks to prevent concurrent over-dispatch or overselling.
- Movement records retain the actor, reference, and before/after stock levels where applicable.
- Talisman provides security headers, clickjacking protection, MIME-sniffing protection, and production HTTPS/HSTS behavior.
- Session cookies are HTTP-only and use `SameSite=Lax`; production cookies are marked secure.
- The AI assistant receives a prepared, role-scoped snapshot rather than database access.

## Architecture

```text
app.py                 Application factory, extensions, security headers, errors
config.py              Environment-backed configuration
db.py                  MySQL connection, query, execute, and transaction helpers
decorators.py          Authentication and role enforcement
audit.py               Administrative audit table and action logging
receipts.py            Goods-received PDF generation
reports.py             Report filters, queries, PDF, and Excel rendering
sockets.py             Authenticated Socket.IO rooms and update notifications
utils.py               Input validation and temporary password generation
routes/auth.py         Login, logout, and password changes
routes/admin.py        HQ/Admin workflows and endpoints
routes/branch.py       Branch workflows and endpoints
routes/ai.py           Role-scoped read-only Gemini assistant
templates/             Jinja pages and shared layout
static/                CSS, frontend behavior, and bundled Chart.js
schema.sql             Core MySQL/MariaDB schema and starter branches
seed.py                Starter account/data seeding utility
wsgi.py                WSGI entry point for server integration
requirements.txt       Python runtime dependencies
```

The application starts through `create_app()` in `app.py`. Database connections are opened per request and closed through the Flask application context. `audit.py` still performs a best-effort `admin_actions` existence check at startup as a defensive fallback, but the table is defined in and should be imported from `schema.sql`.

## Current Scope And Limitations

The system covers the production-to-sale inventory lifecycle, branch transfers, sales history, operational reporting, receipts, realtime refresh notifications, and read-only assistance. It does not currently provide:

- Supplier purchase orders or procurement workflows.
- Barcode scanning.
- Refunds, returns, or sale reversals.
- Customer profiles or customer relationship management.
- Payment processing.
- Multi-currency accounting.
- Batch-linked sales or FIFO deduction.
- Automated deployment orchestration.

The repository does not include automated end-to-end tests for the complete production-to-sale workflow.
