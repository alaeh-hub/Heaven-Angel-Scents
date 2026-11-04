from flask import current_app


from heaven_angel.routes.ai_tools.catalog import get_bulk_batches, get_formulas, get_packages, get_partner_inquiries, get_production_costs, get_raw_materials, get_suppliers
from heaven_angel.routes.ai_tools.sales import get_branch_performance, get_credit_purchases, get_customers, get_financials, get_recent_sales, get_sales_summary
from heaven_angel.routes.ai_tools.stock import check_stock, get_discrepancies, get_low_stock, get_pending_deliveries, propose_stock_request


_TOOL_IMPL = {
    "check_stock": check_stock,
    "get_low_stock": get_low_stock,
    "get_pending_deliveries": get_pending_deliveries,
    "get_sales_summary": get_sales_summary,
    "get_credit_purchases": get_credit_purchases,
    "get_customers": get_customers,
    "get_recent_sales": get_recent_sales,
    "get_discrepancies": get_discrepancies,
    "get_branch_performance": get_branch_performance,
    "get_financials": get_financials,
    "get_raw_materials": get_raw_materials,
    "get_suppliers": get_suppliers,
    "get_formulas": get_formulas,
    "get_bulk_batches": get_bulk_batches,
    "get_production_costs": get_production_costs,
    "get_packages": get_packages,
    "get_partner_inquiries": get_partner_inquiries,
    "propose_stock_request": propose_stock_request,
}


# HQ-side data a Branch session must never see. Enforced in dispatch(),
# not only by leaving these out of a Branch session's declarations.
_ADMIN_ONLY = {
    "get_raw_materials", "get_suppliers", "get_formulas", "get_bulk_batches",
    "get_production_costs", "get_packages", "get_partner_inquiries",
    "get_branch_performance", "get_financials",
}


def dispatch(name, args, ctx):
    """Run a tool by name. Never raises — always returns a JSON-safe dict,
    since this result goes straight back to the model as a functionResponse."""
    fn = _TOOL_IMPL.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'."}
    if name in _ADMIN_ONLY and ctx.get("role") != "Admin":
        return {"error": "That information is only available to HQ admins."}
    try:
        return fn(args or {}, ctx)
    except Exception:
        current_app.logger.exception("AI tool '%s' failed", name)
        return {"error": "That lookup failed unexpectedly."}
