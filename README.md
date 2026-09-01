# Heaven & Angel Scents — Inventory & Distribution Platform

Internal operations system for a perfume brand: one HQ warehouse, multiple
retail branches, a distributor/reseller partner program, and a read-only
AI assistant — all built on Flask, MySQL, and Socket.IO.

This document describes **system architecture and application flow only**.
It intentionally excludes installation/setup instructions.

---

## 1. Overview

The platform coordinates four cooperating roles around a single source of
truth (MySQL):

| Role | Access surface | Core concern |
|---|---|---|
| **HQ Admin** | `/admin/*` | Catalog, warehouse production, dispatch, partners, packages, accounts, reporting |
| **Branch Staff** | `/branch/*` | Local inventory, sales/refills, stock requests, receiving shipments |
| **Distributor / Reseller** | `/partner-portal/<slug>/*` | Browses bundled packages, submits inquiries — public, unauthenticated |
| **Any signed-in user** | `/ai/*` | Role-scoped, read-only conversational assistant over their own data |

Every write in the system is designed to leave a trail: a stock movement
row, an audit-log row, or both — so **Inventory Log** and **Admin Log**
together form a complete, append-only history of "what changed and who
changed it."

---

## 2. High-Level Architecture

```
                                   ┌─────────────────────────────┐
                                   │        Browser Clients       │
                                   │  Admin UI · Branch UI ·      │
                                   │  Public Partner Portal ·     │
                                   │  AI Chat Widget              │
                                   └───────────────┬──────────────┘
                                                    │ HTTPS (Talisman/CSP, CSRF)
                          ┌─────────────────────────┼─────────────────────────┐
                          │                          │                         │
                 WSGI request/response      WebSocket / long-poll     REST-style POST
                          │                          │                         │
┌─────────────────────────▼──────────────────────────▼─────────────────────────▼─────┐
│                                   Flask Application (app.py)                        │
│                                                                                       │
│   Security middleware:  Flask-Talisman (CSP/HSTS) · Flask-WTF (CSRF)                │
│                         Flask-Limiter (rate limiting) · session-based auth            │
│                                                                                       │
│   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐          │
│   │  auth bp      │ │  admin bp     │ │  branch bp    │ │  portal bp    │  ai bp    │
│   │  /            │ │  /admin/*     │ │  /branch/*    │ │  /partner-    │  /ai/*    │
│   │  /login       │ │  admin_       │ │  branch_      │ │   portal/*    │  login_   │
│   │  /logout      │ │  required     │ │  required     │ │  (public,     │  required │
│   │  /change-pw   │ │               │ │               │ │   slug-gated) │           │
│   └───────┬───────┘ └───────┬───────┘ └───────┬───────┘ └───────┬───────┘           │
│           │                 │                 │                 │                    │
│           └────────────┬────┴────────┬────────┴────────┬────────┘                    │
│                         │             │                 │                             │
│                 decorators.py    audit.py          mailer.py                          │
│              (RBAC + session   (admin_actions   (SMTP, best-effort,                   │
│               revalidation)     audit trail)     never blocks a write)                │
│                         │             │                 │                             │
│                         └──────┬──────┴────────┬────────┘                             │
│                                │                │                                       │
│                            db.py (connection, query/execute, transaction())            │
│                                │                                                        │
└────────────────────────────────┼────────────────────────────────────────────────────────┘
                                  │
                          ┌───────▼────────┐        ┌──────────────────────────┐
                          │     MySQL       │        │   sockets.py (Socket.IO)  │
                          │  (schema.sql)   │        │  rooms: "admin",           │
                          │  transactional  │        │  "branch:<id>"             │
                          │  writes         │        │  events: data_changed,     │
                          └─────────────────┘        │  bell_notification         │
                                                       └──────────────┬─────────────┘
                                                                      │
                                                       ┌──────────────▼─────────────┐
                                                       │  Frontend (main.js) listens │
                                                       │  for scope changes, silently│
                                                       │  re-fetches affected views  │
                                                       └────────────────────────────┘

                          ┌──────────────────────────┐
                          │   External integration    │
                          │   Gemini API (ai.py)       │
                          │   — read-only JSON snapshot│
                          │     of role-scoped data     │
                          └──────────────────────────┘
```

### 2.1 Application layers

| Layer | Modules | Responsibility |
|---|---|---|
| **Entry points** | `app.py` (dev server + factory), `wsgi.py` (production WSGI) | App factory, config selection, CSP, blueprint registration, Socket.IO init |
| **Configuration** | `config.py` | Environment-driven `Config` / `ProductionConfig`, mail, AI, rate-limit, partner-portal secret |
| **Routing / controllers** | `routes/auth.py`, `routes/admin.py`, `routes/branch.py`, `routes/portal.py`, `routes/ai.py` | Request handling, form validation, orchestration of business rules |
| **Cross-cutting concerns** | `decorators.py`, `audit.py`, `sockets.py`, `mailer.py`, `utils.py` | Access control, audit trail, realtime push, outbound email, shared validation/formatting helpers |
| **Data access** | `db.py` | Request-scoped MySQL connection, `query()`/`execute()`, atomic `transaction()` context manager |
| **Persistence** | `schema.sql` (MySQL/InnoDB) | Normalized relational schema — the single source of truth |
| **Presentation** | Jinja templates (`base.html`, `_macros.html`, admin/branch/public template sets), `static/js/main.js`, `motion.js`, vendored `chart_umd_min.js`, `style.css` | Server-rendered HTML with progressive enhancement (smart tables, live charts, realtime badges) |
| **External services** | Gemini (`ai.py`), SMTP (`mailer.py`) | AI assistant completions; best-effort partner-inquiry email notifications |

### 2.2 Why this shape

- **Server-rendered + realtime, not an SPA.** Every page is rendered by
  Flask/Jinja on load; `main.js` then opens a single Socket.IO connection
  per tab and silently re-fetches a page's own data when a relevant
  **scope** (`requests`, `inventory`, `sales`, `products`, …) changes
  elsewhere — no client-side data layer to keep in sync.
- **One request, one connection, one transaction.** `db.py` hands each
  request a single connection (`flask.g`); `transaction()` nests safely so
  a multi-table write (e.g. dispatch a delivery → decrement HQ stock →
  insert a movement log) either fully commits or fully rolls back.
- **Decorators as the single authorization chokepoint.** `login_required`
  / `admin_required` / `branch_required` all funnel through
  `_require_session()`, which **re-reads the account's live status from
  the database on every request** — a deactivation or forced password
  reset takes effect on the very next click, not on next login.
- **Audit and movement history are structurally separate.** `admin_actions`
  (via `audit.py`) records *who changed configuration* (accounts,
  products, branches, packages, partner records). `stock_movement_logs`
  records *what happened to stock* (production, dispatch, receipt, sale,
  refill, adjustment, damage). Both are append-only and both fail soft —
  a broken audit write never blocks the underlying action.
- **The partner portal is a separate trust boundary.** `routes/portal.py`
  requires no login; a random per-deployment `PARTNER_PORTAL_SLUG` string
  in the URL stands in for authentication (checked with
  `secrets.compare_digest` to avoid timing leaks), and it is intentionally
  never linked from any signed-in page.
- **The AI assistant is read-only by construction.** `routes/ai.py`
  builds a JSON snapshot already scoped to the caller's role/branch,
  hands it to Gemini inside a system prompt that explicitly forbids
  inventing data or performing actions, and returns plain text only.

---

## 3. Core Domain Model

```
branches (is_hq flag marks the Main/Warehouse branch)
   └─ branch_inventory (per-branch stock_qty, reorder_level, price)
   └─ users (role: Admin | Branch; Branch users are pinned to one branch)

products (sku = base_code + unit suffix, e.g. A1-85ML)
   └─ branch_inventory (one row per branch × sku)
   └─ package_items (many-to-many: packages ⇄ products, with qty per set)
   └─ stock_request_items / production_logs / sales (line-level activity)

stock_requests ("deliveries" — header)            production_logs
   └─ stock_request_items (line items: sku,        (adds finished units
      requested/dispatched/received/damaged qty,    straight into HQ
      unit_price snapshot)                          warehouse stock)
   status: Pending → In Transit → Fulfilled
                  ↘ Rejected

sales (Sale | Refill; Cash | Salary Deduction)
   └─ decrements branch_inventory.stock_qty (Refill: cost only, no stock impact)

stock_movement_logs (append-only ledger)
   movement_type: PRODUCTION · DISPATCH · RECEIPT · SALE · REFILL ·
                  ADJUSTMENT · DAMAGE

packages (bundle of products, partner_scope: Distributor | Reseller | Both,
          discount_percent off the reference total)
   └─ package_items

partners (Distributor | Reseller; first inquiry "wins" the record —
          name/contact are never overwritten by later inquiries)
   └─ partner_inquiries (pipeline: New → Contacted → Follow-up / On Hold
                          → Closed / Declined; order_amount snapshot;
                          package_name_snapshot frozen at submit time)

admin_actions (audit trail: actor, action, target, details, timestamp)
```

Key modeling decisions worth calling out:

- **SKU = base code + unit.** `utils.build_sku()` derives the real primary
  key (e.g. `A1-85ML`) from an admin-entered base code and a fixed unit
  list, so the same fragrance can exist at several sizes without manual
  SKU invention, and each size still carries its own price and stock.
- **Deliveries, not single-item requests.** A `stock_requests` row is a
  *shipment header*; any number of SKUs travel under it via
  `stock_request_items`, each with its own requested/dispatched/received/
  damaged quantity and a frozen unit price.
- **Package pricing is always recomputed, never trusted from the client.**
  Both the admin package pages and the public portal compute
  `reference_total` (sum of current product prices × qty) and apply
  `discount_percent` server-side; `partner_inquiries.order_amount` freezes
  that computed number at submit time so historical "Closed" revenue
  never drifts if prices change later.
- **A partner's canonical name/contact comes from their *first* inquiry
  only.** Every individual inquiry keeps its own submitted details
  unedited forever, so the partner list stays stable while the full,
  literal history remains inspectable per partner.

---

## 4. End-to-End Flows

### 4.1 Authentication & session flow

1. `GET /login` renders a tabbed form (Admin / Branch login type).
2. `POST /login` — rate-limited (10/min) — validates credentials against
   `users.password_hash` (Werkzeug hash), confirms the account's `role`
   matches the selected tab, and confirms `is_active`.
3. On success, `session` is populated (`user_id`, `role`, `branch_id`,
   `must_change_password`, …) and the user is routed to the matching
   dashboard.
4. If `must_change_password` is set (fresh account or post-reset), every
   subsequent request is redirected to `/change-password` until cleared —
   enforced centrally in `decorators._require_session()`, not per-route.
5. Every authenticated request re-validates the account against the
   database (not just the session cookie), so admin-side deactivation or
   a forced reset takes effect immediately, network-wide.

### 4.2 Warehouse → branch replenishment flow ("Stock Requests")

```
Branch                      HQ Admin                          System
  │                             │                                 │
  │  Request Stock (multi-SKU)  │                                 │
  ├────────────────────────────►│  stock_requests: Pending        │
  │                             │  + stock_request_items rows      │
  │                             │◄── notify_admin(["requests"]) ───┤ (realtime)
  │                             │                                 │
  │                             │  Review & Dispatch               │
  │                             │  (adjust qty per line, 0 = skip) │
  │                             ├──────────────► status: In Transit│
  │                             │                 HQ stock decremented,
  │                             │                 DISPATCH movement logged
  │◄── notify_admin_and_branch(["requests","inventory","movement_logs"])
  │                             │                                 │
  │  Receive Shipment            │                                 │
  │  (enter received + damaged   │                                 │
  │   per line, per delivery)    │                                 │
  ├───────────────────────────────────────────────► status: Fulfilled
  │                                                  branch stock incremented,
  │                                                  RECEIPT (+ DAMAGE) movement logged
  │◄── notify_admin_and_branch(["requests","inventory","movement_logs"])
  │                             │                                 │
  │                             │  Reject (Pending only) ──────────► status: Rejected
```

- A delivery can also be **Rejected** directly from Pending, with a
  confirmation prompt, and no stock movement is created.
- Once **Fulfilled**, the delivery becomes read-only and its receipt
  (`request_receipt`) is downloadable, mirroring exactly what shipped,
  arrived, and was damaged, per line.

### 4.3 Production flow (warehouse only)

`Admin → Production Log` selects a product, its unit/SKU, a quantity, and
an optional batch code → inserts a `production_logs` row and increments
HQ's own `branch_inventory` row (HQ is just `branch_id` for the warehouse,
flagged `is_hq = TRUE`) → a `PRODUCTION` movement is logged → realtime
`production`/`inventory` scopes notify every open Admin tab.

### 4.4 Sales / refill flow (per branch, including HQ's own counter)

1. Cashier selects a product → unit (SKU) cascades in; the reference price
   pre-fills but can be overridden per sale.
2. **Type**: `Sale` (customer takes a bottle, stock decrements) or
   `Refill` (customer's own bottle, product cost only, **no stock
   change**).
3. **Payment**: `Cash` (normal register total) or `Salary Deduction`
   (free-text employee name, flagged distinctly for payroll
   reconciliation — not tied to a login account).
4. A `sales` row is inserted; for `Sale` type, branch stock is
   decremented and a `SALE`/`REFILL` movement is logged; realtime
   `sales`/`inventory` scopes fire.

### 4.5 Partner portal & inquiry flow (public, unauthenticated)

```
Distributor/Reseller (private slug link)
        │
        ▼
GET /partner-portal/<slug>/packages         ── slug checked via secrets.compare_digest, else 404
        │  browse active packages (optionally filtered by type)
        ▼
GET .../packages/<id>                       ── full detail: contents, reference vs. discounted price
        │  clicks "Inquire About This Package" → in-page modal
        ▼
POST .../packages/<id>/inquire              ── rate-limited (5/min, 30/day per IP)
        │
        ├─ validate required fields (company/contact/phone/email; server-side, never trusted from client)
        ├─ recompute order_amount server-side (never trust a hidden form field)
        ├─ INSERT partner_inquiries (status defaults to "New", package_name_snapshot frozen)
        ├─ _find_or_create_partner(): match by email, then phone
        │      match  → bump last_inquiry_at / inquiry_count only (name/contact untouched)
        │      no match → create new partners row from this submission
        ├─ mailer.send_partner_inquiry_email() — best-effort, never blocks the save
        └─ notify_admin(["partners","partner_inquiries"]) + notify_bell(...)  ── realtime
        │
        ▼
Admin reviews the "New" lead on Partner Inquiries, works it through
New → Contacted → Follow-up/On Hold → Closed/Declined.
Only "Closed" inquiries count toward a partner's package-sales totals
and the dashboard's "Top packages" / "Top partners" figures.
```

### 4.6 Realtime propagation (Socket.IO)

- On connect, `sockets.py` gates room membership behind the existing
  Flask session: **Admin** joins room `"admin"`; **Branch** staff join
  `"branch:<branch_id>"`. An unauthenticated socket connection is refused
  outright.
- Every mutating route calls one of `notify_admin()`, `notify_branch()`,
  `notify_admin_and_branch()`, or `notify_all()` with a short list of
  coarse **scopes** (`requests`, `inventory`, `movement_logs`,
  `production`, `sales`, `products`, `branches`, `users`, `partners`,
  `partner_inquiries`) — the payload never carries actual data, only
  "something in this scope changed."
- `main.js` maps the current page to the scope(s) it cares about and
  silently re-fetches itself in the background — so two open tabs (e.g.
  HQ dispatching a delivery while the branch's Receive Shipment page is
  open) stay in sync without a manual refresh.
- A separate, human-readable **bell notification** channel
  (`notify_bell`) carries an actual message (e.g. "New reseller inquiry
  from …") for the topbar notification dropdown — the only realtime
  event meant to be read directly rather than acted on programmatically.

### 4.7 AI assistant flow (`/ai`)

1. `GET /ai/` renders the chat UI (`login_required` only — any signed-in
   role).
2. `POST /ai/chat` — rate-limited per user (`AI_CHAT_RATE_LIMIT`, default
   15/min & 150/day):
   - Builds a **role-scoped JSON snapshot** server-side:
     - **Admin** → catalog stats, low stock across all branches, pending
       deliveries with line items, per-branch and HQ revenue, today's
       figures.
     - **Branch** → that branch's own inventory, low stock, pending/
       in-transit deliveries, today's sales, recent sales.
   - Injects the snapshot plus a fixed `SYSTEM_PROMPT` (hard rules: use
     only the snapshot, never invent SKUs/prices, cannot perform any
     action — only names the sidebar page that can, plain-text replies
     only) and up to 10 prior turns into a call to the Gemini API.
   - Returns the model's plain-text reply, or a friendly error if Gemini
     is unreachable/unconfigured — the assistant never silently
     fabricates an answer.

### 4.8 Audit trail flow

Any admin-side configuration change (create/toggle a user, reset a
password, add/edit a product, add a branch, add/edit a partner or
package, update an inquiry's status or remarks, …) calls
`audit.log_action(action, target, details)` immediately after the write.
This is intentionally **separate** from `stock_movement_logs`:

| Log | Captures | Viewed on |
|---|---|---|
| `admin_actions` | Configuration/administrative changes, by whom | **Admin Log** |
| `stock_movement_logs` | Every quantity change to stock, by type | **Inventory Log** |

Both are append-only, paginated, searchable tables in the UI; neither can
be edited or deleted from the app itself.

---

## 5. Security Architecture

- **Transport & headers** — Flask-Talisman applies a strict
  Content-Security-Policy, forces HTTPS/HSTS in production, and sets
  secure defaults for framing/MIME-sniffing protection.
- **CSRF** — Flask-WTF issues and validates a token on every
  state-changing form across admin, branch, and public portal routes.
- **Session hardening** — `HttpOnly`, `SameSite=Lax` cookies, `Secure`
  cookies in production.
- **Rate limiting** — Flask-Limiter caps login attempts, the public
  inquiry endpoint (by IP), and the AI chat endpoint (by user) — with an
  explicit startup warning if the storage backend is left as in-memory
  under a multi-worker deployment (limits would be multiplied per
  worker).
- **Role-based access control** — three decorators
  (`login_required` / `admin_required` / `branch_required`) funnel
  through one shared check that re-verifies `is_active` and role against
  the database on every request, not just at login.
- **Public surface isolation** — the partner portal has no session
  dependency at all; its only gate is a constant-time comparison against
  a long, per-deployment random slug that is never linked from the
  authenticated app.
- **Fail-soft side effects** — audit logging and outbound email are both
  best-effort and independently wrapped so that a logging or SMTP failure
  never rolls back or blocks the primary write it's attached to.
- **Atomic multi-table writes** — anything that touches more than one
  table as a unit (dispatch, receive, production, sale) runs inside
  `db.transaction()`, so partial writes can't leave stock and its ledger
  entry out of sync.

---

## 6. Module Reference

| File | Purpose |
|---|---|
| `app.py` / `wsgi.py` | App factory, CSP, blueprint registration, dev vs. production entry points |
| `config.py` | Environment-driven configuration (`Config` / `ProductionConfig`) |
| `db.py` | Request-scoped MySQL connection; `query`, `execute`, `transaction()` |
| `decorators.py` | `login_required`, `admin_required`, `branch_required` — live RBAC |
| `audit.py` | `admin_actions` table bootstrap + `log_action()` |
| `sockets.py` | Socket.IO room management and scoped realtime broadcasts |
| `mailer.py` | Best-effort SMTP notification for new partner inquiries |
| `utils.py` | Shared constants (units, sale types, partner types) and input validators |
| `routes/auth.py` | Login, logout, forced/self-service password change |
| `routes/admin.py` | HQ-side: dashboard, catalog, production, requests, branches, partners, packages, accounts, logs |
| `routes/branch.py` | Branch-side: dashboard, inventory, sales, stock requests, receiving |
| `routes/portal.py` | Public, unauthenticated partner package browsing + inquiry submission |
| `routes/ai.py` | Role-scoped snapshot builder + Gemini chat proxy |
| `schema.sql` | Relational schema (MySQL/InnoDB) — single source of truth |
| `seed.py` | Dev-only starter accounts (refuses to run when `APP_ENV=production`) |
| `static/js/main.js` | Realtime scope-to-page mapping, smart tables, notification bell |
| `static/js/motion.js` | Vendored UI motion/animation helpers |
| `static/js/chart_umd_min.js` | Vendored charting library for reports/dashboard visuals |
| `static/css/style.css` | Shared design system (cards, badges, tables, forms, themes) |

---

## 7. Reporting & Aggregation Surfaces

- **Admin Dashboard** — branch count, pending requests, low-stock alerts,
  total capital (from raw materials), total revenue (branch sales +
  package sales), net profit, low-stock table, recent requests, top
  sellers, top packages, top partners, recent inventory activity.
- **Branch Dashboard** — SKUs carried, own low-stock count, today's
  sales/revenue, own low-stock table, own open requests.
- **Reports** — deeper financial/inventory breakdowns built on the same
  aggregation queries as the dashboard, exportable via the receipts
  module for individual fulfilled deliveries.

All monetary aggregates that feed "Top packages" / "Top partners" /
partner "Package sales" figures count **only Closed** partner inquiries —
a New, Contacted, Follow-up, or On Hold lead has not yet converted and is
deliberately excluded, consistently, everywhere that figure appears.
