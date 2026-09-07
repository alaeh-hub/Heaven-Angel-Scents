# Backup & Disaster Recovery

This app's entire business state — every sale, every stock movement, every
account — lives in one MySQL database, plus a small amount of local disk
state (uploaded product photos). Losing the server without a working backup
means losing the business's records, not just the app. This document is the
strategy; `scripts/backup_db.sh` / `scripts/restore_db.sh` are the tools.

## What gets backed up

| What | Where | Backed up by |
|---|---|---|
| All application data (branches, users, products, stock, sales, requests, partners, audit logs, ...) | MySQL, `MYSQL_DB` | `mysqldump` (`scripts/backup_db.sh`) |
| Uploaded product photos | `static/uploads/` | `tar` (`scripts/backup_db.sh`) |
| Secrets (`SECRET_KEY`, DB/mail credentials, `PARTNER_PORTAL_SLUG`, ...) | `.env` | **Not** part of the routine backup below — see "Secrets" |

`schema.sql` and the application code itself are already backed up implicitly:
they're in git.

## Taking a backup

```bash
./scripts/backup_db.sh
```

Dumps the database (`--single-transaction`, so it doesn't lock tables or block
the live app) and archives `static/uploads/`, both gzip-compressed and
timestamped, into `./backups/` by default. Reads DB credentials from `.env`
the same way the app does. Deletes its own local copies older than
`BACKUP_RETENTION_DAYS` (default 14) — see "Retention" for why that's only
half the story.

Env vars it reads: `BACKUP_DIR` (default `./backups`), `BACKUP_RETENTION_DAYS`
(default `14`), plus the same `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` /
`MYSQL_PASSWORD` / `MYSQL_DB` the app itself uses.

## Schedule

Run it nightly via cron on the production host:

```cron
# 2:00 AM daily
0 2 * * * cd /path/to/heaven-and-angel && ./scripts/backup_db.sh >> /var/log/heaven-and-angel-backup.log 2>&1
```

(On a Windows deployment, the equivalent is a Task Scheduler task running the
script under Git Bash or WSL on the same schedule.)

## Retention

`backup_db.sh` only prunes its **local** copies after `BACKUP_RETENTION_DAYS`
— that bounds disk usage on the server itself, it is not a substitute for
off-site retention. A reasonable overall policy:

- Keep every daily backup locally for 14 days (the script's default).
- Keep at least one backup per month for a year at the off-site destination
  below, for the rare case a bad state goes unnoticed for weeks.

## Off-site copy

**A backup that only exists on the server it's backing up isn't a backup** —
it doesn't survive the disk failing, the server being wiped, or the hosting
account being lost. After `backup_db.sh` runs, copy the new files somewhere
else entirely. Any of these work; pick one and wire it into the same cron
job (right after the `backup_db.sh` line):

- `rclone copy ./backups remote:heaven-and-angel-backups` (S3, Backblaze B2,
  Google Drive, etc. — whatever `rclone` config you set up)
- `rsync -av ./backups user@offsite-host:/backups/heaven-and-angel/`
- A cloud provider's own scheduled snapshot/backup feature, if the database
  is hosted on one (RDS, Cloud SQL, etc.) — often the easier option if
  you're already on one.

## Secrets

`.env` is **not** included in `backup_db.sh` on purpose — bundling live
credentials into the same backup files that get copied off-site (and kept
for a year, per Retention above) widens who and what can end up with them.
Store `.env`'s contents separately, in whatever secrets manager or password
manager the team already uses, and keep it up to date by hand when a
credential rotates.

## Restoring

```bash
./scripts/restore_db.sh backups/heaven_and_angel_scents_20250101_020000.sql.gz --target-db heaven_and_angel_scents_restore_test
```

Always restore into `--target-db <some other name>` first — never straight
over a database an in-use app is currently reading — and confirm the restored
data looks right (the script prints exactly what to check) before ever
pointing production at a restored database. Add `--force` to skip the
confirmation prompt for scripted use.

## Testing a restore

A backup nobody has ever restored is a guess, not a plan. Quarterly (put it
on a calendar, it will not happen otherwise):

1. Pick a recent backup file.
2. `./scripts/restore_db.sh <file> --target-db heaven_and_angel_scents_restore_test`
3. Point a throwaway app instance at it (`MYSQL_DB=heaven_and_angel_scents_restore_test`)
   and actually log in, check the dashboard, spot-check a few recent sales.
4. Drop the throwaway database.
5. Note the date this was last tested somewhere the team will see it (this
   file is as good a place as any — add a line below).

If any step is surprising or fails, that's exactly what this drill is for:
better to find out on a Tuesday afternoon than during an actual outage.

### Restore drill log

| Date | Backup tested | Result | Notes |
|---|---|---|---|
| _(none yet)_ | | | |
