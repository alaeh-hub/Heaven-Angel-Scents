from flask import current_app, session

from db import execute, query


def ensure_table():
    """No-op once admin_actions exists (the normal case). Creates it,
    with a warning, only as a fallback for a database that hasn't
    picked up the schema.sql change yet — so audit logging degrades
    gracefully instead of raising on every admin action.
    """
    exists = query(
        """SELECT 1 FROM information_schema.tables
           WHERE table_schema = DATABASE() AND table_name = 'admin_actions'""",
        fetchone=True,
    )
    if exists:
        return

    current_app.logger.warning(
        "admin_actions table not found — creating it now as a fallback. "
        "This database predates admin_actions being added to schema.sql; "
        "re-run schema.sql against it (or apply just the new table) to pick "
        "it up the normal way and avoid relying on this fallback."
    )
    execute(
        """CREATE TABLE IF NOT EXISTS admin_actions (
               action_id BIGINT AUTO_INCREMENT PRIMARY KEY,
               actor_user_id INT NULL,
               actor_username VARCHAR(80) NULL,
               action VARCHAR(50) NOT NULL,
               target VARCHAR(120) NULL,
               details VARCHAR(255) NULL,
               created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
               INDEX idx_admin_actions_created_at (created_at)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
    )


def log_action(action, target=None, details=None):
    """Record one admin action. Best-effort: a failed audit write should
    never be the reason the action it's recording fails, so this only
    logs to the app logger on failure rather than raising."""
    try:
        execute(
            """INSERT INTO admin_actions (actor_user_id, actor_username, action, target, details)
               VALUES (%s, %s, %s, %s, %s)""",
            (session.get("user_id"), session.get(
                "username"), action, target, details),
        )
    except Exception:
        current_app.logger.exception(
            "Failed to write audit log entry: action=%s target=%s", action, target
        )
