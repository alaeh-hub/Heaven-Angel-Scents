"""routes/branch.py's record_sale() — the stock arithmetic at the heart
of the app: a Sale must decrement branch_inventory by exactly the sold
quantity and leave a matching ledger entry, a Refill must touch the
sales/reporting numbers without moving stock at all, and overselling
must be rejected outright rather than letting stock go negative.
"""
from factories import (count_sales, get_form_token, get_inventory_qty,
                        last_movement_log, login, make_branch, make_inventory,
                        make_product, make_user)

RECORD_SALE_URL = "/branch/record-sale"


def _sell(client, sku, qty, unit_price, sale_type="Sale", payment_method="Cash",
          form_token=None):
    if form_token is None:
        form_token = get_form_token(client, RECORD_SALE_URL)
    return client.post(
        RECORD_SALE_URL,
        data={
            "sku": sku,
            "sale_type": sale_type,
            "payment_method": payment_method,
            "qty_sold": str(qty),
            "unit_price": str(unit_price),
            "form_token": form_token,
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


def test_zero_price_sale_is_rejected(client, sql):
    """A ₱0 Sale/Refill is otherwise a way to move stock out with no
    revenue and nothing marking it as a freebie — record_sale() must
    require unit_price > 0, not just >= 0."""
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=10)

    resp = _sell(client, sku, qty=1, unit_price="0", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 10
    assert count_sales(sql, branch_id, sku) == 0


def test_negative_price_sale_is_rejected(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=10)

    resp = _sell(client, sku, qty=1, unit_price="-5", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 10
    assert count_sales(sql, branch_id, sku) == 0


def test_zero_quantity_sale_is_rejected(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=10)

    resp = _sell(client, sku, qty=0, unit_price="50.00", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 10
    assert count_sales(sql, branch_id, sku) == 0


def test_nan_price_sale_is_rejected(client, sql):
    """parse_positive_decimal must reject "nan" explicitly — Decimal('nan')
    is neither <= 0 nor > 0 in the ordinary sense (comparing it raises),
    and float('nan') <= 0 is silently False, so a naive port of the old
    float-based check would let a NaN price sail through as "valid"."""
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=10)

    resp = _sell(client, sku, qty=1, unit_price="nan", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, branch_id, sku) == 10
    assert count_sales(sql, branch_id, sku) == 0


def test_double_submitting_the_same_sale_form_only_records_it_once(client, sql):
    """A double-click, browser back-button resubmit, or a client retrying
    a dropped response must not double-charge/double-decrement — see
    utils.issue_form_token()/consume_form_token()."""
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=10)

    form_token = get_form_token(client, RECORD_SALE_URL)
    first = _sell(client, sku, qty=3, unit_price="50.00", form_token=form_token)
    assert first.status_code == 302
    assert count_sales(sql, branch_id, sku) == 1
    assert get_inventory_qty(sql, branch_id, sku) == 7

    # Resubmitting the exact same form body (same token) a second time —
    # the token was already consumed by the first POST.
    second = _sell(client, sku, qty=3, unit_price="50.00", form_token=form_token)
    assert second.status_code == 302
    assert count_sales(sql, branch_id, sku) == 1
    assert get_inventory_qty(sql, branch_id, sku) == 7


def test_missing_form_token_is_rejected(client, sql):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=10)

    resp = _sell(client, sku, qty=1, unit_price="50.00", form_token="")

    assert resp.status_code == 302
    assert count_sales(sql, branch_id, sku) == 0
