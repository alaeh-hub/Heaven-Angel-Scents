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
import re
import uuid

from werkzeug.security import generate_password_hash

_FORM_TOKEN_RE = re.compile(rb'name="form_token" value="([^"]*)"')


def get_form_token(client, endpoint):
    """GET `endpoint` and pull out the hidden form_token field a route
    protected by utils.issue_form_token()/consume_form_token() renders.

    Needed before POSTing to any such route in a test: the token is
    single-use and tied to the session by the GET that rendered it (see
    utils.py's module docstring on single-use form tokens) — a POST
    with no token, or last test's stale one, is now rejected the same
    way a real double-submit would be.
    """
    resp = client.get(endpoint)
    match = _FORM_TOKEN_RE.search(resp.data)
    assert match, f"No form_token hidden field found on {endpoint}"
    return match.group(1).decode("utf-8")


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


def make_raw_material(sql, unit="Gram", package_qty="100.000", package_cost="100.00",
                       stock_qty=None):
    """Insert a raw material the way admin.py's materials() would —
    cost_per_unit worked out from package_cost/package_qty, stock_qty
    defaulting to the full package_qty (a freshly-added, unused material)
    unless a test wants to start it partially drawn down.
    """
    name = f"Test Material {unique_suffix()}"
    cost_per_unit = float(package_cost) / float(package_qty)
    if stock_qty is None:
        stock_qty = package_qty
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO raw_materials
               (material_name, unit, purchase_mode, package_qty, package_cost, cost_per_unit, stock_qty)
           VALUES (%s, %s, 'Package', %s, %s, %s, %s)""",
        (name, unit, package_qty, package_cost, cost_per_unit, stock_qty),
    )
    sql.commit()
    material_id = cur.lastrowid
    cur.close()
    return material_id


def get_raw_material(sql, material_id):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT * FROM raw_materials WHERE material_id = %s", (material_id,))
    row = cur.fetchone()
    cur.close()
    return row


def make_formula(sql, unit, items):
    """Set a packaging size's formula directly (bypassing save_formula()'s
    own route/CSRF/form plumbing, but matching its actual replace
    semantics) — `items` is a list of (material_id, qty_per_unit) pairs.

    Formulas are shared per unit (85ML, 50ML, ...), a small fixed set —
    not a fresh, uniquely-named row per test the way make_product()'s
    SKUs are. Always deleting this unit's existing rows before inserting
    the new ones (same as save_formula() itself) keeps tests that reuse
    a unit deterministic regardless of what an earlier test in the same
    session left behind; passing an empty `items` list clears it back to
    "no formula set" for a test that specifically needs that state.
    """
    cur = sql.cursor()
    cur.execute("DELETE FROM unit_formula_items WHERE unit = %s", (unit,))
    for material_id, qty_per_unit in items:
        cur.execute(
            """INSERT INTO unit_formula_items (unit, material_id, qty_per_unit)
               VALUES (%s, %s, %s)""",
            (unit, material_id, qty_per_unit),
        )
    sql.commit()
    cur.close()


def get_cogs_logs(sql, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM cogs_logs WHERE sku = %s ORDER BY cogs_log_id", (sku,))
    rows = cur.fetchall()
    cur.close()
    return rows


def make_stock_request(sql, branch_id, items, status="Pending"):
    """Insert a delivery header + line items directly (bypassing
    request_stock() itself) so dispatch/receive/reject tests can set up
    a request already in whatever state (and with whatever
    dispatched_qty/received_qty already filled in) they need to exercise,
    without going through the full multi-step request -> dispatch ->
    receive flow just to get there.

    Each entry in `items` is a dict with at least sku/requested_qty;
    unit_price/dispatched_qty/received_qty/damaged_qty all default the
    same way a fresh Pending request line would. Returns request_id.
    """
    cur = sql.cursor()
    cur.execute(
        "INSERT INTO stock_requests (branch_id, delivery_number, status) VALUES (%s, %s, %s)",
        (branch_id, f"DR-TEST-{unique_suffix()}", status),
    )
    request_id = cur.lastrowid
    for item in items:
        cur.execute(
            """INSERT INTO stock_request_items
               (request_id, sku, requested_qty, unit_price, dispatched_qty, received_qty, damaged_qty)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (request_id, item["sku"], item["requested_qty"],
             item.get("unit_price", "0.00"), item.get("dispatched_qty"),
             item.get("received_qty"), item.get("damaged_qty", 0)),
        )
    sql.commit()
    cur.close()
    return request_id


def get_request_status(sql, request_id):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT status FROM stock_requests WHERE request_id = %s", (request_id,))
    row = cur.fetchone()
    cur.close()
    return row["status"] if row else None


def get_request_item(sql, request_id, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM stock_request_items WHERE request_id = %s AND sku = %s",
        (request_id, sku),
    )
    row = cur.fetchone()
    cur.close()
    return row


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
