"""Two branch/admin rules added together:

- Branches can only request bottled products from HQ, never BULK
  (routes/branch.py's request_stock()), enforced server-side.
- The development-only Clear Data page (routes/admin.py's clear_data())
  is a 404 unless ALLOW_DATA_RESET is on, needs the exact confirmation
  phrase, and keeps accounts/branches while emptying everything else.
"""
from factories import (get_form_token, login, make_branch, make_inventory,
                       make_product, make_user)

REQUEST_STOCK_URL = "/branch/request-stock"


def _signed_in_branch(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    return branch_id


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def _count(sql, table, where="1=1", params=()):
    cur = sql.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)
    (n,) = cur.fetchone()
    cur.close()
    return n


# ---------------------------------------------------------------- bottle-only requests
def test_request_stock_page_lists_bottles_but_not_bulk(client, sql):
    _signed_in_branch(client, sql)
    bottle = make_product(sql, unit="50ML")
    bulk = make_product(sql, unit="BULK")

    html = client.get(REQUEST_STOCK_URL).get_data(as_text=True)

    assert bottle in html
    assert bulk not in html


def test_request_stock_rejects_a_tampered_bulk_sku(client, sql):
    branch_id = _signed_in_branch(client, sql)
    bulk = make_product(sql, unit="BULK")
    token = get_form_token(client, REQUEST_STOCK_URL)

    resp = client.post(REQUEST_STOCK_URL, data={
        "sku[]": [bulk], "requested_qty[]": ["2"], "form_token": token})

    assert resp.status_code == 302
    assert _count(sql, "stock_requests", "branch_id = %s", (branch_id,)) == 0


def test_dashboard_hides_reorder_for_low_stock_bulk(client, sql):
    branch_id = _signed_in_branch(client, sql)
    bulk = make_product(sql, unit="BULK")
    make_inventory(sql, branch_id, bulk, stock_qty=0, reorder_level=5)

    html = client.get("/branch/").get_data(as_text=True)

    assert f"sku={bulk}" not in html
    assert "reorder all" not in html


# ---------------------------------------------------------------- clear data
def test_clear_data_is_404_when_not_enabled(app, client, sql, monkeypatch):
    monkeypatch.setitem(app.config, "ALLOW_DATA_RESET", False)
    _signed_in_admin(client, sql)

    assert client.get("/admin/clear-data").status_code == 404
    assert client.post("/admin/clear-data", data={"confirm": "CLEAR ALL DATA"}).status_code == 404


def test_clear_data_is_admin_only(app, client, sql, monkeypatch):
    monkeypatch.setitem(app.config, "ALLOW_DATA_RESET", True)
    _signed_in_branch(client, sql)

    assert client.get("/admin/clear-data").status_code in (302, 403)


def test_clear_data_needs_the_exact_phrase(app, client, sql, monkeypatch):
    monkeypatch.setitem(app.config, "ALLOW_DATA_RESET", True)
    _signed_in_admin(client, sql)
    sku = make_product(sql)

    client.post("/admin/clear-data", data={"confirm": "clear all data"})

    assert _count(sql, "products", "sku = %s", (sku,)) == 1


def test_clear_data_empties_business_tables_but_keeps_accounts(app, client, sql, monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, "ALLOW_DATA_RESET", True)
    # Never touch the real static/uploads during tests.
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    photos = tmp_path / "uploads" / "products"
    photos.mkdir(parents=True)
    (photos / "a.jpg").write_bytes(b"x")

    admin = _signed_in_admin(client, sql)
    branch_id = make_branch(sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=3)

    resp = client.post("/admin/clear-data", data={"confirm": "CLEAR ALL DATA"})

    assert resp.status_code == 302
    assert _count(sql, "products") == 0
    assert _count(sql, "branch_inventory") == 0
    assert _count(sql, "users", "username = %s", (admin["username"],)) == 1
    assert _count(sql, "branches", "branch_id = %s", (branch_id,)) == 1
    assert _count(sql, "unit_cogs_settings") > 0
    assert not (photos / "a.jpg").exists()
    # The reset itself is the first entry in the fresh Admin Log.
    assert _count(sql, "admin_actions", "action = 'clear_all_data'") == 1


# ---------------------------------------------------------------- BULK hidden from branch filters / low stock
def test_branch_inventory_unit_filter_has_no_bulk_option(client, sql):
    branch_id = _signed_in_branch(client, sql)
    # The filter only renders when the branch carries something.
    make_inventory(sql, branch_id, make_product(sql, unit="50ML"), stock_qty=1)

    html = client.get("/branch/inventory").get_data(as_text=True)

    assert '<option value="50ML"' in html
    assert '<option value="BULK"' not in html


def test_branch_low_stock_ignores_bulk(client, sql):
    branch_id = _signed_in_branch(client, sql)
    bulk = make_product(sql, unit="BULK")
    make_inventory(sql, branch_id, bulk, stock_qty=0, reorder_level=5)

    html = client.get("/branch/").get_data(as_text=True)

    assert bulk not in html


def test_admin_low_stock_page_and_dashboard_ignore_bulk(client, sql):
    branch_id = make_branch(sql)
    bulk = make_product(sql, unit="BULK")
    bottle = make_product(sql, unit="50ML")
    make_inventory(sql, branch_id, bulk, stock_qty=0, reorder_level=5)
    make_inventory(sql, branch_id, bottle, stock_qty=0, reorder_level=5)
    _signed_in_admin(client, sql)

    page = client.get(f"/admin/low-stock?branch_id={branch_id}").get_data(as_text=True)

    assert bottle in page
    assert bulk not in page


def test_branch_skus_carried_counts_base_codes(client, sql):
    import re
    import uuid
    branch_id = _signed_in_branch(client, sql)
    code = "T" + uuid.uuid4().hex[:6].upper()
    cur = sql.cursor()
    for unit, category in (("85ML", "Bottled"), ("50ML", "Bottled"), ("BULK", "Bulk/Refill")):
        cur.execute(
            "INSERT INTO products (sku, item_name, variant, category, unit, price) "
            "VALUES (%s, 'Seraph', 'Unisex', %s, %s, 1)", (f"{code}-{unit}", category, unit))
        cur.execute("INSERT INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, 5)",
                    (branch_id, f"{code}-{unit}"))
    sql.commit()
    cur.close()

    html = client.get("/branch/").get_data(as_text=True)

    assert re.search(r'SKUs carried</div>\s*<div class="stat-value">1</div>', html)
