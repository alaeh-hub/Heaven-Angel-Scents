
from heaven_angel.db import query
from heaven_angel.services.finance import PACKAGE_DATE_SQL, cogs_sold
from heaven_angel.services.reports import DISCREPANCY_ITEM_JOIN, DISCREPANCY_UNITS_SQL

from heaven_angel.routes.ai_tools._common import MAX_ROWS, _branch_filter, _resolve_branch_ids


PERIOD_TMPL = {
    "today": "DATE({col}) = CURDATE()",
    "this_week": "YEARWEEK({col}, 3) = YEARWEEK(CURDATE(), 3)",
    "this_month": "YEAR({col}) = YEAR(CURDATE()) AND MONTH({col}) = MONTH(CURDATE())",
    "last_month": ("YEAR({col}) = YEAR(CURDATE() - INTERVAL 1 MONTH) "
                   "AND MONTH({col}) = MONTH(CURDATE() - INTERVAL 1 MONTH)"),
    "last_7_days": "DATE({col}) >= CURDATE() - INTERVAL 6 DAY",
    "last_30_days": "DATE({col}) >= CURDATE() - INTERVAL 29 DAY",
    "this_year": "YEAR({col}) = YEAR(CURDATE())",
    "all_time": "1 = 1",
}


PERIOD_NAMES = ", ".join(PERIOD_TMPL)


def period_sql(period, col):
    """WHERE fragment for a named period on a timestamp column. Only ever
    formatted with a hard-coded column name, never model input."""
    return PERIOD_TMPL[period].format(col=col)


_PERIOD_SQL = {name: period_sql(name, "s.sold_at") for name in PERIOD_TMPL}


# Bulk products' qty_sold is mL, not bottles, so every "units" figure below
# counts bottles only and bulk volume comes back separately as bulk_ml.
# Both need `products p` joined on s.sku.
_UNITS_SQL = "COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END),0) AS units"


_BULK_ML_SQL = "COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END),0) AS bulk_ml"


def get_sales_summary(args, ctx):
    period = (args.get("period") or "today").strip()
    if period not in _PERIOD_SQL:
        return {"error": f"period must be one of {sorted(_PERIOD_SQL)}."}

    branch_name = (args.get("branch_name") or "").strip()
    by_branch = bool(args.get("by_branch"))
    # HQ records its own sales too (admin Record Sale), so "HQ" is a
    # valid location here.
    branch_ids, err = _resolve_branch_ids(ctx, branch_name, include_hq=True)
    if err:
        return {"error": err}

    conditions = [_PERIOD_SQL[period]]
    params = []
    if branch_ids is not None:
        if not branch_ids:
            return {"error": "No matching branch."}
        placeholders = ",".join(["%s"] * len(branch_ids))
        conditions.append(f"s.branch_id IN ({placeholders})")
        params.extend(branch_ids)

    totals = query(
        f"SELECT {_UNITS_SQL}, {_BULK_ML_SQL}, COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue "
        "FROM sales s JOIN products p ON s.sku = p.sku WHERE " + " AND ".join(conditions),
        tuple(params), fetchone=True,
    )
    top_items = query(
        f"SELECT p.item_name, {_UNITS_SQL}, {_BULK_ML_SQL}, SUM(s.qty_sold*s.unit_price) AS revenue "
        "FROM sales s JOIN products p ON s.sku = p.sku WHERE " + " AND ".join(conditions) +
        " GROUP BY p.item_name ORDER BY units DESC, bulk_ml DESC LIMIT 5",
        tuple(params),
    )
    # Sale vs Refill vs Freebie, Cash vs Credit, so "how much went on
    # credit" or "how many refills" doesn't need a tool of its own.
    by_type = query(
        "SELECT s.sale_type, s.payment_method, COUNT(*) AS transactions, "
        f"{_UNITS_SQL}, {_BULK_ML_SQL}, COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue "
        "FROM sales s JOIN products p ON s.sku = p.sku WHERE " + " AND ".join(conditions) +
        " GROUP BY s.sale_type, s.payment_method ORDER BY revenue DESC",
        tuple(params),
    )
    result = {
        "period": period,
        "units": totals["units"],
        "bulk_ml": totals["bulk_ml"],
        "revenue": float(totals["revenue"]),
        "top_items": [
            {"item_name": r["item_name"], "units": r["units"], "bulk_ml": r["bulk_ml"],
             "revenue": float(r["revenue"])}
            for r in top_items
        ],
        "by_type": [
            {"sale_type": r["sale_type"], "payment_method": r["payment_method"],
             "transactions": r["transactions"], "units": r["units"], "bulk_ml": r["bulk_ml"],
             "revenue": float(r["revenue"])}
            for r in by_type
        ],
    }

    # Dashboard / Reports "Total revenue" also counts Closed partner
    # package orders (partner_inquiries.order_amount). Only fleet-wide —
    # a package order isn't tied to any branch.
    if ctx["role"] == "Admin" and branch_ids is None:
        pkg = query(
            "SELECT COUNT(*) AS n, COALESCE(SUM(order_amount),0) AS revenue FROM partner_inquiries "
            "WHERE status = 'Closed' AND " + period_sql(period, "created_at"),
            fetchone=True,
        )
        result["package_order_count"] = pkg["n"]
        result["package_revenue"] = float(pkg["revenue"])
        result["total_revenue"] = round(result["revenue"] + result["package_revenue"], 2)

    # by_branch: one GROUP BY query instead of the model having to loop
    # get_sales_summary once per branch_name (which is what used to blow
    # through MAX_TOOL_ROUNDS on "how does revenue split across branches"
    # -style questions — there was previously no way to get a per-branch
    # breakdown except calling this tool once per branch, and no tool to
    # even list branch names first). Only meaningful when the result
    # isn't already scoped to one named branch; LEFT JOIN so a branch
    # with zero sales in the period still shows up as 0 instead of being
    # silently missing from the split.
    if by_branch and branch_ids is None:
        by_branch_rows = query(
            f"SELECT b.branch_name, b.is_hq, {_UNITS_SQL}, {_BULK_ML_SQL}, "
            "COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue "
            "FROM branches b LEFT JOIN sales s ON s.branch_id = b.branch_id AND " +
            _PERIOD_SQL[period] + " LEFT JOIN products p ON p.sku = s.sku" +
            " GROUP BY b.branch_id, b.branch_name ORDER BY revenue DESC"
        )
        result["by_branch"] = [
            {"branch_name": r["branch_name"], "is_hq": bool(r["is_hq"]),
             "units": r["units"], "bulk_ml": r["bulk_ml"], "revenue": float(r["revenue"])}
            for r in by_branch_rows
        ]

    return result


def get_credit_purchases(args, ctx):
    """Store-credit ("utang") taken per buyer, grouped the same way as the
    Credit Purchases page (branch.credit_purchases)."""
    frag, params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "s.branch_id")
    if err:
        return {"error": err}
    rows = query(
        "SELECT b.branch_name, COALESCE(s.buyer_name, bu.username) AS buyer_name, "
        f"COUNT(*) AS transactions, {_UNITS_SQL}, {_BULK_ML_SQL}, "
        "COALESCE(SUM(s.qty_sold*s.unit_price),0) AS amount, MAX(s.sold_at) AS last_taken_at "
        "FROM sales s JOIN branches b ON s.branch_id = b.branch_id "
        "JOIN products p ON s.sku = p.sku "
        "LEFT JOIN users bu ON s.buyer_user_id = bu.user_id "
        "WHERE s.payment_method = 'Credit'" + frag +
        " GROUP BY b.branch_name, COALESCE(s.buyer_name, bu.username) "
        "ORDER BY amount DESC LIMIT %s",
        tuple(params + [MAX_ROWS]),
    )
    return {
        "results": [
            {"branch": r["branch_name"], "buyer": r["buyer_name"] or "(unnamed)",
             "transactions": r["transactions"], "units": r["units"], "bulk_ml": r["bulk_ml"],
             "amount": float(r["amount"]), "last_taken_at": r["last_taken_at"].isoformat()}
            for r in rows
        ],
        "note": "All-time totals of Credit-payment sales per buyer. The system doesn't record repayments.",
    }


# ---------------------------------------------------------------------------
# Tool: get_customers  (both roles, branch-scoped)
# ---------------------------------------------------------------------------


def get_customers(args, ctx):
    """Named walk-in customers, derived from sales.customer_name the same
    way the Customers pages do (there's no separate customers table):
    address from the customer's first sale, plus where they last bought."""
    name = (args.get("name") or "").strip()
    frag, branch_params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "s.branch_id")
    if err:
        return {"error": err}
    where = "s.customer_name IS NOT NULL AND s.customer_name <> ''" + frag
    name_params = []
    if name:
        where += " AND s.customer_name LIKE %s"
        name_params.append(f"%{name}%")
    # Placeholder order: address subquery, last-branch subquery, inner
    # aggregate (branch + name), LIMIT.
    params = branch_params + branch_params + branch_params + name_params + [MAX_ROWS]
    inner = (
        f"SELECT s.customer_name, COUNT(*) AS transactions, {_UNITS_SQL}, {_BULK_ML_SQL}, "
        "COALESCE(SUM(s.qty_sold*s.unit_price),0) AS spent, "
        "MIN(s.sold_at) AS first_sold_at, MAX(s.sold_at) AS last_visit, "
        "GROUP_CONCAT(DISTINCT b.branch_name ORDER BY b.branch_name SEPARATOR ', ') AS branches "
        "FROM sales s JOIN branches b ON s.branch_id = b.branch_id "
        "JOIN products p ON s.sku = p.sku "
        "WHERE " + where + " GROUP BY s.customer_name"
    )
    rows = query(
        "SELECT agg.*, "
        "(SELECT f.customer_address FROM sales f WHERE f.customer_name = agg.customer_name "
        " AND f.sold_at = agg.first_sold_at" + frag.replace("s.branch_id", "f.branch_id") +
        " ORDER BY f.sale_id LIMIT 1) AS address, "
        "(SELECT lb.branch_name FROM sales l JOIN branches lb ON l.branch_id = lb.branch_id "
        " WHERE l.customer_name = agg.customer_name AND l.sold_at = agg.last_visit" +
        frag.replace("s.branch_id", "l.branch_id") +
        " ORDER BY l.sale_id DESC LIMIT 1) AS last_branch "
        "FROM (" + inner + ") agg ORDER BY agg.spent DESC LIMIT %s",
        tuple(params),
    )
    return {
        "results": [
            {"customer": r["customer_name"], "address": r["address"] or None,
             "transactions": r["transactions"], "units": r["units"], "bulk_ml": r["bulk_ml"],
             "spent": float(r["spent"]), "last_visit": r["last_visit"].isoformat(),
             "last_branch": r["last_branch"], "branches": r["branches"]}
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Admin-only tools: raw materials, suppliers, COGS settings (Formulas
# page), bulk batches, production cost (COGS), packages, partner
# inquiries, branch performance, financials. See _ADMIN_ONLY.
# ---------------------------------------------------------------------------


_SALE_TYPES = {"Sale", "Refill", "Freebie"}


def get_recent_sales(args, ctx):
    """Individual sale rows, newest first — the Sales History / Record
    Sale "Recent sales" list."""
    sale_type = (args.get("sale_type") or "").strip()
    if sale_type and sale_type not in _SALE_TYPES:
        return {"error": f"sale_type must be one of {sorted(_SALE_TYPES)}."}
    try:
        limit = max(1, min(int(args.get("limit") or 10), MAX_ROWS))
    except (TypeError, ValueError):
        limit = 10
    frag, params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "s.branch_id")
    if err:
        return {"error": err}
    sql = (
        "SELECT s.sold_at, b.branch_name, p.item_name, p.sku, p.unit, s.sale_type, s.payment_method, "
        "s.qty_sold, s.unit_price, COALESCE(s.buyer_name, bu.username) AS buyer, s.customer_name "
        "FROM sales s JOIN branches b ON s.branch_id = b.branch_id "
        "JOIN products p ON s.sku = p.sku "
        "LEFT JOIN users bu ON s.buyer_user_id = bu.user_id "
        "WHERE 1=1" + frag
    )
    if sale_type:
        sql += " AND s.sale_type = %s"
        params.append(sale_type)
    sql += " ORDER BY s.sold_at DESC, s.sale_id DESC LIMIT %s"
    params.append(limit)
    rows = query(sql, tuple(params))
    return {
        "results": [
            {"sold_at": r["sold_at"].isoformat(), "branch": r["branch_name"],
             "item_name": r["item_name"], "sku": r["sku"], "sale_type": r["sale_type"],
             "payment_method": r["payment_method"], "qty": r["qty_sold"],
             "qty_unit": "mL" if r["unit"] == "BULK" else "bottles",
             "unit_price": float(r["unit_price"]),
             "total": float(r["qty_sold"] * r["unit_price"]),
             "customer": r["customer_name"] or "Walk-in",
             "credit_buyer": r["buyer"] if r["payment_method"] == "Credit" else None}
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Tool: get_discrepancies  (both roles, branch-scoped)
# ---------------------------------------------------------------------------


def get_branch_performance(args, ctx):
    """Per retail branch (HQ excluded), same figures as the Branch
    Performance page: bottles and bulk mL sold, revenue, current stock,
    discrepancy units and turnover (bottles sold / bottles on hand)."""
    period = (args.get("period") or "all_time").strip()
    if period not in PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    rows = query(
        f"""SELECT b.branch_name,
                  COALESCE(sa.units_sold, 0) AS units_sold, COALESCE(sa.bulk_ml_sold, 0) AS bulk_ml_sold,
                  COALESCE(sa.revenue, 0) AS revenue, COALESCE(sa.sales_count, 0) AS sales_count,
                  COALESCE(st.total_stock, 0) AS total_stock, COALESCE(st.bulk_ml_stock, 0) AS bulk_ml_stock,
                  COALESCE(d.discrepancy_units, 0) AS discrepancy_units
           FROM branches b
           LEFT JOIN (
               SELECT s.branch_id,
                      SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END) AS units_sold,
                      SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END) AS bulk_ml_sold,
                      SUM(s.qty_sold * s.unit_price) AS revenue, COUNT(*) AS sales_count
               FROM sales s JOIN products p ON p.sku = s.sku
               WHERE {period_sql(period, "s.sold_at")} GROUP BY s.branch_id
           ) sa ON sa.branch_id = b.branch_id
           LEFT JOIN (
               SELECT bi.branch_id,
                      SUM(CASE WHEN p.unit <> 'BULK' THEN bi.stock_qty ELSE 0 END) AS total_stock,
                      SUM(CASE WHEN p.unit = 'BULK' THEN bi.stock_qty ELSE 0 END) AS bulk_ml_stock
               FROM branch_inventory bi JOIN products p ON p.sku = bi.sku GROUP BY bi.branch_id
           ) st ON st.branch_id = b.branch_id
           LEFT JOIN (
               SELECT sml.branch_id, SUM({DISCREPANCY_UNITS_SQL}) AS discrepancy_units
               FROM stock_movement_logs sml {DISCREPANCY_ITEM_JOIN}
               WHERE sml.reference_type = 'STOCK_REQUEST' AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
                 AND {period_sql(period, "sml.created_at")}
               GROUP BY sml.branch_id
           ) d ON d.branch_id = b.branch_id
           WHERE b.is_hq = FALSE
           ORDER BY revenue DESC, b.branch_name"""
    )
    return {
        "period": period,
        "note": "Retail branches only (HQ excluded). Stock on hand is as of now. "
                "Turnover = bottles sold / bottles on hand now.",
        "results": [
            {"branch": r["branch_name"], "sales_count": r["sales_count"],
             "bottles_sold": r["units_sold"], "bulk_ml_sold": r["bulk_ml_sold"],
             "revenue": float(r["revenue"]), "bottles_on_hand": r["total_stock"],
             "bulk_ml_on_hand": r["bulk_ml_stock"], "discrepancy_units": r["discrepancy_units"],
             "turnover": round(float(r["units_sold"]) / float(r["total_stock"]), 2) if r["total_stock"] else None}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Tool: get_financials  (admin only)
# ---------------------------------------------------------------------------


def get_financials(args, ctx):
    """Business-level figures, same rules as the Dashboard / Reports page:
    revenue = register sales (HQ + branches) + Closed partner package
    orders; cost of goods sold = the production cost of what was sold or
    shipped (finance.py); gross profit = revenue - cost of goods sold.
    Rent, payroll etc. aren't tracked."""
    period = (args.get("period") or "this_month").strip()
    if period not in PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    sales_rev = query(
        "SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales WHERE " + period_sql(period, "sold_at"),
        fetchone=True,
    )["v"]
    pkg = query(
        "SELECT COUNT(*) AS n, COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries "
        "WHERE status = 'Closed' AND " + period_sql(period, "COALESCE(closed_at, created_at)"),
        fetchone=True,
    )
    produced = query(
        "SELECT COALESCE(SUM(total_cogs), 0) AS v FROM cogs_logs WHERE " + period_sql(period, "created_at"),
        fetchone=True,
    )["v"]
    cogs = cogs_sold(period_sql(period, "s.sold_at"), period_sql(period, PACKAGE_DATE_SQL))
    materials = query(
        "SELECT COALESCE(SUM(package_cost), 0) AS v FROM raw_materials WHERE " + period_sql(period, "created_at"),
        fetchone=True,
    )["v"]
    top_packages = query(
        "SELECT package_name_snapshot AS package, COUNT(*) AS orders, SUM(order_amount) AS revenue "
        "FROM partner_inquiries WHERE status = 'Closed' AND package_name_snapshot IS NOT NULL AND " +
        period_sql(period, "COALESCE(closed_at, created_at)") +
        " GROUP BY package_name_snapshot ORDER BY revenue DESC LIMIT 5"
    )
    revenue = float(sales_rev) + float(pkg["v"])
    return {
        "period": period,
        "sales_revenue": float(sales_rev),
        "package_revenue": float(pkg["v"]),
        "package_orders_closed": pkg["n"],
        "total_revenue": round(revenue, 2),
        "cost_of_goods_sold": round(float(cogs), 2),
        "gross_profit": round(revenue - float(cogs), 2),
        "cost_of_goods_produced": float(produced),
        "raw_materials_purchased": float(materials),
        "top_packages": [
            {"package": r["package"], "orders": r["orders"], "revenue": float(r["revenue"] or 0)}
            for r in top_packages
        ],
        "note": "Package orders count as revenue only once Closed, dated by when they were closed. "
                "Gross profit subtracts the cost of goods sold, not the cost of goods produced in the period. "
                "Gross profit doesn't subtract rent, payroll or other costs the system doesn't track.",
    }


# ---------------------------------------------------------------------------
# Tool: propose_stock_request  (writes a DRAFT only — see module docstring)
# ---------------------------------------------------------------------------
