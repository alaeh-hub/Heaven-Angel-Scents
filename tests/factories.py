"""Tiny row-builders for tests — each inserts one row directly via a raw
DB connection (the `sql` fixture in conftest.py) and returns what a test
needs to act on it. Every name is uuid-suffixed so tests never collide
with each other or with schema.sql's own seeded branches (1/2/3), even
though they all share one test database for the whole session (see
conftest.py's `_test_database` fixture) — nothing here ever cleans up
after itself, by design: the entire test database is dropped at the end
of the session, so leftover rows are harmless as long as every test only
ever looks at its own uniquely-named rows.
"""
import uuid

from werkzeug.security import generate_password_hash


def unique_suffix():
    return uuid.uuid4().hex[:8]


def make_branch(sql, name=None, is_hq=False):
    name = name or f"Test Branch {unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        "INSERT INTO branches (branch_name, location, is_hq) VALUES (%s, %s, %s)",
        (name, "Nowhere", is_hq),
    )
    sql.commit()
    branch_id = cur.lastrowid
    cur.close()
    return branch_id


def make_product(sql, price="100.00", unit="50ML", variant="Unisex"):
    sku = f"TST-{unique_suffix().upper()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO products (sku, item_name, variant, unit, price)
           VALUES (%s, %s, %s, %s, %s)""",
        (sku, f"Test Product {sku}", variant, unit, price),
    )
    sql.commit()
    cur.close()
    return sku


def make_inventory(sql, branch_id, sku, stock_qty, reorder_level=10):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO branch_inventory (branch_id, sku, stock_qty, reorder_level)
           VALUES (%s, %s, %s, %s)""",
        (branch_id, sku, stock_qty, reorder_level),
    )
    sql.commit()
    cur.close()


def get_inventory_qty(sql, branch_id, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s",
        (branch_id, sku),
    )
    row = cur.fetchone()
    cur.close()
    return row["stock_qty"] if row else None


def make_user(sql, role, branch_id=None, password="Test-Passw0rd!",
              is_active=True, must_change_password=False):
    username = f"test_{role.lower()}_{unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO users (username, password_hash, role, branch_id, is_active, must_change_password)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (username, generate_password_hash(password), role, branch_id,
         is_active, must_change_password),
    )
    sql.commit()
    user_id = cur.lastrowid
    cur.close()
    return {"user_id": user_id, "username": username, "password": password, "role": role}


def count_sales(sql, branch_id, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT COUNT(*) AS c FROM sales WHERE branch_id = %s AND sku = %s",
        (branch_id, sku),
    )
    row = cur.fetchone()
    cur.close()
    return row["c"]


def last_movement_log(sql, branch_id, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        """SELECT * FROM stock_movement_logs
           WHERE branch_id = %s AND sku = %s
           ORDER BY log_id DESC LIMIT 1""",
        (branch_id, sku),
    )
    row = cur.fetchone()
    cur.close()
    return row


def set_account_active(sql, user_id, is_active):
    cur = sql.cursor()
    cur.execute("UPDATE users SET is_active = %s WHERE user_id = %s",
                (is_active, user_id))
    sql.commit()
    cur.close()


def login(client, username, password, login_type):
    """POST to /login as this test module's own client, following no
    redirects — callers assert on the redirect itself (302 + Location)
    or chain a follow-up request."""
    return client.post(
        "/login",
        data={"username": username, "password": password, "login_type": login_type},
    )
