"""routes/branch.py's request_stock() — a branch's delivery request to
HQ. Covers the two guarantees schema.sql's own comments call out: the
unit price on each line is snapshotted from the catalog at request time
(so a later price change never rewrites a past delivery's value), and a
submission naming the same SKU twice is merged into one line rather than
silently creating two.
"""
from factories import (get_form_token, get_inventory_qty, get_request_item,
                        get_request_status, last_movement_log, login,
                        make_branch, make_inventory, make_product,
                        make_stock_request, make_user)

HQ_BRANCH_ID = 1  # schema.sql seeds this as the HQ warehouse — see admin.py's own HQ_BRANCH_ID
REQUEST_STOCK_URL = "/branch/request-stock"
RECEIVE_STOCK_URL = "/branch/receive-stock"


def _signed_in_branch(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    return branch_id


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


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

    form_token = get_form_token(client, REQUEST_STOCK_URL)
    resp = client.post(
        REQUEST_STOCK_URL,
        data={"sku[]": [sku], "requested_qty[]": ["7"], "form_token": form_token},
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

    form_token = get_form_token(client, REQUEST_STOCK_URL)
    resp = client.post(
        REQUEST_STOCK_URL,
        data={"sku[]": [sku, sku], "requested_qty[]": ["3", "4"], "form_token": form_token},
    )
    assert resp.status_code == 302

    request_id = _latest_request_id(sql, branch_id)
    items = _request_items(sql, request_id)
    assert len(items) == 1
    assert items[0]["requested_qty"] == 7


def test_double_submitting_the_same_request_form_only_creates_one_delivery(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="10.00")

    form_token = get_form_token(client, REQUEST_STOCK_URL)
    data = {"sku[]": [sku], "requested_qty[]": ["5"], "form_token": form_token}

    first = client.post(REQUEST_STOCK_URL, data=data)
    assert first.status_code == 302
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT COUNT(*) AS c FROM stock_requests WHERE branch_id = %s", (branch_id,))
    assert cur.fetchone()["c"] == 1
    cur.close()

    # Same token, resubmitted — must not create a second delivery.
    second = client.post(REQUEST_STOCK_URL, data=data)
    assert second.status_code == 302
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT COUNT(*) AS c FROM stock_requests WHERE branch_id = %s", (branch_id,))
    assert cur.fetchone()["c"] == 1
    cur.close()


# ---------------------------------------------------------------- dispatch_request
def test_dispatch_decrements_hq_stock_and_moves_request_to_in_transit(client, sql):
    # Deliberately not _signed_in_branch() here — /login short-circuits
    # to a redirect (without actually switching accounts) if the client
    # already has a session, so a branch login followed by an admin
    # login on the same client wouldn't really end up signed in as
    # Admin. dispatch/reject only need a branch_id to attach the
    # request to, not an authenticated branch session.
    branch_id = make_branch(sql)
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="10.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=50)
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 20}], status="Pending")

    resp = client.post(
        f"/admin/requests/{request_id}/dispatch",
        data={"item_id[]": [str(get_request_item(sql, request_id, sku)["item_id"])],
              "dispatched_qty[]": ["20"]},
    )

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 30
    assert get_request_status(sql, request_id) == "In Transit"
    item = get_request_item(sql, request_id, sku)
    assert item["dispatched_qty"] == 20
    movement = last_movement_log(sql, HQ_BRANCH_ID, sku)
    assert movement["movement_type"] == "DISPATCH"
    assert movement["change_qty"] == -20


def test_dispatch_more_than_hq_stock_on_hand_is_rejected(client, sql):
    branch_id = make_branch(sql)
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="10.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=5)
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 20}], status="Pending")

    resp = client.post(
        f"/admin/requests/{request_id}/dispatch",
        data={"item_id[]": [str(get_request_item(sql, request_id, sku)["item_id"])],
              "dispatched_qty[]": ["20"]},
    )

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 5
    assert get_request_status(sql, request_id) == "Pending"
    assert get_request_item(sql, request_id, sku)["dispatched_qty"] is None


def test_dispatch_more_than_requested_qty_is_rejected(client, sql):
    branch_id = make_branch(sql)
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="10.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=100)
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 20}], status="Pending")

    resp = client.post(
        f"/admin/requests/{request_id}/dispatch",
        data={"item_id[]": [str(get_request_item(sql, request_id, sku)["item_id"])],
              "dispatched_qty[]": ["21"]},
    )

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 100
    assert get_request_status(sql, request_id) == "Pending"


# ---------------------------------------------------------------- receive_stock
def test_receive_stock_adds_to_branch_inventory_and_marks_fulfilled(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="10.00")
    make_inventory(sql, branch_id, sku, stock_qty=5)
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 10, "dispatched_qty": 10}],
        status="In Transit",
    )
    item = get_request_item(sql, request_id, sku)

    resp = client.post(
        RECEIVE_STOCK_URL,
        data={
            "request_id": str(request_id),
            "item_id[]": [str(item["item_id"])],
            "received_qty[]": ["10"],
            "damaged_qty[]": ["0"],
        },
    )

    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 15
    assert get_request_status(sql, request_id) == "Fulfilled"
    movement = last_movement_log(sql, branch_id, sku)
    assert movement["movement_type"] == "RECEIPT"
    assert movement["change_qty"] == 10


def test_receive_stock_shortfall_is_logged_as_an_adjustment(client, sql):
    """dispatched=10 but only 6 received and 0 reported damaged -> 4
    units unaccounted for must be written to the ledger (ADJUSTMENT),
    not just flashed and forgotten — see receive_stock()'s comment on
    this being the actual audit trail HQ follow-up depends on."""
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="10.00")
    make_inventory(sql, branch_id, sku, stock_qty=0)
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 10, "dispatched_qty": 10}],
        status="In Transit",
    )
    item = get_request_item(sql, request_id, sku)

    resp = client.post(
        RECEIVE_STOCK_URL,
        data={
            "request_id": str(request_id),
            "item_id[]": [str(item["item_id"])],
            "received_qty[]": ["6"],
            "damaged_qty[]": ["0"],
        },
    )

    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 6
    cur = sql.cursor(dictionary=True)
    cur.execute(
        """SELECT * FROM stock_movement_logs
           WHERE branch_id = %s AND sku = %s AND movement_type = 'ADJUSTMENT'
           ORDER BY log_id DESC LIMIT 1""",
        (branch_id, sku),
    )
    adjustment = cur.fetchone()
    cur.close()
    assert adjustment is not None
    assert "4" in adjustment["notes"]


def test_receiving_more_than_dispatched_is_rejected(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="10.00")
    make_inventory(sql, branch_id, sku, stock_qty=0)
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 10, "dispatched_qty": 10}],
        status="In Transit",
    )
    item = get_request_item(sql, request_id, sku)

    resp = client.post(
        RECEIVE_STOCK_URL,
        data={
            "request_id": str(request_id),
            "item_id[]": [str(item["item_id"])],
            "received_qty[]": ["8"],
            "damaged_qty[]": ["5"],  # 8 + 5 > 10 dispatched
        },
    )

    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 0
    assert get_request_status(sql, request_id) == "In Transit"


def test_confirming_the_same_delivery_twice_only_receives_it_once(client, sql):
    """No form_token needed here — receive_stock()'s own FOR UPDATE +
    status='In Transit' guard already makes a second confirmation a
    no-op, since the first one already flips status to Fulfilled."""
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="10.00")
    make_inventory(sql, branch_id, sku, stock_qty=0)
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 10, "dispatched_qty": 10}],
        status="In Transit",
    )
    item = get_request_item(sql, request_id, sku)
    data = {
        "request_id": str(request_id),
        "item_id[]": [str(item["item_id"])],
        "received_qty[]": ["10"],
        "damaged_qty[]": ["0"],
    }

    first = client.post(RECEIVE_STOCK_URL, data=data)
    assert first.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 10

    second = client.post(RECEIVE_STOCK_URL, data=data)
    assert second.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 10  # unchanged, not 20


# ---------------------------------------------------------------- reject_request
def test_rejecting_a_pending_request_marks_it_rejected(client, sql):
    branch_id = make_branch(sql)
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="10.00")
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 5}], status="Pending")

    resp = client.post(f"/admin/requests/{request_id}/reject")

    assert resp.status_code == 302
    assert get_request_status(sql, request_id) == "Rejected"


def test_rejecting_an_already_dispatched_request_is_a_no_op(client, sql):
    """The rowcount check in reject_request() is what tells a real
    double-submit/race apart from a request that's genuinely still
    Pending — without it this would silently "succeed" a second time."""
    branch_id = make_branch(sql)
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="10.00")
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 5, "dispatched_qty": 5}],
        status="In Transit",
    )

    resp = client.post(f"/admin/requests/{request_id}/reject")

    assert resp.status_code == 302
    assert get_request_status(sql, request_id) == "In Transit"
