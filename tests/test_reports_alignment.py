"""Report generation stays aligned with the pages: every report type
builds and renders (PDF + Excel) for the roles that can pull it, in every
time mode, and the page rules (low stock, Closed-only package sales,
bulk mL kept apart, branch-only filters) hold in the generated data.
"""
from factories import make_branch, make_inventory, make_product
from reports import REPORT_TYPES, get_report, parse_report_filters, render_report_excel, render_report_pdf


def test_every_report_builds_and_renders(app, sql):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=1, reorder_level=10)
    with app.test_request_context():
        for key, meta in REPORT_TYPES.items():
            modes = ("recent", "all", "month") if meta["windowed"] else ("recent",)
            scopes = [None] + ([branch_id] if meta["branch"] else [])
            for mode in modes:
                for scope in scopes:
                    report = get_report(key, parse_report_filters({"mode": mode}), branch_scope=scope)
                    if report["rows"]:
                        assert render_report_pdf(report).getvalue(), key
                        assert render_report_excel(report).getvalue(), key


def test_low_stock_report_matches_low_stock_rule(app, sql):
    branch_id = make_branch(sql)
    low = make_product(sql)
    bulk = make_product(sql, unit="BULK")
    make_inventory(sql, branch_id, low, stock_qty=2, reorder_level=10)
    make_inventory(sql, branch_id, bulk, stock_qty=0, reorder_level=10)
    with app.test_request_context():
        report = get_report("low_stock", parse_report_filters({}), branch_scope=branch_id)
    skus = {r["sku"] for r in report["rows"]}
    assert low in skus
    assert bulk not in skus


def test_branch_filter_only_labels_reports_that_use_it(app, sql):
    branch_id = make_branch(sql)
    with app.test_request_context():
        products = get_report("products", parse_report_filters({"branch_id": str(branch_id)}))
    # Products ignores the Branch filter, so a leftover branch_id must not
    # put a branch name in the subtitle.
    assert products["subtitle"] == "Snapshot as of now"


def test_partner_inquiry_amounts_only_total_closed_sales(app, sql):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO partner_inquiries (partner_type, company_name, contact_person, phone, email,
                                          package_name_snapshot, order_amount, status)
           VALUES ('Reseller', 'Lead Co', 'A', '0917', 'a@example.com', 'Starter (5 items)', 1000, 'New'),
                  ('Reseller', 'Buyer Co', 'B', '0918', 'b@example.com', 'Starter (5 items)', 2500, 'Closed')"""
    )
    sql.commit()
    cur.close()
    with app.test_request_context():
        report = get_report("partner_inquiries", parse_report_filters({"mode": "all"}))
    rows = {r["company_name"].split(" — ")[0]: r for r in report["rows"]}
    assert rows["Lead Co"]["sale_amount"] is None
    assert rows["Buyer Co"]["sale_amount"] == 2500
    assert rows["Buyer Co"]["package_name_snapshot"] == "Starter"
    assert "package_value" not in (report["totals"] or {})
