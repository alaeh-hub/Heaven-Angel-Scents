"""routes/branch.py's request_stock() — a branch's delivery request to
HQ. Covers the two guarantees schema.sql's own comments call out: the
unit price on each line is snapshotted from the catalog at request time
(so a later price change never rewrites a past delivery's value), and a
submission naming the same SKU twice is merged into one line rather than
silently creating two.
"""
from factories import login, make_branch, make_product, make_user


def _signed_in_branch(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    return branch_id


def _request_items(sql, request_id):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM stock_request_items WHERE request_id = %s ORDER BY item_id",
        (request_id,),
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def _latest_request_id(sql, branch_id):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        """SELECT request_id FROM stock_requests
           WHERE branch_id = %s ORDER BY request_id DESC LIMIT 1""",
        (branch_id,),
    )
    row = cur.fetchone()
    cur.close()
    return row["request_id"]


def test_request_stock_snapshots_current_price_onto_the_line_item(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="123.45")

    resp = client.post(
        "/branch/request-stock",
        data={"sku[]": [sku], "requested_qty[]": ["7"]},
    )
    assert resp.status_code == 302

    request_id = _latest_request_id(sql, branch_id)
    items = _request_items(sql, request_id)
    assert len(items) == 1
    assert items[0]["sku"] == sku
    assert items[0]["requested_qty"] == 7
    assert str(items[0]["unit_price"]) == "123.45"

    # Changing the catalog price afterwards must not retroactively change
    # what this already-submitted line is worth.
    cur = sql.cursor()
    cur.execute("UPDATE products SET price = 999.00 WHERE sku = %s", (sku,))
    sql.commit()
    cur.close()
    items_after = _request_items(sql, request_id)
    assert str(items_after[0]["unit_price"]) == "123.45"


def test_request_stock_merges_duplicate_skus_into_one_line(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="10.00")

    resp = client.post(
        "/branch/request-stock",
        data={"sku[]": [sku, sku], "requested_qty[]": ["3", "4"]},
    )
    assert resp.status_code == 302

    request_id = _latest_request_id(sql, branch_id)
    items = _request_items(sql, request_id)
    assert len(items) == 1
    assert items[0]["requested_qty"] == 7
