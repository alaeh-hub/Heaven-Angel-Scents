
from flask import (
    abort, flash, jsonify, redirect, render_template,
    request, send_file, session, url_for,
)

from heaven_angel.db import query
from heaven_angel.decorators import admin_required
from heaven_angel.services.reports import (REPORT_TYPES, get_report,
                     parse_report_filters, render_report_excel, render_report_pdf)
from heaven_angel.utils import (
    PRODUCT_UNITS, business_today,
)

from heaven_angel.routes.admin._common import bp
from heaven_angel.services import admin_report_data as report_data
from heaven_angel.services.trends import TREND_GRANULARITIES


# LOW_STOCK_WHERE (imported from reports.py) is the shared low-stock
# condition for the dashboard, Low Stock page, the Low Stock / Stock
# reports and ai_tools.py's get_low_stock: HQ's warehouse plus every
# branch, at or below its own reorder level, never BULK (Bulk/Refill
# has no reorder level anywhere, HQ included).


# ---------------------------------------------------------------- reports
@bp.route("/reports")
@admin_required
def reports():
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")
    report_types = [
        {"key": key, "label": meta["label"], "windowed": meta["windowed"]}
        for key, meta in REPORT_TYPES.items() if meta["admin"]
    ]
    # All-time, across every branch — qty_sold/unit_price on `sales`
    # already carries everything this needs, no extra table required.
    top_sellers = query(
        """SELECT p.sku, p.item_name, p.variant,
                  SUM(s.qty_sold) AS total_units,
                  SUM(s.qty_sold * s.unit_price) AS total_revenue
           FROM sales s
           JOIN products p ON p.sku = s.sku
           WHERE p.unit <> 'BULK'  -- bulk qty_sold is mL, not bottles
           GROUP BY p.sku, p.item_name, p.variant
           ORDER BY total_units DESC LIMIT 3"""
    )

    # Best-selling packages — same "only Closed counts as a real sale"
    # rule as everywhere else package revenue is computed (see
    # partners()'s docstring and schema.sql's note on
    # partner_inquiries.order_amount). Joined through packages so a
    # package that's since been deleted (package_id set NULL on the
    # inquiry) drops out here rather than showing as a nameless row —
    # its revenue still counts in the totals below, just not in this
    # per-package breakdown.
    top_packages = query(
        """SELECT pkg.package_id, pkg.package_name,
                  COUNT(pinq.inquiry_id) AS order_count,
                  COALESCE(SUM(pinq.order_amount), 0) AS total_revenue
           FROM partner_inquiries pinq
           JOIN packages pkg ON pkg.package_id = pinq.package_id
           WHERE pinq.status = 'Closed'
           GROUP BY pkg.package_id, pkg.package_name
           ORDER BY total_revenue DESC LIMIT 3"""
    )

    # Top partners by package sales — same Closed-only rule. Joined
    # through partners for the same reason as above (a since-deleted
    # partner drops out of this breakdown, not out of the totals).
    #
    # Grouped by company name (case/whitespace-insensitive), not
    # partner_id: the Partners page intentionally keeps a separate
    # partner_id (and its own inquiry history) per submission even when
    # the same company sends more than one inquiry — see partners.html's
    # note on name/contact info coming from each inquiry's own first
    # record. That's correct there, but on this dashboard summary two
    # partner_id rows for "Hello World" reading as two different
    # distributors is misleading — this rolls them into one line with a
    # combined order count and total. MIN(partner_id)/MIN(partner_name)
    # just picks one representative row per company for the link/label;
    # MAX(partner_type) does the same for type (all rows for one company
    # are expected to share a type in practice).
    top_partners = query(
        """SELECT MIN(p.partner_id) AS partner_id, MIN(p.partner_name) AS partner_name,
                  MAX(p.partner_type) AS partner_type,
                  COUNT(pinq.inquiry_id) AS order_count,
                  COALESCE(SUM(pinq.order_amount), 0) AS total_spent
           FROM partner_inquiries pinq
           JOIN partners p ON p.partner_id = pinq.partner_id
           WHERE pinq.status = 'Closed'
           GROUP BY LOWER(TRIM(p.partner_name))
           ORDER BY total_spent DESC LIMIT 3"""
    )

    return render_template(
        "admin/reports.html", branch_list=branch_list, report_types=report_types, unit_choices=PRODUCT_UNITS,
        current_month=business_today().strftime("%Y-%m"),
        top_sellers=top_sellers, top_packages=top_packages, top_partners=top_partners,
    )


@bp.route("/reports/generate")
@admin_required
def generate_report():
    """Download a filtered report as PDF or Excel — see reports.py.

    Any admin can pull any branch's data here (or "all branches"), same
    as every other admin page; there's no branch scoping to apply.
    """
    report_type = request.args.get("type", "")
    fmt = request.args.get("format", "pdf")
    meta = REPORT_TYPES.get(report_type)
    if meta is None or not meta["admin"] or fmt not in ("pdf", "xlsx"):
        abort(404)

    filters = parse_report_filters(request.args)
    report = get_report(report_type, filters, branch_scope=None,
                        actor_label=f"HQ Admin — {session.get('username')}")

    if report["row_count"] == 0:
        flash(
            f"No data matches the selected filters for {report['title']}.", "warning")
        return redirect(url_for("admin.reports"))

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
@admin_required
def reports_data():
    granularity = request.args.get("granularity", "daily")
    trend_bucket = TREND_GRANULARITIES.get(granularity, TREND_GRANULARITIES["daily"])

    by_variant, by_branch, stock_by_branch = report_data.sales_breakdowns()
    totals, package_totals = report_data.all_time_totals()
    financials, windowed_revenue, windowed_materials_cost = report_data.windowed_financials(trend_bucket)
    return jsonify(
        by_variant=by_variant, by_branch=by_branch,
        movement_trend=report_data.movement_trend(trend_bucket),
        stock_by_branch=stock_by_branch, totals=totals, financials=financials,
        kpi=report_data.kpi_tiles(granularity, trend_bucket, windowed_revenue, windowed_materials_cost),
        sales_by_channel=report_data.sales_by_channel(package_totals),
    )
