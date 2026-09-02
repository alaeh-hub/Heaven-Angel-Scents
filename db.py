import contextlib

import mysql.connector
from flask import current_app, g


def get_db():
    """Return a request-scoped MySQL connection, opening one if needed."""
    if "db" not in g:
        g.db = mysql.connector.connect(
            host=current_app.config["MYSQL_HOST"],
            port=current_app.config["MYSQL_PORT"],
            user=current_app.config["MYSQL_USER"],
            password=current_app.config["MYSQL_PASSWORD"],
            database=current_app.config["MYSQL_DB"],
            autocommit=False,
        )
        cur = g.db.cursor()
        cur.execute("SET time_zone = '+08:00'")
        cur.close()
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, params=None, fetchone=False, dictionary=True):
    """Run a SELECT and return rows (list of dicts by default)."""
    conn = get_db()
    cur = conn.cursor(dictionary=dictionary)
    cur.execute(sql, params or ())
    result = cur.fetchone() if fetchone else cur.fetchall()
    cur.close()
    return result


def execute(sql, params=None, commit=True):
    """Run an INSERT/UPDATE/DELETE. Returns (lastrowid, rowcount).

    Pass commit=False when this call is one step inside a `with
    transaction():` block — the block's own commit/rollback is what
    should decide whether the write sticks, not this call.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params or ())
    lastrowid, rowcount = cur.lastrowid, cur.rowcount
    if commit:
        conn.commit()
    cur.close()
    return lastrowid, rowcount


class TransactionAborted(Exception):
    """Raise this from inside a `with transaction():` block to abort it
    with a specific, user-facing message — instead of `return`ing
    directly out of the block.

    A bare `return` inside a `with` block is NOT an exception as far as
    the `with` statement is concerned: the block is considered to have
    exited "normally", so transaction()'s `except Exception:` branch
    below never runs, and the `conn.commit()` right after `yield` still
    fires. Concretely: if a per-item loop writes rows for item 1, then
    hits a validation failure on item 2 and does
    `return redirect(...)` to bail out, item 1's writes get committed
    anyway — a partial, half-applied transaction — even though the
    whole operation was meant to be rejected.

    Raising TransactionAborted (or any Exception subclass) instead
    forces transaction()'s except branch, which rolls back before
    re-raising, so nothing partial is ever committed. Catch this
    specific type at the call site, outside the `with` block, to show
    the message it carries; a plain `except Exception:` around the same
    call still catches it too (it IS an Exception), so existing generic
    error handling doesn't need to change — just add a more specific
    `except TransactionAborted as err: flash(str(err), ...)` before it
    if you want the precise message surfaced instead of a generic one.
    """


@contextlib.contextmanager
def transaction():
    """Group several writes into one atomic unit on the request's connection.


    Usage:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT ... FOR UPDATE", (...))
            row = cur.fetchone()
            cur.execute("UPDATE ...", (...))
            cur.execute("INSERT INTO stock_movement_logs ...", (...))
            cur.close()

    Or, for simpler cases, call the existing execute()/query() helpers
    inside the block with commit=False — they share the same
    request-scoped connection, so nothing is actually persisted until
    this context manager commits at the end.

    If any exception escapes the block, everything done inside it is
    rolled back and the exception re-raises to the caller (the route
    is expected to catch it, flash a message, and redirect).
    """
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_app(app):
    app.teardown_appcontext(close_db)
