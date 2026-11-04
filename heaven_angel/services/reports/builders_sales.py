"""Report builders for sales, customers, partners and packages."""
from heaven_angel.services.reports.constants import DISCREPANCY_ITEM_JOIN, DISCREPANCY_UNITS_SQL, MAX_ROWS
from heaven_angel.services.reports.filters import describe_window, time_window, window_where


from heaven_angel.services.finance import PACKAGE_COGS_SQL, PACKAGE_DATE_SQL, SALES_COGS_SQL
from heaven_angel.db import query


def report_sales_history(filters, branch_scope):
    where, params = "", []
    if branch_scope is not None:
        where += " AND s.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND s.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["variant"] != "all":
        where += " AND p.variant = %s"
        params.append(filters["variant"])
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["sale_type"] != "all":
        where += " AND s.sale_type = %s"
        params.append(filters["sale_type"])
    if filters["payment_method"] != "all":
        where += " AND s.payment_method = %s"
        params.append(filters["payment_method"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR s.customer_name LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = time_window(
        "s.sold_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT s.sold_at, b.branch_name, p.item_name, p.sku, p.variant, p.unit,
                   s.qty_sold, s.unit_price, (s.qty_sold * s.unit_price) AS line_total,
                   s.sale_type, s.payment_method, COALESCE(s.buyer_name, bu.username) AS buyer_username,
                   s.customer_name
            FROM sales s
            JOIN branches b ON s.branch_id = b.branch_id
            JOIN products p ON s.sku = p.sku
            LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["unit_price"] = float(r["unit_price"])
        r["line_total"] = float(r["line_total"])
        r["buyer_username"] = r["buyer_username"] or "—"
        r["customer_name"] = r["customer_name"] or "Walk-in"

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [("sold_at", "Sold", "datetime")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU",
                                       "str"), ("variant", "Variant", "badge:variant"),
        ("unit", "Unit", "str"), ("sale_type",
                                  "Type", "badge:sale_type"), ("qty_sold", "Qty", "int"),
        ("unit_price", "Unit Price", "money"), ("line_total", "Total", "money"),
        ("payment_method", "Payment", "badge:payment_method"), ("buyer_username",
                                                                "Buyer (if Credit)", "str"),
        ("customer_name", "Customer", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_credit_purchases(filters, branch_scope):
    """Sales taken on credit rather than cash — an employee against their
    own pay, or a customer buying on store credit ("utang"). Same shape
    as sales_history but always scoped to payment_method = 'Credit', so
    HQ/branch staff can pull exactly what needs to be collected from or
    deducted for each buyer for a given period.
    """
    where, params = "", ["Credit"]
    if branch_scope is not None:
        where += " AND s.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND s.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["sale_type"] != "all":
        where += " AND s.sale_type = %s"
        params.append(filters["sale_type"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR COALESCE(s.buyer_name, bu.username) LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = time_window(
        "s.sold_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT s.sold_at, b.branch_name, p.item_name, p.sku, p.unit, s.sale_type,
                   s.qty_sold, s.unit_price, (s.qty_sold * s.unit_price) AS line_total,
                   COALESCE(s.buyer_name, bu.username, '(unspecified)') AS buyer_username
            FROM sales s
            JOIN branches b ON s.branch_id = b.branch_id
            JOIN products p ON s.sku = p.sku
            LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
            WHERE s.payment_method = %s {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["unit_price"] = float(r["unit_price"])
        r["line_total"] = float(r["line_total"])

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [("sold_at", "Date", "datetime"), ("buyer_username", "Buyer", "str")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU",
                                       "str"), ("sale_type", "Type", "badge:sale_type"),
        ("qty_sold", "Qty", "int"), ("unit_price", "Unit Price", "money"),
        ("line_total", "Amount Owed", "money"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_partners(filters, branch_scope):
    """One row per partner. total_sales/closed_order_count are computed
    the same way the Partners page computes "package sales" — only
    inquiries an admin has marked Closed count, same rule the Partners
    and Dashboard pages both follow (see partners.html's footnote).

    Contact Person/Phone/Email collapse into one Contact column, and
    Total/Closed Inquiries collapse into one Inquiries column — same
    shape the Partners page itself already shows (see the "Contact" and
    "Package sales" cells in partners.html), so the report isn't more
    spread out than the screen it's summarizing. Address is dropped
    entirely: it's an optional field on the partner-portal form and is
    almost always blank in practice, so keeping its own column mostly
    just added width for a column that read "—" on nearly every row.
    """
    where, params = "", []
    if filters["partner_type"] != "all":
        where += " AND p.partner_type = %s"
        params.append(filters["partner_type"])
    if filters["search"]:
        where += " AND (p.partner_name LIKE %s OR p.contact_person LIKE %s OR p.phone LIKE %s OR p.email LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like, like]

    rows = query(
        f"""SELECT p.partner_name, p.partner_type, p.contact_person, p.phone, p.email,
                   p.inquiry_count, p.last_inquiry_at, p.created_at,
                   COALESCE(SUM(CASE WHEN pi.status = 'Closed' THEN pi.order_amount ELSE 0 END), 0) AS total_sales,
                   COUNT(CASE WHEN pi.status = 'Closed' THEN 1 END) AS closed_order_count
            FROM partners p
            LEFT JOIN partner_inquiries pi ON pi.partner_id = p.partner_id
            WHERE 1=1 {where}
            GROUP BY p.partner_id
            ORDER BY p.partner_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["total_sales"] = float(r["total_sales"])
        r["contact"] = " · ".join(
            p for p in (r.pop("contact_person"), r.pop("phone"), r.pop("email")) if p
        ) or "—"
        r["inquiries_summary"] = f"{r['inquiry_count']} total · {r['closed_order_count']} closed"

    # 4th element = relative PDF column width (see render_report_pdf's
    # width-weight comment). Contact now carries three merged fields so
    # it gets the biggest share; Type/Inquiries are short so they shrink.
    columns = [
        ("partner_name", "Partner", "str", 1.2),
        ("partner_type", "Type", "badge:partner_type", 0.7),
        ("contact", "Contact", "str", 1.8),
        ("inquiries_summary", "Inquiries", "str", 0.95),
        ("total_sales", "Package Sales", "money", 1.0),
        ("last_inquiry_at", "Last Inquiry", "datetime", 1.0),
        ("created_at", "On File Since", "datetime", 1.0),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def report_packages(filters, branch_scope):
    """One row per package. Reference/order totals mirror package_detail's
    footer math (sum of qty * products.price, then the package's own
    discount_percent applied) — see packages.html / package_detail.html.
    """
    where, params = "", []
    if filters["package_scope"] != "all":
        where += " AND pk.partner_scope = %s"
        params.append(filters["package_scope"])
    if filters["package_status"] != "all":
        where += " AND pk.is_active = %s"
        params.append(filters["package_status"] == "Active")
    if filters["search"]:
        where += " AND (pk.package_name LIKE %s OR pk.description LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]

    rows = query(
        f"""SELECT pk.package_name, pk.description, pk.partner_scope, pk.discount_percent,
                   pk.is_active, pk.created_at,
                   COUNT(pki.package_item_id) AS item_count,
                   COALESCE(SUM(pki.qty * pr.price), 0) AS reference_total
            FROM packages pk
            LEFT JOIN package_items pki ON pki.package_id = pk.package_id
            LEFT JOIN products pr ON pki.sku = pr.sku
            WHERE 1=1 {where}
            GROUP BY pk.package_id
            ORDER BY pk.package_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["description"] = r["description"] or "—"
        discount = float(r["discount_percent"])
        r["discount_percent"] = f"{discount:.2f}%"
        reference_total = float(r["reference_total"])
        r["reference_total"] = reference_total
        r["discounted_total"] = round(
            reference_total * (1 - discount / 100), 2)
        r["is_active"] = "Active" if r["is_active"] else "Retired"

    columns = [
        ("package_name", "Package", "str"), ("description", "Description", "str"),
        ("partner_scope", "Available To",
         "badge:package_scope"), ("item_count", "Items", "int"),
        ("reference_total", "Reference Value",
         "money"), ("discount_percent", "Discount", "str"),
        ("discounted_total", "Order Price",
         "money"), ("is_active", "Status", "badge:package_status"),
        ("created_at", "Created", "datetime"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def report_partner_inquiries(filters, branch_scope):
    """One row per inquiry — the same permanent, never-edited-except-
    status/remarks history shown on the Partner Inquiries page. Only
    Closed inquiries represent actual revenue (order_amount); everything
    else is still a lead — see schema.sql's partner_inquiries comment
    and partners.html/dashboard.html's matching footnotes.
    """
    where, params = "", []
    if filters["partner_type"] != "all":
        where += " AND pi.partner_type = %s"
        params.append(filters["partner_type"])
    if filters["inquiry_status"] != "all":
        where += " AND pi.status = %s"
        params.append(filters["inquiry_status"])
    if filters["search"]:
        where += (" AND (pi.company_name LIKE %s OR pi.contact_person LIKE %s "
                  "OR pi.package_name_snapshot LIKE %s OR pi.remarks LIKE %s)")
        like = f"%{filters['search']}%"
        params += [like, like, like, like]
    time_where, order, limit_n, truncated = time_window(
        "pi.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT pi.created_at, pi.company_name, pi.partner_type, pi.contact_person, pi.phone,
                   pi.email, pi.preferred_contact, pi.package_name_snapshot, pi.order_amount,
                   pi.status, pi.fulfilled_at, pi.remarks
            FROM partner_inquiries pi
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        contact_person = r.pop("contact_person")
        r["company_name"] = (
            f"{r['company_name']} — {contact_person}" if contact_person else r["company_name"]
        )
        preferred = r.pop("preferred_contact")
        r["contact"] = " · ".join(p for p in (
            r.pop("phone"), r.pop("email"),
            f"prefers {preferred}" if preferred else None) if p) or "—"
        # Same short package name the Partner Inquiries page shows.
        r["package_name_snapshot"] = (r["package_name_snapshot"] or "—").split(" (")[0]
        # Nullable — see schema.sql's comment: NULL means "unknown"
        # (older row predating this column), never coerced to 0. The
        # quoted package value is shown for every lead, but only a
        # Closed inquiry is a sale (same rule as the Dashboard/Partners),
        # so only sale_amount is totalled.
        amount = r.pop("order_amount")
        r["package_value"] = float(amount) if amount is not None else None
        r["sale_amount"] = float(amount) if amount is not None and r["status"] == "Closed" else None
        r["remarks"] = r["remarks"] or "—"

    # See report_partners' comment above — same reasoning: Contact
    # Person now rides along inside Company, and Phone/Email collapse
    # into one Contact column, so both the column count and the short
    # badge/status columns shrink; Remarks/Package/Contact (the ones
    # that actually need room) grow. Address and HQ Notified (whether
    # the notification email happened to send) are dropped outright —
    # Address is almost always blank, and HQ Notified is an internal
    # mailer-ops flag, not information about the partner or the sale.
    columns = [
        ("created_at", "When", "datetime", 0.9),
        ("company_name", "Company", "str", 1.3),
        ("partner_type", "Type", "badge:partner_type", 0.7),
        ("contact", "Contact", "str", 1.5),
        ("package_name_snapshot", "Package", "str", 1.05),
        ("package_value", "Package Value", "money", 0.9),
        ("sale_amount", "Sale (Closed only)", "money", 0.9),
        ("status", "Status", "badge:inquiry_status", 0.8),
        # When a Closed order was shipped out of HQ stock (Fulfill);
        # "—" = not fulfilled yet.
        ("fulfilled_at", "Fulfilled", "datetime", 0.85),
        ("remarks", "Remarks (internal)", "str", 1.5),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_customers(filters, branch_scope):
    """The repeat-customer directory — same data as the admin/branch
    Customers pages, built from sales.customer_name (walk-ins with no
    name are left out, since there's nothing to group them by).

    Address is the one on that customer's earliest sale, the same
    "first sale wins" convention as both Customers pages — looked up
    with a correlated subquery (earliest by sold_at, then sale_id) so a
    customer with two sales at the same timestamp still gets exactly one
    row. A branch account (branch_scope) only ever sees its own sales;
    admin can narrow to one branch or see the fleet-wide rollup, where
    a customer who shopped at several branches is still one row.
    """
    scope_sql, scope_params = "", []
    if branch_scope is not None:
        scope_sql, scope_params = " AND {a}.branch_id = %s", [branch_scope]
    elif filters["branch_id"] != "all":
        scope_sql, scope_params = " AND {a}.branch_id = %s", [filters["branch_id"]]

    search_sql, search_params = "", []
    if filters["search"]:
        search_sql = " AND sa.customer_name LIKE %s"
        search_params = [f"%{filters['search']}%"]

    rows = query(
        f"""SELECT agg.customer_name AS name,
                   (SELECT s2.customer_address FROM sales s2
                    WHERE s2.customer_name = agg.customer_name{scope_sql.format(a="s2")}
                    ORDER BY s2.sold_at, s2.sale_id LIMIT 1) AS address,
                   (SELECT lb.branch_name FROM sales l JOIN branches lb ON lb.branch_id = l.branch_id
                    WHERE l.customer_name = agg.customer_name{scope_sql.format(a="l")}
                    ORDER BY l.sold_at DESC, l.sale_id DESC LIMIT 1) AS last_branch,
                   agg.branches, agg.purchase_count, agg.total_units, agg.total_bulk_ml,
                   agg.total_spent, agg.last_purchase_at
            FROM (
                SELECT sa.customer_name,
                       GROUP_CONCAT(DISTINCT b.branch_name ORDER BY b.branch_name SEPARATOR ', ') AS branches,
                       COUNT(*) AS purchase_count,
                       -- Bulk qty_sold is mL: kept apart from bottles.
                       COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_units,
                       COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                       COALESCE(SUM(sa.qty_sold * sa.unit_price), 0) AS total_spent,
                       MAX(sa.sold_at) AS last_purchase_at
                FROM sales sa
                JOIN branches b ON sa.branch_id = b.branch_id
                JOIN products p ON p.sku = sa.sku
                WHERE sa.customer_name IS NOT NULL AND sa.customer_name <> ''
                      {scope_sql.format(a="sa")}{search_sql}
                GROUP BY sa.customer_name
            ) agg
            ORDER BY agg.total_spent DESC LIMIT {MAX_ROWS}""",
        tuple(scope_params + scope_params + scope_params + search_params),
    )
    for r in rows:
        r["address"] = r["address"] or "—"
        r["total_units"] = int(r["total_units"])
        r["total_bulk_ml"] = int(r["total_bulk_ml"])
        r["total_spent"] = float(r["total_spent"])

    columns = [("name", "Customer", "str"), ("address", "Address", "str")]
    if branch_scope is None:
        columns.append(("branches", "Branches Shopped", "str"))
    columns += [
        ("purchase_count", "Purchases", "int"),
        ("total_units", "Bottles Bought", "int"),
        ("total_bulk_ml", "Bulk Bought (mL)", "int"),
        ("total_spent", "Total Spent", "money"),
        ("last_purchase_at", "Last Purchase", "datetime"),
    ]
    if branch_scope is None:
        columns.append(("last_branch", "Last At", "str"))
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now — named customers only (walk-ins excluded)"


def report_branch_performance(filters, branch_scope):
    """One row per retail branch — the Branch Performance page as a
    report: sales count, units, revenue and delivery discrepancies over
    the chosen window, plus current stock on hand and the same rough
    turnover figure (units sold / current stock) the page shows.

    This is a per-branch total, so there's no "most recent N rows" to
    take — Recent mode reads as all time. Range/Month bound the sales
    and discrepancies (stock on hand is always as of now; there's no
    stock history to take it at a past date from).
    """
    sales_params, disc_params = [], []
    if filters["mode"] in ("range", "month"):
        sales_where, _, _, _ = time_window("s.sold_at", filters, sales_params)
        disc_where, _, _, _ = time_window("sml.created_at", filters, disc_params)
        note = describe_window(filters, False)
    else:
        sales_where = disc_where = ""
        note = "All time"

    rows = query(
        f"""SELECT b.branch_name,
                   COALESCE(sales_agg.sales_count, 0) AS sales_count,
                   COALESCE(sales_agg.units_sold, 0) AS units_sold,
                   COALESCE(sales_agg.bulk_ml_sold, 0) AS bulk_ml_sold,
                   COALESCE(sales_agg.revenue, 0) AS revenue,
                   COALESCE(stock_agg.total_stock, 0) AS total_stock,
                   COALESCE(stock_agg.bulk_ml_stock, 0) AS bulk_ml_stock,
                   COALESCE(disc_agg.discrepancy_count, 0) AS discrepancy_count
            FROM branches b
            LEFT JOIN (
                -- Bottles and bulk mL kept apart (bulk qty/stock is mL).
                SELECT s.branch_id, COUNT(*) AS sales_count,
                       SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END) AS units_sold,
                       SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END) AS bulk_ml_sold,
                       SUM(s.qty_sold * s.unit_price) AS revenue
                FROM sales s JOIN products p ON p.sku = s.sku
                WHERE 1=1 {sales_where} GROUP BY s.branch_id
            ) sales_agg ON sales_agg.branch_id = b.branch_id
            LEFT JOIN (
                SELECT bi.branch_id,
                       SUM(CASE WHEN p.unit <> 'BULK' THEN bi.stock_qty ELSE 0 END) AS total_stock,
                       SUM(CASE WHEN p.unit = 'BULK' THEN bi.stock_qty ELSE 0 END) AS bulk_ml_stock
                FROM branch_inventory bi JOIN products p ON p.sku = bi.sku GROUP BY bi.branch_id
            ) stock_agg ON stock_agg.branch_id = b.branch_id
            LEFT JOIN (
                SELECT sml.branch_id, SUM({DISCREPANCY_UNITS_SQL}) AS discrepancy_count
                FROM stock_movement_logs sml
                {DISCREPANCY_ITEM_JOIN}
                WHERE sml.reference_type = 'STOCK_REQUEST'
                  AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
                      {disc_where}
                GROUP BY sml.branch_id
            ) disc_agg ON disc_agg.branch_id = b.branch_id
            WHERE b.is_hq = FALSE
            ORDER BY revenue DESC, b.branch_name""",
        tuple(sales_params + disc_params),
    )
    for r in rows:
        r["units_sold"] = int(r["units_sold"])
        r["bulk_ml_sold"] = int(r["bulk_ml_sold"])
        r["revenue"] = float(r["revenue"])
        r["total_stock"] = int(r["total_stock"])
        r["bulk_ml_stock"] = int(r["bulk_ml_stock"])
        # None (shown as "—") when there's no stock on hand to divide by,
        # same as the page — not a misleading 0 or an unbounded number.
        r["turnover"] = round(r["units_sold"] / r["total_stock"], 2) if r["total_stock"] else None

    columns = [
        ("branch_name", "Branch", "str"),
        ("sales_count", "Sales", "int"),
        ("units_sold", "Bottles Sold", "int"),
        ("bulk_ml_sold", "Bulk Sold (mL)", "int"),
        ("revenue", "Revenue", "money"),
        ("total_stock", "Bottles on Hand (now)", "int"),
        ("bulk_ml_stock", "Bulk on Hand (mL)", "int"),
        ("discrepancy_count", "Discrepancies", "int"),
        ("turnover", "Turnover", "num"),
    ]
    return columns, rows, False, note


def report_financial_summary(filters, branch_scope):
    """Dashboard / Reports money figures for the chosen window, one row
    per figure: register sales (HQ + branches) + Closed partner package
    orders = total revenue; cost of goods SOLD (finance.py: what the sold
    and shipped goods cost to make); gross profit = revenue - cost of goods
    sold; cost of goods produced (capital) and raw materials bought are
    shown for comparison only."""
    def total(sql, date_col):
        params = []
        frag = window_where(date_col, filters, params)
        return query(sql + frag, tuple(params), fetchone=True)["v"]

    sales = float(total("SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales WHERE 1=1", "sold_at"))
    closed_on = "COALESCE(closed_at, created_at)"
    packages = float(total(
        "SELECT COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries WHERE status = 'Closed'", closed_on))
    package_orders = int(total(
        "SELECT COUNT(*) AS v FROM partner_inquiries WHERE status = 'Closed'", closed_on))
    produced = float(total("SELECT COALESCE(SUM(total_cogs), 0) AS v FROM cogs_logs WHERE 1=1", "created_at"))
    cogs = (float(total(SALES_COGS_SQL + " WHERE 1=1", "s.sold_at"))
            + float(total(PACKAGE_COGS_SQL, PACKAGE_DATE_SQL)))
    materials = float(total(
        """SELECT COALESCE(SUM(package_cost), 0) AS v FROM (
               SELECT rm.created_at AS purchased_at,
                      GREATEST(rm.package_cost - COALESCE(re.total_cost, 0), 0) AS package_cost
               FROM raw_materials rm
               LEFT JOIN (
                   SELECT material_id, SUM(cost) AS total_cost
                   FROM material_restock_events GROUP BY material_id
               ) re ON re.material_id = rm.material_id
               UNION ALL
               SELECT purchased_at, cost AS package_cost
               FROM material_restock_events
           ) material_purchases WHERE 1=1""", "purchased_at"))
    revenue = sales + packages
    rows = [
        {"figure": "Register sales (HQ + branches)", "figure_amount": sales},
        {"figure": f"Partner package orders (Closed, {package_orders})", "figure_amount": packages},
        {"figure": "Total revenue", "figure_amount": revenue},
        {"figure": "Cost of goods sold", "figure_amount": cogs},
        {"figure": "Gross profit (revenue − cost of goods sold)", "figure_amount": revenue - cogs},
        {"figure": "Cost of goods produced (capital, comparison only)", "figure_amount": produced},
        {"figure": "Raw materials purchased (comparison only)", "figure_amount": materials},
    ]
    columns = [("figure", "Figure", "str", 2.5), ("figure_amount", "Amount", "money", 1.0)]
    note = describe_window(filters, False) if filters["mode"] in ("range", "month") else "All time"
    return columns, rows, False, note + " — gross profit excludes rent, payroll and other untracked costs"


def report_voided_sales(filters, branch_scope):
    """Sales removed by Void (sale_voids) — never counted in any sales
    figure — with who voided each one and why."""
    where, params = "", []
    if branch_scope is not None:
        where += " AND v.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND v.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["search"]:
        where += " AND (v.item_name LIKE %s OR v.sku LIKE %s OR v.reason LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = time_window("v.voided_at", filters, params)
    where += time_where
    rows = query(
        f"""SELECT v.voided_at, b.branch_name, v.item_name, v.sku, v.unit, v.sale_type,
                   v.qty_sold, (v.qty_sold * v.unit_price) AS line_total, v.sold_at,
                   COALESCE(v.voided_by_username, '—') AS voided_by, v.reason
            FROM sale_voids v JOIN branches b ON b.branch_id = v.branch_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["line_total"] = float(r["line_total"])
    columns = [] if branch_scope is not None else [("branch_name", "Location", "str")]
    columns = [("voided_at", "Voided", "datetime")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU", "str"), ("unit", "Unit", "str"),
        ("sale_type", "Type", "badge:sale_type"), ("qty_sold", "Qty", "int"),
        ("line_total", "Amount", "money"), ("sold_at", "Originally Sold", "datetime"),
        ("voided_by", "Voided By", "str"), ("reason", "Reason", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)
