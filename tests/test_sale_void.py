"""Void a sale (sale_stock.void_sale): stock goes back (SALE_VOID), the
sale leaves every total, and a permanent sale_voids record keeps who and
why. HQ can void any location's sale; branch staff only their own, and
only on the day it was recorded. A voided refill returns its bulk mL.
"""
from factories import (count_sales, get_form_token, get_inventory_qty, last_movement_log, login,
                        make_branch, make_inventory, make_product, make_user, unique_suffix)

HQ = 1


def _sell(client, url, sku, qty, sale_type="Sale", unit_price="50.00"):
    return client.post(url, data={
        "sku": sku, "sale_type": sale_type, "payment_method": "Cash", "qty_sold": str(qty),
        "unit_price": unit_price, "form_token": get_form_token(client, url),
    })


def _last_sale_id(sql, branch_id, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT MAX(sale_id) AS id FROM sales WHERE branch_id = %s AND sku = %s", (branch_id, sku))
    row = cur.fetchone()
    cur.close()
    return row["id"]


def _void_record(sql, sale_id):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT * FROM sale_voids WHERE sale_id = %s", (sale_id,))
    row = cur.fetchone()
    cur.close()
    return row


def _branch_user(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    return branch_id


def _admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")


def test_branch_voids_todays_sale_and_stock_returns(client, sql):
    branch_id = _branch_user(client, sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=10)
    _sell(client, "/branch/record-sale", sku, 3)
    sale_id = _last_sale_id(sql, branch_id, sku)
    assert get_inventory_qty(sql, branch_id, sku) == 7

    client.post(f"/branch/sales/{sale_id}/void", data={"reason": "Entered twice"})

    assert get_inventory_qty(sql, branch_id, sku) == 10
    assert count_sales(sql, branch_id, sku) == 0
    record = _void_record(sql, sale_id)
    assert record["reason"] == "Entered twice" and record["qty_sold"] == 3
    log = last_movement_log(sql, branch_id, sku)
    assert (log["movement_type"], log["change_qty"]) == ("SALE_VOID", 3)


def test_void_needs_a_reason(client, sql):
    branch_id = _branch_user(client, sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=10)
    _sell(client, "/branch/record-sale", sku, 1)
    sale_id = _last_sale_id(sql, branch_id, sku)

    client.post(f"/branch/sales/{sale_id}/void", data={"reason": "  "})

    assert count_sales(sql, branch_id, sku) == 1


def test_branch_cannot_void_an_older_sale_or_another_branchs(client, sql):
    branch_id = _branch_user(client, sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=10)
    _sell(client, "/branch/record-sale", sku, 1)
    sale_id = _last_sale_id(sql, branch_id, sku)
    cur = sql.cursor()
    cur.execute("UPDATE sales SET recorded_at = NOW() - INTERVAL 2 DAY WHERE sale_id = %s", (sale_id,))
    other = make_branch(sql)
    other_sku = make_product(sql)
    cur.execute("INSERT INTO sales (branch_id, sku, qty_sold, unit_price) VALUES (%s, %s, 1, 10)",
                (other, other_sku))
    other_sale = cur.lastrowid
    sql.commit()
    cur.close()

    html = client.post(f"/branch/sales/{sale_id}/void", data={"reason": "late"},
                       follow_redirects=True).get_data(as_text=True)
    assert "ask HQ" in html
    client.post(f"/branch/sales/{other_sale}/void", data={"reason": "not mine"})

    assert count_sales(sql, branch_id, sku) == 1
    assert count_sales(sql, other, other_sku) == 1


def test_admin_voids_any_sale_including_old_ones(client, sql):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=4)
    cur = sql.cursor()
    # An old sale from before sale logs carried reference_id: void falls
    # back to returning its own qty.
    cur.execute("""INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sold_at, recorded_at)
                   VALUES (%s, %s, 2, 50, NOW() - INTERVAL 30 DAY, NOW() - INTERVAL 30 DAY)""",
                (branch_id, sku))
    sale_id = cur.lastrowid
    sql.commit()
    cur.close()
    _admin(client, sql)

    client.post(f"/admin/sales/{sale_id}/void", data={"reason": "Customer returned it"})

    assert count_sales(sql, branch_id, sku) == 0
    assert get_inventory_qty(sql, branch_id, sku) == 6
    assert "Customer returned it" in client.get("/admin/sales-history").get_data(as_text=True)


def test_voiding_a_refill_returns_its_bulk_ml(client, sql):
    base = f"VD{unique_suffix().upper()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO products (sku, item_name, variant, category, unit, price) VALUES
           (%s, 'Void Scent', 'Unisex', 'Bottled', '10ML', 20.00),
           (%s, 'Void Scent', 'Unisex', 'Bulk/Refill', 'BULK', 2.00)""",
        (f"{base}-10ML", f"{base}-BULK"),
    )
    sql.commit()
    cur.close()
    make_inventory(sql, HQ, f"{base}-10ML", stock_qty=0)
    make_inventory(sql, HQ, f"{base}-BULK", stock_qty=100)
    _admin(client, sql)

    _sell(client, "/admin/record-sale", f"{base}-10ML", 3, sale_type="Refill", unit_price="15.00")
    assert get_inventory_qty(sql, HQ, f"{base}-BULK") == 70
    sale_id = _last_sale_id(sql, HQ, f"{base}-10ML")

    client.post(f"/admin/sales/{sale_id}/void", data={"reason": "Wrong scent"})

    assert get_inventory_qty(sql, HQ, f"{base}-BULK") == 100
    assert get_inventory_qty(sql, HQ, f"{base}-10ML") == 0


def test_admin_void_redirect_stays_inside_admin(client, sql):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=1)
    cur = sql.cursor()
    cur.execute("INSERT INTO sales (branch_id, sku, qty_sold, unit_price) VALUES (%s, %s, 1, 10)", (branch_id, sku))
    sale_id = cur.lastrowid
    sql.commit()
    cur.close()
    _admin(client, sql)
    resp = client.post(f"/admin/sales/{sale_id}/void",
                       data={"reason": "x", "next": "https://evil.example.com/"})
    assert "evil.example.com" not in resp.headers["Location"]


def test_branch_sales_history_offers_void_only_for_todays_sales(client, sql):
    branch_id = _branch_user(client, sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=10)
    _sell(client, "/branch/record-sale", sku, 1)
    sale_id = _last_sale_id(sql, branch_id, sku)
    html = client.get("/branch/sales-history").get_data(as_text=True)
    assert f"/branch/sales/{sale_id}/void" in html
