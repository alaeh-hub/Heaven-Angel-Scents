# Backup & Disaster Recovery

This app's entire business state — every sale, every stock movement, every
account — lives in one MySQL database, plus a small amount of local disk
state (uploaded product photos). Losing the server without a working backup
means losing the business's records, not just the app. This document is the
strategy; `mysqldump` and `mysql` (below) are the tools.

## What gets backed up

| What | Where | Backed up by |
|---|---|---|
| All application data (branches, users, products, stock, sales, requests, partners, audit logs, ...) | MySQL, `MYSQL_DB` | `mysqldump` |
| Uploaded product photos | `static/uploads/` | `tar` |
| Secrets (`SECRET_KEY`, DB/mail credentials, `PARTNER_PORTAL_SLUG`, ...) | `.env` | **Not** part of the routine backup below — see "Secrets" |

`schema.sql` and the application code itself are already backed up implicitly:
they're in git.

## Taking a backup

```bash
cd /opt/heaven-and-angel
mkdir -p backups
ts=$(date +%Y%m%d_%H%M%S)
mysqldump -u USER -p --single-transaction --routines --triggers --events   heaven_and_angel_scents | gzip > backups/heaven_and_angel_scents_$ts.sql.gz
tar -czf backups/uploads_$ts.tar.gz static/uploads
```

`--single-transaction` means it doesn't lock tables or block the live app.
Use the `MYSQL_USER` / `MYSQL_DB` values from `.env`. The first file is the
database, the second the uploaded product photos. Delete old copies by hand,
or with `find backups -mtime +14 -delete`.

Docker Compose deployments already run this daily: the `backup` service
writes a dump and a photos archive into `./backups` and prunes old ones.

You can also download a backup from inside the app (Settings → Backup); that
file is capped by email attachment size, so use the commands above for a
full copy.

## Schedule

Run the backup nightly via cron on the production host, for example by saving
the commands above as a small script of your own and scheduling it:

```cron
# 2:00 AM daily
0 2 * * * /path/to/your-backup-script >> /var/log/heaven-and-angel-backup.log 2>&1
```

(Docker Compose deployments don't need this; the `backup` service does it.)

## Retention

Pruning local copies only bounds disk usage on the server itself; it is not a
substitute for off-site retention. A reasonable overall policy:

- Keep every daily backup locally for 14 days.
- Keep at least one backup per month for a year at the off-site destination
  below, for the rare case a bad state goes unnoticed for weeks.

## Off-site copy

**A backup that only exists on the server it's backing up isn't a backup** —
it doesn't survive the disk failing, the server being wiped, or the hosting
account being lost. After a backup runs, copy the new files somewhere
else entirely. Any of these work; pick one and run it right after the backup
in the same cron job:

- `rclone copy ./backups remote:heaven-and-angel-backups` (S3, Backblaze B2,
  Google Drive, etc. — whatever `rclone` config you set up)
- `rsync -av ./backups user@offsite-host:/backups/heaven-and-angel/`
- A cloud provider's own scheduled snapshot/backup feature, if the database
  is hosted on one (RDS, Cloud SQL, etc.) — often the easier option if
  you're already on one.

### Automated off-host copy (compose `offsite` service)

`docker compose --profile offsite up -d` starts an `rclone` container that
copies `./backups` to a remote every 6 hours (`copy`, never `sync`, so local
deletions never remove the off-host copy). Configure the remote in `.env`
(see `.env.example`): `OFFSITE_DEST` plus `RCLONE_CONFIG_OFFSITE_*`
variables for any rclone backend (S3/R2/B2, SFTP, ...). Set
`OFFSITE_HEARTBEAT_URL` to a dead-man's-switch monitor so you are alerted if
copies stop. The remote credentials should have write-only/append rights
where the provider allows it, and the bucket should be encrypted at rest.

## Secrets

`.env` is **not** included in the backup on purpose — bundling live
credentials into the same backup files that get copied off-site (and kept
for a year, per Retention above) widens who and what can end up with them.
Store `.env`'s contents separately, in whatever secrets manager or password
manager the team already uses, and keep it up to date by hand when a
credential rotates.

## Restoring

```bash
mysql -u USER -p -e "CREATE DATABASE heaven_and_angel_scents_restore_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
gunzip -c backups/heaven_and_angel_scents_20250101_020000.sql.gz | mysql -u USER -p heaven_and_angel_scents_restore_test
```

Always restore into a database with a different name first, never straight
over one an in-use app is reading, and check the restored data (log in against
it, spot-check recent sales) before pointing production at it. To restore for
real, do the same with the live database name after dropping and recreating
it, with the app stopped. Photos come back with
`tar -xzf backups/uploads_<timestamp>.tar.gz`.

## Testing a restore

A backup nobody has ever restored is a guess, not a plan. Quarterly (put it
on a calendar, it will not happen otherwise):

1. Pick a recent backup file.
2. Restore it into `heaven_and_angel_scents_restore_test` (commands above).
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
| 2026-10-23 | `heaven_and_angel_scents_20261023_031542.sql.gz` | OK: 33 tables, 4 branches, 2 users, 3862 sales; restored in ~2s into a throwaway mysql:8.4 container | Database only (technical drill). Still to do: point a throwaway app instance at a restored DB, and restore from the *off-host* copy. |

## Emailed backup (Settings -> Backup)

Admins can press **Email backup now** in the sidebar's Settings dialog. It
builds one `.zip` and emails it over the same SMTP account the partner
inquiries use (to `BACKUP_NOTIFY_EMAIL`, or `PARTNER_INQUIRY_NOTIFY_EMAIL` if
that is blank):

- `database.sql` - full restore script (`mysql -u <user> -p <db> < database.sql`)
- `csv/<table>.csv` - every table as a spreadsheet-friendly file
- `uploads/` - product photos
- `MANIFEST.txt` - timestamp, row counts, restore steps

It is a consistent snapshot (one REPEATABLE READ transaction) and does not
block the live app. If the zip would exceed `BACKUP_EMAIL_MAX_MB` (default 18)
the photos, then the CSVs, are dropped; if `database.sql` alone is too big, use
the `mysqldump` command in this document instead. Each attempt is recorded in the Admin Log.

The zip contains customer details and password hashes and is **not
encrypted** - treat that mailbox accordingly. This does not replace the
nightly off-server copy described above; it is the quick "give me a copy now".

### Restoring one (Settings -> Backup -> Restore from a backup)

Choose the `.zip` from Email backup, enter your password and type `RESTORE`.
The whole file is validated first (only the statements this app writes are
accepted - anything else is refused); then a safety copy of the *current*
data is written to `backups/before-restore-<stamp>.zip`; then every table in
the backup is dropped and recreated with the backup's data, and the product
photos are copied back. Everyone is held on a "restore in progress" page
while it runs, and you are signed out at the end (your account is whatever
the backup says it was). Tables that exist now but not in the backup are
left alone. Backups made with `mysqldump` (`.sql.gz`) are
not accepted here - restore those with `gunzip -c file.sql.gz | mysql ...` as shown above.

### Reminders and automatic backups (Settings -> Backup)

How often should you back up? **Weekly is the safe choice** for a shop that records sales every day:
a month-old backup can mean losing a month of sales if the server dies. Every 15 days is the
minimum we would suggest; monthly only for very low activity. The choices are Week / 15 days / Month
(default 15 days).

- **Reminder (always on):** once the newest successful backup is that many calendar days old (or there
  has never been one), admins see a banner above every page - **Back up now**, **Make it automatic**,
  or the x to be reminded again tomorrow - and a red dot on the sidebar's Settings button. It disappears
  as soon as a backup succeeds. If automatic backups are on but failing, the banner says why.
- **Automatic backups (opt-in):** turn on "Back up automatically and email it to me" and pick an hour.
  A background thread in the app checks every 5 minutes; when a backup is due and the hour has been
  reached it builds and emails the same zip as the manual button, logged in the Admin Log as
  "Automatic backup". A manual backup restarts the count. A failed automatic attempt is retried after 6
  hours. A MySQL advisory lock stops two workers running the same backup. Needs mail configured and the
  app running at that hour (use `BACKUP_SCHEDULER=0` to disable the thread in a given process).
- The schedule lives in the `app_settings` table, which (like the Admin Log) is kept when you restore.
