"""Running backups (by hand or on a schedule) and deciding when one is due.

Schedule model (all stored via appsettings):
  backup_days  - 7, 15 or 30: how often a backup should happen. Always drives
                 the in-app reminder; also drives automatic backups when on.
  backup_auto  - '1' to have the server build and email the backup itself.
  backup_hour  - 0-23, the business-time hour automatic backups wait for.

Every day counts in calendar days, in business time (see utils.business_now):
a backup on Oct 21 with "every 7 days" is due on Oct 28, from the chosen hour
on for automatic ones. Any successful backup - manual or automatic - resets
the clock.

One scheduler thread per process wakes every few minutes. A MySQL advisory
lock makes sure that, even with several workers, only one of them runs a
given automatic backup.
"""
import datetime
import threading
import time

from flask import current_app

import appsettings
from backup import TooLargeError, build_backup_zip
from db import execute, get_db, query
from mailer import backup_email_configured, send_backup_email
from utils import business_now

backup_lock = threading.Lock()          # held for the whole of a backup
backup_state = {"phase": ""}            # what the running backup is doing
restore_running = lambda: False         # replaced by routes/admin.py

AUTO_ACTOR = "Automatic backup"
ALLOWED_DAYS = (7, 15, 30)
DEFAULTS = {"days": 15, "auto": False, "hour": 2}
_RETRY_AFTER_FAILURE = datetime.timedelta(hours=6)
_TICK_SECONDS = 300
_reminder_cache = {"at": 0.0, "value": None}


# ------------------------------------------------------------------ running
def run_backup_job(app, actor_id, actor_name):
    """Build and email one backup and log the outcome. The caller must have
    acquired backup_lock; this releases it."""
    with app.app_context():
        automatic = actor_name == AUTO_ACTOR
        action, details = "BACKUP_EMAIL_FAILED", "The backup could not be created (see the server log)."
        try:
            max_bytes = int(app.config["BACKUP_EMAIL_MAX_MB"] * 1024 * 1024)
            backup_state["phase"] = "Packing up your data"
            data, filename, summary = build_backup_zip(max_bytes)
            backup_state["phase"] = "Sending the email"
            size_mb = summary["bytes"] / 1024 / 1024
            included = "database + CSVs" + (" + photos" if summary["uploads"] else "")
            body = f"""Attached is a full backup of the Heaven & Angel Scents system{' (made automatically on schedule)' if automatic else ''}.

File: {filename} ({size_mb:.1f} MB)
Contains: {included} - {summary['tables']} tables, {summary['rows']:,} rows.

database.sql restores everything:
  mysql -u <user> -p <database> < database.sql
  (or use Settings > Backup > Restore from a backup in the app)
The csv/ folder holds each table as a spreadsheet-friendly file.
MANIFEST.txt inside the zip lists row counts and restore steps.

KEEP THIS PRIVATE: it contains customer details and account password hashes.
Save it somewhere safe, then delete this email from shared or forwarded mailboxes.
"""
            ok, err = send_backup_email(
                zip_bytes=data, filename=filename,
                subject=f"Heaven & Angel Scents backup - {business_now().strftime('%b %d, %Y %I:%M %p')}",
                body=body,
            )
            if ok:
                action = "BACKUP_EMAIL"
                details = (f"{size_mb:.1f} MB, {summary['tables']} tables, {summary['rows']:,} rows"
                           + ("" if summary["uploads"] else ", photos not included")
                           + ("" if summary["csv"] else ", CSVs not included"))
            else:
                details = err or details
        except TooLargeError as exc:
            details = str(exc)
        except Exception:
            app.logger.exception("Database backup failed")
        finally:
            try:
                execute(
                    """INSERT INTO admin_actions (actor_user_id, actor_username, action, target, details)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (actor_id, actor_name, action, "database backup", details[:255]),
                )
            except Exception:
                app.logger.exception("Could not record the backup result")
            _reminder_cache["at"] = 0.0          # re-evaluate the reminder right away
            backup_state["phase"] = ""
            backup_lock.release()


# ----------------------------------------------------------------- schedule
def get_schedule():
    saved = appsettings.get_many(("backup_days", "backup_auto", "backup_hour"))
    try:
        days = int(saved.get("backup_days", DEFAULTS["days"]))
    except ValueError:
        days = DEFAULTS["days"]
    if days not in ALLOWED_DAYS:
        days = DEFAULTS["days"]
    try:
        hour = int(saved.get("backup_hour", DEFAULTS["hour"]))
    except ValueError:
        hour = DEFAULTS["hour"]
    return {"days": days, "auto": saved.get("backup_auto") == "1", "hour": min(max(hour, 0), 23)}


def save_schedule(days, auto, hour):
    appsettings.set_many({"backup_days": days, "backup_auto": "1" if auto else "0", "backup_hour": hour})
    _reminder_cache["at"] = 0.0


def _last_events():
    ok = query("""SELECT action_id, created_at FROM admin_actions WHERE action = 'BACKUP_EMAIL'
                  ORDER BY action_id DESC LIMIT 1""", fetchone=True)
    failed = query("""SELECT action_id, actor_username, details, created_at FROM admin_actions
                      WHERE action = 'BACKUP_EMAIL_FAILED' ORDER BY action_id DESC LIMIT 1""", fetchone=True)
    if failed and ok and failed["action_id"] < ok["action_id"]:
        failed = None                           # superseded by a later success
    return ok, failed


def overview(schedule=None):
    """Where the schedule stands right now."""
    schedule = schedule or get_schedule()
    now = business_now()
    ok, failed = _last_events()
    last_ok_at = ok["created_at"] if ok else None
    if last_ok_at:
        due_date = last_ok_at.date() + datetime.timedelta(days=schedule["days"])
        days_since = (now.date() - last_ok_at.date()).days
    else:
        due_date, days_since = now.date(), None
    overdue = now.date() >= due_date
    slot = datetime.datetime.combine(due_date, datetime.time(schedule["hour"]))
    return {
        **schedule,
        "last_ok_at": last_ok_at,
        "days_since": days_since,
        "overdue": overdue,
        "due_now": overdue and now.hour >= schedule["hour"],
        "next_run": max(slot, now),
        "failed": failed,
    }


def reminder():
    """What the banner needs, or None. Cached briefly: it is computed on
    every page render for an admin."""
    now = time.time()
    if now - _reminder_cache["at"] > 60:
        try:
            ov = overview()
            value = None
            auto_failed = ov["failed"] if (ov["auto"] and ov["failed"]) else None
            if ov["overdue"] or auto_failed:
                value = {
                    "days_since": ov["days_since"], "days": ov["days"], "auto": ov["auto"],
                    "failed": auto_failed["details"] if auto_failed else None,
                }
        except Exception:
            current_app.logger.exception("Could not work out the backup reminder")
            value = None
        _reminder_cache.update(at=now, value=value)
    return _reminder_cache["value"]


# ---------------------------------------------------------------- scheduler
def _tick(app):
    with app.app_context():
        if restore_running():
            return
        schedule = get_schedule()
        if not schedule["auto"] or not backup_email_configured(app.config):
            return
        ov = overview(schedule)
        if not ov["due_now"]:
            return
        failed = ov["failed"]
        if failed and failed["actor_username"] == AUTO_ACTOR \
                and business_now() - failed["created_at"] < _RETRY_AFTER_FAILURE:
            return                              # it failed recently; don't hammer the mail server

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK('ha_auto_backup', 0)")
        got = cur.fetchone()[0]
        cur.close()
        if got != 1:
            return                              # another worker has it
        try:
            if backup_lock.acquire(blocking=False):
                app.logger.info("Running the scheduled backup")
                run_backup_job(app, None, AUTO_ACTOR)     # releases backup_lock
        finally:
            cur = conn.cursor()
            cur.execute("SELECT RELEASE_LOCK('ha_auto_backup')")
            cur.fetchone()
            cur.close()


def start_scheduler(app):
    """Start the background thread that runs scheduled backups."""
    def loop():
        time.sleep(45)                          # let the app finish starting
        while True:
            try:
                _tick(app)
            except Exception:
                app.logger.exception("Backup scheduler tick failed")
            time.sleep(_TICK_SECONDS)

    threading.Thread(target=loop, name="backup-scheduler", daemon=True).start()
