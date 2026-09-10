"""routes/admin.py's formula editor (save_formula) and the rebuilt
log_material_usage() — logging a production batch off the formula for a
product's PACKAGING SIZE (85ML, 50ML, 1L, 100ML, 10ML, 3ML Tester)
instead of picking raw materials one by one. A formula belongs to a
size, not a specific product — every scent sold in, say, 85ML shares the
one 85ML formula (see unit_formula_items in schema.sql) — so these tests
also cover that sharing directly, not just the single-product path.

Covers the core financial guarantee this feature exists for: total_cogs
(== "capital" on the dashboard) is computed from that size's cost per
unit at the moment a batch is logged, kept separate from what's been
spent buying raw material packages, and every ingredient's stock only
moves when the whole batch can actually be fulfilled.

Formulas are shared, session-wide state per unit — a small fixed set of
6 values, not a fresh row per test the way make_product()'s SKUs are —
so every test here calls make_formula() (which replaces a unit's rows,
same as save_formula() itself) immediately before asserting anything
that depends on that unit's exact formula, rather than assuming a unit
starts out empty.
"""
from decimal import Decimal

from factories import (get_cogs_logs, get_form_token, get_raw_material,
                        login, make_formula, make_product, make_raw_material,
                        make_user)

SAVE_FORMULA_URL = "/admin/formulas/save"
LOG_USAGE_URL = "/admin/materials/log-usage"
MATERIALS_URL = "/admin/materials"


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def _formula_items(sql, unit):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM unit_formula_items WHERE unit = %s ORDER BY material_id", (unit,))
    rows = cur.fetchall()
    cur.close()
    return rows


def _usage_logs_for_cogs(sql, cogs_log_id):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM material_usage_logs WHERE cogs_log_id = %s ORDER BY material_id",
        (cogs_log_id,),
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def test_formulas_page_renders_before_and_after_a_formula_exists(client, sql):
    _signed_in_admin(client, sql)
    resp = client.get("/admin/formulas")
    assert resp.status_code == 200
    # All 6 packaging sizes are always listed, formula or not.
    assert b"85ML" in resp.data and b"3ML Tester" in resp.data

    mat_a = make_raw_material(sql)
    make_formula(sql, "85ML", [(mat_a, "2")])
    resp = client.get("/admin/formulas")
    assert resp.status_code == 200


def test_materials_page_renders_before_and_after_a_formula_product_exists(client, sql):
    _signed_in_admin(client, sql)
    resp = client.get(MATERIALS_URL)
    assert resp.status_code == 200

    sku = make_product(sql, unit="50ML")
    mat_a = make_raw_material(sql)
    make_formula(sql, "50ML", [(mat_a, "2")])
    resp = client.get(MATERIALS_URL)
    assert resp.status_code == 200
    assert sku.encode() in resp.data


def test_save_formula_creates_then_replaces_its_items(client, sql):
    _signed_in_admin(client, sql)
    mat_a = make_raw_material(sql)
    mat_b = make_raw_material(sql)

    resp = client.post(SAVE_FORMULA_URL, data={
        "unit": "10ML",
        "material_id[]": [str(mat_a), str(mat_b)],
        "qty_per_unit[]": ["2.5", "1"],
    })
    assert resp.status_code == 302

    items = _formula_items(sql, "10ML")
    assert {i["material_id"] for i in items} == {mat_a, mat_b}

    # Saving again with a different set replaces the old rows entirely,
    # not appends to them.
    mat_c = make_raw_material(sql)
    client.post(SAVE_FORMULA_URL, data={
        "unit": "10ML",
        "material_id[]": [str(mat_c)],
        "qty_per_unit[]": ["3"],
    })
    items = _formula_items(sql, "10ML")
    assert [i["material_id"] for i in items] == [mat_c]

    # Submitting with no rows at all clears the formula.
    client.post(SAVE_FORMULA_URL, data={"unit": "10ML"})
    assert _formula_items(sql, "10ML") == []


def test_save_formula_rejects_a_qty_per_unit_above_current_stock(client, sql):
    _signed_in_admin(client, sql)
    make_formula(sql, "1L", [])  # start from a known-empty formula
    # 5 pieces on hand.
    mat_a = make_raw_material(
        sql, unit="Piece", package_qty="5.000", package_cost="50.00")

    resp = client.post(SAVE_FORMULA_URL, data={
        "unit": "1L",
        "material_id[]": [str(mat_a)],
        "qty_per_unit[]": ["6"],  # more than the 5 on hand
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"on hand" in resp.data
    assert _formula_items(sql, "1L") == []

    # Exactly at stock is fine; nothing on-hand is left over, but a
    # formula line calling for exactly what's on hand is still valid.
    resp = client.post(SAVE_FORMULA_URL, data={
        "unit": "1L",
        "material_id[]": [str(mat_a)],
        "qty_per_unit[]": ["5"],
    })
    assert resp.status_code == 302
    items = _formula_items(sql, "1L")
    assert len(items) == 1
    assert Decimal(items[0]["qty_per_unit"]) == Decimal("5.0000")


def test_formula_is_shared_by_every_product_of_the_same_unit(client, sql):
    """The whole point of keying by unit rather than sku: two different
    products (different scents/variants) of the same packaging size use
    the exact same formula and cost per unit."""
    _signed_in_admin(client, sql)
    mat_a = make_raw_material(
        sql, package_qty="100.000", package_cost="200.00")  # ₱2.00/unit
    make_formula(sql, "1L", [(mat_a, "3")])  # ₱6.00/unit for every 1L product

    sku_1 = make_product(sql, unit="1L")
    sku_2 = make_product(sql, unit="1L")

    resp = client.get(MATERIALS_URL)
    assert resp.status_code == 200
    # Both SKUs are offered in the picker, both priced off the same formula.
    assert sku_1.encode() in resp.data
    assert sku_2.encode() in resp.data

    token = get_form_token(client, MATERIALS_URL)
    client.post(LOG_USAGE_URL, data={
        "form_token": token, "sku": sku_1, "qty_produced": "2",
    })
    logs = get_cogs_logs(sql, sku_1)
    assert Decimal(logs[0]["cogs_per_unit"]) == Decimal("6.0000")

    token = get_form_token(client, MATERIALS_URL)
    client.post(LOG_USAGE_URL, data={
        "form_token": token, "sku": sku_2, "qty_produced": "5",
    })
    logs = get_cogs_logs(sql, sku_2)
    # Same formula, same cost per unit, even though it's a different SKU.
    assert Decimal(logs[0]["cogs_per_unit"]) == Decimal("6.0000")


def test_log_material_usage_computes_cogs_and_deducts_every_ingredient(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, unit="100ML")
    # 100 grams for ₱200 -> ₱2.00/gram; 50 pieces for ₱25 -> ₱0.50/piece
    mat_a = make_raw_material(
        sql, unit="Gram", package_qty="100.000", package_cost="200.00")
    mat_b = make_raw_material(
        sql, unit="Piece", package_qty="50.000", package_cost="25.00")
    # 2.5g + 1 piece per unit -> cost per unit = 2.5*2.00 + 1*0.50 = ₱5.50
    make_formula(sql, "100ML", [(mat_a, "2.5"), (mat_b, "1")])

    token = get_form_token(client, MATERIALS_URL)
    resp = client.post(LOG_USAGE_URL, data={
        "form_token": token,
        "sku": sku,
        "qty_produced": "4",
    })
    assert resp.status_code == 302

    logs = get_cogs_logs(sql, sku)
    assert len(logs) == 1
    batch = logs[0]
    assert Decimal(batch["qty_produced"]) == Decimal("4")
    assert Decimal(batch["cogs_per_unit"]) == Decimal("5.5000")
    # 5.50 * 4 = 22.00 — this is "capital"
    assert Decimal(batch["total_cogs"]) == Decimal("22.00")

    mat_a_row = get_raw_material(sql, mat_a)
    mat_b_row = get_raw_material(sql, mat_b)
    # 100 - (2.5 * 4) = 90 ; 50 - (1 * 4) = 46
    assert Decimal(mat_a_row["stock_qty"]) == Decimal("90.000")
    assert Decimal(mat_b_row["stock_qty"]) == Decimal("46.000")

    usage_rows = _usage_logs_for_cogs(sql, batch["cogs_log_id"])
    assert len(usage_rows) == 2
    by_material = {r["material_id"]: r["qty_used"] for r in usage_rows}
    assert Decimal(by_material[mat_a]) == Decimal("10.000")
    assert Decimal(by_material[mat_b]) == Decimal("4.000")


def test_log_material_usage_insufficient_stock_rolls_back_everything(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, unit="1L")
    mat_a = make_raw_material(
        sql, package_qty="100.000", package_cost="100.00")
    # Only 1 unit's worth on hand for the second ingredient.
    mat_b = make_raw_material(
        sql, package_qty="10.000", package_cost="10.00", stock_qty="1.000")
    make_formula(sql, "1L", [(mat_a, "1"), (mat_b, "1")])

    token = get_form_token(client, MATERIALS_URL)
    resp = client.post(LOG_USAGE_URL, data={
        "form_token": token,
        "sku": sku,
        "qty_produced": "5",  # needs 5 of mat_b, only 1 on hand
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"on hand" in resp.data

    # Nothing committed — not even the deduction against mat_a, which
    # had plenty of stock on its own.
    assert get_cogs_logs(sql, sku) == []
    assert Decimal(get_raw_material(sql, mat_a)["stock_qty"]) == Decimal("100.000")
    assert Decimal(get_raw_material(sql, mat_b)["stock_qty"]) == Decimal("1.000")


def test_log_material_usage_without_a_formula_is_rejected(client, sql):
    _signed_in_admin(client, sql)
    make_formula(sql, "3ML Tester", [])  # explicitly no formula
    sku = make_product(sql, unit="3ML Tester")

    token = get_form_token(client, MATERIALS_URL)
    resp = client.post(LOG_USAGE_URL, data={
        "form_token": token,
        "sku": sku,
        "qty_produced": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"no formula" in resp.data.lower()
    assert get_cogs_logs(sql, sku) == []


def test_dashboard_capital_is_cogs_not_raw_material_purchases(client, sql):
    """Fleet-wide sums (financials.capital / raw_materials_purchased) can
    already carry rows from other tests sharing this session's database
    (see conftest.py — nothing is cleaned up between tests), so this
    asserts on the *delta* this test's own actions caused, never an
    absolute total.
    """
    _signed_in_admin(client, sql)
    before = client.get("/admin/api/reports-data").get_json()["financials"]

    sku = make_product(sql, unit="10ML")
    mat_a = make_raw_material(
        sql, package_qty="100.000", package_cost="1000.00")
    make_formula(sql, "10ML", [(mat_a, "1")])  # ₱10.00/unit

    # Buying the material alone moves "raw materials bought" but not
    # "capital" — only logging a batch against the formula should move
    # capital.
    after_purchase = client.get(
        "/admin/api/reports-data").get_json()["financials"]
    assert Decimal(str(after_purchase["capital"])) == Decimal(
        str(before["capital"]))
    assert Decimal(str(after_purchase["raw_materials_purchased"])) - Decimal(
        str(before["raw_materials_purchased"])) == Decimal("1000.00")

    token = get_form_token(client, MATERIALS_URL)
    client.post(LOG_USAGE_URL, data={
        "form_token": token, "sku": sku, "qty_produced": "3",
    })

    after_batch = client.get("/admin/api/reports-data").get_json()["financials"]
    assert Decimal(str(after_batch["capital"])) - Decimal(
        str(before["capital"])) == Decimal("30.00")
    # Raw materials bought is unchanged by logging usage — the two
    # figures move independently.
    assert Decimal(str(after_batch["raw_materials_purchased"])) == Decimal(
        str(after_purchase["raw_materials_purchased"]))


def test_dashboard_and_reports_pages_render(client, sql):
    _signed_in_admin(client, sql)
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/reports").status_code == 200
    assert client.get("/admin/api/reports-data").status_code == 200
