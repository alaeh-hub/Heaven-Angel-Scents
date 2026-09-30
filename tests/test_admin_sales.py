"""routes/admin.py's record_sale() — HQ's own copy of branch.record_sale()
(see its docstring: "Mirrors branch.record_sale()"). This hand-duplicated
implementation had no test of its own before this file — test_sales.py
only ever exercised the branch-side copy, so the two could silently drift
apart with nothing to catch it. Mirrors test_sales.py's cases against the
HQ warehouse (branch_id=1) instead of a regular branch.
"""
from factories import (count_sales, get_form_token, get_inventory_qty,
                        last_movement_log, login, make_inventory,
                        make_product, make_user, unique_suffix)

RECORD_SALE_URL = "/admin/record-sale"
HQ_BRANCH_ID = 1  # schema.sql seeds this as the HQ warehouse


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


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def test_sale_decrements_hq_stock_and_logs_a_matching_movement(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=10)

    resp = _sell(client, sku, qty=3, unit_price="50.00", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 7
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 1
    movement = last_movement_log(sql, HQ_BRANCH_ID, sku)
    assert movement["movement_type"] == "SALE"
    assert movement["change_qty"] == -3


def _make_scent_pair(sql, bulk_ml):
    """A1-style pair: a 50ML bottle SKU and its scent's BULK SKU (same
    base code), with bulk_ml of bulk at HQ and 10 bottles."""
    base = f"RF{unique_suffix().upper()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO products (sku, item_name, variant, category, unit, price) VALUES
           (%s, %s, 'Unisex', 'Bottled', '50ML', 50.00),
           (%s, %s, 'Unisex', 'Bulk/Refill', 'BULK', 5.00)""",
        (f"{base}-50ML", f"Refill Scent {base}", f"{base}-BULK", f"Refill Scent {base}"),
    )
    sql.commit()
    cur.close()
    make_inventory(sql, HQ_BRANCH_ID, f"{base}-50ML", stock_qty=10)
    make_inventory(sql, HQ_BRANCH_ID, f"{base}-BULK", stock_qty=bulk_ml)
    return f"{base}-50ML", f"{base}-BULK"


def test_refill_draws_the_scents_bulk_ml_not_bottles(client, sql):
    _signed_in_admin(client, sql)
    bottle, bulk = _make_scent_pair(sql, bulk_ml=500)

    resp = _sell(client, bottle, qty=2, unit_price="20.00", sale_type="Refill")

    assert resp.status_code == 302
    # Customer brought their own bottle: bottles untouched, 2 x 50 mL of bulk poured.
    assert get_inventory_qty(sql, HQ_BRANCH_ID, bottle) == 10
    assert get_inventory_qty(sql, HQ_BRANCH_ID, bulk) == 400
    assert count_sales(sql, HQ_BRANCH_ID, bottle) == 1
    movement = last_movement_log(sql, HQ_BRANCH_ID, bulk)
    assert movement["movement_type"] == "REFILL"
    assert movement["change_qty"] == -100


def test_refill_is_blocked_without_enough_bulk(client, sql):
    _signed_in_admin(client, sql)
    bottle, bulk = _make_scent_pair(sql, bulk_ml=60)

    resp = _sell(client, bottle, qty=2, unit_price="20.00", sale_type="Refill")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, bulk) == 60
    assert count_sales(sql, HQ_BRANCH_ID, bottle) == 0


def test_overselling_hq_stock_is_rejected(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=2)

    resp = _sell(client, sku, qty=5, unit_price="50.00", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 2
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 0


def test_zero_price_sale_is_rejected(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=10)

    resp = _sell(client, sku, qty=1, unit_price="0", sale_type="Sale")

    assert resp.status_code == 302
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 10
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 0


def test_credit_sale_without_a_buyer_name_is_rejected(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=10)

    form_token = get_form_token(client, RECORD_SALE_URL)
    resp = client.post(
        RECORD_SALE_URL,
        data={
            "sku": sku, "sale_type": "Sale", "payment_method": "Credit",
            "qty_sold": "1", "unit_price": "50.00", "buyer_name": "",
            "form_token": form_token,
        },
    )

    assert resp.status_code == 302
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 0


def test_double_submitting_the_same_sale_form_only_records_it_once(client, sql):
    _signed_in_admin(client, sql)
    sku = make_product(sql, price="50.00")
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=10)

    form_token = get_form_token(client, RECORD_SALE_URL)
    first = _sell(client, sku, qty=3, unit_price="50.00", form_token=form_token)
    assert first.status_code == 302
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 1

    second = _sell(client, sku, qty=3, unit_price="50.00", form_token=form_token)
    assert second.status_code == 302
    assert count_sales(sql, HQ_BRANCH_ID, sku) == 1
    assert get_inventory_qty(sql, HQ_BRANCH_ID, sku) == 7
