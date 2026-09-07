"""routes/branch.py's record_sale() — the stock arithmetic at the heart
of the app: a Sale must decrement branch_inventory by exactly the sold
quantity and leave a matching ledger entry, a Refill must touch the
sales/reporting numbers without moving stock at all, and overselling
must be rejected outright rather than letting stock go negative.
"""
from factories import (count_sales, get_inventory_qty, last_movement_log,
                        login, make_branch, make_inventory, make_product,
                        make_user)


def _sell(client, sku, qty, unit_price, sale_type="Sale", payment_method="Cash"):
    return client.post(
        "/branch/record-sale",
        data={
            "sku": sku,
            "sale_type": sale_type,
            "payment_method": payment_method,
            "qty_sold": str(qty),
            "unit_price": str(unit_price),
        },
    )


def _signed_in_branch(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    return branch_id


def test_sale_decrements_stock_and_logs_a_matching_movement(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=10)

    resp = _sell(client, sku, qty=3, unit_price="50.00", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 7
    assert count_sales(sql, branch_id, sku) == 1
    movement = last_movement_log(sql, branch_id, sku)
    assert movement["movement_type"] == "SALE"
    assert movement["change_qty"] == -3
    assert movement["before_qty"] == 10
    assert movement["after_qty"] == 7


def test_refill_leaves_stock_untouched(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=10)

    resp = _sell(client, sku, qty=1, unit_price="20.00", sale_type="Refill")

    assert resp.status_code == 302
    # Still a real, ledgered transaction (it counts toward sales/revenue
    # reporting) — just with zero stock impact.
    assert get_inventory_qty(sql, branch_id, sku) == 10
    assert count_sales(sql, branch_id, sku) == 1
    movement = last_movement_log(sql, branch_id, sku)
    assert movement["movement_type"] == "REFILL"
    assert movement["change_qty"] == 0
    assert movement["before_qty"] == 10
    assert movement["after_qty"] == 10


def test_overselling_is_rejected_and_leaves_stock_unchanged(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=2)

    resp = _sell(client, sku, qty=5, unit_price="50.00", sale_type="Sale")

    # record_sale() always redirects back to the form whether the sale
    # succeeded or was rejected — the real assertion is that nothing
    # actually moved, not the HTTP status.
    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 2
    assert count_sales(sql, branch_id, sku) == 0


def test_selling_exactly_the_remaining_stock_is_allowed(client, sql):
    """The boundary case for the overselling check
    (`stock_row["stock_qty"] < qty`) — equal amounts must still succeed."""
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=4)

    resp = _sell(client, sku, qty=4, unit_price="50.00", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 0
    assert count_sales(sql, branch_id, sku) == 1
