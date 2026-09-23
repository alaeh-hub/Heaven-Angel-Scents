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


def make_partner(sql, partner_type="Distributor", name=None):
    name = name or f"Test Partner {unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO partners (partner_type, partner_name, contact_person, phone, email, address)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (partner_type, name, "Test Contact", "0917 000 0000", "test@example.com", "Test Address"),
    )
    sql.commit()
    partner_id = cur.lastrowid
    cur.close()
    return partner_id


def make_package(sql, name=None, discount_percent="10.00", partner_scope="Both"):
    name = name or f"Test Package {unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO packages (package_name, description, partner_scope, discount_percent)
           VALUES (%s, %s, %s, %s)""",
        (name, "Test description", partner_scope, discount_percent),
    )
    sql.commit()
    package_id = cur.lastrowid
    cur.close()
    return package_id


def make_partner_inquiry(sql, partner_type="Distributor", company_name=None,
                         package_name_snapshot="Test Package", status="New"):
    company_name = company_name or f"Test Company {unique_suffix()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO partner_inquiries
               (partner_type, company_name, contact_person, phone, email,
                package_name_snapshot, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (partner_type, company_name, "Test Contact", "0917 000 0000",
         "test@example.com", package_name_snapshot, status),
    )
    sql.commit()
    inquiry_id = cur.lastrowid
    cur.close()
    return inquiry_id


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


def make_raw_material(sql, unit="Gram", package_qty="100.000", package_cost="100.00"):
    """Insert a raw material the way admin.py's materials() would — a
    purchase log entry, cost_per_unit worked out from package_cost/
    package_qty, and stock_qty seeded to package_qty (a freshly added
    material starts with everything it was just bought at fully in
    stock — see raw_materials in schema.sql). Nothing deducts from
    stock_qty except a bulk batch actually consuming it (see
    make_bulk_batch()/create_bulk_batch()).
    """
    name = f"Test Material {unique_suffix()}"
    cost_per_unit = float(package_cost) / float(package_qty)
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO raw_materials
               (material_name, unit, purchase_mode, package_qty, package_cost, cost_per_unit, stock_qty)
           VALUES (%s, %s, 'Package', %s, %s, %s, %s)""",
        (name, unit, package_qty, package_cost, cost_per_unit, package_qty),
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


PRODUCTION_URL = "/admin/production"


def log_production(client, sku, qty_produced, batch_code=None, bulk_batch_id=None,
                    rate_per_ml=None, **kwargs):
    """POST to admin.production() the way the "Log a production run" form
    does. A Bottled size now requires bulk_batch_id (its real cost/mL
    prices the run — see bulk_batches in schema.sql); Bulk/Refill instead
    requires rate_per_ml. Callers pass whichever one applies to their
    product's category.
    """
    token = get_form_token(client, PRODUCTION_URL)
    data = {"form_token": token, "sku": sku, "qty_produced": str(qty_produced)}
    if batch_code:
        data["batch_code"] = batch_code
    if bulk_batch_id is not None:
        data["bulk_batch_id"] = str(bulk_batch_id)
    if rate_per_ml is not None:
        data["rate_per_ml"] = str(rate_per_ml)
    return client.post(PRODUCTION_URL, data=data, **kwargs)


def make_bulk_batch(sql, items, input_qty="1000.000", input_unit="Milliliter", scent_name=None):
    """Insert a bulk batch directly (bypassing create_bulk_batch()'s own
    route/transaction, but matching its actual cost math) for tests that
    need an existing batch to bottle from rather than testing batch
    creation itself. `items` is a list of (material_id, qty_used,
    cost_per_unit) triples. Unlike the real route, this does NOT deduct
    raw_materials.stock_qty — tests covering that deduction go through
    create_bulk_batch() directly instead.
    """
    ml_per_unit = {"Milliliter": 1, "Liter": 1000, "Gallon": 3785.411784}
    total_volume_ml = float(input_qty) * ml_per_unit[input_unit]
    total_cost = sum(float(qty) * float(cost) for _, qty, cost in items)
    cost_per_ml = total_cost / total_volume_ml if total_volume_ml else 0

    cur = sql.cursor()
    cur.execute(
        """INSERT INTO bulk_batches
               (scent_name, input_qty, input_unit, total_volume_ml, total_cost, cost_per_ml, remaining_ml)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (scent_name or f"Test Batch {unique_suffix()}", input_qty, input_unit,
         total_volume_ml, total_cost, cost_per_ml, total_volume_ml),
    )
    batch_id = cur.lastrowid
    for material_id, qty_used, cost_per_unit in items:
        # qty_used_unit is stored alongside qty_used/cost_per_unit_snapshot
        # (see migration 39 in schema.sql) — tests here always give amounts
        # already in the material's own purchase unit, so use that.
        cur.execute("SELECT unit FROM raw_materials WHERE material_id = %s", (material_id,))
        qty_used_unit = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO bulk_batch_materials
                   (batch_id, material_id, qty_used, qty_used_unit, cost_per_unit_snapshot, line_cost)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (batch_id, material_id, qty_used, qty_used_unit, cost_per_unit,
             float(qty_used) * float(cost_per_unit)),
        )
    sql.commit()
    cur.close()
    return batch_id


def set_unit_cogs(sql, unit, base_cost_per_unit):
    """Set a Bottled packaging size's flat base cost of goods directly
    (bypassing save_unit_cogs()'s own route, but matching its upsert
    semantics) — see unit_cogs_settings in schema.sql. This is what
    production() logs as cogs_per_unit for that size, independent of
    whichever bulk batch a run draws from (see make_bulk_batch()).
    """
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO unit_cogs_settings (unit, base_cost_per_unit) VALUES (%s, %s)
           ON DUPLICATE KEY UPDATE base_cost_per_unit = VALUES(base_cost_per_unit)""",
        (unit, base_cost_per_unit),
    )
    sql.commit()
    cur.close()


def get_bulk_batch(sql, batch_id):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT * FROM bulk_batches WHERE batch_id = %s", (batch_id,))
    row = cur.fetchone()
    cur.close()
    return row


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
