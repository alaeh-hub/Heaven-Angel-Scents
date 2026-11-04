
from flask import render_template

from heaven_angel.db import query
from heaven_angel.decorators import branch_required
from heaven_angel.utils import (
    BOTTLE_UNITS,
)

from heaven_angel.routes.branch._common import _branch_id, bp


# ---------------------------------------------------------------- inventory
@bp.route("/inventory")
@branch_required
def inventory():
    bid = _branch_id()
    rows = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, p.price, p.image_path,
                  bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND p.unit <> 'BULK'
           ORDER BY p.item_name""",
        (bid,),
    )
    # Bottles only: branches never stock BULK (Bulk/Refill) — they can't
    # request it, and a refill doesn't deduct branch stock — so a BULK
    # row here would only ever read as a meaningless low-stock alert.
    return render_template("branch/inventory.html", rows=rows, unit_choices=BOTTLE_UNITS)


# ---------------------------------------------------------------- movement logs
@bp.route("/movement-logs")
@branch_required
def movement_logs():
    """Production/Sale/Refill activity only, scoped to this branch.

    Same exclusion as admin.movement_logs(): stock-request-driven
    movements (DISPATCH, RECEIPT, DAMAGE, ADJUSTMENT — every row tagged
    reference_type='STOCK_REQUEST' by dispatch_request() /
    receive_stock()) are left out here, since a delivery's own review
    page and receipt PDF already show that history in full. See
    Receive Shipment for that instead.
    """
    bid = _branch_id()
    logs = query(
        """SELECT sml.*, p.item_name
           FROM stock_movement_logs sml
           JOIN products p ON sml.sku = p.sku
           WHERE sml.branch_id = %s
             AND (sml.reference_type IS NULL OR sml.reference_type != 'STOCK_REQUEST')
           ORDER BY sml.created_at DESC LIMIT 200""",
        (bid,),
    )
    return render_template("branch/movement_logs.html", logs=logs)


# ---------------------------------------------------------------- shipment discrepancies
@bp.route("/discrepancies")
@branch_required
def discrepancies():
    """The flip side of movement_logs() above: only DAMAGE and ADJUSTMENT
    rows tied to a delivery (reference_type='STOCK_REQUEST') — units
    reported damaged in transit, or dispatched but never received/
    reported damaged (see receive_stock()'s shortfall handling). Each
    delivery's own receipt already shows this per-item, but there's no
    other place to see "which deliveries have had problems" across
    time without opening every Fulfilled receipt one by one.
    """
    bid = _branch_id()
    rows = query(
        """SELECT sml.*, p.item_name, sr.delivery_number
           FROM stock_movement_logs sml
           JOIN products p ON sml.sku = p.sku
           LEFT JOIN stock_requests sr
             ON sml.reference_type = 'STOCK_REQUEST' AND sml.reference_id = sr.request_id
           WHERE sml.branch_id = %s
             AND sml.reference_type = 'STOCK_REQUEST'
             AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
           ORDER BY sml.created_at DESC LIMIT 200""",
        (bid,),
    )
    return render_template("branch/discrepancies.html", rows=rows)
