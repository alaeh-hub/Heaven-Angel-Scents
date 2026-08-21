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
    """Run an INSERT/UPDATE/DELETE. Returns (lastrowid, rowcount)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params or ())
    lastrowid, rowcount = cur.lastrowid, cur.rowcount
    if commit:
        conn.commit()
    cur.close()
    return lastrowid, rowcount


def init_app(app):
    app.teardown_appcontext(close_db)
