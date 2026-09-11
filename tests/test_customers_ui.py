"""Customer directory (branch and admin/fleet-wide) — same collapsible
mobile-card treatment as Record Sale's "Recent sales" (see
test_record_sale_ui.py): a customer's row collapses to just their name/
address, with the rest (Purchases, Units, Total spent, Last purchase,
and — admin only — Branches shopped/Last at) behind a "Details" toggle.
Only checks the rendered markup — the collapse/expand interaction
itself is CSS + client-side JS, out of reach for a server-side test.
"""
from factories import (get_form_token, login, make_branch, make_inventory,
                        make_product, make_user, unique_suffix)

BRANCH_RECORD_SALE_URL = "/branch/record-sale"


def _sell_with_customer(client, sku, customer_name):
    form_token = get_form_token(client, BRANCH_RECORD_SALE_URL)
    return client.post(
        BRANCH_RECORD_SALE_URL,
        data={
            "sku": sku,
            "sale_type": "Sale",
            "payment_method": "Cash",
            "qty_sold": "1",
            "unit_price": "500.00",
            "customer_name": customer_name,
            "form_token": form_token,
        },
    )


def test_branch_customer_directory_renders_collapsible_mobile_markup(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=10)
    # Unique per test run — customers are grouped by exact name text
    # (see admin.customers()'s own docstring), and this DB is shared
    # across the whole test session with nothing cleaned up between
    # tests, so a fixed literal name would collide with (and get
    # merged into) any other test's rows using the same name.
    _sell_with_customer(client, sku, f"Test Customer {unique_suffix()}")

    resp = client.get("/branch/customers")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # Purchases, Units, Total spent, Last purchase collapse away.
    assert html.count("mobile-detail") == 4


def test_admin_customer_directory_renders_collapsible_mobile_markup(client, sql):
    branch_id = make_branch(sql)
    branch_user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, branch_user["username"], branch_user["password"], "Branch")

    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=10)
    _sell_with_customer(client, sku, f"Test Customer {unique_suffix()}")

    client.post("/logout")
    admin = make_user(sql, role="Admin", branch_id=None)
    login(client, admin["username"], admin["password"], "Admin")

    resp = client.get("/admin/customers")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # Branches shopped, Purchases, Units, Total spent, Last purchase,
    # Last at collapse away — 6 per row. Admin's directory is
    # fleet-wide and grouped by exact customer_name (see
    # admin.customers()'s own docstring), so it also picks up any
    # other named customer already in this shared, never-reset test
    # DB (e.g. the branch-scoped test above) — count distinct names
    # rather than assume this is the only row.
    cur = sql.cursor()
    cur.execute(
        "SELECT COUNT(DISTINCT customer_name) FROM sales "
        "WHERE customer_name IS NOT NULL AND customer_name <> ''"
    )
    customer_count = cur.fetchone()[0]
    cur.close()
    assert html.count("mobile-detail") == customer_count * 6
