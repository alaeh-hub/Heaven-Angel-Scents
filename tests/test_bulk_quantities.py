"""Bulk products' qty_sold is recorded in mL, not bottles — so every
"units" figure (Customers, the branch dashboard, reports) must keep bulk
mL apart from bottle counts rather than adding the two together.
"""
from factories import login, make_branch, make_product, make_user, unique_suffix
from reports import get_report, parse_report_filters, render_report_excel, render_report_pdf


def _add_sale(sql, branch_id, sku, qty, unit_price, customer_name=None):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, customer_name)
           VALUES (%s, %s, %s, %s, %s)""",
        (branch_id, sku, qty, unit_price, customer_name),
    )
    sql.commit()
    cur.close()


def _setup_mixed_sales(sql):
    branch_id = make_branch(sql)
    bottle_sku = make_product(sql)
    bulk_sku = make_product(sql, unit="BULK")
    customer = f"Bulk Buyer {unique_suffix()}"
    _add_sale(sql, branch_id, bottle_sku, 2, "500.00", customer)
    _add_sale(sql, branch_id, bulk_sku, 100, "5.00", customer)
    return branch_id, customer


def test_branch_customers_units_keep_bulk_ml_separate(client, sql):
    branch_id, customer = _setup_mixed_sales(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    html = client.get("/branch/customers").get_data(as_text=True)

    assert customer in html
    assert "2 <span class=\"qty-stack-word\">bottles</span>" in html
    assert "100 <span class=\"qty-stack-word\">mL bulk</span>" in html
    assert ">102<" not in html


def test_branch_dashboard_headline_is_revenue_with_bottles_and_ml_apart(client, sql):
    branch_id, _ = _setup_mixed_sales(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    html = client.get("/branch/").get_data(as_text=True)

    assert "Today's revenue" in html
    assert "2 bottles sold · 100 mL bulk" in html


def test_customers_report_splits_bottles_and_bulk_ml(app, sql):
    branch_id, customer = _setup_mixed_sales(sql)
    with app.test_request_context():
        report = get_report("customers", parse_report_filters({}), branch_scope=branch_id)

    keys = [c[0] for c in report["columns"]]
    assert "total_bulk_ml" in keys
    row = next(r for r in report["rows"] if r["name"] == customer)
    assert row["total_units"] == 2
    assert row["total_bulk_ml"] == 100


def test_sales_history_report_totals_bottles_and_ml_apart(app, sql):
    branch_id, _ = _setup_mixed_sales(sql)
    with app.test_request_context():
        report = get_report("sales_history", parse_report_filters({"mode": "recent"}),
                            branch_scope=branch_id)

    keys = [c[0] for c in report["columns"]]
    assert "qty_sold" in keys and "qty_sold_bulk_ml" in keys
    assert report["totals"]["qty_sold"] == 2
    assert report["totals"]["qty_sold_bulk_ml"] == 100

    # Both renderers handle the split columns (None cells, separate totals).
    assert render_report_pdf(report).getvalue()
    assert render_report_excel(report).getvalue()


def test_admin_pages_render_with_bulk_sales(client, sql):
    _setup_mixed_sales(sql)
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")

    for url in ("/admin/", "/admin/customers", "/admin/branch-performance",
                "/admin/api/reports-data", "/admin/record-sale", "/admin/reports", "/admin/sales-history",
                "/admin/low-stock"):
        assert client.get(url).status_code == 200, url
    data = client.get("/admin/api/reports-data").get_json()
    assert "bulk_ml" in data["totals"]


def test_branch_pages_render_with_bulk_sales(client, sql):
    branch_id, _ = _setup_mixed_sales(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    for url in ("/branch/sales-history", "/branch/credit-purchases",
                "/branch/api/reports-data", "/branch/record-sale", "/branch/reports"):
        assert client.get(url).status_code == 200, url
    data = client.get("/branch/api/reports-data").get_json()
    assert data["totals"]["bulk_ml"] in (100, "100")
