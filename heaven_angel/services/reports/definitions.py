"""The report catalog: which reports exist, their columns, filters and who may run them."""




# ---------------------------------------------------------------- registry
# admin / branch: whether that role can generate this report at all.
# windowed: whether a time-window (Recent/Range/All) control applies.
REPORT_TYPES = {
    "products":        {"label": "Products",        "admin": True, "branch": False, "windowed": False},
    "production_log":  {"label": "Production Log",  "admin": True, "branch": False, "windowed": True},
    # Materials/Suppliers/Formulas/Cost of Goods Log — admin-only (raw
    # materials and cost of goods are HQ-side bookkeeping; branch
    # accounts never see the Materials/Formulas pages either). Materials
    # and Cost of Goods Log are dated logs (windowed); Suppliers and
    # Formulas are catalog-style snapshots, same reasoning as Products/
    # Accounts/Partners/Packages above.
    "materials":       {"label": "Materials",        "admin": True, "branch": False, "windowed": True},
    "suppliers":       {"label": "Suppliers",        "admin": True, "branch": False, "windowed": False},
    "formulas":        {"label": "Formulas (Cost of Goods)", "admin": True, "branch": False, "windowed": False},
    "cogs_logs":       {"label": "Cost of Goods Log", "admin": True, "branch": False, "windowed": True},
    # Bulk Batches — the step between raw materials and bottling (see
    # bulk_batches in schema.sql). Admin-only like the rest of the
    # materials/cost-of-goods bookkeeping above, and a dated log (when
    # each batch was mixed), so windowed.
    "bulk_batches":    {"label": "Bulk Batches",     "admin": True, "branch": False, "windowed": True},
    "branch_stock":    {"label": "Stock (HQ & Branches)", "admin": True, "branch": True,  "windowed": False,
                        "branch_label": "My Inventory"},
    # Low Stock — the Low Stock page as a report: every SKU at or below
    # its reorder level (LOW_STOCK_WHERE), HQ first, most critical first.
    "low_stock":       {"label": "Low Stock",        "admin": True, "branch": True,  "windowed": False},
    "stock_requests":  {"label": "Stock Requests",   "admin": True, "branch": True,  "windowed": True},
    "inventory_log":   {"label": "Inventory Log",    "admin": True, "branch": True,  "windowed": True},
    "sales_history":   {"label": "Sales History",    "admin": True, "branch": True,  "windowed": True},
    "credit_purchases": {"label": "Credit Purchases", "admin": True, "branch": True,
                         "windowed": True, "branch_label": "Credit Purchases"},
    # Voided Sales — the permanent record of sales removed by Void
    # (sale_voids): what it was, who voided it and why.
    "voided_sales":    {"label": "Voided Sales",     "admin": True, "branch": True,  "windowed": True},
    # Customers — the repeat-customer directory (built from
    # sales.customer_name, no table of its own). A per-customer rollup
    # like the Customers pages, so a snapshot rather than windowed.
    "customers":       {"label": "Customers",        "admin": True, "branch": True,  "windowed": False},
    # Discrepancies — damaged/short units on deliveries, the same
    # DAMAGE/ADJUSTMENT rows both Discrepancies pages list. Dated, so
    # windowed.
    "discrepancies":   {"label": "Discrepancies",    "admin": True, "branch": True,  "windowed": True},
    # Branch Performance — one summary row per retail branch (the Branch
    # Performance page). Windowed so revenue/units can be pulled for a
    # month or a date range; "Recent N" doesn't mean anything for a
    # per-branch total, so it reads as all time (see the builder).
    "branch_performance": {"label": "Branch Performance", "admin": True, "branch": False, "windowed": True},
    # Financial Summary — the Dashboard / Reports page money figures for
    # a window: register sales + Closed package orders, cost of goods,
    # gross profit, raw materials bought. Recent mode reads as all time.
    "financial_summary": {"label": "Financial Summary", "admin": True, "branch": False, "windowed": True},
    "accounts":        {"label": "Accounts",         "admin": True, "branch": False, "windowed": False},
    # Partners & Distribution — admin-only (branch accounts never see
    # this section of the app at all, same as Accounts above).
    # Partners/Packages are catalog-style snapshots (not windowed),
    # same reasoning as Products/Accounts. Partner Inquiries is a dated
    # pipeline of leads, so it gets the Recent/Range/All time window.
    "partners":        {"label": "Partners",         "admin": True, "branch": False, "windowed": False},
    "packages":        {"label": "Packages",         "admin": True, "branch": False, "windowed": False},
    "partner_inquiries": {"label": "Partner Inquiries", "admin": True, "branch": False, "windowed": True},
    # Admin Log / Login Activity — the two audit pages, as dated logs.
    "admin_log":       {"label": "Admin Log",        "admin": True, "branch": False, "windowed": True},
    "login_activity":  {"label": "Login Activity",   "admin": True, "branch": False, "windowed": True},
}


# Report types whose builder actually honors filters["branch_id"]. Only
# these get a branch name in the subtitle, so a Branch picked for one
# report type and left behind on the form never mislabels another.
BRANCH_FILTERED_TYPES = {
    "branch_stock", "low_stock", "stock_requests", "inventory_log", "sales_history",
    "credit_purchases", "customers", "discrepancies", "voided_sales",
}
