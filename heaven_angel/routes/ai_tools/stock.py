from flask import current_app, url_for

from heaven_angel.db import query, transaction
from heaven_angel.services.reports import DISCREPANCY_ITEM_JOIN, DISCREPANCY_UNITS_SQL, LOW_STOCK_WHERE
from heaven_angel.utils import BOTTLE_UNITS, ValidationError

try:
    from heaven_angel.services import audit
except ImportError:  # pragma: no cover - audit logging is best-effort
    audit = None

try:
    from heaven_angel import sockets
except ImportError:  # pragma: no cover - realtime push is best-effort
    sockets = None

from heaven_angel.routes.ai_tools._common import MAX_ROWS, _branch_filter, _resolve_branch_ids
from heaven_angel.routes.ai_tools.sales import PERIOD_NAMES, PERIOD_TMPL, period_sql


def check_stock(args, ctx):
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


def get_low_stock(args, ctx):
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


def get_pending_deliveries(args, ctx):
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


def get_discrepancies(args, ctx):
    """Delivery discrepancies — DAMAGE (damaged on arrival) and ADJUSTMENT
    (dispatched but never received) — with units counted the same way as
    the Discrepancies pages and report (reports.DISCREPANCY_UNITS_SQL;
    these log rows' own change_qty is often 0)."""
    period = (args.get("period") or "all_time").strip()
    if period not in PERIOD_TMPL:
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
        "AND " + period_sql(period, "sml.created_at") + frag
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


def propose_stock_request(args, ctx):
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
