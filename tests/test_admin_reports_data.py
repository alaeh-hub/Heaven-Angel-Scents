"""The admin Reports page's chart endpoint: its JSON shape and a couple of
figures, for every granularity (an unknown one falls back to daily)."""
import pytest

from factories import login, make_branch, make_inventory, make_product, make_user

KEYS = {"by_variant", "by_branch", "movement_trend", "stock_by_branch", "totals",
        "financials", "kpi", "sales_by_channel"}


@pytest.mark.parametrize("granularity", ["daily", "weekly", "monthly", "yearly", "bogus"])
def test_reports_data_shape_and_totals(client, sql, granularity):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, 20)
    cur = sql.cursor()
    cur.execute("INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sold_at) "
                "VALUES (%s, %s, 3, 120.00, NOW() - INTERVAL 1 DAY)", (branch_id, sku))
    cur.execute("INSERT INTO cogs_logs (sku, qty_produced, cogs_per_unit, total_cogs, created_at) "
                "VALUES (%s, 10, 30, 300, NOW() - INTERVAL 2 DAY)", (sku,))
    sql.commit()
    cur.close()
    admin = make_user(sql, role="Admin")
    login(client, admin["username"], admin["password"], "Admin")

    data = client.get(f"/admin/api/reports-data?granularity={granularity}").get_json()

    assert KEYS <= set(data)
    assert float(data["totals"]["revenue"]) >= 360
    assert float(data["financials"]["capital"]) >= 300
    assert data["kpi"]["series"], "the KPI series should have one point per bucket"
    assert {c["name"] for c in data["sales_by_channel"]}
