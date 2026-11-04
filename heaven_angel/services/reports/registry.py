"""Maps each report type to its builder and runs one (get_report)."""
from heaven_angel.services.reports.builders_admin import report_accounts, report_admin_log, report_login_activity
from heaven_angel.services.reports.builders_inventory import report_branch_stock, report_bulk_batches, report_cogs_logs, report_discrepancies, report_formulas, report_inventory_log, report_low_stock, report_materials, report_production_log, report_products, report_stock_requests, report_suppliers
from heaven_angel.services.reports.builders_sales import report_branch_performance, report_credit_purchases, report_customers, report_financial_summary, report_packages, report_partner_inquiries, report_partners, report_sales_history, report_voided_sales
from heaven_angel.services.reports.definitions import BRANCH_FILTERED_TYPES, REPORT_TYPES
from heaven_angel.services.reports.totals import compute_totals, split_bulk_quantities


from heaven_angel.db import query
from heaven_angel.utils import (
    business_now,
)


def _branch_name(branch_id):
    if branch_id in (None, "all"):
        return None
    row = query("SELECT branch_name FROM branches WHERE branch_id = %s",
                (branch_id,), fetchone=True)
    return row["branch_name"] if row else None


_BUILDERS = {
    "products": report_products,
    "production_log": report_production_log,
    "materials": report_materials,
    "suppliers": report_suppliers,
    "formulas": report_formulas,
    "cogs_logs": report_cogs_logs,
    "bulk_batches": report_bulk_batches,
    "branch_stock": report_branch_stock,
    "low_stock": report_low_stock,
    "stock_requests": report_stock_requests,
    "inventory_log": report_inventory_log,
    "sales_history": report_sales_history,
    "credit_purchases": report_credit_purchases,
    "voided_sales": report_voided_sales,
    "customers": report_customers,
    "discrepancies": report_discrepancies,
    "branch_performance": report_branch_performance,
    "accounts": report_accounts,
    "partners": report_partners,
    "packages": report_packages,
    "partner_inquiries": report_partner_inquiries,
    "financial_summary": report_financial_summary,
    "admin_log": report_admin_log,
    "login_activity": report_login_activity,
}


def get_report(report_type, filters, branch_scope=None, actor_label=""):
    """Build a report dict: title, subtitle, window_note, columns, rows, truncated.

    branch_scope: pass the signed-in branch's branch_id to force every
    query above to that branch and drop the Branch column entirely (used
    by routes/branch.py). Leave None for the admin side, where the
    branch_id filter (or "all branches") from the querystring applies.
    """
    if report_type not in _BUILDERS:
        raise ValueError(f"Unknown report type: {report_type}")

    meta = REPORT_TYPES[report_type]
    label = meta["branch_label"] if (
        branch_scope is not None and "branch_label" in meta) else meta["label"]

    columns, rows, truncated, window_note = _BUILDERS[report_type](
        filters, branch_scope)
    columns = split_bulk_quantities(columns, rows)

    scoped_branch_name = _branch_name(branch_scope) if branch_scope is not None else (
        _branch_name(filters.get("branch_id"))
        if report_type in BRANCH_FILTERED_TYPES and filters.get("branch_id") not in (None, "all")
        else None
    )
    subtitle_bits = [scoped_branch_name] if scoped_branch_name else []
    if meta["windowed"]:
        subtitle_bits.append(window_note)
    subtitle = " · ".join(subtitle_bits) if subtitle_bits else window_note

    return {
        "report_type": report_type,
        "title": label,
        "subtitle": subtitle,
        "actor_label": actor_label,
        "generated_at": business_now(),
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        # {column_key: summed_value} for every money/int column, or None
        # — see compute_totals(). Both render_report_pdf() and
        # render_report_excel() add a bold Total row from this when present.
        "totals": compute_totals(columns, rows),
    }
