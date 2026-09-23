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
    requested_qty, unit_price).
  - products.sku is VARCHAR(50) (per utils.py's build_sku() comment).
  - branches(branch_id, branch_name, is_hq).
  - Draft approval (routes/ai.py's approve_draft) generates the real
    delivery_number as "DR-<request_id zero-padded to 6 digits>" — the
    same convention branch.request_stock() uses — so AI-originated
    deliveries look identical to normal ones in the Stock Requests list.
"""
import datetime

from flask import current_app, url_for

from db import query, transaction
from utils import ValidationError

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

def _resolve_branch_ids(ctx, branch_name):
    """Turn an (optional, model-supplied) branch_name into a list of
    branch_ids the caller is actually allowed to see.

    Branch-role users ALWAYS get their own branch_id, regardless of what
    the model passed — this is the actual access boundary, not the
    model's argument. Admins may name a branch (fuzzy match) or leave it
    blank to mean "every branch".
    """
    if ctx["role"] != "Admin":
        return [ctx["branch_id"]], None

    if not branch_name:
        return None, None  # None = no filter = every branch, admin only

    rows = query(
        "SELECT branch_id, branch_name FROM branches WHERE branch_name LIKE %s AND is_hq = FALSE",
        (f"%{branch_name}%",),
    )
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

    branch_ids, err = _resolve_branch_ids(ctx, branch_name)
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

    sql = (
        "SELECT b.branch_name, p.sku, p.item_name, p.unit, p.variant, "
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
                "sku": r["sku"],
                "item_name": r["item_name"],
                "unit": r["unit"],
                "variant": r["variant"],
                "stock_qty": r["stock_qty"],
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
    branch_ids, err = _resolve_branch_ids(ctx, branch_name)
    if err:
        return {"error": err}

    sql = (
        "SELECT b.branch_name, p.sku, p.item_name, p.unit, bi.stock_qty, bi.reorder_level "
        "FROM branch_inventory bi "
        "JOIN branches b ON bi.branch_id = b.branch_id "
        "JOIN products p ON bi.sku = p.sku "
        "WHERE b.is_hq = FALSE AND bi.stock_qty <= bi.reorder_level"
    )
    params = []
    if branch_ids is not None:
        if not branch_ids:
            return {"results": [], "note": "No matching branch."}
        placeholders = ",".join(["%s"] * len(branch_ids))
        sql += f" AND bi.branch_id IN ({placeholders})"
        params.extend(branch_ids)
    sql += " ORDER BY bi.stock_qty ASC LIMIT %s"
    params.append(MAX_ROWS)

    rows = query(sql, tuple(params))
    return {
        "results": [
            {
                "branch": r["branch_name"],
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
    valid_statuses = {"Pending", "In Transit", "Fulfilled", "Rejected"}
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
        conditions.append("sr.status IN ('Pending','In Transit')")
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


def _get_sales_summary(args, ctx):
    period = (args.get("period") or "today").strip()
    if period not in _PERIOD_SQL:
        return {"error": f"period must be one of {sorted(_PERIOD_SQL)}."}

    branch_name = (args.get("branch_name") or "").strip()
    by_branch = bool(args.get("by_branch"))
    branch_ids, err = _resolve_branch_ids(ctx, branch_name)
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
        "SELECT COALESCE(SUM(s.qty_sold),0) AS units, COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue "
        "FROM sales s WHERE " + " AND ".join(conditions),
        tuple(params), fetchone=True,
    )
    top_items = query(
        "SELECT p.item_name, SUM(s.qty_sold) AS units, SUM(s.qty_sold*s.unit_price) AS revenue "
        "FROM sales s JOIN products p ON s.sku = p.sku WHERE " + " AND ".join(conditions) +
        " GROUP BY p.item_name ORDER BY units DESC LIMIT 5",
        tuple(params),
    )
    # Sale vs Refill vs Freebie, Cash vs Credit, so "how much went on
    # credit" or "how many refills" doesn't need a tool of its own.
    by_type = query(
        "SELECT s.sale_type, s.payment_method, COUNT(*) AS transactions, "
        "COALESCE(SUM(s.qty_sold),0) AS units, COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue "
        "FROM sales s WHERE " + " AND ".join(conditions) +
        " GROUP BY s.sale_type, s.payment_method ORDER BY revenue DESC",
        tuple(params),
    )
    result = {
        "period": period,
        "units": totals["units"],
        "revenue": float(totals["revenue"]),
        "top_items": [
            {"item_name": r["item_name"], "units": r["units"], "revenue": float(r["revenue"])}
            for r in top_items
        ],
        "by_type": [
            {"sale_type": r["sale_type"], "payment_method": r["payment_method"],
             "transactions": r["transactions"], "units": r["units"], "revenue": float(r["revenue"])}
            for r in by_type
        ],
    }

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
            "SELECT b.branch_name, COALESCE(SUM(s.qty_sold),0) AS units, "
            "COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue "
            "FROM branches b LEFT JOIN sales s ON s.branch_id = b.branch_id AND " +
            _PERIOD_SQL[period] +
            " GROUP BY b.branch_id, b.branch_name ORDER BY revenue DESC"
        )
        result["by_branch"] = [
            {"branch_name": r["branch_name"], "units": r["units"], "revenue": float(r["revenue"])}
            for r in by_branch_rows
        ]

    return result


def _branch_filter(ctx, branch_name, column):
    """(sql_fragment, params, error) restricting `column` to the branches
    the caller may see. An empty fragment means every branch (Admin with
    no branch named)."""
    branch_ids, err = _resolve_branch_ids(ctx, branch_name)
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
        "COUNT(*) AS transactions, COALESCE(SUM(s.qty_sold),0) AS units, "
        "COALESCE(SUM(s.qty_sold*s.unit_price),0) AS amount, MAX(s.sold_at) AS last_taken_at "
        "FROM sales s JOIN branches b ON s.branch_id = b.branch_id "
        "LEFT JOIN users bu ON s.buyer_user_id = bu.user_id "
        "WHERE s.payment_method = 'Credit'" + frag +
        " GROUP BY b.branch_name, COALESCE(s.buyer_name, bu.username) "
        "ORDER BY amount DESC LIMIT %s",
        tuple(params + [MAX_ROWS]),
    )
    return {
        "results": [
            {"branch": r["branch_name"], "buyer": r["buyer_name"] or "(unnamed)",
             "transactions": r["transactions"], "units": r["units"],
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
    way the Customers pages do (there's no separate customers table)."""
    name = (args.get("name") or "").strip()
    frag, params, err = _branch_filter(ctx, (args.get("branch_name") or "").strip(), "s.branch_id")
    if err:
        return {"error": err}
    sql = (
        "SELECT s.customer_name, COUNT(*) AS transactions, COALESCE(SUM(s.qty_sold),0) AS units, "
        "COALESCE(SUM(s.qty_sold*s.unit_price),0) AS spent, MAX(s.sold_at) AS last_visit, "
        "GROUP_CONCAT(DISTINCT b.branch_name ORDER BY b.branch_name SEPARATOR ', ') AS branches "
        "FROM sales s JOIN branches b ON s.branch_id = b.branch_id "
        "WHERE s.customer_name IS NOT NULL AND s.customer_name <> ''" + frag
    )
    if name:
        sql += " AND s.customer_name LIKE %s"
        params.append(f"%{name}%")
    sql += " GROUP BY s.customer_name ORDER BY spent DESC LIMIT %s"
    params.append(MAX_ROWS)
    rows = query(sql, tuple(params))
    return {
        "results": [
            {"customer": r["customer_name"], "transactions": r["transactions"], "units": r["units"],
             "spent": float(r["spent"]), "last_visit": r["last_visit"].isoformat(),
             "branches": r["branches"]}
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Admin-only tools: raw materials, suppliers, formulas, bulk batches,
# production cost (COGS), packages, partner inquiries. See _ADMIN_ONLY.
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
    """Per-bottle-size material recipe (the Formulas page): what one unit
    of a size uses and what that costs in materials."""
    unit = (args.get("unit") or "").strip().upper()
    sql = (
        "SELECT ufi.unit, rm.material_name, rm.unit AS material_unit, ufi.qty_per_unit, ufi.line_cost "
        "FROM unit_formula_items ufi JOIN raw_materials rm ON ufi.material_id = rm.material_id"
    )
    params = []
    if unit:
        sql += " WHERE ufi.unit = %s"
        params.append(unit)
    sql += " ORDER BY ufi.unit, rm.material_name LIMIT %s"
    params.append(MAX_ROWS * 2)
    rows = query(sql, tuple(params))
    by_unit = {}
    for r in rows:
        entry = by_unit.setdefault(r["unit"], {"unit": r["unit"], "materials": [], "material_cost_per_unit": 0.0})
        line = float(r["line_cost"] or 0)
        entry["materials"].append({"material": r["material_name"], "qty_per_unit": float(r["qty_per_unit"]),
                                   "material_unit": r["material_unit"], "line_cost": line})
        entry["material_cost_per_unit"] = round(entry["material_cost_per_unit"] + line, 2)
    return {"results": list(by_unit.values())}


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
    return {
        "results": [
            {"batch_code": r["batch_code"], "scent": r["scent_name"],
             "total_volume_ml": float(r["total_volume_ml"] or 0), "remaining_ml": float(r["remaining_ml"] or 0),
             "cost_per_ml": float(r["cost_per_ml"] or 0), "total_cost": float(r["total_cost"] or 0),
             "created_at": r["created_at"].isoformat()}
            for r in rows
        ]
    }


def _get_production_costs(args, ctx):
    period = (args.get("period") or "this_month").strip()
    if period not in _PERIOD_TMPL:
        return {"error": f"period must be one of {PERIOD_NAMES}."}
    where = _period_sql(period, "c.created_at")
    totals = query(
        "SELECT COALESCE(SUM(c.qty_produced),0) AS units, COALESCE(SUM(c.total_cogs),0) AS cogs "
        "FROM cogs_logs c WHERE " + where, fetchone=True,
    )
    rows = query(
        "SELECT p.item_name, p.unit, SUM(c.qty_produced) AS units, SUM(c.total_cogs) AS cogs "
        "FROM cogs_logs c JOIN products p ON c.sku = p.sku WHERE " + where +
        " GROUP BY p.sku, p.item_name, p.unit ORDER BY cogs DESC LIMIT %s",
        (MAX_ROWS,),
    )
    return {
        "period": period,
        "units_produced": totals["units"],
        "total_cogs": float(totals["cogs"]),
        "by_product": [
            {"item_name": r["item_name"], "unit": r["unit"], "units": r["units"], "cogs": float(r["cogs"]),
             "cogs_per_unit": round(float(r["cogs"]) / r["units"], 2) if r["units"] else None}
            for r in rows
        ],
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
        "status, created_at FROM partner_inquiries"
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
             "status": r["status"], "received_at": r["created_at"].isoformat()}
            for r in rows
        ],
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
        f"SELECT sku, item_name FROM products WHERE sku IN ({placeholders})", tuple(skus)
    )
    found_skus = {r["sku"] for r in found}
    missing = [s for s in skus if s not in found_skus]
    if missing:
        return {"error": f"Unknown SKU(s): {', '.join(missing)}."}

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
                "A human must approve it on the Drafts page before it becomes a real delivery.",
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
}

_READ_ONLY_DECLARATIONS = [
    {
        "name": "check_stock",
        "description": "Look up current stock for a product by SKU or name. Admins may filter by branch_name; if omitted, returns every branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "Exact SKU, e.g. A1-85ML."},
                "item_name": {"type": "string", "description": "Partial product/fragrance name to search for."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok)."},
            },
        },
    },
    {
        "name": "get_low_stock",
        "description": "List SKUs at or below their reorder level. Admins may filter by branch_name; if omitted, returns every branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok)."},
            },
        },
    },
    {
        "name": "get_pending_deliveries",
        "description": "List stock requests (deliveries) and their line items. Defaults to Pending/In Transit only.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "One of Pending, In Transit, Fulfilled, Rejected."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok)."},
            },
        },
    },
    {
        "name": "get_sales_summary",
        "description": (
            "Totals and top-selling items for a time period. To compare branches or answer "
            "how revenue/units split across branches, set by_branch=true and leave branch_name "
            "blank — this returns every branch's totals in one call (branches with zero sales "
            "in the period included as 0). Don't call this once per branch name. Also returns "
            "by_type: the split by sale_type (Sale, Refill, Freebie) and payment_method (Cash, Credit)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "One of " + PERIOD_NAMES + ". Defaults to today."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok). Leave blank when using by_branch."},
                "by_branch": {"type": "boolean", "description": "Admin only. If true, also returns a per-branch breakdown of units/revenue for the period. Ignored if branch_name is set."},
            },
        },
    },
]

_READ_ONLY_DECLARATIONS += [
    {
        "name": "get_credit_purchases",
        "description": "Store-credit (utang) sales grouped by buyer: transactions, units, amount, last date. All time.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok)."},
            },
        },
    },
    {
        "name": "get_customers",
        "description": "Named walk-in customers ranked by total spent, with visit count, last visit and branches shopped. Optionally search by name.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Partial customer name to search for."},
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
        "description": "Per-bottle-size material formula: the materials and quantities one unit of that size uses, and its material cost per unit.",
        "parameters": {"type": "object", "properties": {
            "unit": {"type": "string", "description": "Bottle size, e.g. 85ML. Omit for every size."},
        }},
    },
    {
        "name": "get_bulk_batches",
        "description": "Bulk scent batches (the stock refills are poured from): remaining mL, total volume, cost per mL.",
        "parameters": {"type": "object", "properties": {
            "active_only": {"type": "boolean", "description": "Defaults to true: only batches with mL remaining."},
        }},
    },
    {
        "name": "get_production_costs",
        "description": "Cost of goods produced (COGS) for a period: total units and cost, plus per product with cost per unit.",
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
        "description": "Inquiries sent from the partner portal: counts by status and the most recent ones.",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "description": "One of New, Contacted, Follow-up, On Hold, Closed, Declined."},
        }},
    },
]

_PROPOSE_DECLARATION = {
    "name": "propose_stock_request",
    "description": (
        "Draft a stock request (delivery) for a branch. This does NOT create a real, "
        "actionable delivery — it saves a draft that a human must review and approve "
        "on the Drafts page before HQ can dispatch it. Use this when the user asks you "
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
