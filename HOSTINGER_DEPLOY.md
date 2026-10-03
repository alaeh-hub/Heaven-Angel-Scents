# Deploying Heaven & Angel to a Hostinger VPS

Step-by-step guide to get this Flask + MySQL + Socket.IO app running in
production on a Hostinger **KVM 2** VPS. This follows the same process as
the app's own `DEPLOY.md` — this file just makes it concrete for Hostinger
specifically, plus the GitHub Actions wiring.

Replace `yourdomain.com` and `<VPS_IP>` throughout with your real domain and
server IP once you have them.

## Prerequisites

- A Hostinger KVM 2 VPS (2 vCPU, 8 GB RAM, 100 GB NVMe) — not shared/cPanel
  hosting, this app needs root access and a persistent process.
- A domain name, with access to its DNS zone (doesn't have to be bought
  through Hostinger).
- Your GitHub repo: `https://github.com/alaeh-hub/Heaven-Angel-Scents`
- An SSH client (Windows: use `ssh` from Git Bash/PowerShell, or PuTTY).

## Step 1 — Provision the VPS and point DNS at it

1. In hPanel, order **KVM 2**, choose **Ubuntu 24.04 LTS** as the OS.
2. Once provisioned, note the server's public IP (`<VPS_IP>`) shown in
   hPanel.
3. In your domain's DNS zone, add an **A record**:
   - Host: `@` (or `www`, or both)
   - Value: `<VPS_IP>`
   - TTL: default
4. Wait for DNS to propagate (`nslookup yourdomain.com` should return
   `<VPS_IP>`).

## Step 2 — Initial server setup

SSH in as root first to do one-time hardening and package installs:

```bash
ssh root@<VPS_IP>
```

Create a non-root deploy user:

```bash
adduser deploy
usermod -aG sudo deploy
```

Give `deploy` passwordless sudo for only the service restart (least
privilege — used later by the GitHub Actions workflow):

```bash
echo "deploy ALL=(root) NOPASSWD: /bin/systemctl restart heaven-and-angel" \
  | tee /etc/sudoers.d/deploy-restart
```

Copy your SSH public key to the new user so you (and later GitHub Actions)
can log in without a password:

```bash
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/   # or paste your pubkey in
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
```

Basic firewall — only SSH, HTTP, HTTPS exposed:

```bash
ufw allow 22
ufw allow 80
ufw allow 443
ufw enable
```

Install everything the app needs:

```bash
apt update && apt upgrade -y
apt install -y python3.12 python3.12-venv python3-pip \
  mysql-server mysql-client nginx git certbot python3-certbot-nginx nodejs npm
```

(If `python3.12` isn't in Ubuntu 24.04's default repo by the time you read
this, add the `deadsnakes` PPA first. Node from Ubuntu's own repo is
usually old — if `node -v` shows below 20, install Node 20+ via
[NodeSource](https://github.com/nodesource/distributions) instead.)

Secure the MySQL install:

```bash
mysql_secure_installation
```

From here on, do everything as the `deploy` user, not root:

```bash
exit
ssh deploy@<VPS_IP>
sudo mkdir -p /opt/heaven-and-angel
sudo chown deploy:deploy /opt/heaven-and-angel
```

## Step 3 — Get the code and install dependencies

```bash
cd /opt
git clone https://github.com/alaeh-hub/Heaven-Angel-Scents.git heaven-and-angel
cd heaven-and-angel
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## Step 4 — MySQL database

```bash
sudo mysql -u root -p < schema.sql
```

Create a dedicated app user (don't use `root` in `.env`):

```sql
sudo mysql -u root -p
CREATE USER 'heaven_app'@'localhost' IDENTIFIED BY '<a long random password>';
GRANT ALL PRIVILEGES ON heaven_and_angel_scents.* TO 'heaven_app'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

## Step 5 — Configure `.env`

```bash
cp .env.example .env
nano .env
```

Fill in at minimum:

```
APP_ENV=production
FLASK_DEBUG=0
SECRET_KEY=<run: python3 -c "import secrets; print(secrets.token_hex(32))">
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=heaven_app
MYSQL_PASSWORD=<the password you set above>
MYSQL_DB=heaven_and_angel_scents
SOCKETIO_CORS_ALLOWED_ORIGINS=https://yourdomain.com
PARTNER_PORTAL_SLUG=<run: python3 -c "import secrets; print(secrets.token_urlsafe(16))">
```

Optional but recommended: `MAIL_*` settings and
`PARTNER_INQUIRY_NOTIFY_EMAIL` for partner-inquiry email notifications (see
`.env.example` for the full Gmail SMTP example).

## Step 6 — Build the front end and copy media assets

```bash
cd public-site
npm ci
npm run build        # writes static/public-site/
cd ..
```

Copy the two gitignored videos the login screen and admin intro need, from
your own machine:

```bash
scp static/video/logo_reveal.mp4 static/video/login_background.mp4 \
  deploy@<VPS_IP>:/opt/heaven-and-angel/static/video/
```

## Step 7 — systemd service

Create `/etc/systemd/system/heaven-and-angel.service`:

```ini
[Unit]
Description=Heaven & Angel Scents
After=network.target mysql.service

[Service]
Type=simple
User=deploy
WorkingDirectory=/opt/heaven-and-angel
EnvironmentFile=/opt/heaven-and-angel/.env
ExecStart=/opt/heaven-and-angel/.venv/bin/gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 wsgi:app --bind 127.0.0.1:8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now heaven-and-angel
sudo systemctl status heaven-and-angel
journalctl -u heaven-and-angel -f   # watch logs
```

**`-w 1` is required, not a placeholder** — rate limiting and Socket.IO both
rely on in-process state and break with more than one worker.

## Step 8 — nginx + TLS

Create `/etc/nginx/sites-available/heaven-and-angel`:

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    # Compress text assets (the React bundle is ~1.2 MB raw, ~350 KB gzipped).
    # Add this once; certbot leaves it alone.
    gzip on;
    gzip_comp_level 5;
    gzip_min_length 1024;
    gzip_vary on;
    gzip_types text/css application/javascript application/json image/svg+xml;

    # Serve static files straight from disk, not through gunicorn. Vite's
    # hashed bundles never change, so cache them for a year. style.css and
    # main.js keep the same filename between deploys, so they must
    # revalidate (ETag, cheap 304) or admins see stale code after a deploy.
    location /static/public-site/assets/ {
        alias /opt/heaven-and-angel/static/public-site/assets/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    location ~* ^/static/.+\.(css|js)$ {
        root /opt/heaven-and-angel;
        add_header Cache-Control "no-cache";
    }
    location /static/ {
        alias /opt/heaven-and-angel/static/;
        expires 7d;
        add_header Cache-Control "public";
    }

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
```

```bash
sudo ln -s /etc/nginx/sites-available/heaven-and-angel /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Get a free TLS cert — certbot edits the config above in place to add the
`443` block and the HTTP→HTTPS redirect automatically:

```bash
sudo certbot --nginx -d yourdomain.com
```

## Step 9 — Bootstrap the first admin account

`seed.py` refuses to run with `APP_ENV=production`, so insert the first
admin directly, once:

```bash
cd /opt/heaven-and-angel
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

Create every other account afterward through **Accounts** in the UI.

## Smoke test

- Load `https://yourdomain.com` — no mixed-content warnings, and
  `Strict-Transport-Security` present in response headers.
- Log in as `admin`; confirm the dashboard loads and Socket.IO connects (no
  realtime-badge errors in the browser console).
- Submit a test partner inquiry at `/partner-portal/<slug>/packages` and
  confirm it shows up under **Partner Inquiries**.

## Step 10 — Wire up GitHub Actions auto-deploy

The repo already has `.github/workflows/deploy.yml`, manually triggered
from the Actions tab. It needs:

1. A dedicated deploy keypair (generate on your own machine, **not** the
   VPS):
   ```bash
   ssh-keygen -t ed25519 -f ./ha_deploy_key -C "github-actions-deploy" -N ""
   ssh-copy-id -i ./ha_deploy_key.pub deploy@<VPS_IP>
   ```
2. A **GitHub deploy key** (read-only) so the VPS can `git pull` without
   your personal credentials: repo → **Settings → Deploy keys → Add deploy
   key**, paste a separate keypair's public half; put its private half at
   `~/.ssh/id_ed25519` on the VPS (as the `deploy` user) and make sure
   `origin` uses the matching `git@github.com:...` SSH URL.
3. In the repo: **Settings → Environments → New environment** →
   `production` (matches `environment: production` in the workflow). Add
   these secrets:

   | Secret | Value |
   |---|---|
   | `DEPLOY_HOST` | `<VPS_IP>` or `yourdomain.com` |
   | `DEPLOY_USER` | `deploy` |
   | `DEPLOY_SSH_KEY` | contents of `ha_deploy_key` (private key, full PEM) |
   | `DEPLOY_PORT` | only if you changed SSH off port 22 |

   Turn on **required reviewers** on the `production` environment so a
   deploy needs a manual approval click.
4. Run it: **Actions → Deploy → Run workflow** on `main`. It SSHes in,
   `git pull`s, reinstalls deps, rebuilds `public-site/`, backs up the DB,
   re-applies `schema.sql`, and restarts the service.

## Step 11 — Backups and ongoing maintenance

- Put `scripts/backup_db.sh` on a nightly cron job (`crontab -e` as
  `deploy`), and copy backups **off** the VPS (S3-compatible bucket,
  Backblaze B2, rclone to another remote) — see `BACKUP.md` for the full
  retention strategy and how to test a restore.
- `static/uploads/` (product photos) lives only on this VPS's disk — make
  sure it's included in your backup routine (`backup_db.sh` already
  archives it).
- Logs go to `journalctl -u heaven-and-angel` only — no rotation or
  external error tracking configured; set `SystemMaxUse=` in
  `/etc/systemd/journald.conf` so logs don't grow unbounded, and consider
  Sentry or similar if you need more than casual tailing.
