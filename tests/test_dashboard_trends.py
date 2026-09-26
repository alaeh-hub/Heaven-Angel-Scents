"""Trend arrows on the dashboard stat tiles (see utils.percent_change()
and the trend() macro in _macros.html): the comparison windows are
computed in SQL, so these check the queries against real sales rows.
"""
from factories import login, make_branch, make_product, make_user


def _add_sale(sql, branch_id, sku, qty, unit_price, sold_at_sql):
    # sold_at_sql is a fixed SQL expression from this file, never user input.
    cur = sql.cursor()
    cur.execute(
        f"""INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sold_at)
            VALUES (%s, %s, %s, %s, {sold_at_sql})""",
        (branch_id, sku, qty, unit_price),
    )
    cur.close()


def test_branch_dashboard_shows_revenue_up_vs_same_time_yesterday(client, sql):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    # Yesterday, before this time of day: 100. Today so far: 150 -> +50%.
    _add_sale(sql, branch_id, sku, 1, "100.00", "NOW() - INTERVAL 1 DAY - INTERVAL 1 SECOND")
    _add_sale(sql, branch_id, sku, 1, "150.00", "NOW() - INTERVAL 1 SECOND")
    # Later yesterday than "now" — outside the comparison window.
    _add_sale(sql, branch_id, sku, 1, "999.00", "NOW() - INTERVAL 1 DAY + INTERVAL 1 MINUTE")
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    html = client.get("/branch/").get_data(as_text=True)

    assert "stat-trend-up" in html
    assert "50.0%" in html


def test_branch_dashboard_hides_trend_with_no_sales_yesterday(client, sql):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    _add_sale(sql, branch_id, sku, 1, "150.00", "NOW() - INTERVAL 1 SECOND")
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    html = client.get("/branch/").get_data(as_text=True)

    # Today's sales is the only tile with a trend on this page.
    assert "stat-trend" not in html


def test_admin_dashboard_shows_revenue_trend(client, sql):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    _add_sale(sql, branch_id, sku, 1, "100.00", "NOW() - INTERVAL 1 MONTH - INTERVAL 1 SECOND")
    _add_sale(sql, branch_id, sku, 1, "100.00", "NOW() - INTERVAL 1 SECOND")
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")

    html = client.get("/admin/").get_data(as_text=True)

    # Exact % depends on other tests' rows in the shared test DB, so
    # just check the badge renders with a comparison.
    assert "stat-trend" in html
    assert "same time last month" in html
