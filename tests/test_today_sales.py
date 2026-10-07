"""The top bar's "Today" sales chip (app.py's today_sales_summary() and
/api/today-sales): a branch sees only its own sales, an admin sees every
branch, and only today's sales count.
"""
from factories import login, make_branch, make_product, make_user
from utils import compact_peso


def _insert_sale(sql, branch_id, sku, qty, unit_price, days_ago=0):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sold_at, recorded_at)
           VALUES (%s, %s, %s, %s, NOW() - INTERVAL %s DAY, NOW() - INTERVAL %s DAY)""",
        (branch_id, sku, qty, unit_price, days_ago, days_ago),
    )
    sql.commit()
    cur.close()


def test_branch_chip_counts_only_its_own_sales_today(client, sql):
    branch_id = make_branch(sql)
    other_branch = make_branch(sql)
    sku = make_product(sql)
    _insert_sale(sql, branch_id, sku, 2, "150.00")
    _insert_sale(sql, branch_id, sku, 1, "100.00")
    _insert_sale(sql, branch_id, sku, 5, "999.00", days_ago=2)
    _insert_sale(sql, other_branch, sku, 3, "500.00")

    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    data = client.get("/api/today-sales").get_json()
    assert data["revenue"] == 400.0
    assert data["count"] == 2
    assert data["revenue_display"] == "₱400.00"

    html = client.get("/branch/").get_data(as_text=True)
    assert 'id="todaySalesChip"' in html
    assert "₱400.00" in html


def test_admin_chip_totals_every_branch(client, sql):
    sku = make_product(sql)
    branch_a = make_branch(sql)
    branch_b = make_branch(sql)

    admin = make_user(sql, role="Admin")
    login(client, admin["username"], admin["password"], "Admin")
    before = client.get("/api/today-sales").get_json()

    _insert_sale(sql, branch_a, sku, 1, "1000.00")
    _insert_sale(sql, branch_b, sku, 2, "250.00")

    after = client.get("/api/today-sales").get_json()
    assert round(after["revenue"] - before["revenue"], 2) == 1500.0
    assert after["count"] - before["count"] == 2
    assert after["scope"] == "all branches"


def test_today_sales_requires_sign_in(client):
    assert client.get("/api/today-sales").status_code == 401


def test_compact_amounts():
    assert compact_peso(0) == "₱0"
    assert compact_peso(850) == "₱850"
    assert compact_peso(10000) == "₱10k"
    assert compact_peso(12450) == "₱12.4k"
    assert compact_peso(1_250_000) == "₱1.2M"
