"""Tools the AI assistant can call (Gemini function calling), instead of
dumping a full data snapshot into the prompt on every turn (the old
approach in ai.py). Each tool is:

  - Scoped server-side by role/branch (ctx["role"], ctx["branch_id"]) —
    the model never gets to choose whose data it reads. Branch staff
    cannot pass a branch_name that isn't their own; it's silently
    overridden, never trusted from the model's arguments.
  - Admin-only tools (HQ catalog/supply/production/partner data — see
    _ADMIN_ONLY) are only declared to an Admin session AND re-checked
    in dispatch(), so a Branch session can't reach them even if the
    model names one it was never offered.
  - Read-only, EXCEPT propose_stock_request, which does not write to
    stock_requests / stock_request_items (the real, active tables) —
    it writes a draft to ai_stock_drafts / ai_stock_draft_items with
    status 'Pending Review'. A human still has to approve it (see the
    new /ai/drafts routes in ai.py) before it becomes a real delivery
    that HQ can dispatch. This is the "human-in-the-loop" boundary:
    the agent can *draft*, never *commit*.

Schema notes (confirmed against schema.sql / routes/ai.py):
  - stock_requests(request_id, branch_id, delivery_number, status,
    requested_at) and stock_request_items(item_id, request_id, sku,
    requested_qty, dispatched_qty, received_qty, damaged_qty, unit_price).
  - Bulk/Refill products (products.unit = 'BULK') keep qty_sold,
    stock_qty and qty_produced in mL, never bottles — every tool keeps
    them apart (bulk_ml / stock_unit) instead of adding them to bottles.
  - products.sku is VARCHAR(50) (per utils.py's build_sku() comment).
  - branches(branch_id, branch_name, is_hq).
  - Draft approval (routes/ai.py's approve_draft) generates the real
    delivery_number as "DR-<request_id zero-padded to 6 digits>" — the
    same convention branch.request_stock() uses — so AI-originated
    deliveries look identical to normal ones in the Stock Requests list.
"""

from flask import current_app, url_for

from db import query, transaction
from reports import DISCREPANCY_ITEM_JOIN, DISCREPANCY_UNITS_SQL, LOW_STOCK_WHERE
from utils import BOTTLE_UNITS, ValidationError, bottle_size_ml

try:
    import audit
except ImportError:  # pragma: no cover - audit logging is best-effort
    audit = None

try:
    import sockets
except ImportError:  # pragma: no cover - realtime push is best-effort
    sockets = None


MAX_ROWS = 30  # cap on any single tool's result rows, to keep responses cheap


# ---------------------------------------------------------------------------
# Branch-name resolution helper (shared by every tool below)
# ---------------------------------------------------------------------------

def _resolve_branch_ids(ctx, branch_name, include_hq=False):
    """Turn an (optional, model-supplied) branch_name into a list of
    branch_ids the caller is actually allowed to see.

    Branch-role users ALWAYS get their own branch_id, regardless of what
    the model passed — this is the actual access boundary, not the
    model's argument. Admins may name a branch (fuzzy match) or leave it
    blank to mean "every branch".

    include_hq lets the name match HQ's own warehouse too (e.g. "HQ"),
    for tools where HQ is one of the locations being reported on.
    """
    if ctx["role"] != "Admin":
        return [ctx["branch_id"]], None

    if not branch_name:
        return None, None  # None = no filter = every branch, admin only

    sql = "SELECT branch_id, branch_name FROM branches WHERE branch_name LIKE %s"
    if not include_hq:
        sql += " AND is_hq = FALSE"
    rows = query(sql, (f"%{branch_name}%",))
    if not rows:
        return [], f"No branch matching '{branch_name}'."
    if len(rows) > 1:
        names = ", ".join(r["branch_name"] for r in rows)
        return [], f"'{branch_name}' matches more than one branch ({names}) — be more specific."
    return [rows[0]["branch_id"]], None


# ---------------------------------------------------------------------------
# Tool: check_stock
# ---------------------------------------------------------------------------

def _check_stock(args, ctx):
    sku = (args.get("sku") or "").strip()
    item_name = (args.get("item_name") or "").strip()
    branch_name = (args.get("branch_name") or "").strip()

    if not sku and not item_name:
        return {"error": "Provide either sku or item_name."}

    branch_ids, err = _resolve_branch_ids(ctx, branch_name, include_hq=True)
    if err:
        return {"error": err}

    conditions = []
    params = []
    if sku:
        conditions.append("p.sku = %s")
        params.append(sku)
    elif item_name:
        conditions.append("p.item_name LIKE %s")
        params.append(f"%{item_name}%")
    if ctx["role"] != "Admin":
        # Same as the branch's My Inventory page: branches only stock
        # bottled sizes, so their (always-0) bulk rows are never shown.
        conditions.append("p.unit <> 'BULK'")

    sql = (
        "SELECT b.branch_name, b.is_hq, p.sku, p.item_name, p.unit, p.variant, p.category, p.price, "
        "bi.stock_qty, bi.reorder_level "
        "FROM branch_inventory bi "
        "JOIN products p ON bi.sku = p.sku "
        "JOIN branches b ON bi.branch_id = b.branch_id "
        "WHERE " + " AND ".join(conditions)
    )
    if branch_ids is not None:
        if not branch_ids:
            return {"results": [], "note": "No matching branch."}
        placeholders = ",".join(["%s"] * len(branch_ids))
        sql += f" AND bi.branch_id IN ({placeholders})"
        params.extend(branch_ids)
    sql += " ORDER BY p.item_name, b.branch_name LIMIT %s"
    params.append(MAX_ROWS)

    rows = query(sql, tuple(params))
    if not rows:
        return {"results": [], "note": "No matching product/branch found."}
    return {
        "results": [
            {
                "branch": r["branch_name"],
                "is_hq": bool(r["is_hq"]),
                "sku": r["sku"],
                "item_name": r["item_name"],
                "unit": r["unit"],
                "variant": r["variant"],
                "category": r["category"],
                "price": float(r["price"]),
                "stock_qty": r["stock_qty"],
                "stock_unit": "mL" if r["unit"] == "BULK" else "bottles",
                "reorder_level": r["reorder_level"],
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Tool: get_low_stock
# ---------------------------------------------------------------------------

def _get_low_stock(args, ctx):
    branch_name = (args.get("branch_name") or "").strip()
    branch_ids, err = _resolve_branch_ids(ctx, branch_name, include_hq=True)
    if err:
        return {"error": err}

    sql = (
        "SELECT b.branch_name, b.is_hq, p.sku, p.item_name, p.unit, bi.stock_qty, bi.reorder_level "
        "FROM branch_inventory bi "
        "JOIN branches b ON bi.branch_id = b.branch_id "
        "JOIN products p ON bi.sku = p.sku "
        # Same rule as the Low Stock page (reports.LOW_STOCK_WHERE):
        # HQ's warehouse and every branch; BULK has no reorder level.
        "WHERE " + LOW_STOCK_WHERE
    )
    params = []
    if branch_ids is not None:
        if not branch_ids:
            return {"results": [], "note": "No matching branch."}
        placeholders = ",".join(["%s"] * len(branch_ids))
        sql += f" AND bi.branch_id IN ({placeholders})"
        params.extend(branch_ids)
    sql += " ORDER BY (bi.stock_qty - bi.reorder_level) ASC, bi.stock_qty ASC LIMIT %s"
    params.append(MAX_ROWS)

    rows = query(sql, tuple(params))
    return {
        "results": [
            {
                "branch": r["branch_name"],
                "is_hq": bool(r["is_hq"]),
                "sku": r["sku"],
                "item_name": r["item_name"],
                "unit": r["unit"],
                "stock_qty": r["stock_qty"],
                "reorder_level": r["reorder_level"],
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Tool: get_pending_deliveries
# ---------------------------------------------------------------------------

def _stock_request_items_by_request(request_ids):
    if not request_ids:
        return {}
    placeholders = ",".join(["%s"] * len(request_ids))
    rows = query(
        f"""SELECT sri.request_id, p.item_name, p.unit, sri.sku, sri.requested_qty, sri.unit_price
            FROM stock_request_items sri JOIN products p ON sri.sku = p.sku
            WHERE sri.request_id IN ({placeholders})
            ORDER BY sri.request_id, p.item_name""",
        tuple(request_ids),
    )
    out = {}
    for r in rows:
        out.setdefault(r["request_id"], []).append({
            "sku": r["sku"],
            "item_name": r["item_name"],
            "unit": r["unit"],
            "requested_qty": r["requested_qty"],
            "unit_price": float(r["unit_price"]),
        })
    return out


def _get_pending_deliveries(args, ctx):
    status = (args.get("status") or "").strip()
    valid_statuses = {"Pending", "In Transit", "Partially Fulfilled", "Fulfilled", "Rejected"}
    if status and status not in valid_statuses:
        return {"error": f"status must be one of {sorted(valid_statuses)}."}

    branch_name = (args.get("branch_name") or "").strip()
    branch_ids, err = _resolve_branch_ids(ctx, branch_name)
    if err:
        return {"error": err}

    conditions = []
    params = []
    if status:
        conditions.append("sr.status = %s")
        params.append(status)
    else:
        conditions.append("sr.status IN ('Pending','Partially Fulfilled','In Transit')")
    if branch_ids is not None:
        if not branch_ids:
            return {"results": [], "note": "No matching branch."}
        placeholders = ",".join(["%s"] * len(branch_ids))
        conditions.append(f"sr.branch_id IN ({placeholders})")
        params.extend(branch_ids)

    sql = (
        "SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at, b.branch_name "
        "FROM stock_requests sr JOIN branches b ON sr.branch_id = b.branch_id "
        "WHERE " + " AND ".join(conditions) +
        " ORDER BY sr.requested_at ASC LIMIT %s"
    )
    params.append(15)
    headers = query(sql, tuple(params))
    items_by_request = _stock_request_items_by_request([h["request_id"] for h in headers])

    return {
        "results": [
            {
                "delivery_number": h["delivery_number"],
                "branch": h["branch_name"],
                "status": h["status"],
                "requested_at": h["requested_at"].isoformat(),
                "items": items_by_request.get(h["request_id"], []),
            }
            for h in headers
        ]
    }


# ---------------------------------------------------------------------------
# Tool: get_sales_summary
# ---------------------------------------------------------------------------

_PERIOD_TMPL = {
    "today": "DATE({col}) = CURDATE()",
    "this_week": "YEARWEEK({col}, 3) = YEARWEEK(CURDATE(), 3)",
    "this_month": "YEAR({col}) = YEAR(CURDATE()) AND MONTH({col}) = MONTH(CURDATE())",
    "last_month": ("YEAR({col}) = YEAR(CURDATE() - INTERVAL 1 MONTH) "
                   "AND MONTH({col}) = MONTH(CURDATE() - INTERVAL 1 MONTH)"),
    "last_7_days": "DATE({col}) >= CURDATE() - INTERVAL 6 DAY",
    "last_30_days": "DATE({col}) >= CURDATE() - INTERVAL 29 DAY",
    "this_year": "YEAR({col}) = YEAR(CURDATE())",
    "all_time": "1 = 1",
}
PERIOD_NAMES = ", ".join(_PERIOD_TMPL)


def _period_sql(period, col):
    """WHERE fragment for a named period on a timestamp column. Only ever
    formatted with a hard-coded column name, never model input."""
    return _PERIOD_TMPL[period].format(col=col)


_PERIOD_SQL = {name: _period_sql(name, "s.sold_at") for name in _PERIOD_TMPL}

# Bulk products' qty_sold is mL, not bottles, so every "units" figure below
# counts bottles only and bulk volume comes back separately as bulk_ml.
# Both need `products p` joined on s.sku.
_UNITS_SQL = "COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END),0) AS units"
_BULK_ML_SQL = "COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END),0) AS bulk_ml"


def _get_sales_summary(args, ctx):
    period = (args.get("period") or "today").strip()
    if period not in _PERIOD_SQL:
        return {"error": f"period must be one of {sorted(_PERIOD_SQL)}."}

    branch_name = (args.get("branch_name") or "").strip()
    by_branch = bool(args.get("by_branch"))
    # HQ records its own sales too (admin Record Sale), so "HQ" is a
    # valid location here.
    branch_ids, err = _resolve_branch_ids(ctx, branch_name, include_hq=True)
    if err:
        return {"error": err}

    conditions = [_PERIOD_SQL[period]]
    params = []
    if branch_ids is not None:
        if not branch_ids:
            return {"error": "No matching branch."}
        placeholders = ",".join(["%s"] * len(branch_ids))
        conditions.append(f"s.branch_id IN ({placeholders})")
        params.extend(branch_ids)

    totals = query(
        f"SELECT {_UNITS_SQL}, {_BULK_ML_SQL}, COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue "
        "FROM sales s JOIN products p ON s.sku = p.sku WHERE " + " AND ".join(conditions),
        tuple(params), fetchone=True,
    )
    top_items = query(
        f"SELECT p.item_name, {_UNITS_SQL}, {_BULK_ML_SQL}, SUM(s.qty_sold*s.unit_price) AS revenue "
        "FROM sales s JOIN products p ON s.sku = p.sku WHERE " + " AND ".join(conditions) +
        " GROUP BY p.item_name ORDER BY units DESC, bulk_ml DESC LIMIT 5",
        tuple(params),
    )
    # Sale vs Refill vs Freebie, Cash vs Credit, so "how much went on
    # credit" or "how many refills" doesn't need a tool of its own.
    by_type = query(
        "SELECT s.sale_type, s.payment_method, COUNT(*) AS transactions, "
        f"{_UNITS_SQL}, {_BULK_ML_SQL}, COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue "
        "FROM sales s JOIN products p ON s.sku = p.sku WHERE " + " AND ".join(conditions) +
        " GROUP BY s.sale_type, s.payment_method ORDER BY revenue DESC",
        tuple(params),
    )
    result = {
        "period": period,
        "units": totals["units"],
        "bulk_ml": totals["bulk_ml"],
        "revenue": float(totals["revenue"]),
        "top_items": [
            {"item_name": r["item_name"], "units": r["units"], "bulk_ml": r["bulk_ml"],
             "revenue": float(r["revenue"])}
            for r in top_items
        ],
        "by_type": [
            {"sale_type": r["sale_type"], "payment_method": r["payment_method"],
             "transactions": r["transactions"], "units": r["units"], "bulk_ml": r["bulk_ml"],
             "revenue": float(r["revenue"])}
            for r in by_type
        ],
    }

    # Dashboard / Reports "Total revenue" also counts Closed partner
    # package orders (partner_inquiries.order_amount). Only fleet-wide —
    # a package order isn't tied to any branch.
    if ctx["role"] == "Admin" and branch_ids is None:
        pkg = query(
            "SELECT COUNT(*) AS n, COALESCE(SUM(order_amount),0) AS revenue FROM partner_inquiries "
            "WHERE status = 'Closed' AND " + _period_sql(period, "created_at"),
            fetchone=True,
        )
        result["package_order_count"] = pkg["n"]
        result["package_revenue"] = float(pkg["revenue"])
        result["total_revenue"] = round(result["revenue"] + result["package_revenue"], 2)

    # by_branch: one GROUP BY query instead of the model having to loop
    # get_sales_summary once per branch_name (which is what used to blow
    # through MAX_TOOL_ROUNDS on "how does revenue split across branches"
    # -style questions — there was previously no way to get a per-branch
    # breakdown except calling this tool once per branch, and no tool to
    # even list branch names first). Only meaningful when the result
    # isn't already scoped to one named branch; LEFT JOIN so a branch
    # with zero sales in the period still shows up as 0 instead of being
    # silently missing from the split.
    if by_branch and branch_ids is None:
        by_branch_rows = query(
            f"SELECT b.branch_name, b.is_hq, {_UNITS_SQL}, {_BULK_ML_SQL}, "
            "COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue "
            "FROM branches b LEFT JOIN sales s ON s.branch_id = b.branch_id AND " +
            _PERIOD_SQL[period] + " LEFT JOIN products p ON p.sku = s.sku" +
            " GROUP BY b.branch_id, b.branch_name ORDER BY revenue DESC"
        )
        result["by_branch"] = [
            {"branch_name": r["branch_name"], "is_hq": bool(r["is_hq"]),
             "units": r["units"], "bulk_ml": r["bulk_ml"], "revenue": float(r["revenue"])}
            for r in by_branch_rows
        ]

    return result


def _branch_filter(ctx, branch_name, column, include_hq=True):
    """(sql_fragment, params, error) restricting `column` to the branches
    the caller may see. An empty fragment means every branch (Admin with
    no branch named). HQ is nameable by default, since HQ records sales
    of its own."""
    branch_ids, err = _resolve_branch_ids(ctx, branch_name, include_hq=include_hq)
    if err:
        return None, None, err
    if branch_ids is None:
        return "", [], None
    if not branch_ids:
        return None, None, "No matching branch."
    placeholders = ",".join(["%s"] * len(branch_ids))
    return f" AND {column} IN ({placeholders})", list(branch_ids), None


# ---------------------------------------------------------------------------
# Tool: get_credit_purchases  (both roles, branch-scoped)
# ---------------------------------------------------------------------------

def _get_credit_purchases(args, ctx):
    """Store-credit ("utang") taken per buyer, grouped the same way as the
    Credit Purchases page (branch.credit_purchases)."""
    frag, params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "s.branch_id")
    if err:
        return {"error": err}
    rows = query(
        "SELECT b.branch_name, COALESCE(s.buyer_name, bu.username) AS buyer_name, "
        f"COUNT(*) AS transactions, {_UNITS_SQL}, {_BULK_ML_SQL}, "
        "COALESCE(SUM(s.qty_sold*s.unit_price),0) AS amount, MAX(s.sold_at) AS last_taken_at "
        "FROM sales s JOIN branches b ON s.branch_id = b.branch_id "
        "JOIN products p ON s.sku = p.sku "
        "LEFT JOIN users bu ON s.buyer_user_id = bu.user_id "
        "WHERE s.payment_method = 'Credit'" + frag +
        " GROUP BY b.branch_name, COALESCE(s.buyer_name, bu.username) "
        "ORDER BY amount DESC LIMIT %s",
        tuple(params + [MAX_ROWS]),
    )
    return {
        "results": [
            {"branch": r["branch_name"], "buyer": r["buyer_name"] or "(unnamed)",
             "transactions": r["transactions"], "units": r["units"], "bulk_ml": r["bulk_ml"],
             "amount": float(r["amount"]), "last_taken_at": r["last_taken_at"].isoformat()}
            for r in rows
        ],
        "note": "All-time totals of Credit-payment sales per buyer. The system doesn't record repayments.",
    }


# ---------------------------------------------------------------------------
# Tool: get_customers  (both roles, branch-scoped)
# ---------------------------------------------------------------------------

def _get_customers(args, ctx):
    """Named walk-in customers, derived from sales.customer_name the same
    way the Customers pages do (there's no separate customers table):
    address from the customer's first sale, plus where they last bought."""
    name = (args.get("name") or "").strip()
    frag, branch_params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "s.branch_id")
    if err:
        return {"error": err}
    where = "s.customer_name IS NOT NULL AND s.customer_name <> ''" + frag
    name_params = []
    if name:
        where += " AND s.customer_name LIKE %s"
        name_params.append(f"%{name}%")
    # Placeholder order: address subquery, last-branch subquery, inner
    # aggregate (branch + name), LIMIT.
    params = branch_params + branch_params + branch_params + name_params + [MAX_ROWS]
    inner = (
        f"SELECT s.customer_name, COUNT(*) AS transactions, {_UNITS_SQL}, {_BULK_ML_SQL}, "
        "COALESCE(SUM(s.qty_sold*s.unit_price),0) AS spent, "
        "MIN(s.sold_at) AS first_sold_at, MAX(s.sold_at) AS last_visit, "
        "GROUP_CONCAT(DISTINCT b.branch_name ORDER BY b.branch_name SEPARATOR ', ') AS branches "
        "FROM sales s JOIN branches b ON s.branch_id = b.branch_id "
        "JOIN products p ON s.sku = p.sku "
        "WHERE " + where + " GROUP BY s.customer_name"
    )
    rows = query(
        "SELECT agg.*, "
        "(SELECT f.customer_address FROM sales f WHERE f.customer_name = agg.customer_name "
        " AND f.sold_at = agg.first_sold_at" + frag.replace("s.branch_id", "f.branch_id") +
        " ORDER BY f.sale_id LIMIT 1) AS address, "
        "(SELECT lb.branch_name FROM sales l JOIN branches lb ON l.branch_id = lb.branch_id "
        " WHERE l.customer_name = agg.customer_name AND l.sold_at = agg.last_visit" +
        frag.replace("s.branch_id", "l.branch_id") +
        " ORDER BY l.sale_id DESC LIMIT 1) AS last_branch "
        "FROM (" + inner + ") agg ORDER BY agg.spent DESC LIMIT %s",
        tuple(params),
    )
    return {
        "results": [
            {"customer": r["customer_name"], "address": r["address"] or None,
             "transactions": r["transactions"], "units": r["units"], "bulk_ml": r["bulk_ml"],
             "spent": float(r["spent"]), "last_visit": r["last_visit"].isoformat(),
             "last_branch": r["last_branch"], "branches": r["branches"]}
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Admin-only tools: raw materials, suppliers, COGS settings (Formulas
# page), bulk batches, production cost (COGS), packages, partner
# inquiries, branch performance, financials. See _ADMIN_ONLY.
# ---------------------------------------------------------------------------

def _get_raw_materials(args, ctx):
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


def _get_suppliers(args, ctx):
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


def _get_formulas(args, ctx):
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


def _get_bulk_batches(args, ctx):
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


def _get_production_costs(args, ctx):
    """COGS logged by production runs. A bulk product's qty_produced is mL
    and its cost is per mL (see admin.production()), so bottles and bulk
    mL are totalled apart and each product reports the matching rate."""
    period = (args.get("period") or "this_month").strip()
    if period not in _PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    where = _period_sql(period, "c.created_at")
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


def _get_packages(args, ctx):
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
        # Same math as admin._package_value().
        reference = float(r["reference_total"])
        discount = float(r["discount_percent"])
        results.append({
            "package": r["package_name"], "for": r["partner_scope"], "active": bool(r["is_active"]),
            "item_count": r["item_count"], "discount_percent": discount,
            "reference_total": reference, "discounted_total": round(reference * (1 - discount / 100), 2),
        })
    return {"results": results}


_INQUIRY_STATUSES = {"New", "Contacted", "Follow-up", "On Hold", "Closed", "Declined"}


def _get_partner_inquiries(args, ctx):
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

_SALE_TYPES = {"Sale", "Refill", "Freebie"}


def _get_recent_sales(args, ctx):
    """Individual sale rows, newest first — the Sales History / Record
    Sale "Recent sales" list."""
    sale_type = (args.get("sale_type") or "").strip()
    if sale_type and sale_type not in _SALE_TYPES:
        return {"error": f"sale_type must be one of {sorted(_SALE_TYPES)}."}
    try:
        limit = max(1, min(int(args.get("limit") or 10), MAX_ROWS))
    except (TypeError, ValueError):
        limit = 10
    frag, params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "s.branch_id")
    if err:
        return {"error": err}
    sql = (
        "SELECT s.sold_at, b.branch_name, p.item_name, p.sku, p.unit, s.sale_type, s.payment_method, "
        "s.qty_sold, s.unit_price, COALESCE(s.buyer_name, bu.username) AS buyer, s.customer_name "
        "FROM sales s JOIN branches b ON s.branch_id = b.branch_id "
        "JOIN products p ON s.sku = p.sku "
        "LEFT JOIN users bu ON s.buyer_user_id = bu.user_id "
        "WHERE 1=1" + frag
    )
    if sale_type:
        sql += " AND s.sale_type = %s"
        params.append(sale_type)
    sql += " ORDER BY s.sold_at DESC, s.sale_id DESC LIMIT %s"
    params.append(limit)
    rows = query(sql, tuple(params))
    return {
        "results": [
            {"sold_at": r["sold_at"].isoformat(), "branch": r["branch_name"],
             "item_name": r["item_name"], "sku": r["sku"], "sale_type": r["sale_type"],
             "payment_method": r["payment_method"], "qty": r["qty_sold"],
             "qty_unit": "mL" if r["unit"] == "BULK" else "bottles",
             "unit_price": float(r["unit_price"]),
             "total": float(r["qty_sold"] * r["unit_price"]),
             "customer": r["customer_name"] or "Walk-in",
             "credit_buyer": r["buyer"] if r["payment_method"] == "Credit" else None}
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Tool: get_discrepancies  (both roles, branch-scoped)
# ---------------------------------------------------------------------------

def _get_discrepancies(args, ctx):
    """Delivery discrepancies — DAMAGE (damaged on arrival) and ADJUSTMENT
    (dispatched but never received) — with units counted the same way as
    the Discrepancies pages and report (reports.DISCREPANCY_UNITS_SQL;
    these log rows' own change_qty is often 0)."""
    period = (args.get("period") or "all_time").strip()
    if period not in _PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    frag, params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "sml.branch_id")
    if err:
        return {"error": err}
    base = (
        "FROM stock_movement_logs sml "
        "JOIN branches b ON sml.branch_id = b.branch_id "
        "JOIN products p ON sml.sku = p.sku "
        "LEFT JOIN stock_requests sr ON sml.reference_id = sr.request_id "
        f"{DISCREPANCY_ITEM_JOIN} "
        "WHERE sml.reference_type = 'STOCK_REQUEST' AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT') "
        "AND " + _period_sql(period, "sml.created_at") + frag
    )
    totals = query(
        f"SELECT sml.movement_type, COUNT(*) AS n, COALESCE(SUM({DISCREPANCY_UNITS_SQL}),0) AS units " + base +
        " GROUP BY sml.movement_type",
        tuple(params),
    )
    rows = query(
        f"SELECT sml.created_at, b.branch_name, sr.delivery_number, p.item_name, p.sku, "
        f"sml.movement_type, {DISCREPANCY_UNITS_SQL} AS units, sml.notes " + base +
        " ORDER BY sml.created_at DESC LIMIT %s",
        tuple(params + [MAX_ROWS]),
    )
    return {
        "period": period,
        "totals_by_type": {r["movement_type"]: {"entries": r["n"], "units": r["units"]} for r in totals},
        "results": [
            {"when": r["created_at"].isoformat(), "branch": r["branch_name"],
             "delivery_number": r["delivery_number"], "item_name": r["item_name"], "sku": r["sku"],
             "type": r["movement_type"], "units": r["units"], "notes": r["notes"]}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Tool: get_branch_performance  (admin only)
# ---------------------------------------------------------------------------

def _get_branch_performance(args, ctx):
    """Per retail branch (HQ excluded), same figures as the Branch
    Performance page: bottles and bulk mL sold, revenue, current stock,
    discrepancy units and turnover (bottles sold / bottles on hand)."""
    period = (args.get("period") or "all_time").strip()
    if period not in _PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    rows = query(
        f"""SELECT b.branch_name,
                  COALESCE(sa.units_sold, 0) AS units_sold, COALESCE(sa.bulk_ml_sold, 0) AS bulk_ml_sold,
                  COALESCE(sa.revenue, 0) AS revenue, COALESCE(sa.sales_count, 0) AS sales_count,
                  COALESCE(st.total_stock, 0) AS total_stock, COALESCE(st.bulk_ml_stock, 0) AS bulk_ml_stock,
                  COALESCE(d.discrepancy_units, 0) AS discrepancy_units
           FROM branches b
           LEFT JOIN (
               SELECT s.branch_id,
                      SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END) AS units_sold,
                      SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END) AS bulk_ml_sold,
                      SUM(s.qty_sold * s.unit_price) AS revenue, COUNT(*) AS sales_count
               FROM sales s JOIN products p ON p.sku = s.sku
               WHERE {_period_sql(period, "s.sold_at")} GROUP BY s.branch_id
           ) sa ON sa.branch_id = b.branch_id
           LEFT JOIN (
               SELECT bi.branch_id,
                      SUM(CASE WHEN p.unit <> 'BULK' THEN bi.stock_qty ELSE 0 END) AS total_stock,
                      SUM(CASE WHEN p.unit = 'BULK' THEN bi.stock_qty ELSE 0 END) AS bulk_ml_stock
               FROM branch_inventory bi JOIN products p ON p.sku = bi.sku GROUP BY bi.branch_id
           ) st ON st.branch_id = b.branch_id
           LEFT JOIN (
               SELECT sml.branch_id, SUM({DISCREPANCY_UNITS_SQL}) AS discrepancy_units
               FROM stock_movement_logs sml {DISCREPANCY_ITEM_JOIN}
               WHERE sml.reference_type = 'STOCK_REQUEST' AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
                 AND {_period_sql(period, "sml.created_at")}
               GROUP BY sml.branch_id
           ) d ON d.branch_id = b.branch_id
           WHERE b.is_hq = FALSE
           ORDER BY revenue DESC, b.branch_name"""
    )
    return {
        "period": period,
        "note": "Retail branches only (HQ excluded). Stock on hand is as of now. "
                "Turnover = bottles sold / bottles on hand now.",
        "results": [
            {"branch": r["branch_name"], "sales_count": r["sales_count"],
             "bottles_sold": r["units_sold"], "bulk_ml_sold": r["bulk_ml_sold"],
             "revenue": float(r["revenue"]), "bottles_on_hand": r["total_stock"],
             "bulk_ml_on_hand": r["bulk_ml_stock"], "discrepancy_units": r["discrepancy_units"],
             "turnover": round(float(r["units_sold"]) / float(r["total_stock"]), 2) if r["total_stock"] else None}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Tool: get_financials  (admin only)
# ---------------------------------------------------------------------------

def _get_financials(args, ctx):
    """Business-level figures, same rules as the Dashboard / Reports page:
    revenue = register sales (HQ + branches) + Closed partner package
    orders; cost of goods = SUM(cogs_logs.total_cogs); gross profit =
    revenue - cost of goods. Rent, payroll etc. aren't tracked."""
    period = (args.get("period") or "this_month").strip()
    if period not in _PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    sales_rev = query(
        "SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales WHERE " + _period_sql(period, "sold_at"),
        fetchone=True,
    )["v"]
    pkg = query(
        "SELECT COUNT(*) AS n, COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries "
        "WHERE status = 'Closed' AND " + _period_sql(period, "created_at"),
        fetchone=True,
    )
    cogs = query(
        "SELECT COALESCE(SUM(total_cogs), 0) AS v FROM cogs_logs WHERE " + _period_sql(period, "created_at"),
        fetchone=True,
    )["v"]
    materials = query(
        "SELECT COALESCE(SUM(package_cost), 0) AS v FROM raw_materials WHERE " + _period_sql(period, "created_at"),
        fetchone=True,
    )["v"]
    top_packages = query(
        "SELECT package_name_snapshot AS package, COUNT(*) AS orders, SUM(order_amount) AS revenue "
        "FROM partner_inquiries WHERE status = 'Closed' AND package_name_snapshot IS NOT NULL AND " +
        _period_sql(period, "created_at") +
        " GROUP BY package_name_snapshot ORDER BY revenue DESC LIMIT 5"
    )
    revenue = float(sales_rev) + float(pkg["v"])
    return {
        "period": period,
        "sales_revenue": float(sales_rev),
        "package_revenue": float(pkg["v"]),
        "package_orders_closed": pkg["n"],
        "total_revenue": round(revenue, 2),
        "cost_of_goods": float(cogs),
        "gross_profit": round(revenue - float(cogs), 2),
        "raw_materials_purchased": float(materials),
        "top_packages": [
            {"package": r["package"], "orders": r["orders"], "revenue": float(r["revenue"] or 0)}
            for r in top_packages
        ],
        "note": "Package orders count as revenue only once Closed, dated by when the inquiry came in. "
                "Gross profit doesn't subtract rent, payroll or other costs the system doesn't track.",
    }


# ---------------------------------------------------------------------------
# Tool: propose_stock_request  (writes a DRAFT only — see module docstring)
# ---------------------------------------------------------------------------

def _propose_stock_request(args, ctx):
    branch_name = (args.get("branch_name") or "").strip()
    items = args.get("items") or []
    note = (args.get("note") or "").strip() or None
    reasoning = (args.get("reasoning") or "").strip() or None

    if ctx["role"] == "Admin":
        if not branch_name:
            return {"error": "branch_name is required for an Admin to propose a delivery."}
        branch_ids, err = _resolve_branch_ids(ctx, branch_name)
        if err:
            return {"error": err}
        if not branch_ids:
            return {"error": f"No branch matching '{branch_name}'."}
        branch_id = branch_ids[0]
    else:
        branch_id = ctx["branch_id"]  # branch staff can only propose for their own branch

    if not isinstance(items, list) or not items:
        return {"error": "items must be a non-empty list of {sku, qty}."}

    cleaned = []
    for entry in items:
        sku = str(entry.get("sku", "")).strip()
        try:
            qty = int(entry.get("qty"))
        except (TypeError, ValueError):
            return {"error": f"Invalid quantity for sku '{sku}'."}
        if not sku or qty <= 0:
            return {"error": f"Invalid item entry: {entry!r}."}
        cleaned.append((sku, qty))

    skus = [c[0] for c in cleaned]
    placeholders = ",".join(["%s"] * len(skus))
    found = query(
        f"SELECT sku, item_name, unit FROM products WHERE sku IN ({placeholders})", tuple(skus)
    )
    found_skus = {r["sku"] for r in found}
    missing = [s for s in skus if s not in found_skus]
    if missing:
        return {"error": f"Unknown SKU(s): {', '.join(missing)}."}
    # Same rule as branch.request_stock(): only bottled sizes are ever
    # requested from HQ.
    bulk = [r["sku"] for r in found if r["unit"] not in BOTTLE_UNITS]
    if bulk:
        return {"error": f"Bulk/Refill products can't be requested from HQ: {', '.join(bulk)}."}

    try:
        with transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO ai_stock_drafts
                   (branch_id, created_by_user_id, created_by_username, status, note, reasoning)
                   VALUES (%s, %s, %s, 'Pending Review', %s, %s)""",
                (branch_id, ctx.get("user_id"), ctx.get("username"), note, reasoning),
            )
            draft_id = cur.lastrowid
            cur.executemany(
                "INSERT INTO ai_stock_draft_items (draft_id, sku, suggested_qty) VALUES (%s, %s, %s)",
                [(draft_id, sku, qty) for sku, qty in cleaned],
            )
            cur.close()
    except ValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        current_app.logger.exception("Failed to create AI stock draft")
        return {"error": "Could not save the draft — please try again."}

    if audit:
        # ctx["branch_name"] only covers a branch-staff caller proposing
        # for their own branch (see ctx() in ai.py). An Admin can name any
        # branch via branch_name/_resolve_branch_ids above, so that cached
        # session value can't be trusted here — look the name up by the
        # branch_id actually used, so the log reads e.g. "Cebu Branch"
        # instead of the raw branch_id for every caller, not just Branch
        # staff.
        branch_row = query(
            "SELECT branch_name FROM branches WHERE branch_id = %s", (branch_id,), fetchone=True
        )
        branch_label = branch_row["branch_name"] if branch_row else f"branch #{branch_id}"
        audit.log_action(
            "propose_ai_stock_request",
            target=f"draft #{draft_id}",
            details=f"{len(cleaned)} SKU(s) proposed for {branch_label}",
        )
    if sockets:
        try:
            sockets.notify_admin_and_branch(branch_id, ["ai_drafts"])
        except Exception:
            current_app.logger.exception("Failed to push ai_drafts realtime notice")

    try:
        review_url = url_for("ai.list_drafts", _external=False)
    except RuntimeError:
        review_url = "/ai/drafts"

    return {
        "draft_id": draft_id,
        "status": "Pending Review",
        "item_count": len(cleaned),
        "review_url": review_url,
        "note": "This is a DRAFT only — nothing has been sent to any branch. "
                "A human must approve it on the AI Drafts page before it becomes a real delivery.",
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_TOOL_IMPL = {
    "check_stock": _check_stock,
    "get_low_stock": _get_low_stock,
    "get_pending_deliveries": _get_pending_deliveries,
    "get_sales_summary": _get_sales_summary,
    "get_credit_purchases": _get_credit_purchases,
    "get_customers": _get_customers,
    "get_recent_sales": _get_recent_sales,
    "get_discrepancies": _get_discrepancies,
    "get_branch_performance": _get_branch_performance,
    "get_financials": _get_financials,
    "get_raw_materials": _get_raw_materials,
    "get_suppliers": _get_suppliers,
    "get_formulas": _get_formulas,
    "get_bulk_batches": _get_bulk_batches,
    "get_production_costs": _get_production_costs,
    "get_packages": _get_packages,
    "get_partner_inquiries": _get_partner_inquiries,
    "propose_stock_request": _propose_stock_request,
}

# HQ-side data a Branch session must never see. Enforced in dispatch(),
# not only by leaving these out of a Branch session's declarations.
_ADMIN_ONLY = {
    "get_raw_materials", "get_suppliers", "get_formulas", "get_bulk_batches",
    "get_production_costs", "get_packages", "get_partner_inquiries",
    "get_branch_performance", "get_financials",
}

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
        "description": "Business financials for a period, same rules as the Dashboard/Reports: sales revenue (HQ + branches), Closed package-order revenue, total revenue, cost of goods produced, gross profit, raw materials purchased, top packages.",
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
