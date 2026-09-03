"""Tools the AI assistant can call (Gemini function calling), instead of
dumping a full data snapshot into the prompt on every turn (the old
approach in ai.py). Each tool is:

  - Scoped server-side by role/branch (ctx["role"], ctx["branch_id"]) —
    the model never gets to choose whose data it reads. Branch staff
    cannot pass a branch_name that isn't their own; it's silently
    overridden, never trusted from the model's arguments.
  - Read-only, EXCEPT propose_stock_request, which does not write to
    stock_requests / stock_request_items (the real, active tables) —
    it writes a draft to ai_stock_drafts / ai_stock_draft_items with
    status 'Pending Review'. A human still has to approve it (see the
    new /ai/drafts routes in ai.py) before it becomes a real delivery
    that HQ can dispatch. This is the "human-in-the-loop" boundary:
    the agent can *draft*, never *commit*.

ASSUMPTIONS — please verify against your actual schema/branch.py before
deploying, since neither was available while writing this:
  - stock_requests(request_id, branch_id, delivery_number, status,
    requested_at) and stock_request_items(item_id, request_id, sku,
    requested_qty, unit_price) — inferred from routes/ai.py's existing
    queries.
  - products.sku is VARCHAR(50) (per utils.py's build_sku() comment).
  - branches(branch_id, branch_name, is_hq).
  - The real delivery_number format used by branch.request_stock() is
    NOT known here — draft approval below generates a placeholder
    ("AI-<draft_id>") that you should swap for whatever convention
    branch.py already uses, so AI-originated deliveries look identical
    to normal ones in the Stock Requests list.
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

_PERIOD_SQL = {
    "today": "DATE(s.sold_at) = CURDATE()",
    "this_week": "YEARWEEK(s.sold_at, 3) = YEARWEEK(CURDATE(), 3)",
    "this_month": "YEAR(s.sold_at) = YEAR(CURDATE()) AND MONTH(s.sold_at) = MONTH(CURDATE())",
}


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
    result = {
        "period": period,
        "units": totals["units"],
        "revenue": float(totals["revenue"]),
        "top_items": [
            {"item_name": r["item_name"], "units": r["units"], "revenue": float(r["revenue"])}
            for r in top_items
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
    "propose_stock_request": _propose_stock_request,
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
            "in the period included as 0). Don't call this once per branch name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "One of today, this_week, this_month. Defaults to today."},
                "branch_name": {"type": "string", "description": "Admin only — restrict to one branch (partial match ok). Leave blank when using by_branch."},
                "by_branch": {"type": "boolean", "description": "Admin only. If true, also returns a per-branch breakdown of units/revenue for the period. Ignored if branch_name is set."},
            },
        },
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
    """Gemini `tools` payload for this session's role. propose_stock_request
    is available to both roles — Admin proposes for any branch (must name
    one), Branch staff can only ever propose for their own."""
    decls = list(_READ_ONLY_DECLARATIONS) + [_PROPOSE_DECLARATION]
    return [{"functionDeclarations": decls}]


def dispatch(name, args, ctx):
    """Run a tool by name. Never raises — always returns a JSON-safe dict,
    since this result goes straight back to the model as a functionResponse."""
    fn = _TOOL_IMPL.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        return fn(args or {}, ctx)
    except Exception:
        current_app.logger.exception("AI tool '%s' failed", name)
        return {"error": "That lookup failed unexpectedly."}
