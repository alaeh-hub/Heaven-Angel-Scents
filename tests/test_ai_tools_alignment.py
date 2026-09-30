"""AI assistant tools stay aligned with the pages and reports: every tool
runs cleanly for both roles, and the bottle / bulk-mL split, HQ naming,
bulk-request guard and COGS maths match the rest of the system.
"""
import routes.ai_tools as ai_tools
from factories import make_branch, make_inventory, make_product, unique_suffix


def _add_sale(sql, branch_id, sku, qty, unit_price, customer_name=None, address=None):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, customer_name, customer_address)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (branch_id, sku, qty, unit_price, customer_name, address),
    )
    sql.commit()
    cur.close()


def _admin_ctx():
    return {"role": "Admin", "branch_id": None, "user_id": None, "username": "test-admin"}


def _branch_ctx(branch_id):
    return {"role": "Branch", "branch_id": branch_id, "user_id": None, "username": "test-branch"}


READ_TOOLS = {
    "check_stock": {"item_name": "Test Product"},
    "get_low_stock": {},
    "get_pending_deliveries": {},
    "get_sales_summary": {"period": "all_time", "by_branch": True},
    "get_credit_purchases": {},
    "get_customers": {},
    "get_recent_sales": {"limit": 5},
    "get_discrepancies": {},
}
ADMIN_TOOLS = {
    "get_raw_materials": {},
    "get_suppliers": {},
    "get_formulas": {},
    "get_bulk_batches": {"active_only": False},
    "get_production_costs": {"period": "all_time"},
    "get_packages": {"include_inactive": True},
    "get_partner_inquiries": {},
    "get_branch_performance": {},
    "get_financials": {"period": "all_time"},
}


def test_every_tool_runs_for_its_roles(app, sql):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    _add_sale(sql, branch_id, sku, 1, "100.00", f"Cust {unique_suffix()}")
    with app.test_request_context():
        for name, args in {**READ_TOOLS, **ADMIN_TOOLS}.items():
            out = ai_tools.dispatch(name, args, _admin_ctx())
            assert "error" not in out, (name, out)
        for name, args in READ_TOOLS.items():
            out = ai_tools.dispatch(name, args, _branch_ctx(branch_id))
            assert "error" not in out, (name, out)
        for name in ADMIN_TOOLS:
            out = ai_tools.dispatch(name, {}, _branch_ctx(branch_id))
            assert out.get("error") == "That information is only available to HQ admins."


def test_sales_tools_keep_bulk_ml_apart_and_customer_address(app, sql):
    branch_id = make_branch(sql)
    bottle = make_product(sql)
    bulk = make_product(sql, unit="BULK")
    customer = f"AI Buyer {unique_suffix()}"
    _add_sale(sql, branch_id, bottle, 2, "500.00", customer, "First Street")
    _add_sale(sql, branch_id, bulk, 100, "5.00", customer, "Second Street")
    with app.test_request_context():
        summary = ai_tools.dispatch("get_sales_summary", {"period": "all_time"}, _branch_ctx(branch_id))
        cust = ai_tools.dispatch("get_customers", {"name": customer}, _branch_ctx(branch_id))
        recent = ai_tools.dispatch("get_recent_sales", {}, _branch_ctx(branch_id))

    assert summary["units"] == 2 and summary["bulk_ml"] == 100
    row = cust["results"][0]
    assert (row["units"], row["bulk_ml"]) == (2, 100)
    assert row["address"] == "First Street"
    assert {r["qty_unit"] for r in recent["results"]} == {"bottles", "mL"}


def test_branch_check_stock_hides_bulk_and_propose_rejects_bulk(app, sql):
    branch_id = make_branch(sql)
    bulk = make_product(sql, unit="BULK")
    make_inventory(sql, branch_id, bulk, stock_qty=0)
    with app.test_request_context():
        stock = ai_tools.dispatch("check_stock", {"sku": bulk}, _branch_ctx(branch_id))
        draft = ai_tools.dispatch("propose_stock_request", {"items": [{"sku": bulk, "qty": 5}]},
                                  _branch_ctx(branch_id))
    assert stock["results"] == []
    assert "Bulk/Refill" in draft["error"]


def test_admin_can_name_hq_for_sales(app, sql):
    with app.test_request_context():
        out = ai_tools.dispatch("get_sales_summary", {"period": "all_time", "branch_name": "HQ"}, _admin_ctx())
    assert "error" not in out, out
