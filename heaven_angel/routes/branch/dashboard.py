from urllib.parse import urlencode

from flask import render_template, url_for

from heaven_angel.db import query
from heaven_angel.decorators import branch_required
from heaven_angel.utils import (
    BOTTLE_UNITS, base_code_from_sku, business_today, percent_change,
)

from heaven_angel.routes.branch._common import _branch_id, bp


# ---------------------------------------------------------------- dashboard
@bp.route("/")
@branch_required
def dashboard():
    bid = _branch_id()
    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (bid,),
    )
    # BULK (Bulk/Refill) is left out: branches can't request it from
    # HQ (see request_stock()), so flagging it as "reorder" is noise.
    low_stock = [row for row in inventory
                 if row["unit"] in BOTTLE_UNITS and row["stock_qty"] <= row["reorder_level"]]
    # "SKUs carried" counts by base code, same as admin's Products page
    # (A1-85ML, A1-50ML and A1-BULK are one SKU).
    sku_count = len({base_code_from_sku(row["sku"], row["unit"]) for row in inventory})

    # Suggested reorder quantity: top back up to the same "full" reference
    # the dashboard/branch_stock fill-bar visualizations use elsewhere
    # (reorder_level * 3), never less than 1. Purely a starting point —
    # see the Reorder button below, which pre-fills Request Stock's cart
    # with this but leaves it fully editable there before submitting.
    for row in low_stock:
        row["suggested_qty"] = max(
            (row["reorder_level"] * 3) - row["stock_qty"], 1)
        # Only bottle sizes can be requested from HQ (see request_stock()).
        row["requestable"] = row["unit"] in BOTTLE_UNITS

    # Pre-built querystring for "Reorder all low stock" — repeated sku/qty
    # pairs that request_stock.html's JS reads with URLSearchParams.getAll()
    # to seed the cart on load. Built here (not in the template) since a
    # list-valued url_for() kwarg produces the same repeated-key querystring
    # but doing it as plain urlencode keeps the two sku[]/qty[] lists
    # trivially guaranteed to line up positionally.
    reorder_all_url = None
    reorderable = [row for row in low_stock if row["requestable"]]
    if reorderable:
        pairs = [("sku", row["sku"]) for row in reorderable] + \
            [("qty", row["suggested_qty"]) for row in reorderable]
        reorder_all_url = url_for(
            "branch.request_stock") + "?" + urlencode(pairs)

    # A "request" is now a multi-item delivery (see stock_request_items) —
    # this dashboard widget only needs the header plus a couple of totals,
    # not the line-item detail itself (that's what the receipt is for).
    pending_requests = query(
        """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                  COALESCE(SUM(GREATEST(sri.requested_qty - COALESCE(sri.received_qty, 0), 0)), 0) AS total_remaining
           FROM stock_requests sr
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
            WHERE sr.branch_id = %s AND sr.status IN ('Pending', 'Partially Fulfilled', 'In Transit')
           GROUP BY sr.request_id, sr.delivery_number, sr.status, sr.requested_at
           ORDER BY sr.requested_at DESC""",
        (bid,),
    )

    today_sales = query(
        """SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.branch_id = %s AND DATE(s.sold_at) = CURDATE()""",
        (bid,), fetchone=True,
    )

    # Last 14 days of daily revenue, zero-filled, for the hero tile's sparkline.
    from datetime import timedelta
    spark_rows = {
        r["day"]: float(r["amount"] or 0) for r in query(
            """SELECT DATE(sold_at) AS day, SUM(qty_sold * unit_price) AS amount
               FROM sales WHERE branch_id = %s AND sold_at >= CURDATE() - INTERVAL 14 DAY
               GROUP BY DATE(sold_at)""", (bid,))
    }
    spark_days = [business_today() - timedelta(days=i) for i in range(13, -1, -1)]
    spark_revenue = [spark_rows.get(d, 0.0) for d in spark_days]
    unit_rows = {
        r["day"]: int(r["units"] or 0) for r in query(
            """SELECT DATE(s.sold_at) AS day, SUM(s.qty_sold) AS units
               FROM sales s JOIN products p ON p.sku = s.sku
               WHERE s.branch_id = %s AND p.unit <> 'BULK' AND s.sold_at >= CURDATE() - INTERVAL 14 DAY
               GROUP BY DATE(s.sold_at)""", (bid,))
    }
    spark_units = [unit_rows.get(d, 0) for d in spark_days]
    week_revenue = sum(spark_revenue[7:])
    prev_week_revenue = sum(spark_revenue[:7])
    week_trend = percent_change(week_revenue, prev_week_revenue)

    # Trend arrow on the Today's sales tile: today's revenue vs.
    # yesterday up to this same time of day, so a morning isn't
    # compared against a whole finished day.
    yesterday_revenue = query(
        """SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales WHERE branch_id = %s
             AND sold_at >= CURDATE() - INTERVAL 1 DAY AND sold_at < NOW() - INTERVAL 1 DAY""",
        (bid,), fetchone=True,
    )["revenue"]
    today_sales["trend"] = percent_change(today_sales["revenue"], yesterday_revenue)

    return render_template(
        "branch/dashboard.html",
        inventory=inventory, low_stock=low_stock,
        pending_requests=pending_requests, today_sales=today_sales,
        reorder_all_url=reorder_all_url, sku_count=sku_count,
        spark_revenue=spark_revenue, spark_units=spark_units,
        week_revenue=week_revenue, week_trend=week_trend,
    )
