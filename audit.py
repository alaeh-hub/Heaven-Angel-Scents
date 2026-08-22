"""Lightweight audit trail for admin-side actions that aren't already
covered by stock_movement_logs (which handles inventory/stock events).

Covers: creating/deactivating/resetting a login account, adding or
discontinuing a product, adding a branch — anything where the only
previous record was a flash message that vanished after a few seconds.
"""
from flask import current_app, session

from db import execute


def ensure_table():
    """Create the admin_actions table if it doesn't exist yet.

    Called once at app startup (see app.py) so a fresh deployment picks
    this up automatically without needing a separate migration step or
    touching schema.sql.
    """
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
            (session.get("user_id"), session.get("username"), action, target, details),
        )
    except Exception:
        current_app.logger.exception(
            "Failed to write audit log entry: action=%s target=%s", action, target
        )
