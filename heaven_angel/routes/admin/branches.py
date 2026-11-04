
from flask import (
    current_app, flash, redirect, render_template,
    request, url_for,
)

from heaven_angel.db import query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.services.reports import (DISCREPANCY_ITEM_JOIN, DISCREPANCY_UNITS_SQL, LOW_STOCK_WHERE)
from heaven_angel.sockets import notify_admin

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- branches
@bp.route("/branches", methods=["GET", "POST"])
@admin_required
def branches():
    if request.method == "POST":
        name = request.form.get("branch_name", "").strip()
        location = request.form.get("location", "").strip()
        if not name:
            flash("Branch name is required.", "error")
        else:
            try:
                with transaction() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO branches (branch_name, location) VALUES (%s, %s)", (
                            name, location)
                    )
                    new_branch_id = cur.lastrowid
                    cur.execute("SELECT sku FROM products")
                    skus = [row[0] for row in cur.fetchall()]
                    for sku in skus:
                        cur.execute(
                            "INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, 0)",
                            (new_branch_id, sku),
                        )
                    cur.close()
                notify_admin("branches")
                log_action("add_branch", target=name, details=location or None)
                flash(f"{name} added.", "success")
            except Exception:
                current_app.logger.exception(
                    "add branch failed for name=%s", name)
                flash("A branch with that name already exists.", "error")
        return redirect(url_for("admin.branches"))

    branch_list = query(
        """SELECT b.*, COUNT(DISTINCT u.user_id) AS user_count
           FROM branches b LEFT JOIN users u ON b.branch_id = u.branch_id
           WHERE b.is_hq = FALSE GROUP BY b.branch_id ORDER BY b.branch_name"""
    )
    return render_template("admin/branches.html", branch_list=branch_list)


# ---------------------------------------------------------------- low stock (fleet-wide)
@bp.route("/low-stock")
@admin_required
def low_stock():
    """Full, searchable/paginated list of every branch SKU at or below
    its reorder level — the dashboard's "Low stock across branches"
    widget only ever shows a preview of 8 rows (see dashboard()'s
    `low_stock` query, LIMIT 8), so a branch with more than a handful
    of SKUs to reorder had no single place to see the rest without
    guessing which ones fell off that preview. This page is that place:
    every low-stock row, filterable per branch, with a per-branch
    summary up top so HQ can prioritize which branch to restock first.
    HQ's own warehouse is listed too (first), so HQ also sees what it
    needs to produce or buy for itself.
    """
    branch_filter = request.args.get("branch_id", "all")
    # HQ's warehouse first, then branches alphabetically.
    branch_list = query(
        "SELECT branch_id, branch_name, is_hq FROM branches ORDER BY is_hq DESC, branch_name")

    # Per-branch rollup, always fleet-wide (the tabs below only filter
    # the row list) — same convention as discrepancies()'s summary.
    summary = query(
        f"""SELECT b.branch_id, b.branch_name, b.is_hq,
                  COUNT(*) AS low_stock_count,
                  COALESCE(SUM(CASE WHEN bi.stock_qty = 0 THEN 1 ELSE 0 END), 0) AS out_of_stock_count
           FROM branch_inventory bi
           JOIN branches b ON bi.branch_id = b.branch_id
           JOIN products p ON bi.sku = p.sku
           WHERE {LOW_STOCK_WHERE}
           GROUP BY b.branch_id, b.branch_name, b.is_hq
           ORDER BY b.is_hq DESC, low_stock_count DESC, b.branch_name"""
    )
    totals = {
        "low_stock_count": sum(s["low_stock_count"] for s in summary),
        "out_of_stock_count": sum(s["out_of_stock_count"] for s in summary),
        "branches_affected": len(summary),
    }

    sql = f"""SELECT b.branch_id, b.branch_name, b.is_hq, p.sku, p.item_name, p.variant, p.unit,
                    p.image_path, bi.stock_qty, bi.reorder_level
             FROM branch_inventory bi
             JOIN branches b ON bi.branch_id = b.branch_id
             JOIN products p ON bi.sku = p.sku
             WHERE {LOW_STOCK_WHERE}"""
    params = []
    if branch_filter != "all":
        sql += " AND b.branch_id = %s"
        params.append(branch_filter)
    # Most critical first: furthest below its own reorder level (a
    # branch sitting at 0/20 sorts ahead of one at 8/10), branch/item
    # as a stable tiebreaker.
    sql += " ORDER BY (bi.stock_qty - bi.reorder_level) ASC, b.branch_name, p.item_name"
    rows = query(sql, tuple(params))

    return render_template(
        "admin/low_stock.html",
        rows=rows, summary=summary, totals=totals,
        branch_list=branch_list, branch_filter=branch_filter,
    )


# ---------------------------------------------------------------- branch performance
@bp.route("/branch-performance")
@admin_required
def branch_performance():
    """Side-by-side comparison of every retail branch — revenue, units
    sold, delivery discrepancies, and a rough inventory-turnover figure
    — so an underperforming or high-shrinkage branch stands out at a
    glance, instead of cross-referencing Branch Stock, Record Sale, and
    Discrepancies by hand for each branch in turn.

    Turnover here is intentionally a simple approximation — all-time
    units sold divided by *current* stock on hand — not a proper
    COGS-based ratio over a matching period, since neither a
    per-branch cost basis nor point-in-time stock history exists to
    compute the textbook version. It's still useful for a relative
    comparison between branches (one selling through its stock several
    times over vs. one barely moving it), just not to be read as an
    absolute number — see the page's own note on this.
    """
    sort = request.args.get("sort", "revenue")
    if sort not in ("revenue", "units", "discrepancies", "turnover"):
        sort = "revenue"

    rows = query(
        f"""SELECT b.branch_id, b.branch_name,
                  COALESCE(sales_agg.units_sold, 0) AS units_sold,
                  COALESCE(sales_agg.bulk_ml_sold, 0) AS bulk_ml_sold,
                  COALESCE(sales_agg.revenue, 0) AS revenue,
                  COALESCE(sales_agg.sales_count, 0) AS sales_count,
                  COALESCE(stock_agg.total_stock, 0) AS total_stock,
                  COALESCE(stock_agg.bulk_ml_stock, 0) AS bulk_ml_stock,
                  COALESCE(disc_agg.discrepancy_count, 0) AS discrepancy_count
           FROM branches b
           LEFT JOIN (
               -- Bottles and bulk mL kept apart: a bulk product's
               -- qty_sold/stock_qty is mL, not a bottle count.
               SELECT s.branch_id,
                      SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END) AS units_sold,
                      SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END) AS bulk_ml_sold,
                      SUM(s.qty_sold * s.unit_price) AS revenue, COUNT(*) AS sales_count
               FROM sales s JOIN products p ON p.sku = s.sku GROUP BY s.branch_id
           ) sales_agg ON sales_agg.branch_id = b.branch_id
           LEFT JOIN (
               SELECT bi.branch_id,
                      SUM(CASE WHEN p.unit <> 'BULK' THEN bi.stock_qty ELSE 0 END) AS total_stock,
                      SUM(CASE WHEN p.unit = 'BULK' THEN bi.stock_qty ELSE 0 END) AS bulk_ml_stock
               FROM branch_inventory bi JOIN products p ON p.sku = bi.sku GROUP BY bi.branch_id
           ) stock_agg ON stock_agg.branch_id = b.branch_id
           LEFT JOIN (
               -- Units damaged + short, same figure as the Discrepancies
               -- page (see reports.py's DISCREPANCY_UNITS_SQL).
               SELECT sml.branch_id, SUM({DISCREPANCY_UNITS_SQL}) AS discrepancy_count
               FROM stock_movement_logs sml
               {DISCREPANCY_ITEM_JOIN}
               WHERE sml.reference_type = 'STOCK_REQUEST'
                 AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
               GROUP BY sml.branch_id
           ) disc_agg ON disc_agg.branch_id = b.branch_id
           WHERE b.is_hq = FALSE"""
    )

    # Turnover is a derived ratio (division by current stock, which can
    # be zero), so it's computed here rather than in SQL — None means
    # "can't be computed" (no stock on hand right now), shown as "—"
    # rather than a misleading 0 or an unbounded number.
    for r in rows:
        r["turnover"] = round(r["units_sold"] / r["total_stock"],
                              2) if r["total_stock"] else None

    # Stable alphabetical pass first, so branches tied on the chosen
    # metric (e.g. two branches with 0 discrepancies) fall back to
    # branch name order rather than whatever order MySQL happened to
    # return them in.
    rows.sort(key=lambda r: r["branch_name"])
    sort_keys = {
        "revenue": lambda r: r["revenue"],
        "units": lambda r: r["units_sold"],
        "discrepancies": lambda r: r["discrepancy_count"],
        # Branches with no computable turnover (zero stock right now)
        # sort to the bottom regardless of direction — there's nothing
        # meaningful to rank them against.
        "turnover": lambda r: (r["turnover"] is not None, r["turnover"] or 0),
    }
    rows.sort(key=sort_keys[sort], reverse=True)

    totals = {
        "revenue": sum(r["revenue"] for r in rows),
        "units_sold": sum(r["units_sold"] for r in rows),
        "bulk_ml_sold": sum(r["bulk_ml_sold"] for r in rows),
        "discrepancy_count": sum(r["discrepancy_count"] for r in rows),
    }

    return render_template(
        "admin/branch_performance.html",
        rows=rows, totals=totals, sort=sort,
    )
