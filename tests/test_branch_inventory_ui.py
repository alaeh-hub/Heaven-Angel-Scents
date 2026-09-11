"""Branch Inventory's unit filter — same fix as admin/products.html's
catalog filter (see test_admin_products.py): defaults to 85ML (the
most commonly sold size) instead of "All units", without removing the
"All units" option itself.
"""
from factories import login, make_branch, make_inventory, make_product, make_user


def test_branch_inventory_unit_filter_defaults_to_85ml(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    sku = make_product(sql, unit="85ML")
    make_inventory(sql, branch_id, sku, stock_qty=10)

    resp = client.get("/branch/inventory")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<option value="">All units</option>' in html
    assert '<option value="85ML" selected>85ML</option>' in html
    assert '<option value="50ML" selected>' not in html
