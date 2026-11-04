
from flask import (
    render_template,
)

from heaven_angel.db import query
from heaven_angel.decorators import admin_required
from heaven_angel.services.finance import PACKAGE_DATE_SQL, UNIT_COST_SQL, cogs_sold
from heaven_angel.services.reports import (LOW_STOCK_WHERE)
from heaven_angel.utils import (
    business_today, percent_change,
)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- dashboard
@bp.route("/")
@admin_required
def dashboard():
    stats = {
        "sku_count": query("SELECT COUNT(*) c FROM products", fetchone=True)["c"],
        "branch_count": query("SELECT COUNT(*) c FROM branches WHERE is_hq = FALSE", fetchone=True)["c"],
        "pending_requests": query(
            "SELECT COUNT(*) c FROM stock_requests WHERE status IN ('Pending', 'Partially Fulfilled')", fetchone=True
        )["c"],
        # HQ's warehouse plus every branch — see LOW_STOCK_WHERE.
        "low_stock_count": query(
            f"""SELECT COUNT(*) c FROM branch_inventory bi
               JOIN branches b ON bi.branch_id = b.branch_id
               JOIN products p ON bi.sku = p.sku
               WHERE {LOW_STOCK_WHERE}""",
            fetchone=True,
        )["c"],
    }

    low_stock = query(
        f"""SELECT b.branch_name, b.is_hq, p.item_name, p.sku, bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi
           JOIN branches b ON bi.branch_id = b.branch_id
           JOIN products p ON bi.sku = p.sku
           WHERE {LOW_STOCK_WHERE}
           ORDER BY bi.stock_qty ASC LIMIT 8"""
    )

    recent_requests = query(
        """SELECT sr.request_id, sr.delivery_number, b.branch_name, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty
           FROM stock_requests sr
           JOIN branches b ON sr.branch_id = b.branch_id
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
           GROUP BY sr.request_id
           ORDER BY sr.requested_at DESC LIMIT 6"""
    )

    # Same exclusion as movement_logs() below: a delivery's own
    # DISPATCH/RECEIPT/DAMAGE/ADJUSTMENT rows (reference_type=
    # 'STOCK_REQUEST') are left off this preview too, so it doesn't show
    # one SKU out of a multi-item delivery as if that were the whole
    # story — that detail belongs on the delivery's own page/receipt.
    recent_activity = query(
        """SELECT sml.created_at, b.branch_name, p.item_name, sml.change_qty, sml.movement_type
           FROM stock_movement_logs sml
           JOIN branches b ON sml.branch_id = b.branch_id
           JOIN products p ON sml.sku = p.sku
           WHERE (sml.reference_type IS NULL OR sml.reference_type != 'STOCK_REQUEST')
           ORDER BY sml.created_at DESC LIMIT 8"""
    )

    # Operational snapshot: bottles on hand per branch (the sales-side
    # rankings — top sellers/packages/partners — live on the Reports page).
    stock_by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN bi.stock_qty ELSE 0 END), 0) AS total_stock
           FROM branch_inventory bi JOIN branches b ON bi.branch_id = b.branch_id
           JOIN products p ON p.sku = bi.sku
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY total_stock DESC"""
    )

    # Business-level financials: total revenue across branch sales AND
    # closed package orders, and total spent on raw materials, so a
    # simple gross profit can be shown at a glance. See reports_data()
    # below for the same figures broken out into charts on the Reports
    # page.
    #
    # package_sales is only ever Closed inquiries' order_amount — see
    # the note above top_packages. It's folded into total_revenue (and
    # therefore profit) the same way branch sales are, since a closed
    # package order is real money in exactly the same sense.
    #
    # "Capital" is no longer its own ledger (see schema.sql's note on
    # capital_contributions being removed) — it's now the cost of goods
    # actually produced: SUM(total_cogs) over cogs_logs, i.e. each
    # formula's cost per unit x how many units were logged as produced
    # against it (see production()). "Raw materials bought" is
    # tracked alongside it (SUM(package_cost) over raw_materials) purely
    # as a comparison figure now — what's been paid for material
    # packages vs. what's actually gone into produced goods — it no
    # longer feeds capital/profit itself.
    total_cogs = query(
        "SELECT COALESCE(SUM(total_cogs), 0) AS v FROM cogs_logs", fetchone=True
    )["v"]
    raw_materials_purchased = query(
        "SELECT COALESCE(SUM(package_cost), 0) AS v FROM raw_materials", fetchone=True
    )["v"]
    branch_sales_revenue = query(
        "SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales", fetchone=True
    )["v"]
    package_sales_revenue = query(
        "SELECT COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries WHERE status = 'Closed'",
        fetchone=True,
    )["v"]
    total_revenue = branch_sales_revenue + package_sales_revenue

    # Trend arrow on the Total revenue tile: month-to-date revenue vs.
    # the same stretch of last month (1st of last month up to this exact
    # moment one month ago), so a half-finished month isn't compared
    # against a whole one. MySQL's `- INTERVAL 1 MONTH` clamps to the
    # month's last day (Mar 31 -> Feb 28/29). Rows between those two
    # windows (the rest of last month) fall in neither sum.
    #
    # A closed package order counts toward the month it was closed in
    # (partner_inquiries.closed_at; older rows fall back to created_at).
    month_revenue = query(
        """SELECT
               COALESCE(SUM(CASE WHEN t.at >= CURDATE() - INTERVAL (DAY(CURDATE()) - 1) DAY
                                 THEN t.amount END), 0) AS current_period,
               COALESCE(SUM(CASE WHEN t.at < NOW() - INTERVAL 1 MONTH
                                 THEN t.amount END), 0) AS previous_period
           FROM (
               SELECT sold_at AS at, qty_sold * unit_price AS amount FROM sales
               UNION ALL
               SELECT COALESCE(closed_at, created_at), order_amount FROM partner_inquiries WHERE status = 'Closed'
           ) t
           WHERE t.at >= CURDATE() - INTERVAL (DAY(CURDATE()) - 1) DAY - INTERVAL 1 MONTH
             AND t.at < NOW()""",
        fetchone=True,
    )

    # Profit is revenue minus cost of goods SOLD (see finance.py), not minus
    # what was produced; production cost stays visible as "capital".
    cogs_total = cogs_sold()
    receivables = query(
        "SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales "
        "WHERE payment_method = 'Credit' AND credit_settled_at IS NULL", fetchone=True)["v"]

    # Last 14 days of revenue (branch sales + closed package orders) and
    # profit (that revenue minus the cost of what was sold that day), zero-
    # filled, for the sparklines on the Total revenue / Net profit tiles.
    from datetime import timedelta
    spark_days = [business_today() - timedelta(days=i) for i in range(13, -1, -1)]
    rev_by_day = {}
    for r in query(
        """SELECT d AS day, SUM(amount) AS amount FROM (
               SELECT DATE(sold_at) AS d, qty_sold * unit_price AS amount FROM sales
                 WHERE sold_at >= CURDATE() - INTERVAL 14 DAY
               UNION ALL
               SELECT DATE(COALESCE(closed_at, created_at)), order_amount FROM partner_inquiries
                 WHERE status = 'Closed' AND COALESCE(closed_at, created_at) >= CURDATE() - INTERVAL 14 DAY
           ) t GROUP BY d"""):
        rev_by_day[r["day"]] = float(r["amount"] or 0)
    production_by_day = {
        r["day"]: float(r["amount"] or 0) for r in query(
            """SELECT DATE(created_at) AS day, SUM(total_cogs) AS amount FROM cogs_logs
               WHERE created_at >= CURDATE() - INTERVAL 14 DAY GROUP BY DATE(created_at)""")
    }
    cogs_by_day = {}
    for r in query(
        f"""SELECT DATE(s.sold_at) AS day, SUM(s.qty_sold * COALESCE(uc.unit_cost, 0)) AS amount
            FROM sales s LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = s.sku
            WHERE s.sold_at >= CURDATE() - INTERVAL 14 DAY GROUP BY DATE(s.sold_at)"""):
        cogs_by_day[r["day"]] = cogs_by_day.get(r["day"], 0.0) + float(r["amount"] or 0)
    for r in query(
        f"""SELECT DATE({PACKAGE_DATE_SQL}) AS day, SUM(pii.qty * COALESCE(uc.unit_cost, 0)) AS amount
            FROM partner_inquiry_items pii
            JOIN partner_inquiries pi ON pi.inquiry_id = pii.inquiry_id
            LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = pii.sku
            WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= CURDATE() - INTERVAL 14 DAY
            GROUP BY DATE({PACKAGE_DATE_SQL})"""):
        cogs_by_day[r["day"]] = cogs_by_day.get(r["day"], 0.0) + float(r["amount"] or 0)
    raw_by_day = {
        r["day"]: float(r["amount"] or 0) for r in query(
            """SELECT DATE(created_at) AS day, SUM(package_cost) AS amount FROM raw_materials
               WHERE created_at >= CURDATE() - INTERVAL 14 DAY GROUP BY DATE(created_at)""")
    }
    spark_cost = [production_by_day.get(d, 0.0) for d in spark_days]
    spark_raw = [raw_by_day.get(d, 0.0) for d in spark_days]
    spark_revenue = [rev_by_day.get(d, 0.0) for d in spark_days]
    spark_profit = [rev_by_day.get(d, 0.0) - cogs_by_day.get(d, 0.0) for d in spark_days]

    financials = {
        "spark_revenue": spark_revenue,
        "spark_profit": spark_profit,
        "spark_cost": spark_cost,
        "spark_raw": spark_raw,
        "profit_margin": ((float(total_revenue) - cogs_total) / float(total_revenue) * 100) if total_revenue else None,
        "receivables": receivables,
        "capital": total_cogs,
        "revenue": total_revenue,
        "branch_sales_revenue": branch_sales_revenue,
        "package_sales_revenue": package_sales_revenue,
        "materials_cost": cogs_total,
        "raw_materials_purchased": raw_materials_purchased,
        "profit": float(total_revenue) - cogs_total,
        "revenue_trend": percent_change(
            month_revenue["current_period"], month_revenue["previous_period"]),
    }

    return render_template(
        "admin/dashboard.html",
        stats=stats, low_stock=low_stock,
        recent_requests=recent_requests, recent_activity=recent_activity,
        stock_by_branch=stock_by_branch, financials=financials,
    )
