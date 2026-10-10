# Docker deployment (Hostinger KVM 2)

Stack: **Caddy** (automatic HTTPS) -> **web** (gunicorn, non-root) -> **db**
(MySQL 8.4). A one-shot **migrate** job applies `schema.sql` on every start,
and a **backup** job dumps the database and uploads daily into `./backups`.
This replaces the systemd + nginx + certbot steps in `HOSTINGER_DEPLOY.md`.

## 1. Server setup (once)

1. Order KVM 2 with Ubuntu 24.04 (or Hostinger's "Ubuntu 24.04 with Docker" template, which skips the install below).
2. Point your domain's **A record** at the server IP and wait for DNS.
3. As root: `curl -fsSL https://get.docker.com | sh`, then `ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw allow 443/udp && ufw enable`.
4. Create a non-root user in the `docker` group and log in as it; use SSH keys and disable password login.

## 2. Configure

```bash
git clone https://github.com/alaeh-hub/Heaven-Angel-Scents.git /opt/heaven-and-angel
cd /opt/heaven-and-angel
cp .env.example .env && nano .env
```

Set at least: `DOMAIN`, `DB_ROOT_PASSWORD`, `DB_APP_PASSWORD`, `SECRET_KEY`,
`PARTNER_PORTAL_SLUG`, plus your `MAIL_*` / `GEMINI_API_KEY` values. Use long
random values (`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`).
`APP_ENV`, debug and data-reset are forced to production-safe values by the
compose file. `chmod 600 .env`.

Copy the two gitignored videos from your PC:
`scp static/video/logo_reveal.mp4 static/video/login_background.mp4 user@IP:/opt/heaven-and-angel/static/video/`
(they are baked into the image on build).

## 3. Start

```bash
docker compose up -d --build
docker compose ps          # web and db should say (healthy)
docker compose logs -f web
```

Caddy gets the certificate on first request; browse to `https://<DOMAIN>`.
Create the first admin (prompts for the password, so it stays out of shell history):
`docker compose exec web python scripts/create_admin.py`

## 4. Update

```bash
git pull --ff-only
docker compose exec backup bash -c 'set -o pipefail; mysqldump -h db -uroot --single-transaction --routines --triggers --events "$MYSQL_DB" | gzip > /backups/pre_deploy.sql.gz'
docker compose up -d --build
```

Or run **Actions -> Deploy** on GitHub: it does the same over SSH and fails
unless web turns healthy. Setup (deploy key, `production` environment secrets
`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`) is in `HOSTINGER_DEPLOY.md`
step 10; the deploy user must be in the `docker` group and the stack must
already be running (`backup` is exec'd for the pre-deploy dump).

## 5. Backups

`./backups` holds a daily DB dump and uploads archive (14 days kept). That
is on the same disk as the data, so **copy it off the server** (rclone/rsync
to another machine or bucket) and enable Hostinger's VPS snapshots too.
Check `docker compose logs backup` for `backup FAILED` and that dump files are
more than a few KB (an empty dump is ~20 bytes). Test a restore once: `gunzip -c backups/<file>.sql.gz | docker compose exec -T db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" <dbname>'`.

## Notes

- Keep `-w 1`: rate limiting and Socket.IO hold in-process state.
- Only Caddy publishes ports; MySQL and the app are reachable only on the Docker network.
- The DB passwords apply only when the `dbdata` volume is first created. Changing them later needs `ALTER USER` inside MySQL.
- Logs rotate automatically (10 MB x 5 per container).
