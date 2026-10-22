"""Tiny key/value store for settings an admin changes from the UI (today:
the backup schedule). One row per key in `app_settings`.

The table is also in schema.sql; like audit.py's admin_actions it is
created on first use if a database predates that, so nothing breaks on an
un-migrated install.
"""
from heaven_angel.db import execute, query

_ready = False


def _ensure():
    global _ready
    if _ready:
        return
    execute(
        """CREATE TABLE IF NOT EXISTS app_settings (
               setting_key   VARCHAR(60) PRIMARY KEY,
               setting_value VARCHAR(255) NOT NULL,
               updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
    )
    _ready = True


def get_many(keys):
    """{key: value} for whichever of `keys` have been saved."""
    _ensure()
    marks = ", ".join(["%s"] * len(keys))
    rows = query(f"SELECT setting_key, setting_value FROM app_settings WHERE setting_key IN ({marks})", tuple(keys))
    return {r["setting_key"]: r["setting_value"] for r in rows}


def set_many(values):
    _ensure()
    for key, value in values.items():
        execute(
            """INSERT INTO app_settings (setting_key, setting_value) VALUES (%s, %s)
               ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)""",
            (key, str(value)),
        )
