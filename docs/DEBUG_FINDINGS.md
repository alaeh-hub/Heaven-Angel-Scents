# Backend Debug Findings Report

**Project:** Heaven & Angel Scents (Flask / MySQL / Caddy, Docker)
**Scope:** repo at `main` (9b51f50) plus the uncommitted changes listed below; CI and deploy workflows, compose stack, dependencies, test suite. Local Docker only; production not examined.
**Last updated:** 2026-10-17
**Investigator:** Claude
**Progress:** Fixing (code-side items done; operational items need the owner)

## Summary

| Severity | Open | In progress | Fixed (not verified) | Fixed (verified) | Accepted risk | Blocked | Not reproducible |
|---|---|---|---|---|---|---|---|
| Critical | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| High | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| Medium | 2 | 0 | 3 | 0 | 0 | 0 | 0 |
| Low | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

F-002 to F-007 come from the backend-production-readiness review of 2026-10-17. F-001 was found while acting on it. The most important open items are that no off-site backup (F-003) and no error monitoring (F-006) exist yet.

## Scope and coverage

- **Examined:** `config.py`, `app.py`, `decorators.py`, `routes/auth.py`, `db.py`, `extensions.py`, product image upload, Dockerfile, `docker-compose.yml`, Caddyfile, CI and deploy workflows, backup/restore scripts, `BACKUP.md`, `requirements.txt`, `public-site` production npm deps.
- **Not examined:** route-level authorization in `routes/admin.py`, `branch.py`, `ai*.py`, `sockets.py`; `schema.sql` constraints and indexes; the production server, `.env` and firewall; manual sale double-submit protection; gevent vs MySQL driver behaviour under concurrency.
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
Flask 3.1.3, Werkzeug 3.1.9, mysql-connector-python 9.1.0, python-dotenv 1.2.2, waitress 3.0.1, requests 2.33.0. CI now runs pip-audit and npm audit (F-005).

**Verification**
- Command run: pip-audit, then `python -m pytest -q`, inside `python:3.12-slim` against a MySQL 8.4 container
- Result: "No known vulnerabilities found"; 198 passed (baseline before the bump: 198 passed)
- Date verified: 2026-10-17

**Remaining risk**
Production must be rebuilt to pick this up. mysql-connector-python moved a major version; tests pass, but watch the first deploy. `waitress` appears unused in the code and could be removed.

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

### F-007: Hardening backlog (unverified or low priority)

- **Severity:** Low
- **Area:** security / performance
- **Source:** readiness review
- **Status:** Open
- **Location:** `app.py` CSP, `routes/auth.py`, `extensions.py`

Items:
- CSP keeps `'unsafe-inline'`.
- Changing a password does not invalidate other sessions.
- Admin accounts have no MFA.
- The rate limiter is in memory and the app runs a single worker.
- The MySQL driver may block the event loop under gevent (unverified).

## Remaining risks

| ID | Risk | Severity | Owner | Decision | Deadline |
|---|---|---|---|---|---|
| F-002 | Production restore unverified | Medium | TBD | Mitigate | TBD |
| F-003 | No off-site backups | Medium | TBD | Mitigate | TBD |
| F-006 | No monitoring or alerting | Medium | TBD | Mitigate | TBD |
| F-007 | Hardening backlog | Low | TBD | Defer | TBD |

## Evidence still needed

- First GitHub Actions run of the modified workflows.
- A production restore drill and a review of the production `.env`.
- Route-level authorization and branch-isolation evidence.

## Next actions

1. Push the changes, confirm CI (including `audit` and `docker-smoke`) is green, then deploy and watch for mysql-connector-python 9.x issues.
2. Create a healthchecks.io-style check and set `BACKUP_HEARTBEAT_URL` in the production `.env`.
3. Choose an off-site destination for backups (F-003) and an uptime/error provider (F-006).
4. Run the restore drill on a production backup and log it in `BACKUP.md`.
5. Review route-level authorization next (not yet examined).

## Change log

| Date | Change |
|---|---|
| 2026-10-17 | Report created with 7 findings. F-001 fixed and verified. |
