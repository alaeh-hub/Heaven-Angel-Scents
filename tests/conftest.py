"""Shared fixtures for the integration test suite.

These tests hit real routes through Flask's test client against a real,
disposable MySQL database — this app has no ORM/repository layer to mock
around db.py's direct mysql-connector calls, so a real database is the
only way to exercise routes/branch.py, routes/admin.py, and
decorators.py the way production actually runs them.

Requires a reachable MySQL server (see config.py's own "stock XAMPP
install" default: localhost:3306, user root, empty password — override
via the usual MYSQL_HOST/PORT/USER/PASSWORD env vars/.env if yours
differs) and a `mysql` CLI binary somewhere findable (see
_find_mysql_client below) to load schema.sql, since it contains
DELIMITER-delimited stored-procedure migration blocks that
mysql-connector-python's own multi-statement execute can't parse.
Nothing here ever touches the real MYSQL_DB from your environment/.env —
every test runs against a separate, throwaway database (default
heaven_and_angel_scents_test) that's dropped and recreated once per test
session and dropped again when the session ends.
"""
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

TEST_DB_NAME = os.environ.get("TEST_MYSQL_DB", "heaven_and_angel_scents_test")

# Everything below must run before `config` (and therefore `app`) is
# imported anywhere — Config's class attributes read os.environ.get(...)
# once, at first import, so setting these later would have no effect.
# MYSQL_DB is force-set (never just defaulted) so tests can never end up
# pointed at whatever real database MYSQL_DB names in the environment or
# .env, no matter what else is configured there.
os.environ["MYSQL_DB"] = TEST_DB_NAME
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("FLASK_DEBUG", "1")
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-real-use")
os.environ.setdefault("PARTNER_PORTAL_SLUG", "pytest-portal-slug")
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("RATELIMIT_STORAGE_URI", "memory://")
# See config.py's RATELIMIT_ENABLED comment: without this, the fixed
# number of requests flask-limiter allows per window (e.g. login's
# "10 per minute") can get exhausted by the test suite itself well
# before any real abuse would, failing unrelated tests.
os.environ.setdefault("RATELIMIT_ENABLED", "0")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = REPO_ROOT / "schema.sql"


def _find_mysql_client():
    """Locate a `mysql` CLI binary. Checked in order: $MYSQL_CLIENT,
    PATH, then common XAMPP install locations (see config.py's own
    "stock XAMPP install" comment) since XAMPP doesn't put mysql.exe on
    PATH by default."""
    override = os.environ.get("MYSQL_CLIENT")
    if override:
        return override
    found = shutil.which("mysql")
    if found:
        return found
    for drive in "CDEF":
        candidate = Path(f"{drive}:/xampp/mysql/bin/mysql.exe")
        if candidate.is_file():
            return str(candidate)
    return None


def _mysql_admin_connection():
    import mysql.connector
    return mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", 3306)),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
    )


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Create a fresh `heaven_and_angel_scents_test` database from
    schema.sql once per test session, and drop it again afterwards.

    schema.sql hardcodes its own database name (`CREATE DATABASE IF NOT
    EXISTS heaven_and_angel_scents` / `USE heaven_and_angel_scents;`) —
    swapped out for the test database name in a throwaway copy rather
    than ever pointing this at the real one.
    """
    mysql_client = _find_mysql_client()
    if not mysql_client:
        pytest.skip(
            "No mysql client found (checked $MYSQL_CLIENT, PATH, and common XAMPP "
            "install paths) — needed to load schema.sql, which uses DELIMITER-based "
            "stored-procedure migration blocks that mysql-connector-python can't "
            "execute directly. Set MYSQL_CLIENT to your mysql binary's path to run "
            "these tests."
        )

    try:
        import mysql.connector  # noqa: F401  (checked separately from the
        # connection attempt below so a missing dependency gets its own,
        # much more actionable message instead of looking like a real
        # "can't reach the server" failure)
    except ImportError:
        pytest.skip(
            "mysql-connector-python isn't installed in this Python environment "
            f"({sys.executable}) — these tests need the project's own dependencies "
            "(requirements.txt + requirements-dev.txt). This usually means pytest "
            "was run outside the project's virtual environment: use "
            "`.venv\\Scripts\\python.exe -m pytest` (Windows) or "
            "`.venv/bin/python -m pytest` rather than a bare `pytest` command, "
            "which can resolve to a different, unrelated Python install on PATH."
        )

    try:
        admin_conn = _mysql_admin_connection()
    except Exception as exc:
        pytest.skip(f"Couldn't reach a MySQL server to run these tests against: {exc}")

    cur = admin_conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{TEST_DB_NAME}`")
    admin_conn.commit()
    cur.close()
    admin_conn.close()

    schema_sql = SCHEMA_SQL.read_text(encoding="utf-8")
    test_schema_sql = schema_sql.replace("heaven_and_angel_scents", TEST_DB_NAME)
    tmp_path = REPO_ROOT / f"_test_schema_{uuid.uuid4().hex}.sql"
    tmp_path.write_text(test_schema_sql, encoding="utf-8")
    try:
        cmd = [
            mysql_client,
            "-h", os.environ.get("MYSQL_HOST", "localhost"),
            "-P", str(os.environ.get("MYSQL_PORT", 3306)),
            "-u", os.environ.get("MYSQL_USER", "root"),
        ]
        password = os.environ.get("MYSQL_PASSWORD", "")
        if password:
            cmd.append(f"-p{password}")
        with open(tmp_path, "rb") as f:
            result = subprocess.run(cmd, stdin=f, capture_output=True)
        if result.returncode != 0:
            pytest.fail(
                "Failed to load schema.sql into the test database:\n"
                + result.stderr.decode(errors="replace")
            )
    finally:
        tmp_path.unlink(missing_ok=True)

    yield

    admin_conn = _mysql_admin_connection()
    cur = admin_conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{TEST_DB_NAME}`")
    admin_conn.commit()
    cur.close()
    admin_conn.close()


@pytest.fixture(scope="session")
def app():
    from app import create_app
    application = create_app()
    application.config.update(
        TESTING=True,
        # Flask-WTF's own documented pattern for testing CSRF-protected
        # forms — checked live per-request, not cached at CSRFProtect(app)
        # time, so setting it post-creation is enough.
        WTF_CSRF_ENABLED=False,
    )
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def sql():
    """A plain mysql-connector connection to the test database, separate
    from the app's own per-request connection (flask.g) — used by tests
    to set up fixture rows and assert on the DB's actual state after
    hitting a route, independent of whatever the route itself renders.

    autocommit=True deliberately, even though most callers (see
    factories.py) already call conn.commit() themselves after a write:
    without it, a bare SELECT with no matching commit/rollback (e.g. a
    factories.py getter called between a setup write and the route
    under test) leaves a REPEATABLE READ transaction open on this
    connection — and a later read on that same still-open transaction
    then sees the snapshot from *before* the route's own write
    committed on its own separate connection, even though the write
    genuinely happened. autocommit=True means every statement here
    (read or write) starts and ends its own transaction, so this
    connection is never left holding a stale snapshot across a route
    call in between.
    """
    import mysql.connector
    conn = mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int(os.environ.get("MYSQL_PORT", 3306)),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=TEST_DB_NAME,
        autocommit=True,
    )
    yield conn
    conn.close()
