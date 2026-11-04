"""Data behind the admin Reports page's charts (the /admin/api/reports-data endpoint).

One function per chart group so each query block is findable and the route
itself only assembles the JSON. Every SQL fragment that depends on the chosen
granularity comes from the fixed allow-list in services/trends.py — never from
user text — so interpolating it stays injection-safe.
"""
from heaven_angel.db import query
from heaven_angel.services.finance import PACKAGE_DATE_SQL, UNIT_COST_SQL, cogs_sold
from heaven_angel.services.trends import trend_bucket_dates
from heaven_angel.utils import business_today


def sales_breakdowns():
    """Units sold by variant, and sales/stock per branch (HQ warehouse excluded)."""
    # Bottles only: a bulk product's qty_sold is mL, not a unit count, so
    # it would swamp the bottle figures if added in. Same for every
    # "units" figure below — bulk mL is reported separately as bulk_ml.
    by_variant = query(
        """SELECT p.variant, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM products p LEFT JOIN sales s ON p.sku = s.sku
           WHERE p.unit <> 'BULK'
           GROUP BY p.variant"""
    )

    # Revenue is a straight SUM of qty_sold * unit_price per sale row. Because
    # unit_price was captured at the moment each sale happened (the branch's
    # effective price at that time), this stays accurate even though branches
    # charge different prices from HQ and from each other.
    by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units_sold,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b LEFT JOIN sales s ON b.branch_id = s.branch_id
           LEFT JOIN products p ON p.sku = s.sku
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )
    stock_by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN bi.stock_qty ELSE 0 END), 0) AS total_stock,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN bi.stock_qty ELSE 0 END), 0) AS bulk_ml
           FROM branch_inventory bi JOIN branches b ON bi.branch_id = b.branch_id
           JOIN products p ON p.sku = bi.sku
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )
    return by_variant, by_branch, stock_by_branch


def movement_trend(trend_bucket):
    """Stock movement per bucket and type, over the chosen window."""
    window_sql = trend_bucket["window"]
    # Bottles and bulk mL are different units — one PRODUCTION batch of
    # 10 bottles and another of 2000mL bulk aren't "2010" of anything.
    # Kept apart here the same way sales/stock totals are split
    # elsewhere (see units_sold/bulk_ml_sold above): each movement_type
    # becomes up to two chart series, "<type> (bottles)" and
    # "<type> (bulk mL)", built client-side from these two columns.
    movement_trend = query(
        f"""SELECT {trend_bucket['trunc']} AS day, sml.movement_type,
                   COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN ABS(sml.change_qty) ELSE 0 END), 0) AS units,
                   COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN ABS(sml.change_qty) ELSE 0 END), 0) AS bulk_ml
            FROM stock_movement_logs sml
            JOIN products p ON p.sku = sml.sku
            WHERE sml.created_at >= NOW() - {window_sql}
            GROUP BY {trend_bucket['trunc']}, sml.movement_type ORDER BY day"""
    )
    for row in movement_trend:
        row["day"] = row["day"].isoformat()
    return movement_trend


def all_time_totals():
    """Returns (totals, package_totals) — both all-time."""
    # Combined totals across every branch, regardless of each branch's price.
    # All-time — feeds the top stat tiles and the Revenue vs. Capital chart,
    # neither of which are windowed by the granularity switch.
    #
    # Revenue here is branch sales AND closed package orders combined —
    # same "Closed only counts as a sale" rule used everywhere else
    # package revenue is computed (see admin.partners()'s docstring and
    # schema.sql's note on partner_inquiries.order_amount). units/
    # package_order_count are kept as separate figures rather than added
    # together, since "a unit sold at a branch" and "a package order" are
    # not the same kind of count.
    branch_totals = query(
        """SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku""",
        fetchone=True,
    )
    package_totals = query(
        """SELECT COUNT(*) AS order_count, COALESCE(SUM(order_amount), 0) AS revenue
           FROM partner_inquiries WHERE status = 'Closed'""",
        fetchone=True,
    )
    totals = {
        "units": branch_totals["units"],
        "bulk_ml": branch_totals["bulk_ml"],
        "revenue": branch_totals["revenue"] + package_totals["revenue"],
        "branch_revenue": branch_totals["revenue"],
        "package_revenue": package_totals["revenue"],
        "package_order_count": package_totals["order_count"],
    }
    return totals, package_totals


def windowed_financials(trend_bucket):
    """Returns (financials, windowed_revenue, windowed_materials_cost) for the chosen window."""
    window_sql = trend_bucket["window"]
    # Business-level financials.
    # - capital: no longer a logged ledger (see schema.sql's note on
    #   capital_contributions being removed) — it's SUM(total_cogs) over
    #   cogs_logs, i.e. the cost of goods actually produced (each
    #   formula's cost per unit x units logged as produced against it —
    #   see production()). Always all-time, paired with all-time
    #   revenue on the Revenue vs. Capital chart, same as before.
    # - raw_materials_purchased: SUM(package_cost) over raw_materials,
    #   all-time — everything ever spent buying material packages. Kept
    #   purely as a comparison figure alongside capital now (see the
    #   Revenue vs. Capital chart), it no longer feeds profit itself.
    # - revenue_windowed / materials_cost / profit: scoped to the same
    #   window as the granularity switch, for the Revenue, Materials &
    #   Profit chart. Profit is a simple gross figure — revenue (branch
    #   sales + closed package orders) minus cost of goods produced in
    #   that window — it doesn't subtract other costs (rent, payroll,
    #   etc.), which the app doesn't currently track. materials_cost here
    #   is windowed by cogs_logs.created_at (when a batch was logged),
    #   not by raw material purchase date — a batch's cost is incurred
    #   when the goods are actually produced, not when the ingredients
    #   were bought.
    #
    #   Package orders are windowed by partner_inquiries.created_at,
    #   same as everything else windowed here — the moment of inquiry,
    #   not the (untracked) moment an admin later marks it Closed. A
    #   package inquired within the window but closed after it falls
    #   outside; one inquired earlier but closed inside the window still
    #   counts, same limitation the rest of this window-based reporting
    #   already has for anything without its own "completed_at" column.
    capital_total = query(
        "SELECT COALESCE(SUM(total_cogs), 0) AS v FROM cogs_logs", fetchone=True
    )["v"]
    raw_materials_purchased = query(
        "SELECT COALESCE(SUM(package_cost), 0) AS v FROM raw_materials", fetchone=True
    )["v"]
    windowed_branch_revenue = query(
        f"SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales WHERE sold_at >= NOW() - {window_sql}",
        fetchone=True,
    )["v"]
    windowed_package_revenue = query(
        f"""SELECT COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries pi
            WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= NOW() - {window_sql}""",
        fetchone=True,
    )["v"]
    windowed_revenue = windowed_branch_revenue + windowed_package_revenue
    # Cost of goods SOLD in the window (finance.py): what the bottles that
    # went out the door — sales, refills, freebies, closed package orders —
    # cost to make. Production batches are capital, not this period's expense.
    windowed_materials_cost = cogs_sold(
        f"s.sold_at >= NOW() - {window_sql}", f"{PACKAGE_DATE_SQL} >= NOW() - {window_sql}")
    financials = {
        "capital": capital_total,
        "raw_materials_purchased": raw_materials_purchased,
        "revenue_windowed": windowed_revenue,
        "branch_revenue_windowed": windowed_branch_revenue,
        "package_revenue_windowed": windowed_package_revenue,
        "materials_cost": windowed_materials_cost,
        "profit": float(windowed_revenue) - float(windowed_materials_cost),
    }
    return financials, windowed_revenue, windowed_materials_cost


def kpi_tiles(granularity, trend_bucket, windowed_revenue, windowed_materials_cost):
    """KPI tiles + the Revenue/Cost/Profit series, with the preceding window for the up/down chips."""
    window_sql = trend_bucket["window"]
    # --- KPI tiles + Revenue/Cost/Profit chart: one point per bucket over
    # the same window, plus the immediately preceding window's totals for
    # the up/down chips. Bucket expressions reuse the allow-listed `trunc`
    # fragment with its column swapped for the table being bucketed.
    def _bucket(col):
        return trend_bucket["trunc"].replace("sml.created_at", col)

    def _series(sql_by_day):
        return {r["day"].isoformat(): r for r in sql_by_day}

    sales_series = _series(query(
        f"""SELECT {_bucket('s.sold_at')} AS day,
                   COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue,
                   COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units
            FROM sales s JOIN products p ON p.sku = s.sku
            WHERE s.sold_at >= NOW() - {window_sql}
            GROUP BY {_bucket('s.sold_at')}"""))
    package_series = _series(query(
        f"""SELECT {_bucket(PACKAGE_DATE_SQL)} AS day, COALESCE(SUM(order_amount), 0) AS revenue
            FROM partner_inquiries pi
            WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= NOW() - {window_sql}
            GROUP BY {_bucket(PACKAGE_DATE_SQL)}"""))
    # Cost of goods sold per bucket: register sales + closed package orders.
    cost_series = _series(query(
        f"""SELECT {_bucket('s.sold_at')} AS day,
                   COALESCE(SUM(s.qty_sold * COALESCE(uc.unit_cost, 0)), 0) AS cost
            FROM sales s LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = s.sku
            WHERE s.sold_at >= NOW() - {window_sql}
            GROUP BY {_bucket('s.sold_at')}"""))
    package_cost_series = _series(query(
        f"""SELECT {_bucket(PACKAGE_DATE_SQL)} AS day,
                   COALESCE(SUM(pii.qty * COALESCE(uc.unit_cost, 0)), 0) AS cost
            FROM partner_inquiry_items pii
            JOIN partner_inquiries pi ON pi.inquiry_id = pii.inquiry_id
            LEFT JOIN {UNIT_COST_SQL} uc ON uc.sku = pii.sku
            WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= NOW() - {window_sql}
            GROUP BY {_bucket(PACKAGE_DATE_SQL)}"""))
    # Cost of goods PRODUCED per bucket — capital, drives the Capital sparkline.
    produced_series = _series(query(
        f"""SELECT {_bucket('created_at')} AS day, COALESCE(SUM(total_cogs), 0) AS cost
            FROM cogs_logs WHERE created_at >= NOW() - {window_sql}
            GROUP BY {_bucket('created_at')}"""))

    raw_series = _series(query(
        f"""SELECT {_bucket('created_at')} AS day, COALESCE(SUM(package_cost), 0) AS raw
            FROM raw_materials WHERE created_at >= NOW() - {window_sql}
            GROUP BY {_bucket('created_at')}"""))

    bucket_days = {d.isoformat() for d in trend_bucket_dates(granularity, business_today())}
    bucket_days |= (set(sales_series) | set(package_series) | set(cost_series) | set(package_cost_series)
                    | set(produced_series) | set(raw_series))
    kpi_series = []
    for day in sorted(bucket_days):
        sale_row = sales_series.get(day)
        pkg_row = package_series.get(day)
        cost_row = cost_series.get(day)
        pkg_cost_row = package_cost_series.get(day)
        produced_row = produced_series.get(day)
        kpi_series.append({
            "day": day,
            "revenue": float((sale_row["revenue"] if sale_row else 0) + (pkg_row["revenue"] if pkg_row else 0)),
            "cost": float((cost_row["cost"] if cost_row else 0) + (pkg_cost_row["cost"] if pkg_cost_row else 0)),
            "produced": float(produced_row["cost"]) if produced_row else 0.0,
            "raw": float(raw_series[day]["raw"]) if day in raw_series else 0.0,
            "units": int(sale_row["units"]) if sale_row else 0,
        })

    window2_sql = trend_bucket["window2"]
    prev_revenue = query(
        f"""SELECT (SELECT COALESCE(SUM(qty_sold * unit_price), 0) FROM sales
                    WHERE sold_at >= NOW() - {window2_sql} AND sold_at < NOW() - {window_sql})
                 + (SELECT COALESCE(SUM(order_amount), 0) FROM partner_inquiries pi
                    WHERE pi.status = 'Closed' AND {PACKAGE_DATE_SQL} >= NOW() - {window2_sql}
                      AND {PACKAGE_DATE_SQL} < NOW() - {window_sql}) AS v""",
        fetchone=True)["v"]
    prev_cost = cogs_sold(
        f"s.sold_at >= NOW() - {window2_sql} AND s.sold_at < NOW() - {window_sql}",
        f"{PACKAGE_DATE_SQL} >= NOW() - {window2_sql} AND {PACKAGE_DATE_SQL} < NOW() - {window_sql}")
    prev_units = query(
        f"""SELECT COALESCE(SUM(s.qty_sold), 0) AS v FROM sales s JOIN products p ON p.sku = s.sku
            WHERE p.unit <> 'BULK' AND s.sold_at >= NOW() - {window2_sql} AND s.sold_at < NOW() - {window_sql}""",
        fetchone=True)["v"]
    windowed_units = query(
        f"""SELECT COALESCE(SUM(s.qty_sold), 0) AS v FROM sales s JOIN products p ON p.sku = s.sku
            WHERE p.unit <> 'BULK' AND s.sold_at >= NOW() - {window_sql}""",
        fetchone=True)["v"]
    kpi = {
        "revenue": float(windowed_revenue), "cost": float(windowed_materials_cost),
        "units": int(windowed_units),
        "prev_revenue": float(prev_revenue), "prev_cost": float(prev_cost),
        "prev_units": int(prev_units),
        "series": kpi_series,
    }
    return kpi


def sales_by_channel(package_totals):
    """Share of all-time sales per channel, largest first."""
    # Share of all-time sales by channel (each branch incl. the HQ
    # warehouse, plus closed package orders) for the donut's legend.
    channel_rows = query(
        """SELECT b.branch_name AS name, COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b LEFT JOIN sales s ON s.branch_id = b.branch_id
           GROUP BY b.branch_id, b.branch_name, b.is_hq
           ORDER BY b.is_hq DESC, b.branch_name""")
    sales_by_channel = [{"name": r["name"], "revenue": float(r["revenue"])} for r in channel_rows]
    if package_totals["revenue"]:
        sales_by_channel.append({"name": "Package orders", "revenue": float(package_totals["revenue"])})
    sales_by_channel = sorted((c for c in sales_by_channel if c["revenue"] > 0),
                              key=lambda c: c["revenue"], reverse=True)
    return sales_by_channel
