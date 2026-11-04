import os
import tempfile
import threading
import time

from flask import (
    abort, current_app, jsonify, request, session,
)
from werkzeug.security import check_password_hash

from heaven_angel.db import execute, query
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.services import backup_jobs
from heaven_angel.services.backup import RestoreError, prepare_restore, restore_uploads, run_restore, save_safety_backup
from heaven_angel.services.backup_jobs import ALLOWED_DAYS
from heaven_angel.services.backup_jobs import backup_lock as _backup_lock
from heaven_angel.services.backup_jobs import backup_state as _backup_state
from heaven_angel.services.backup_jobs import run_backup_job as _backup_job
from heaven_angel.extensions import limiter
from heaven_angel.services.mailer import backup_email_configured, backup_recipient
from heaven_angel.utils import (
    business_today,
)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------------------
# Settings -> Backup: snapshot the database and email it to HQ (see
# backup.py for the format). Building + sending can take a while, so the
# POST only starts a background thread; the Settings dialog polls
# backup_status(), which reads the outcome back from admin_actions (so it
# doesn't depend on which worker handled the POST).
# ---------------------------------------------------------------------------
def _mask_email(address):
    name, _, domain = (address or "").partition("@")
    if not domain:
        return ""
    return f"{name[:1]}{'*' * max(2, len(name) - 1)}@{domain}"


@bp.route("/backup/email", methods=["POST"])
@limiter.limit("12 per hour")
@admin_required
def backup_email():
    if not backup_email_configured(current_app.config):
        return jsonify(error="Email isn't set up yet - add MAIL_SERVER, MAIL_DEFAULT_SENDER and "
                             "PARTNER_INQUIRY_NOTIFY_EMAIL (or BACKUP_NOTIFY_EMAIL) to .env."), 400
    if not _backup_lock.acquire(blocking=False):
        return jsonify(error="A backup is already running."), 409
    log_action("BACKUP_REQUESTED", "database backup")
    _backup_state["phase"] = "Starting"
    try:
        threading.Thread(
            target=_backup_job,
            args=(current_app._get_current_object(), session.get("user_id"), session.get("username")),
            daemon=True,
        ).start()
    except Exception:
        _backup_lock.release()
        raise
    return jsonify(state="running"), 202


@bp.route("/backup/status")
@admin_required
def backup_status():
    # Newest success, and the newest failure only if it is more recent than
    # that success - an old failure must not keep showing once a later
    # backup worked (or after a restore).
    rows = query(
        """SELECT action_id, actor_username, action, details, created_at
           FROM admin_actions WHERE action IN ('BACKUP_EMAIL', 'BACKUP_EMAIL_FAILED')
           ORDER BY action_id DESC LIMIT 25"""
    )

    def shape(r):
        return {"id": r["action_id"], "by": r["actor_username"], "details": r["details"],
                "at": r["created_at"].strftime("%b %d, %Y %I:%M %p")}

    last_ok = next((r for r in rows if r["action"] == "BACKUP_EMAIL"), None)
    last_failed = next((r for r in rows if r["action"] == "BACKUP_EMAIL_FAILED"
                        and (not last_ok or r["action_id"] > last_ok["action_id"])), None)
    cfg = current_app.config
    ov = backup_jobs.overview()
    return jsonify(
        configured=backup_email_configured(cfg),
        recipient=_mask_email(backup_recipient(cfg)),
        running=_backup_lock.locked(),
        phase=_backup_state["phase"],
        latest_id=rows[0]["action_id"] if rows else None,
        last_ok=shape(last_ok) if last_ok else None,
        last_failed=shape(last_failed) if last_failed else None,
        schedule={
            "days": ov["days"], "auto": ov["auto"], "hour": ov["hour"],
            "overdue": ov["overdue"], "days_since": ov["days_since"],
            "next_run": ov["next_run"].strftime("%a, %b %d, %Y %I:%M %p") if ov["auto"] else None,
            "run_soon": ov["auto"] and ov["due_now"],
        },
    )


@bp.route("/backup/schedule", methods=["POST"])
@limiter.limit("60 per hour")
@admin_required
def backup_schedule_save():
    data = request.get_json(silent=True) or {}
    try:
        days, hour = int(data.get("days")), int(data.get("hour"))
    except (TypeError, ValueError):
        return jsonify(error="Pick how often and what time."), 400
    auto = bool(data.get("auto"))
    if days not in ALLOWED_DAYS or not 0 <= hour <= 23:
        return jsonify(error="That schedule is not one of the choices."), 400
    if auto and not backup_email_configured(current_app.config):
        return jsonify(error="Email isn't set up yet, so automatic backups can't be turned on."), 400
    backup_jobs.save_schedule(days, auto, hour)
    log_action("BACKUP_SCHEDULE", "database backup",
               f"every {days} days, automatic {'on at %02d:00' % hour if auto else 'off'}")
    ov = backup_jobs.overview()
    return jsonify(
        ok=True,
        next_run=ov["next_run"].strftime("%a, %b %d, %Y %I:%M %p") if ov["auto"] else None,
        run_soon=ov["auto"] and ov["due_now"],
    )


@bp.route("/backup/snooze", methods=["POST"])
@admin_required
def backup_snooze():
    """Hide the reminder banner for the rest of today (this browser session)."""
    session["backup_snooze_date"] = business_today().isoformat()
    return jsonify(ok=True)


@bp.app_context_processor
def inject_backup_reminder():
    """Drives the reminder banner and the dot on the sidebar's Settings
    button: only for a signed-in admin, never while snoozed today."""
    if session.get("role") != "Admin" or request.endpoint in (None, "static"):
        return {}
    if session.get("backup_snooze_date") == business_today().isoformat():
        return {}
    try:
        return {"backup_reminder": backup_jobs.reminder()}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Settings -> Backup -> Restore. Replaces the database with the contents of
# a backup made by Email backup (see backup.py). Guarded by: admin only,
# the admin's password + a typed phrase (a short-lived token in the
# session), a full validation pass before anything is touched, an
# automatic safety copy of the current data on disk, and a lock so only
# one runs at a time. While it runs every other request gets a 503, since
# tables are being dropped and recreated underneath them.
# ---------------------------------------------------------------------------
_restore_lock = threading.Lock()


_upload_lock = threading.Lock()


_upload = {"path": None, "received": 0, "total": 0, "user": None}


_restore_state = {"running": False, "phase": "", "error": None, "done": False, "summary": ""}


backup_jobs.set_restore_probe(lambda: _restore_state["running"])


_RESTORE_PHRASE = "RESTORE"


_RESTORE_MAX_BYTES = 250 * 1024 * 1024


@bp.before_app_request
def _hold_requests_during_restore():
    if _restore_state["running"] and request.endpoint not in ("admin.restore_status", "static"):
        return ("The system is being restored from a backup. Please wait a minute, then refresh.", 503)


def _restore_job(app, path, actor_id, actor_name):
    with app.app_context():
        zf = None
        safety = None
        action, details = "RESTORE_FAILED", "The restore failed (see the server log)."
        try:
            _restore_state.update(phase="Checking the backup file", error=None, done=False, summary="")
            statements, tables, zf = prepare_restore(path)
            _restore_state["phase"] = "Saving a safety copy of the current data"
            safety = save_safety_backup()
            _restore_state["phase"] = "Restoring the database"
            run_restore(statements)
            photos = 0
            try:
                _restore_state["phase"] = "Restoring product photos"
                photos = restore_uploads(zf)
            except Exception:
                app.logger.exception("Photos could not be restored")
            action = "RESTORE_OK"
            details = f"{len(tables)} tables restored, {photos} photos; previous data saved as {os.path.basename(safety)}"
            _restore_state["summary"] = details
        except RestoreError as exc:
            details = str(exc)
            _restore_state["error"] = details
        except Exception:
            app.logger.exception("Database restore failed")
            details = ("The restore stopped part-way."
                       + (f" Your previous data is saved as {os.path.basename(safety)} in the backups folder." if safety else ""))
            _restore_state["error"] = details
        finally:
            if zf:
                zf.close()
            try:
                os.remove(path)
            except OSError:
                pass
            try:
                execute(
                    """INSERT INTO admin_actions (actor_user_id, actor_username, action, target, details)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (actor_id, actor_name, action, "database restore", details[:255]),
                )
            except Exception:
                app.logger.exception("Could not record the restore result")
            _restore_state.update(running=False, done=True, phase="")
            _restore_lock.release()


@bp.route("/restore/verify", methods=["POST"])
@limiter.limit("6 per hour")
@admin_required
def restore_verify():
    data = request.get_json(silent=True) or {}
    user = query("SELECT password_hash FROM users WHERE user_id = %s", (session["user_id"],), fetchone=True)
    if data.get("phrase") != _RESTORE_PHRASE:
        return jsonify(error=f"Type {_RESTORE_PHRASE} exactly to confirm."), 400
    if not user or not check_password_hash(user["password_hash"], data.get("password") or ""):
        log_action("RESTORE_DENIED", "database restore", "wrong password")
        return jsonify(error="That password is not correct."), 403
    session["restore_ok_until"] = time.time() + 300
    return jsonify(ok=True)


@bp.route("/restore/chunk", methods=["POST"])
@admin_required
def restore_chunk():
    # The backup is sent in ~4 MB pieces (raw bodies, CSRF token in the
    # X-CSRFToken header): the app-wide request limit is far smaller than a
    # backup, and CSRF protection reads every request body up to that
    # limit, so one big upload cannot get through. Pieces must arrive in
    # order; the last one starts the restore.
    until = session.get("restore_ok_until", 0)
    if time.time() > until:
        return jsonify(error="Confirm your password again, then upload."), 403
    session["restore_ok_until"] = time.time() + 120      # keep a slow upload alive

    try:
        offset = int(request.args.get("offset", ""))
        total = int(request.args.get("total", ""))
    except ValueError:
        return jsonify(error="Bad upload request."), 400
    if total <= 0 or total > _RESTORE_MAX_BYTES or offset < 0:
        return jsonify(error="That file is too large to restore here."), 413
    chunk = request.get_data()
    if not chunk or offset + len(chunk) > total:
        return jsonify(error="Bad upload piece."), 400

    with _upload_lock:
        if offset == 0:
            if _upload["path"] and os.path.exists(_upload["path"]):
                os.remove(_upload["path"])
            fd, path = tempfile.mkstemp(prefix="restore-", suffix=".upload")
            os.close(fd)
            _upload.update(path=path, received=0, total=total, user=session.get("user_id"))
        if (not _upload["path"] or _upload["received"] != offset or _upload["total"] != total
                or _upload["user"] != session.get("user_id")):
            return jsonify(error="The upload got out of step - start again."), 409
        with open(_upload["path"], "ab") as out:
            out.write(chunk)
        _upload["received"] += len(chunk)
        if _upload["received"] < total:
            return jsonify(received=_upload["received"])

        # Complete: hand it to the restore job.
        path, _upload["path"] = _upload["path"], None
        session.pop("restore_ok_until", None)
        if not _restore_lock.acquire(blocking=False):
            os.remove(path)
            return jsonify(error="A restore is already running."), 409
        log_action("RESTORE_STARTED", "database restore", f"{total / 1024 / 1024:.1f} MB uploaded")
        _restore_state.update(running=True, done=False, error=None, summary="", phase="Starting")
        try:
            threading.Thread(
                target=_restore_job,
                args=(current_app._get_current_object(), path, session.get("user_id"), session.get("username")),
                daemon=True,
            ).start()
        except Exception:
            _restore_state["running"] = False
            _restore_lock.release()
            raise
    return jsonify(state="running"), 202


@bp.route("/restore/status")
def restore_status():
    # Session-only check on purpose: mid-restore the users table is being
    # rebuilt, so the usual database-backed admin check could fail.
    if session.get("role") != "Admin":
        abort(403)
    return jsonify(
        running=_restore_state["running"], done=_restore_state["done"],
        phase=_restore_state["phase"], error=_restore_state["error"], summary=_restore_state["summary"],
    )
