
from flask import abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from heaven_angel.db import query
from heaven_angel.decorators import branch_required
from heaven_angel.services.reports import REPORT_TYPES, get_report, parse_report_filters, render_report_excel, render_report_pdf
from heaven_angel.services.trends import TREND_GRANULARITIES
from heaven_angel.utils import (
    business_today, PRODUCT_UNITS,
)

from heaven_angel.routes.branch._common import _branch_id, bp


# ---------------------------------------------------------------- reports
@bp.route("/reports")
@branch_required
def reports():
    report_types = [
        {"key": key, "label": meta.get(
            "branch_label", meta["label"]), "windowed": meta["windowed"]}
        for key, meta in REPORT_TYPES.items() if meta["branch"]
    ]
    return render_template(
        "branch/reports.html", report_types=report_types, unit_choices=PRODUCT_UNITS,
        current_month=business_today().strftime("%Y-%m"),
    )


@bp.route("/reports/generate")
@branch_required
def generate_report():
    """Download a filtered report as PDF or Excel, scoped to this branch.

    branch_scope is always this signed-in branch's own branch_id — any
    branch_id present in the querystring is ignored by reports.py, so
    editing the URL can't pull another branch's data.
    """
    report_type = request.args.get("type", "")
    fmt = request.args.get("format", "pdf")
    meta = REPORT_TYPES.get(report_type)
    if meta is None or not meta["branch"] or fmt not in ("pdf", "xlsx"):
        abort(404)

    bid = _branch_id()
    filters = parse_report_filters(request.args)
    report = get_report(
        report_type, filters, branch_scope=bid,
        actor_label=f"{session.get('branch_name')} — {session.get('username')}",
    )

    if report["row_count"] == 0:
        flash(
            f"No data matches the selected filters for {report['title']}.", "warning")
        return redirect(url_for("branch.reports"))

    stamp = business_today().isoformat()
    if fmt == "xlsx":
        buf = render_report_excel(report)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        buf = render_report_pdf(report)
        mimetype = "application/pdf"
    return send_file(
        buf, mimetype=mimetype, as_attachment=True,
        download_name=f"{report_type}-{stamp}.{fmt}",
    )


@bp.route("/api/reports-data")
@branch_required
def reports_data():
    """Chart data for the branch Reports page — everything here is scoped
    to this signed-in branch's own branch_id, same as every query above.
    There's no branch_id in the querystring to trust or ignore.
    """
    bid = _branch_id()

    # Unit figures below count bottles only: a bulk product's qty_sold
    # (and stock_qty) is mL, which would swamp the bottle counts if added
    # in. Bulk volume is reported separately as bulk_ml where it matters.
    by_variant = query(
        """SELECT p.variant, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM products p
           LEFT JOIN sales s ON p.sku = s.sku AND s.branch_id = %s
           WHERE p.unit <> 'BULK'
           GROUP BY p.variant""",
        (bid,),
    )

    # Daily units sold for the last 14 days — the branch-scoped stand-in
    # for the admin page's "units sold by branch" chart, which doesn't
    # make sense when there's only one branch to look at.
    sales_trend = query(
        """SELECT DATE(s.sold_at) AS day, COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units_sold,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.branch_id = %s AND s.sold_at >= NOW() - INTERVAL 14 DAY
           GROUP BY DATE(s.sold_at) ORDER BY day""",
        (bid,),
    )

    top_products = query(
        """SELECT p.item_name, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM sales s JOIN products p ON s.sku = p.sku
           WHERE s.branch_id = %s AND p.unit <> 'BULK'
           GROUP BY p.sku, p.item_name
           ORDER BY units_sold DESC LIMIT 8""",
        (bid,),
    )

    stock_by_variant = query(
        """SELECT p.variant, COALESCE(SUM(bi.stock_qty), 0) AS total_stock
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND p.unit <> 'BULK'
           GROUP BY p.variant""",
        (bid,),
    )

    granularity = request.args.get("granularity", "daily")
    trend_bucket = TREND_GRANULARITIES.get(
        granularity, TREND_GRANULARITIES["daily"])
    # Bottles and bulk mL are different units — kept apart the same way
    # sales/stock totals are split elsewhere, rather than summed into
    # one meaningless number (see admin.reports_data()'s movement_trend).
    movement_trend = query(
        f"""SELECT {trend_bucket['trunc']} AS day, sml.movement_type,
                   COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN ABS(sml.change_qty) ELSE 0 END), 0) AS units,
                   COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN ABS(sml.change_qty) ELSE 0 END), 0) AS bulk_ml
            FROM stock_movement_logs sml
            JOIN products p ON p.sku = sml.sku
            WHERE sml.branch_id = %s AND sml.created_at >= NOW() - {trend_bucket['window']}
            GROUP BY {trend_bucket['trunc']}, sml.movement_type ORDER BY day""",
        (bid,),
    )

    # Cash sales vs. employee purchases deducted from salary, and plain
    # sales vs. refills — both scoped to this branch only.
    payment_breakdown = query(
        """SELECT s.payment_method, COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units_sold, COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.branch_id = %s GROUP BY s.payment_method""",
        (bid,),
    )
    sale_type_breakdown = query(
        """SELECT s.sale_type, COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units_sold, COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.branch_id = %s GROUP BY s.sale_type""",
        (bid,),
    )

    totals = query(
        """SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units, COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku WHERE s.branch_id = %s""",
        (bid,), fetchone=True,
    )

    for row in sales_trend:
        row["day"] = row["day"].isoformat()
        row["revenue"] = float(row["revenue"])
    for row in movement_trend:
        row["day"] = row["day"].isoformat()

    return jsonify(
        by_variant=by_variant, sales_trend=sales_trend, top_products=top_products,
        stock_by_variant=stock_by_variant, movement_trend=movement_trend,
        payment_breakdown=payment_breakdown, sale_type_breakdown=sale_type_breakdown, totals=totals,
        today=business_today().isoformat(),
    )
