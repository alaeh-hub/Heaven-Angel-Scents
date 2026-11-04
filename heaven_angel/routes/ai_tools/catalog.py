
from heaven_angel.db import query
from heaven_angel.utils import BOTTLE_UNITS, bottle_size_ml

from heaven_angel.routes.ai_tools._common import MAX_ROWS
from heaven_angel.routes.ai_tools.sales import PERIOD_NAMES, PERIOD_TMPL, period_sql


def get_raw_materials(args, ctx):
    name = (args.get("name") or "").strip()
    sql = (
        "SELECT rm.material_name, rm.unit, rm.stock_qty, rm.cost_per_unit, rm.purchase_mode, "
        "s.supplier_name FROM raw_materials rm "
        "LEFT JOIN suppliers s ON rm.supplier_id = s.supplier_id"
    )
    params = []
    if name:
        sql += " WHERE rm.material_name LIKE %s"
        params.append(f"%{name}%")
    sql += " ORDER BY rm.stock_qty ASC, rm.material_name LIMIT %s"
    params.append(MAX_ROWS)
    rows = query(sql, tuple(params))
    return {
        "results": [
            {"material": r["material_name"], "unit": r["unit"], "stock_qty": float(r["stock_qty"] or 0),
             "cost_per_unit": float(r["cost_per_unit"] or 0), "purchase_mode": r["purchase_mode"],
             "supplier": r["supplier_name"]}
            for r in rows
        ],
        "note": "Sorted lowest stock first.",
    }


def get_suppliers(args, ctx):
    name = (args.get("name") or "").strip()
    sql = (
        "SELECT s.supplier_name, s.contact_person, s.phone, s.email, "
        "COUNT(rm.material_id) AS material_count, "
        "GROUP_CONCAT(rm.material_name ORDER BY rm.material_name SEPARATOR ', ') AS materials "
        "FROM suppliers s LEFT JOIN raw_materials rm ON rm.supplier_id = s.supplier_id"
    )
    params = []
    if name:
        sql += " WHERE s.supplier_name LIKE %s"
        params.append(f"%{name}%")
    sql += " GROUP BY s.supplier_id ORDER BY s.supplier_name LIMIT %s"
    params.append(MAX_ROWS)
    rows = query(sql, tuple(params))
    return {
        "results": [
            {"supplier": r["supplier_name"], "contact_person": r["contact_person"], "phone": r["phone"],
             "email": r["email"], "material_count": r["material_count"], "materials": r["materials"]}
            for r in rows
        ]
    }


def get_formulas(args, ctx):
    """Cost-of-goods settings (the Formulas page): a flat base cost per
    bottle size, charged per bottle produced, plus the Bulk/Refill rate
    per mL. Same tables production() and the Formulas report read
    (unit_cogs_settings / bulk_rate_settings); the old per-material
    recipe table (unit_formula_items) is no longer used."""
    unit = (args.get("unit") or "").strip().upper()
    sql = "SELECT unit, base_cost_per_unit FROM unit_cogs_settings"
    params = []
    if unit:
        sql += " WHERE unit = %s"
        params.append(unit)
    rows = query(sql, tuple(params))
    by_unit = {r["unit"]: float(r["base_cost_per_unit"] or 0) for r in rows}
    rate = query("SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1", fetchone=True)
    result = {
        "bottle_sizes": [
            {"unit": u, "base_cost_per_unit": by_unit.get(u, 0.0)}
            for u in BOTTLE_UNITS if not unit or u == unit
        ],
    }
    if not unit or unit == "BULK":
        result["bulk_rate_per_ml"] = float(rate["rate_per_ml"]) if rate else 0.0
    return result


def get_bulk_batches(args, ctx):
    active_only = args.get("active_only", True)
    sql = (
        "SELECT batch_code, scent_name, total_volume_ml, remaining_ml, cost_per_ml, total_cost, created_at "
        "FROM bulk_batches"
    )
    if active_only:
        sql += " WHERE remaining_ml > 0"
    sql += " ORDER BY created_at DESC LIMIT %s"
    rows = query(sql, (MAX_ROWS,))
    results = []
    for r in rows:
        remaining = float(r["remaining_ml"] or 0)
        results.append({
            "batch_code": r["batch_code"], "scent": r["scent_name"],
            "total_volume_ml": float(r["total_volume_ml"] or 0), "remaining_ml": remaining,
            "cost_per_ml": float(r["cost_per_ml"] or 0), "total_cost": float(r["total_cost"] or 0),
            # Same yield figure the Bulk Batches page shows: how many full
            # bottles of each size the remaining mL could still fill.
            "bottles_possible": {u: int(remaining // float(bottle_size_ml(u))) for u in BOTTLE_UNITS},
            "created_at": r["created_at"].isoformat(),
        })
    return {"results": results}


def get_production_costs(args, ctx):
    """COGS logged by production runs. A bulk product's qty_produced is mL
    and its cost is per mL (see admin.production()), so bottles and bulk
    mL are totalled apart and each product reports the matching rate."""
    period = (args.get("period") or "this_month").strip()
    if period not in PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    where = period_sql(period, "c.created_at")
    totals = query(
        "SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN c.qty_produced ELSE 0 END),0) AS units, "
        "COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN c.qty_produced ELSE 0 END),0) AS bulk_ml, "
        "COALESCE(SUM(c.total_cogs),0) AS cogs "
        "FROM cogs_logs c JOIN products p ON c.sku = p.sku WHERE " + where, fetchone=True,
    )
    rows = query(
        "SELECT p.item_name, p.unit, SUM(c.qty_produced) AS qty, SUM(c.total_cogs) AS cogs "
        "FROM cogs_logs c JOIN products p ON c.sku = p.sku WHERE " + where +
        " GROUP BY p.sku, p.item_name, p.unit ORDER BY cogs DESC LIMIT %s",
        (MAX_ROWS,),
    )
    by_product = []
    for r in rows:
        qty = float(r["qty"] or 0)
        cogs = float(r["cogs"] or 0)
        rate = round(cogs / qty, 4 if r["unit"] == "BULK" else 2) if qty else None
        entry = {"item_name": r["item_name"], "unit": r["unit"], "cogs": cogs}
        if r["unit"] == "BULK":
            entry.update({"bulk_ml": qty, "cogs_per_ml": rate})
        else:
            entry.update({"units": qty, "cogs_per_unit": rate})
        by_product.append(entry)
    return {
        "period": period,
        "units_produced": float(totals["units"]),
        "bulk_ml_produced": float(totals["bulk_ml"]),
        "total_cogs": float(totals["cogs"]),
        "by_product": by_product,
    }


def get_packages(args, ctx):
    include_inactive = bool(args.get("include_inactive"))
    sql = (
        "SELECT pkg.package_name, pkg.partner_scope, pkg.discount_percent, pkg.is_active, "
        "COUNT(pi.package_item_id) AS item_count, COALESCE(SUM(pi.qty * p.price), 0) AS reference_total "
        "FROM packages pkg LEFT JOIN package_items pi ON pi.package_id = pkg.package_id "
        "LEFT JOIN products p ON p.sku = pi.sku"
    )
    if not include_inactive:
        sql += " WHERE pkg.is_active = TRUE"
    sql += " GROUP BY pkg.package_id ORDER BY pkg.created_at DESC LIMIT %s"
    rows = query(sql, (MAX_ROWS,))
    results = []
    for r in rows:
        # Same math as finance.package_value().
        reference = float(r["reference_total"])
        discount = float(r["discount_percent"])
        results.append({
            "package": r["package_name"], "for": r["partner_scope"], "active": bool(r["is_active"]),
            "item_count": r["item_count"], "discount_percent": discount,
            "reference_total": reference, "discounted_total": round(reference * (1 - discount / 100), 2),
        })
    return {"results": results}


_INQUIRY_STATUSES = {"New", "Contacted", "Follow-up", "On Hold", "Closed", "Declined"}


def get_partner_inquiries(args, ctx):
    status = (args.get("status") or "").strip()
    if status and status not in _INQUIRY_STATUSES:
        return {"error": f"status must be one of {sorted(_INQUIRY_STATUSES)}."}
    counts = query("SELECT status, COUNT(*) AS n FROM partner_inquiries GROUP BY status")
    sql = (
        "SELECT company_name, contact_person, partner_type, package_name_snapshot, order_amount, "
        "status, fulfilled_at, created_at FROM partner_inquiries"
    )
    params = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(15)
    rows = query(sql, tuple(params))
    return {
        "counts_by_status": {r["status"]: r["n"] for r in counts},
        "results": [
            {"company": r["company_name"], "contact_person": r["contact_person"],
             "partner_type": r["partner_type"], "package": r["package_name_snapshot"],
             "order_amount": float(r["order_amount"]) if r["order_amount"] is not None else None,
             "counts_as_sale": r["status"] == "Closed" and r["order_amount"] is not None,
             "status": r["status"],
             "fulfilled_at": r["fulfilled_at"].isoformat() if r["fulfilled_at"] else None, "received_at": r["created_at"].isoformat()}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Tool: get_recent_sales  (both roles, branch-scoped)
# ---------------------------------------------------------------------------
