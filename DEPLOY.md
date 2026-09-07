# Production Deployment

`README.md` covers architecture and application flow only, by design. This
document covers the other half: how to actually get this app running in
production — the real WSGI stack, every environment variable that changes
behavior once you're live, and the steps that are easy to get wrong on a
first deploy. See `BACKUP.md` separately for backups/restore.

Target platform below is a single Linux host (systemd + nginx). The app
itself is plain Flask/MySQL and doesn't require any of that specifically —
adapt the process-manager and reverse-proxy sections to whatever your
platform uses, the rest applies regardless.

## 1. Prerequisites

- Python 3.12+
- MySQL 8.x — the only version this is actually tested against (CI runs
  schema.sql and the test suite against the `mysql:8.4` image; older MySQL
  or MariaDB may well work too, just untested)
- A reverse proxy able to terminate TLS and proxy WebSocket upgrades (nginx
  used below)
- `mysql`/`mysqldump` client binaries on the host (also needed by
  `scripts/backup_db.sh`, see `BACKUP.md`)

## 2. Get the code and install dependencies

```bash
git clone <your-repo-url> /opt/heaven-and-angel
cd /opt/heaven-and-angel
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` includes the production WSGI stack (`gunicorn`, `gevent`,
`gevent-websocket`) already — nothing extra to install for that.

## 3. Database

```bash
mysql -u root -p < schema.sql
```

This creates the `heaven_and_angel_scents` database and every table.
`schema.sql` is safe to re-run — every `CREATE TABLE` is `IF NOT EXISTS` and
every schema change since is its own guarded migration block — but there's
no real migration tool (Alembic/Flyway) behind it. **Before running it
against a database that already has real data in it** (i.e. every deploy
after the first), read through what's changed since your last deploy and
back up first (see `BACKUP.md`) — there's no automatic rollback if a
migration block does something unexpected.

Create a dedicated MySQL user for the app rather than using `root` in
production:

```sql
CREATE USER 'heaven_app'@'localhost' IDENTIFIED BY '<a long random password>';
GRANT ALL PRIVILEGES ON heaven_and_angel_scents.* TO 'heaven_app'@'localhost';
FLUSH PRIVILEGES;
```

## 4. Configuration (`.env`)

Copy `.env.example` to `.env` and fill it in. Every variable below has a
real, non-obvious production consequence — this table exists because none of
this is otherwise written down in one place:

| Variable | Required? | What happens if you get it wrong |
|---|---|---|
| `APP_ENV` | **Yes** — set to `production` | Anything else (including unset) uses `Config` instead of `ProductionConfig`: `SESSION_COOKIE_SECURE` stays `False` (cookies sent over plain HTTP) and Talisman won't force HTTPS/HSTS. |
| `FLASK_DEBUG` | Set to `0` (or leave unset) | `1` enables Flask's debugger — never expose that publicly, it allows arbitrary code execution from a browser. Also gates the `SECRET_KEY` startup check below. |
| `SECRET_KEY` | **Yes** | App refuses to boot (`RuntimeError` in `app.py`) with the placeholder default unless `FLASK_DEBUG=1`. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"` and never reuse it across environments. |
| `MYSQL_HOST`/`MYSQL_PORT`/`MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_DB` | **Yes** | Point these at the dedicated app user from step 3, not `root`. |
| `NUM_PROXIES` | Set to `1` if nginx (or any reverse proxy) sits in front | Left at `0` behind a proxy: every IP-based rate limit (login, the partner-portal inquiry form) buckets by nginx's own address for *all* visitors combined, not per-visitor — logged as a startup warning if you forget. |
| `RATELIMIT_STORAGE_URI` | Set to a shared store if running >1 worker | Default `memory://` only works correctly with exactly one worker process (`gunicorn -w 1`, see below) — each additional worker gets its own separate counters, silently multiplying every rate limit. |
| `SOCKETIO_MESSAGE_QUEUE` | Set to the same store if running >1 worker | Same single-worker constraint as above, for realtime pushes (`notify_admin`/`notify_branch`/etc.) instead of rate limits — a tab connected to a different worker than the one that made a change misses that update until it manually refreshes. |
| `SOCKETIO_CORS_ALLOWED_ORIGINS` | Set to your real domain(s) | Left unset, Socket.IO accepts connections from any origin — signed-in data is still session-gated (see `sockets.py`), but this is real defense-in-depth you're skipping. |
| `PARTNER_PORTAL_SLUG` | **Yes**, if you use the partner portal | Unset: a random slug is generated per process, changes on every restart, and differs across workers — the link you hand a distributor breaks the moment the app restarts. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(16))"`. |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | No | Blank key just disables the AI assistant (it tells users it isn't configured) — not a boot failure. |
| `AI_CHAT_RATE_LIMIT` | No | Defaults are sane; only matters if you want to change Gemini cost exposure per user. |
| `MAIL_*` / `PARTNER_INQUIRY_NOTIFY_EMAIL` | No | Blank disables email entirely — partner inquiries are still saved and visible in-app either way, nothing is lost, you just won't get notified by email. |
| `RATELIMIT_ENABLED` | No — leave unset (defaults on) | Only ever set to `0` by the test suite. Setting this in a real deployment's `.env` would disable rate limiting app-wide. |

**One thing this table can't cover from `.env` alone:** `RATELIMIT_STORAGE_URI`
unset + `NUM_PROXIES` unset are each individually easy to leave wrong on a
first deploy because the app still runs fine either way — it just silently
misbehaves under the specific conditions (a proxy, or >1 worker) that a
first deploy often doesn't have yet. Read the two startup warnings in your
logs the first time you boot with `APP_ENV=production` — they name exactly
this.

## 5. Bootstrapping the first admin account

`seed.py` refuses to run at all when `APP_ENV=production` (see its own
docstring) — it exists for local dev, not for planting known accounts on a
real database. Its own suggested alternative is "create them through the
app's own Accounts page instead" — but that page is `admin_required`, which
means it needs an existing Admin account to sign in as, which is exactly
what you don't have yet on a brand-new production database.

Insert the very first admin account directly, once, with a real password
you set yourself (not a temp one to remember to change):

```bash
.venv/bin/python -c "
from app import create_app
from db import execute
from werkzeug.security import generate_password_hash
import getpass

password = getpass.getpass('New admin password: ')
with create_app().app_context():
    execute(
        'INSERT INTO users (username, password_hash, role, branch_id, must_change_password) '
        'VALUES (%s, %s, %s, %s, %s)',
        ('admin', generate_password_hash(password), 'Admin', None, False),
    )
print('admin account created')
"
```

Log in as `admin`, then create every other account (branch staff, additional
admins) through **Accounts** in the UI from here on — this direct-SQL path
is only ever needed once, for the account that creates the rest.

## 6. Running the app

Local dev (`py app.py`) uses Flask-SocketIO's built-in threading mode and
doesn't apply here. Production needs an async worker because Socket.IO holds
long-lived connections:

```bash
.venv/bin/gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 wsgi:app --bind 127.0.0.1:8000
```

**`-w 1` is not a placeholder — it's a real, current constraint.** Both
`RATELIMIT_STORAGE_URI` and `SOCKETIO_MESSAGE_QUEUE` default to per-process
state; running more than one worker without pointing both at a shared Redis
instance silently multiplies rate limits and breaks realtime sync across
tabs on different workers (see the table above). Scale up by pointing both
at Redis first, not by adding `-w` before that.

### systemd unit (`/etc/systemd/system/heaven-and-angel.service`)

```ini
[Unit]
Description=Heaven & Angel Scents
After=network.target mysql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/heaven-and-angel
EnvironmentFile=/opt/heaven-and-angel/.env
ExecStart=/opt/heaven-and-angel/.venv/bin/gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 wsgi:app --bind 127.0.0.1:8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now heaven-and-angel
sudo journalctl -u heaven-and-angel -f   # logs — see "Logging" below
```

`EnvironmentFile=` reads `.env` directly as `KEY=value` pairs. Unlike
`scripts/backup_db.sh`/`restore_db.sh` (which parse `.env` by hand
specifically to avoid this), systemd's own parser takes the rest of each
line as the value rather than word-splitting on spaces, so the unquoted
`AI_CHAT_RATE_LIMIT=15 per minute;150 per day` style `.env.example` already
uses works fine here too — no reformatting needed.

## 7. Reverse proxy (nginx)

Terminates TLS, sets `NUM_PROXIES=1`'s counterpart headers, and proxies
Socket.IO's WebSocket upgrade:

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }

    location /socket.io/ {
        proxy_pass http://127.0.0.1:8000/socket.io/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}
```

This is exactly one hop between the client and the app, matching
`NUM_PROXIES=1`. If you put this behind a CDN or load balancer too, count
every hop and set `NUM_PROXIES` accordingly.

## 8. Persistent files outside the database

`static/uploads/` (product photos, written by `routes/admin.py`'s
`_save_product_image()`) lives on local disk and is **not** part of the git
repo or the database — a redeploy that recreates the filesystem (a fresh
container, a new host) loses every uploaded product photo unless this
directory is on persistent storage (a mounted volume, or excluded from
whatever "clean checkout" step your deploy process does) and included in
your backup routine (`scripts/backup_db.sh` already archives it — see
`BACKUP.md`).

## 9. Backups

Set up `scripts/backup_db.sh` on a nightly cron job and copy backups
off-host — see `BACKUP.md` for the full strategy, retention policy, and how
to actually test a restore (not optional — do this before you need it for
real).

## 10. Logging

Everything currently goes to stdout/stderr with color formatting (see
`app.py`'s `_ColorFormatter`) — there's no file handler, log rotation, or
external error tracking (Sentry or similar) configured. Under systemd,
`journalctl -u heaven-and-angel` is your log store; set
`journalctl`'s own retention (`SystemMaxUse=` in `/etc/systemd/journald.conf`
or a per-unit override) so logs don't grow unbounded, and consider adding
external error tracking before you rely on this for anything beyond casual
tailing.

## 11. Redeploying / updating

```bash
cd /opt/heaven-and-angel
git pull
.venv/bin/pip install -r requirements.txt   # in case dependencies changed
mysql -u root -p heaven_and_angel_scents < schema.sql   # back up first — see step 3
sudo systemctl restart heaven-and-angel
```

## 12. Post-deploy smoke test

- Load the site over HTTPS — confirm no mixed-content warnings and that
  `Strict-Transport-Security` is present in the response headers (Talisman).
- Log in as the admin account created in step 5; confirm the dashboard
  loads and Socket.IO connects (no realtime-badge errors in the browser
  console).
- Submit a test partner inquiry through
  `/partner-portal/<slug>/packages` and confirm it appears on **Partner
  Inquiries**.
- Check the two startup warnings mentioned in step 4 are actually gone from
  the logs (`NUM_PROXIES`, `RATELIMIT_STORAGE_URI`) if your deployment has a
  reverse proxy or more than one worker.
