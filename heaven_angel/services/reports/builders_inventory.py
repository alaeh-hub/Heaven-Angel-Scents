"""Report builders for stock, production, materials and discrepancies."""
from heaven_angel.services.reports.constants import DISCREPANCY_ITEM_JOIN, DISCREPANCY_UNITS_SQL, LOW_STOCK_WHERE, MAX_ROWS
from heaven_angel.services.reports.filters import describe_window, time_window


from heaven_angel.db import query
from heaven_angel.utils import (
    BOTTLE_UNITS, bottle_size_ml,
)


# ---------------------------------------------------------------- per-type builders
def report_products(filters, branch_scope):
    # NOTE: products.is_active was dropped from the schema (products are
    # now edited in place from the admin Products page instead of being
    # discontinued/reactivated — see schema.sql's migration block), so
    # there is no active/discontinued status left to filter or show here.
    where, params = "", []
    if filters["variant"] != "all":
        where += " AND p.variant = %s"
        params.append(filters["variant"])
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]

    rows = query(
        f"""SELECT p.sku, p.item_name, p.variant, p.unit, p.price,
                   COALESCE(SUM(bi.stock_qty), 0) AS total_stock
            FROM products p LEFT JOIN branch_inventory bi ON p.sku = bi.sku
            WHERE 1=1 {where}
            GROUP BY p.sku ORDER BY p.item_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["price"] = float(r["price"])

    columns = [
        ("sku", "SKU", "str"), ("item_name", "Item",
                                "str"), ("variant", "Variant", "badge:variant"),
        ("unit", "Unit", "str"), ("price", "HQ Price", "money"),
        ("total_stock", "Total Stock (all branches)", "int"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def report_production_log(filters, branch_scope):
    where, params = "", []
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR pl.batch_code LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = time_window(
        "pl.produced_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT pl.produced_at, p.sku, p.item_name, p.unit, pl.batch_code, pl.qty_produced,
                   c.cogs_per_unit, c.total_cogs
            FROM production_logs pl JOIN products p ON pl.sku = p.sku
            LEFT JOIN (
                SELECT production_log_id, MAX(cogs_per_unit) AS cogs_per_unit, SUM(total_cogs) AS total_cogs
                FROM cogs_logs WHERE production_log_id IS NOT NULL GROUP BY production_log_id
            ) c ON c.production_log_id = pl.log_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["batch_code"] = r["batch_code"] or "—"
        # Same cost figures the Production Log page shows per run (from
        # cogs_logs); a bulk run's cost is per mL.
        r["cogs_per_unit"] = float(r["cogs_per_unit"]) if r["cogs_per_unit"] is not None else None
        r["total_cogs"] = float(r["total_cogs"]) if r["total_cogs"] is not None else None

    columns = [
        ("produced_at", "Produced", "datetime"), ("sku",
                                                  "SKU", "str"), ("item_name", "Item", "str"),
        ("unit", "Unit", "str"), ("batch_code", "Batch",
                                  "str"), ("qty_produced", "Qty Produced", "int"),
        ("cogs_per_unit", "Cost / Unit (or / mL)", "money"), ("total_cogs", "Cost of Goods", "money"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_materials(filters, branch_scope):
    """Dated initial/baseline purchases and individual restocks. Older
    restocks made before event tracking remain combined into the baseline
    row because their individual dates and amounts were never stored.
    branch_scope is unused (materials aren't branch-scoped) but every
    builder is called with it.
    """
    where, params = "", []
    if filters["search"]:
        where += " AND (material_name LIKE %s OR receipt_number LIKE %s OR supplier_name LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = time_window(
        "purchased_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT * FROM (
                SELECT rm.created_at AS purchased_at, rm.material_name, s.supplier_name, rm.unit,
                       rm.purchase_mode, GREATEST(rm.package_qty - COALESCE(re.total_qty, 0), 0) AS package_qty,
                       GREATEST(rm.package_cost - COALESCE(re.total_cost, 0), 0) AS package_cost,
                       CASE
                           WHEN rm.package_qty > COALESCE(re.total_qty, 0)
                           THEN GREATEST(rm.package_cost - COALESCE(re.total_cost, 0), 0)
                                / (rm.package_qty - COALESCE(re.total_qty, 0))
                           ELSE rm.cost_per_unit
                       END AS cost_per_unit,
                       rm.receipt_number, rm.stock_qty AS stock_on_hand
                FROM raw_materials rm
                LEFT JOIN suppliers s ON rm.supplier_id = s.supplier_id
                LEFT JOIN (
                    SELECT material_id, SUM(qty) AS total_qty, SUM(cost) AS total_cost
                    FROM material_restock_events GROUP BY material_id
                ) re ON re.material_id = rm.material_id
                UNION ALL
                SELECT mre.purchased_at, mre.material_name_snapshot,
                        COALESCE(mre.supplier_name_snapshot, s.supplier_name), mre.unit_snapshot,
                        'Restock', mre.qty, mre.cost,
                        CASE WHEN mre.qty > 0 THEN mre.cost / mre.qty ELSE 0 END,
                        NULL, rm.stock_qty
                FROM material_restock_events mre
                LEFT JOIN suppliers s ON mre.supplier_id = s.supplier_id
                LEFT JOIN raw_materials rm ON rm.material_id = mre.material_id
            ) purchases
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["supplier_name"] = r["supplier_name"] or "— none on file —"
        r["package_qty"] = float(r["package_qty"])
        r["package_cost"] = float(r["package_cost"])
        r["cost_per_unit"] = float(r["cost_per_unit"])
        r["stock_on_hand"] = float(r["stock_on_hand"] or 0)
        r["receipt_number"] = r["receipt_number"] or "—"

    columns = [
        ("purchased_at", "Purchased", "datetime"),
        ("material_name", "Material", "str"),
        ("supplier_name", "Supplier", "str"),
        ("unit", "Unit", "str"),
        ("purchase_mode", "Purchase Mode", "str"),
        ("package_qty", "Quantity", "num"),
        ("package_cost", "Cost", "money"),
        ("cost_per_unit", "Cost / Unit", "money"),
        ("stock_on_hand", "Stock on Hand", "num"),
        ("receipt_number", "Receipt #", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_suppliers(filters, branch_scope):
    """One row per supplier, rolled up the same way the Materials page's
    merged Suppliers directory is (see routes/admin/materials.py's materials()) —
    a catalog-style snapshot, not windowed, same reasoning as Products/
    Accounts/Partners above.
    """
    where, params = "", []
    if filters["search"]:
        where += " AND (s.supplier_name LIKE %s OR s.contact_person LIKE %s OR s.phone LIKE %s OR s.email LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like, like]

    rows = query(
        f"""SELECT s.supplier_name, s.contact_person, s.phone, s.email, s.created_at,
                   COUNT(DISTINCT rm.material_id) AS material_count,
                   COALESCE(SUM(rm.package_cost), 0) AS total_spent,
                   MAX(CASE WHEN re.last_restock_at > rm.created_at
                            THEN re.last_restock_at ELSE rm.created_at END) AS last_purchase_at
            FROM suppliers s
            LEFT JOIN raw_materials rm ON rm.supplier_id = s.supplier_id
            LEFT JOIN (
                SELECT material_id, MAX(purchased_at) AS last_restock_at
                FROM material_restock_events GROUP BY material_id
            ) re ON re.material_id = rm.material_id
            WHERE 1=1 {where}
            GROUP BY s.supplier_id, s.supplier_name, s.contact_person, s.phone, s.email, s.created_at
            ORDER BY total_spent DESC, s.supplier_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["total_spent"] = float(r["total_spent"])
        r["contact"] = " · ".join(
            p for p in (r.pop("contact_person"), r.pop("phone"), r.pop("email")) if p
        ) or "—"

    columns = [
        ("supplier_name", "Supplier", "str"),
        ("contact", "Contact", "str"),
        ("material_count", "Materials Supplied", "int"),
        ("total_spent", "Total Spent", "money"),
        ("last_purchase_at", "Latest New Material", "datetime"),
        ("created_at", "On File Since", "datetime"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def report_formulas(filters, branch_scope):
    """The cost of goods on file right now: the flat base cost per
    Bottled packaging size (unit_cogs_settings) plus the single per-mL
    rate for Bulk/Refill (bulk_rate_settings) — exactly what the
    Formulas page edits and what production() logs as cogs_per_unit.

    This used to list unit_formula_items (a per-size materials recipe),
    but production() no longer reads that table — it was superseded by
    unit_cogs_settings (see schema.sql's migration 38) — so the report
    was showing a recipe that no longer drives any cost. A snapshot, not
    windowed, same as before. The unit filter still applies: a bottle
    size narrows to that size's row, BULK narrows to the bulk rate.
    """
    rows = []
    if filters["unit"] != "BULK":
        where, params = "", []
        if filters["unit"] != "all":
            where = " WHERE unit = %s"
            params.append(filters["unit"])
        for r in query(
            f"""SELECT unit, base_cost_per_unit, updated_at
                FROM unit_cogs_settings{where}
                ORDER BY FIELD(unit, '85ML', '50ML', '10ML', '3ML')""",
            tuple(params),
        ):
            rows.append({
                "unit": r["unit"], "basis": "Per bottle",
                "cost_per_unit": float(r["base_cost_per_unit"]),
                "updated_at": r["updated_at"],
            })
    if filters["unit"] in ("all", "BULK"):
        bulk = query(
            "SELECT rate_per_ml, updated_at FROM bulk_rate_settings WHERE id = 1", fetchone=True)
        if bulk:
            rows.append({
                "unit": "BULK", "basis": "Per mL (Bulk/Refill)",
                "cost_per_unit": float(bulk["rate_per_ml"]),
                "updated_at": bulk["updated_at"],
            })

    columns = [
        ("unit", "Packaging Size", "str"),
        ("basis", "Charged", "str"),
        ("cost_per_unit", "Cost of Goods", "money"),
        ("updated_at", "Last Changed", "datetime"),
    ]
    return columns, rows, False, "Snapshot as of now — the cost of goods each production run is charged"


def report_cogs_logs(filters, branch_scope):
    """One row per cost-of-goods batch (cogs_logs) — every time a
    production run was costed off a formula (see routes/admin/production.py's
    production()). This is the line-item detail behind the dashboard's
    "Total Capital" tile and the Reports page's Revenue vs. Capital
    chart, both of which are just SUM(total_cogs) over this same table
    — so "All time" on this report reconciles exactly to that figure.
    """
    where, params = "", []
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["search"]:
        where += (" AND (p.item_name LIKE %s OR p.sku LIKE %s OR pl.batch_code LIKE %s"
                  " OR bb.batch_code LIKE %s OR bb.scent_name LIKE %s)")
        like = f"%{filters['search']}%"
        params += [like, like, like, like, like]
    time_where, order, limit_n, truncated = time_window(
        "c.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT c.created_at, p.sku, p.item_name, p.unit, pl.batch_code,
                   COALESCE(bb.batch_code, bb.scent_name) AS bulk_batch,
                   c.qty_produced, c.cogs_per_unit, c.total_cogs, u.username
            FROM cogs_logs c
            JOIN products p ON c.sku = p.sku
            LEFT JOIN production_logs pl ON c.production_log_id = pl.log_id
            LEFT JOIN bulk_batches bb ON c.bulk_batch_id = bb.batch_id
            LEFT JOIN users u ON c.created_by_user_id = u.user_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["batch_code"] = r["batch_code"] or "—"
        r["bulk_batch"] = r["bulk_batch"] or "—"
        r["qty_produced"] = float(r["qty_produced"])
        r["cogs_per_unit"] = float(r["cogs_per_unit"])
        r["total_cogs"] = float(r["total_cogs"])
        r["username"] = r["username"] or "—"

    columns = [
        ("created_at", "Logged", "datetime"),
        ("sku", "SKU", "str"), ("item_name", "Item", "str"),
        ("unit", "Unit", "str"), ("batch_code", "Batch", "str"),
        # Which bulk batch a Bottled run was filled from (cogs_logs.
        # bulk_batch_id) — "—" for Bulk/Refill runs and older rows.
        ("bulk_batch", "Filled From", "str"),
        ("qty_produced", "Qty Produced", "num"),
        ("cogs_per_unit", "Cost / Unit", "money"),
        ("total_cogs", "Total Cost of Goods", "money"),
        ("username", "Logged By", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_branch_stock(filters, branch_scope):
    where, params = "", []
    if branch_scope is not None:
        where += " AND b.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND b.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["variant"] != "all":
        where += " AND p.variant = %s"
        params.append(filters["variant"])
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]
    if filters["low_stock_only"]:
        where += f" AND {LOW_STOCK_WHERE}"
    if branch_scope is not None:
        # Same as My Inventory: a branch only ever stocks bottled sizes.
        where += " AND p.unit <> 'BULK'"

    # No more per-branch price override — every branch sells at
    # products.price, so this is just stock levels per branch now.
    # (products.is_active no longer exists — see schema.sql's migration
    # block — so there's nothing to filter out here anymore.)
    rows = query(
        f"""SELECT b.branch_name, p.sku, p.item_name, p.variant, p.unit, p.price AS hq_price,
                   bi.stock_qty, bi.reorder_level
            FROM branch_inventory bi
            JOIN branches b ON bi.branch_id = b.branch_id
            JOIN products p ON bi.sku = p.sku
            WHERE 1=1 {where}
            ORDER BY b.is_hq DESC, b.branch_name, p.item_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["hq_price"] = float(r["hq_price"])

    columns = [("branch_name", "Branch", "str")
               ] if branch_scope is None else []
    columns += [
        ("sku", "SKU", "str"), ("item_name", "Item",
                                "str"), ("variant", "Variant", "badge:variant"),
        ("unit", "Unit", "str"), ("hq_price", "HQ Price", "money"),
        ("stock_qty", "Stock Qty", "int"), ("reorder_level", "Reorder Level", "int"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def report_stock_requests(filters, branch_scope):
    """One row per product on a delivery.

    A stock request is now a delivery *header* (stock_requests) that can
    carry several products, each its own line in stock_request_items —
    sku/requested_qty/dispatched_qty/received_qty/damaged_qty all live on
    the item row now, not on the request itself (see schema.sql's
    migration block and receipts.py). So this joins through
    stock_request_items rather than reading those columns off sr
    directly, and surfaces delivery_number (the human-facing identifier
    used everywhere else in the app) alongside them.
    """
    where, params = "", []
    if branch_scope is not None:
        where += " AND sr.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND sr.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["status"] != "all":
        where += " AND sr.status = %s"
        params.append(filters["status"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR sr.delivery_number LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = time_window(
        "sr.requested_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT sr.requested_at, sr.delivery_number, b.branch_name, p.item_name, p.sku,
                   sri.requested_qty, sri.dispatched_qty, sri.received_qty, sri.damaged_qty,
                   GREATEST(sri.requested_qty - COALESCE(sri.received_qty, 0), 0) AS remaining_qty, sr.status,
                   sri.unit_price, (sri.requested_qty * sri.unit_price) AS line_value
            FROM stock_request_items sri
            JOIN stock_requests sr ON sri.request_id = sr.request_id
            JOIN branches b ON sr.branch_id = b.branch_id
            JOIN products p ON sri.sku = p.sku
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        for k in ("dispatched_qty", "received_qty", "damaged_qty"):
            r[k] = r[k] or 0
        r["unit_price"] = float(r["unit_price"])
        r["line_value"] = float(r["line_value"])

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [
        ("requested_at", "Requested", "datetime"),
        ("delivery_number", "Delivery #", "str"),
    ] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU",
                                       "str"), ("requested_qty", "Requested Qty", "int"),
        ("dispatched_qty", "Dispatched Qty",
         "int"), ("received_qty", "Received Qty", "int"),
        ("damaged_qty", "Damaged Qty", "int"), ("remaining_qty", "Remaining Qty", "int"),
        ("unit_price", "Unit Price", "money"),
        ("line_value", "Value", "money"), ("status", "Status", "badge:status"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_inventory_log(filters, branch_scope):
    where, params = "", []
    if branch_scope is not None:
        where += " AND sml.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND sml.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["movement_type"] != "all":
        where += " AND sml.movement_type = %s"
        params.append(filters["movement_type"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR sml.notes LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = time_window(
        "sml.created_at", filters, params)
    where += time_where

    rows = query(
        # Delivery DAMAGE/ADJUSTMENT rows carry negative change_qty values
        # for the units that never entered branch stock. DISCREPANCY_UNITS_SQL
        # also supports legacy zero-valued rows from before that change.
        f"""SELECT sml.created_at, b.branch_name, p.item_name, p.sku, p.unit,
                   sml.movement_type,
                   CASE WHEN sml.reference_type = 'STOCK_REQUEST'
                             AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
                        THEN -{DISCREPANCY_UNITS_SQL} ELSE sml.change_qty END AS change_qty,
                   sml.notes
            FROM stock_movement_logs sml
            JOIN branches b ON sml.branch_id = b.branch_id
            JOIN products p ON sml.sku = p.sku
            {DISCREPANCY_ITEM_JOIN}
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["notes"] = r["notes"] or "—"
        r["change_qty"] = int(r["change_qty"])

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [("created_at", "When", "datetime")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU",
                                       "str"), ("movement_type", "Type", "badge:movement_type"),
        ("change_qty", "Change", "int"), ("notes", "Notes", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_bulk_batches(filters, branch_scope):
    """One row per bulk batch (bulk_batches) — what was mixed, how much,
    what it really cost, and how much is left to bottle from. Materials
    are folded into one "Materials Used" cell per batch (from
    bulk_batch_materials) rather than one row per ingredient, so the
    batch totals stay one-row-per-batch and total up correctly.
    """
    where, params = "", []
    if filters["search"]:
        where += " AND (bb.scent_name LIKE %s OR bb.batch_code LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]
    time_where, order, limit_n, truncated = time_window(
        "bb.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT bb.created_at, bb.batch_code, bb.scent_name, bb.input_qty, bb.input_unit, bb.notes,
                   bb.total_volume_ml, bb.remaining_ml, bb.total_cost, bb.cost_per_ml,
                   (SELECT GROUP_CONCAT(
                        CONCAT(rm.material_name, ' ', TRIM(TRAILING '.' FROM TRIM(TRAILING '0' FROM bbm.qty_used)),
                               ' ', COALESCE(bbm.qty_used_unit, rm.unit))
                        ORDER BY rm.material_name SEPARATOR ', ')
                    FROM bulk_batch_materials bbm
                    JOIN raw_materials rm ON rm.material_id = bbm.material_id
                    WHERE bbm.batch_id = bb.batch_id) AS materials_used,
                   u.username
            FROM bulk_batches bb
            LEFT JOIN users u ON bb.created_by_user_id = u.user_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["batch_code"] = r["batch_code"] or "—"
        r["total_volume_ml"] = float(r["total_volume_ml"])
        r["remaining_ml"] = float(r["remaining_ml"])
        r["used_ml"] = r["total_volume_ml"] - r["remaining_ml"]
        r["total_cost"] = float(r["total_cost"])
        r["cost_per_ml"] = float(r["cost_per_ml"])
        r["materials_used"] = r["materials_used"] or "—"
        r["username"] = r["username"] or "—"
        r["size_entered"] = f"{float(r.pop('input_qty')):g} {r.pop('input_unit')}"
        # Same "bottles left" figure as the Bulk Batches page: full
        # bottles of each size the remaining mL could still fill.
        r["bottles_left"] = " · ".join(
            f"{int(r['remaining_ml'] // float(bottle_size_ml(u)))}×{u}" for u in BOTTLE_UNITS
        )
        r["notes"] = r["notes"] or "—"

    columns = [
        ("created_at", "Mixed", "datetime"),
        ("batch_code", "Batch", "str"), ("scent_name", "Scent", "str"),
        ("size_entered", "Size", "str"),
        ("materials_used", "Materials Used", "str"),
        ("total_volume_ml", "Volume (mL)", "num"),
        ("used_ml", "Bottled (mL)", "num"),
        ("remaining_ml", "Remaining (mL)", "num"),
        ("bottles_left", "Bottles Left", "str"),
        ("total_cost", "Batch Cost", "money"),
        ("cost_per_ml", "Cost / mL", "money"),
        ("username", "Made By", "str"),
        ("notes", "Notes", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_discrepancies(filters, branch_scope):
    """Delivery discrepancies — DAMAGE (reported damaged at receipt) and
    ADJUSTMENT (dispatched but never received) rows tied to a delivery
    (reference_type='STOCK_REQUEST'), the same rows both Discrepancies
    pages list. New rows carry negative change_qty values for units that
    never entered branch stock; DISCREPANCY_UNITS_SQL falls back to the
    delivery item quantities for legacy zero-valued rows.
    """
    where, params = "", []
    if branch_scope is not None:
        where += " AND sml.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND sml.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR sr.delivery_number LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    if filters["movement_type"] in ("DAMAGE", "ADJUSTMENT"):
        where += " AND sml.movement_type = %s"
        params.append(filters["movement_type"])
    time_where, order, limit_n, truncated = time_window(
        "sml.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT sml.created_at, b.branch_name, sr.delivery_number, p.item_name, p.sku,
                   sml.movement_type, {DISCREPANCY_UNITS_SQL} AS units_lost, sml.notes
            FROM stock_movement_logs sml
            JOIN branches b ON sml.branch_id = b.branch_id
            JOIN products p ON sml.sku = p.sku
            LEFT JOIN stock_requests sr
              ON sml.reference_type = 'STOCK_REQUEST' AND sml.reference_id = sr.request_id
            {DISCREPANCY_ITEM_JOIN}
            WHERE sml.reference_type = 'STOCK_REQUEST'
              AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
              {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["delivery_number"] = r["delivery_number"] or "—"
        r["units_lost"] = int(r["units_lost"])
        r["notes"] = r["notes"] or "—"

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [("created_at", "When", "datetime")] + columns + [
        ("delivery_number", "Delivery", "str"),
        ("item_name", "Item", "str"), ("sku", "SKU", "str"),
        ("movement_type", "Type", "badge:movement_type"),
        ("units_lost", "Units Lost", "int"), ("notes", "Notes", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_low_stock(filters, branch_scope):
    """The Low Stock page as a report: every SKU at or below its own
    reorder level (LOW_STOCK_WHERE — bulk never counts), HQ's warehouse
    first, then most critical (furthest below reorder level) first."""
    where, params = "", []
    if branch_scope is not None:
        where += " AND bi.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND bi.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]
    rows = query(
        f"""SELECT b.branch_name, b.is_hq, p.sku, p.item_name, p.variant, p.unit,
                   bi.stock_qty, bi.reorder_level, (bi.reorder_level - bi.stock_qty) AS shortfall
            FROM branch_inventory bi
            JOIN branches b ON bi.branch_id = b.branch_id
            JOIN products p ON bi.sku = p.sku
            WHERE {LOW_STOCK_WHERE} {where}
            ORDER BY b.is_hq DESC, (bi.stock_qty - bi.reorder_level) ASC, p.item_name
            LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        if r.pop("is_hq"):
            r["branch_name"] = f"{r['branch_name']} (HQ)"
    columns = [] if branch_scope is not None else [("branch_name", "Location", "str")]
    columns += [
        ("sku", "SKU", "str"), ("item_name", "Item", "str"),
        ("variant", "Variant", "badge:variant"), ("unit", "Unit", "str"),
        ("stock_qty", "Stock Qty", "int"), ("reorder_level", "Reorder Level", "int"),
        ("shortfall", "Below Reorder By", "int"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now — at or below reorder level"
