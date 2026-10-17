# Backend Debug Findings Report

**Project:** Heaven & Angel Scents (Flask / MySQL / Caddy, Docker)
**Scope:** repo at `main` (9b51f50) plus the uncommitted changes listed below; CI and deploy workflows, compose stack, dependencies, test suite. Local Docker only; production not examined.
**Last updated:** 2026-10-17
**Investigator:** Claude
**Progress:** Fixing (code-side items done; operational items and admin MFA remain)

## Summary

| Severity | Open | In progress | Fixed (not verified) | Fixed (verified) | Accepted risk | Blocked | Not reproducible |
|---|---|---|---|---|---|---|---|
| Critical | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| High | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| Medium | 2 | 0 | 3 | 2 | 0 | 0 | 0 |
| Low | 1 | 0 | 1 | 3 | 0 | 0 | 0 |

F-002 to F-007 come from the backend-production-readiness review of 2026-10-17. F-001 was found while acting on it. The most important open items are that no off-site backup (F-003) and no error monitoring (F-006) exist yet.

## Scope and coverage

- **Examined:** `config.py`, `app.py`, `decorators.py`, `routes/auth.py`, `db.py`, `extensions.py`, product image upload, Dockerfile, `docker-compose.yml`, Caddyfile, CI and deploy workflows, backup/restore scripts, `BACKUP.md`, `requirements.txt`, `public-site` production npm deps.
- **Also examined (round 2):** every route's decorator (all routes are gated), object-ID routes in `branch.py`, `scan.py`, `ai.py` (drafts), `receipts.py` and `sale_stock.py` branch scoping, `sockets.py`, AI tool branch scoping (`_resolve_branch_ids`), user-management routes.
- **Not examined:** the bodies of the Admin-only routes in `routes/admin.py` beyond user management (Admin sees all branches by design); `schema.sql` constraints and indexes; the production server, `.env` and firewall; manual sale double-submit protection.
- **Assumptions:** production dependencies match `requirements.txt`.
- **Environment for verification:** Docker, `python:3.12-slim`, a throwaway MySQL 8.4 container (the running local stack was not touched).

## Findings

### F-001: Pinned dependencies have known vulnerabilities

- **Severity:** High (why: Werkzeug, Flask and requests sit on the request path of an internet-facing app; many advisories)
- **Area:** security
- **Source:** scanner (pip-audit, run during this work)
- **Status:** Fixed (verified)
- **Location:** `requirements.txt`

**Reproduction and evidence**
`pip-audit -r requirements.txt` before the change reported advisories for flask 3.0.3 (PYSEC-2026-2151), werkzeug 3.0.3 (eight IDs, fixed between 3.0.6 and 3.1.9), mysql-connector-python 8.4.0 (PYSEC-2026-1685), python-dotenv 1.0.1, waitress 3.0.0 and requests 2.32.3. `npm audit --omit=dev --audit-level=high` on `public-site` found 0 vulnerabilities.

**Root cause**
Exact version pins with no dependency scan in CI.

**Fix**
Flask 3.1.3, Werkzeug 3.1.9, mysql-connector-python 9.1.0, python-dotenv 1.2.2, requests 2.33.0. `waitress` was unused and has been removed. CI now runs pip-audit and npm audit (F-005).

**Verification**
- Command run: pip-audit, then `python -m pytest -q`, inside `python:3.12-slim` against a MySQL 8.4 container
- Result: "No known vulnerabilities found"; 198 passed (baseline before the bump: 198 passed)
- Date verified: 2026-10-17

**Remaining risk**
Production must be rebuilt to pick this up. mysql-connector-python moved a major version; tests pass, but watch the first deploy.

### F-002: Backup restore never exercised on a production dump

- **Severity:** Medium (why: an untested backup may not restore; it only matters if data is lost)
- **Area:** operations
- **Source:** readiness review
- **Status:** Fixed (not verified). A local dump was restored, but no production backup was.
- **Location:** `BACKUP.md` drill log

**Reproduction and evidence**
The drill log said "(none yet)". The newest local dump, `backups/heaven_and_angel_scents_20261017_055410.sql.gz`, restored into a scratch database: 32 tables, 1 user, 2 branches. Two 20-byte (empty) dumps from 2026-10-10 exist in `backups/`, which shows the earlier empty-backup failure; the size check in compose and deploy now guards against it.

**Root cause**
No drill was run.

**Fix**
Ran a local restore. The owner still needs to run the drill on a production backup and record it in `BACKUP.md`.

**Verification**
- Command run: `gzip -dc <dump> | mysql restore_drill` in a MySQL 8.4 container, then row counts
- Result: success as above
- Date verified: 2026-10-17 (local data only)

**Remaining risk**
Restoring a production backup, and running the app against it, are unverified.

### F-003: No off-site backup copy

- **Severity:** Medium (why: losing the host destroys the data and the backups together)
- **Area:** operations
- **Source:** readiness review
- **Status:** Open
- **Location:** `docker-compose.yml` backup service, `BACKUP.md`

**Root cause**
Backups are written only to `./backups` on the same host, and the off-site copy is manual.

**Fix**
Not done. It needs a destination (S3-compatible bucket, rclone target) and credentials from the owner.

**Remaining risk**
Total data loss if the host fails.

### F-004: Backup failures are silent

- **Severity:** Medium
- **Area:** operations
- **Source:** readiness review
- **Status:** Fixed (not verified)
- **Location:** `docker-compose.yml` backup service, `.env.example`

**Root cause**
A failed backup is only logged to container stderr, and nothing alerts anyone.

**Fix**
An optional `BACKUP_HEARTBEAT_URL` is pinged with curl after each successful backup. A monitor such as healthchecks.io then alerts when pings stop.

**Verification**
`docker-compose.yml` parses as valid YAML, and curl exists in the running backup container. The ping has not been exercised end to end. To verify: set the URL, run `docker compose up -d backup`, confirm the ping arrives, then stop the container and confirm the alert fires.

**Remaining risk**
It does nothing until the owner creates a check and sets the variable.

### F-005: No dependency audit or stack smoke test in CI; deploy not gated on CI

- **Severity:** Medium
- **Area:** configuration / CI
- **Source:** readiness review
- **Status:** Fixed (not verified)
- **Location:** `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`

**Fix**
- New CI jobs: `audit` (pip-audit and npm audit) and `docker-smoke` (builds db, migrate and web from a throwaway `.env` and waits for healthy).
- `deploy.yml` now refuses to run unless a successful `ci.yml` run exists for the commit.

**Verification**
Both workflow files parse as YAML, and the pip-audit and npm audit commands were run locally. The workflows have not run on GitHub, so the `gh run list` gate and the smoke job are untested. The first push should confirm them.

**Remaining risk**
Rollback is still manual: restore the pre-deploy dump and redeploy the previous commit. This should be documented.

### F-006: No error monitoring or uptime alerting

- **Severity:** Medium
- **Area:** operations
- **Source:** readiness review
- **Status:** Open
- **Location:** n/a

**Fix**
Not done. It needs the owner's choice of provider: an external uptime check on `/healthz`, plus Sentry or equivalent.

**Remaining risk**
Outages and 500 errors are noticed only when a user reports them.

### F-007: Hardening backlog

- **Severity:** Low
- **Area:** security / performance
- **Source:** readiness review
- **Status:** Open (remaining: admin MFA, `style-src` inline styles, in-memory rate limiter on a single worker)
- **Location:** `app.py` CSP, `routes/auth.py`, `extensions.py`

Done from this list: session invalidation on password change (F-010), gevent/driver blocking (F-011), script CSP (F-012), `waitress` removal (F-001). Not done:
- Admin accounts have no MFA. This is a feature with lockout risk (enrolment, recovery codes, a login second step), so I did not add it without a design decision.
- `style-src` still allows inline styles because the templates contain about 600 inline `style=` attributes.
- The rate limiter is in memory and the app runs a single worker (fine at this scale).

### F-008: Branch account with no branch was treated as unrestricted (fail-open)

- **Severity:** Medium (why: needs an account whose `branch_id` is NULL; no current admin route creates one, but `users.branch_id` is `ON DELETE SET NULL`, so deleting a branch row would; impact is cross-branch read, void and credit-settle)
- **Area:** security
- **Source:** code review (this round)
- **Status:** Fixed (verified)
- **Location:** `routes/scan.py` `_scope()`, `sale_stock.py` `void_sale` / `settle_credit` (`branch_id=None` means HQ)

**Reproduction and evidence**
The new test `test_branch_account_without_a_branch_is_not_treated_as_unrestricted` failed before the fix: `/scan/verify` returned HTTP 200 for another branch's sale for a Branch user with `branch_id` NULL.

**Root cause**
Helpers read `branch_id=None` as "unrestricted", and the access gate never checked that a Branch account has a branch.

**Fix**
`decorators._require_session` now signs out and refuses any Branch account with no branch.

**Verification**
- Command run: `python -m pytest -q` in `python:3.12-slim` against MySQL 8.4
- Result: 213 passed (198 at the start of the session)
- Regression tests: `tests/test_branch_isolation.py` (5 tests; the other 4 confirm that receipts, scan verify, void/settle and AI drafts already refuse another branch's data)
- Date verified: 2026-10-17

**Remaining risk**
Passing `branch_id=None` to mean "HQ" is still a fragile convention; the access gate now guarantees only real Admins reach it.

### F-009: Branch isolation across routes (review result)

- **Severity:** n/a (no defect found)
- **Area:** security
- **Source:** readiness review follow-up
- **Status:** Fixed (verified): no defect found, evidence recorded
- **Location:** `routes/branch.py`, `routes/scan.py`, `routes/ai.py`, `receipts.py`, `sale_stock.py`, `routes/ai_tools.py`

Every route has an access decorator. Object-ID routes filter by the session's branch (receipts return 404, void/settle return 400, AI drafts return 403, receive-stock joins on `branch_id`). AI tools resolve branch IDs server-side, so a Branch user cannot widen scope through the model. New tests prove the receipt, scan, void, settle and draft cases.

### F-010: Other sessions survived a password change or reset

- **Severity:** Low
- **Area:** security
- **Source:** readiness review
- **Status:** Fixed (verified)
- **Location:** `decorators.py`, `routes/auth.py`

**Fix**
The session stores a short hash of the password hash (`pwv`); a mismatch signs the session out on its next request, for self-service changes and HQ resets. The session that changed the password stays signed in.

**Verification**
`tests/test_session_security.py` (3 tests); full suite 213 passed. Deploy-time effect: everyone currently signed in is signed out once, because existing sessions lack `pwv`.

### F-011: One slow query froze the whole app (gevent with the C-extension driver)

- **Severity:** Medium (why: production runs one gevent worker, so any slow query or report stalled every user)
- **Area:** performance
- **Source:** readiness review (suspected), then measured
- **Status:** Fixed (verified)
- **Location:** `db.py`, `config.py`

**Reproduction and evidence**
gunicorn with the gevent worker, 5 concurrent requests each running `SELECT SLEEP(1)`: 5.0 s with the default C-extension driver (requests fully serialized).

**Root cause**
mysql-connector's C extension does socket I/O in native code that gevent cannot switch away from.

**Fix**
New `MYSQL_USE_PURE` setting (default on) passes `use_pure=True` to the pool.

**Verification**
Same experiment against the real app code: 1.0 s with `MYSQL_USE_PURE=1`, 5.0 s with `MYSQL_USE_PURE=0`. Full suite 213 passed on the pure driver.

**Remaining risk**
The pure-Python driver is somewhat slower per query; not measured under production load.

### F-012: Scripts allowed by the CSP's inline-script exemption

- **Severity:** Low
- **Area:** security
- **Source:** code comment in `app.py` (known gap)
- **Status:** Fixed (not verified in a browser)
- **Location:** `app.py`, all templates, `static/js/main.js`

**Fix**
`script-src` no longer allows inline scripts without a nonce. All 27 inline `<script>` tags carry a per-request nonce. The 10 inline `on*=` handlers became `data-call`, `data-autosubmit`, `data-confirm` and `data-reveal-*` attributes handled by delegated listeners in `main.js`. Soft navigation (`runPageScripts`) re-creates page scripts from a fetched page that has a different nonce, so it now stamps the live page's nonce (from `<meta name="csp-nonce">`).

**Verification**
`tests/test_csp.py` checks the header and nonces on the login page and every static GET page for Admin and Branch, and that no `on*=` handlers remain. 213 tests pass. **Not verified in a real browser:** the soft-navigation script re-run and the converted buttons (Materials, Products, Bulk Batches modals, Partner Inquiries status select, Users role select, confirm dialogs).

**Remaining risk**
If soft navigation or a converted control breaks, the symptom is a button or chart that does nothing; the browser console will show a CSP error naming the blocked script.

### F-013: Realtime connections ignored live account state

- **Severity:** Low
- **Area:** security
- **Source:** code review
- **Status:** Fixed (verified)
- **Location:** `sockets.py`

**Fix**
The Socket.IO connect handler now applies the same live checks as HTTP routes (active, password fingerprint, has a branch).

**Verification**
`tests/test_socket_auth.py` (4 tests); full suite 213 passed.

## Remaining risks

| ID | Risk | Severity | Owner | Decision | Deadline |
|---|---|---|---|---|---|
| F-002 | Production restore unverified | Medium | TBD | Mitigate | TBD |
| F-003 | No off-site backups | Medium | TBD | Mitigate | TBD |
| F-006 | No monitoring or alerting | Medium | TBD | Mitigate | TBD |
| F-007 | Admin MFA, inline styles in CSP, in-memory limiter | Low | TBD | Defer | TBD |
| F-012 | CSP change not smoke-tested in a browser | Low | TBD | Mitigate | before next deploy |

## Evidence still needed

- Browser smoke test of the CSP change (F-012) on a staging or local stack.
- First GitHub Actions run of the modified workflows.
- A production restore drill and a review of the production `.env`.
- Route-level authorization and branch-isolation evidence.

## Next actions

1. Push the changes, confirm CI (including `audit` and `docker-smoke`) is green, then deploy and watch for mysql-connector-python 9.x issues.
2. Create a healthchecks.io-style check and set `BACKUP_HEARTBEAT_URL` in the production `.env`.
3. Choose an off-site destination for backups (F-003) and an uptime/error provider (F-006).
4. Run the restore drill on a production backup and log it in `BACKUP.md`.
5. Smoke-test the CSP change in a browser (F-012) before deploying.
6. Decide on admin MFA (F-007); an opt-in TOTP design would avoid locking out HQ.

## Change log

| Date | Change |
|---|---|
| 2026-10-17 | Report created with 7 findings. F-001 fixed and verified. |
| 2026-10-17 | Round 2: authorization review done; F-008 to F-013 added. 213 tests pass. |
