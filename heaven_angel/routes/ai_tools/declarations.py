

from heaven_angel.routes.ai_tools.sales import PERIOD_NAMES


BULK_NOTE_TEXT = "units = bottles only; bulk/refill volume comes back separately as bulk_ml (mL), never added to units."


_READ_ONLY_DECLARATIONS = [
    {
        "name": "check_stock",
        "description": "Look up current stock and the selling price for a product by SKU or name. For a Bulk/Refill product (unit BULK) stock_qty is mL, not bottles (see stock_unit). Branch staff only see bottled sizes, same as My Inventory. Admins may filter by branch_name (\"HQ\" for HQ's warehouse); if omitted, returns HQ and every branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "Exact SKU, e.g. A1-85ML."},
                "item_name": {"type": "string", "description": "Partial product/fragrance name to search for."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one location, HQ or a branch (partial match ok)."},
            },
        },
    },
    {
        "name": "get_low_stock",
        "description": "List SKUs at or below their reorder level, across HQ's own warehouse (is_hq: true) and every branch, most critical (furthest below reorder level) first — same as the Low Stock page. Bulk/refill stock has no reorder level and is never listed. Admins may filter by branch_name (use \"HQ\" for HQ's warehouse); if omitted, returns HQ and every branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch_name": {"type": "string", "description": "Admin only — restrict to one location, HQ or a branch (partial match ok)."},
            },
        },
    },
    {
        "name": "get_pending_deliveries",
        "description": "List stock requests (deliveries) and their line items. Defaults to Pending/In Transit only.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "One of Pending, In Transit, Partially Fulfilled, Fulfilled, Rejected."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok)."},
            },
        },
    },
    {
        "name": "get_sales_summary",
        "description": (
            "Register-sales totals and top-selling items for a time period (HQ and branches). "
            + BULK_NOTE_TEXT + " revenue = register sales only; for an Admin with no branch_name "
            "it also returns package_revenue (Closed partner package orders) and total_revenue "
            "(the Dashboard's Total revenue figure). To compare locations, set by_branch=true and "
            "leave branch_name blank — every location's totals in one call (is_hq marks HQ; zero "
            "sales included as 0). Don't call this once per branch name. Also returns by_type: "
            "the split by sale_type (Sale, Refill, Freebie) and payment_method (Cash, Credit)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "One of " + PERIOD_NAMES + ". Defaults to today."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one location, HQ or a branch (partial match ok). Leave blank when using by_branch."},
                "by_branch": {"type": "boolean", "description": "Admin only. If true, also returns a per-location breakdown (HQ included) of units/bulk_ml/revenue for the period. Ignored if branch_name is set."},
            },
        },
    },
]


_READ_ONLY_DECLARATIONS += [
    {
        "name": "get_credit_purchases",
        "description": "Store-credit (utang) sales grouped by buyer (per branch for Admins): transactions, bottle units, bulk_ml (mL), amount owed, last date. All time; repayments aren't recorded.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok)."},
            },
        },
    },
    {
        "name": "get_customers",
        "description": "Named walk-in customers ranked by total spent, same as the Customers page: address (from their first sale), visit count, bottle units, bulk_ml (mL), last visit, last branch and branches shopped. Optionally search by name.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Partial customer name to search for."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok)."},
            },
        },
    },
]


_READ_ONLY_DECLARATIONS += [
    {
        "name": "get_recent_sales",
        "description": "Individual sales, newest first (the Sales History list): item, sale type, payment, qty (qty_unit is mL for bulk/refill, otherwise bottles), price, total, customer, credit buyer.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many sales, 1-30. Defaults to 10."},
                "sale_type": {"type": "string", "description": "Optional: Sale, Refill or Freebie."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one location, HQ or a branch (partial match ok)."},
            },
        },
    },
    {
        "name": "get_discrepancies",
        "description": "Delivery discrepancies (the Discrepancies page): DAMAGE = arrived damaged, ADJUSTMENT = dispatched but never received. Units lost per entry and totals by type.",
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "One of " + PERIOD_NAMES + ". Defaults to all_time."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok)."},
            },
        },
    },
]


_ADMIN_DECLARATIONS = [
    {
        "name": "get_raw_materials",
        "description": "HQ raw materials (oils, alcohol, bottles and so on): stock on hand, unit, cost per unit, supplier. Lowest stock first.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Partial material name to search for."},
        }},
    },
    {
        "name": "get_suppliers",
        "description": "Suppliers with contact details and which raw materials each one supplies.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Partial supplier name to search for."},
        }},
    },
    {
        "name": "get_formulas",
        "description": "Cost-of-goods settings (the Formulas page): the flat base cost per bottle size (85ML/50ML/10ML/3ML) charged per bottle produced, and the Bulk/Refill rate per mL.",
        "parameters": {"type": "object", "properties": {
            "unit": {"type": "string", "description": "Bottle size, e.g. 85ML, or BULK for the per-mL rate. Omit for all."},
        }},
    },
    {
        "name": "get_bulk_batches",
        "description": "Bulk scent batches (the stock refills and bottles are filled from): remaining mL, total volume, cost per mL, and how many bottles of each size the remaining mL could still fill.",
        "parameters": {"type": "object", "properties": {
            "active_only": {"type": "boolean", "description": "Defaults to true: only batches with mL remaining."},
        }},
    },
    {
        "name": "get_production_costs",
        "description": "Cost of goods produced (COGS) for a period: bottles produced, bulk mL produced (kept separate), total cost, and per product with cost per bottle (or cost per mL for bulk).",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "description": "One of " + PERIOD_NAMES + ". Defaults to this_month."},
        }},
    },
    {
        "name": "get_packages",
        "description": "Partner packages (bundles for distributors and resellers): item count, discount, reference and discounted totals.",
        "parameters": {"type": "object", "properties": {
            "include_inactive": {"type": "boolean", "description": "Also include deactivated packages."},
        }},
    },
    {
        "name": "get_partner_inquiries",
        "description": "Inquiries sent from the partner portal: counts by status and the most recent ones. order_amount only counts as a sale once status is Closed; General inquiries have no package and no amount. fulfilled_at is when a Closed order was shipped out of HQ stock (null = confirmed but not shipped yet).",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "description": "One of New, Contacted, Follow-up, On Hold, Closed, Declined."},
        }},
    },
    {
        "name": "get_branch_performance",
        "description": "Per retail branch (HQ excluded), same as the Branch Performance page: sales count, bottles sold, bulk mL sold, revenue, bottles and bulk mL on hand now, discrepancy units and turnover (bottles sold / bottles on hand).",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "description": "One of " + PERIOD_NAMES + ". Defaults to all_time. Stock on hand is always as of now."},
        }},
    },
    {
        "name": "get_financials",
        "description": "Business financials for a period, same rules as the Dashboard/Reports: sales revenue (HQ + branches), Closed package-order revenue, total revenue, cost of goods sold, gross profit, cost of goods produced (capital), raw materials purchased, top packages.",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "description": "One of " + PERIOD_NAMES + ". Defaults to this_month."},
        }},
    },
]


_PROPOSE_DECLARATION = {
    "name": "propose_stock_request",
    "description": (
        "Draft a stock request (delivery) for a branch. This does NOT create a real, "
        "actionable delivery — it saves a draft that a human must review and approve "
        "on the AI Drafts page before HQ can dispatch it. Only bottled sizes (85ML/50ML/10ML/3ML) "
        "can be requested — never Bulk/Refill. Use this when the user asks you "
        "to put together a reorder, or when you're recommending one based on low stock."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "branch_name": {
                "type": "string",
                "description": "Required for an Admin (which branch this delivery is for). Ignored for branch staff — always their own branch.",
            },
            "items": {
                "type": "array",
                "description": "List of {sku, qty} to include.",
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "qty": {"type": "integer"},
                    },
                    "required": ["sku", "qty"],
                },
            },
            "note": {"type": "string", "description": "Short human-readable note shown on the draft."},
            "reasoning": {"type": "string", "description": "Why you're proposing this (e.g. 'below reorder level, 3 weeks of sales history')."},
        },
        "required": ["items"],
    },
}


def get_tool_declarations(role):
    """Gemini `tools` payload for this session's role. The HQ-side tools
    (_ADMIN_DECLARATIONS) are declared to Admin only. propose_stock_request
    is available to both roles — Admin proposes for any branch (must name
    one), Branch staff can only ever propose for their own."""
    decls = list(_READ_ONLY_DECLARATIONS)
    if role == "Admin":
        decls += _ADMIN_DECLARATIONS
    decls.append(_PROPOSE_DECLARATION)
    return [{"functionDeclarations": decls}]
