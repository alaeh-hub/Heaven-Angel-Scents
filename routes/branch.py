import decimal
import uuid
from urllib.parse import urlencode

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from db import TransactionAborted, query, transaction
from decorators import branch_required
from receipts import build_receipt_pdf, build_sale_receipt_pdf
from reports import REPORT_TYPES, get_report, parse_report_filters, render_report_excel, render_report_pdf
from sales_import import build_sales_import_template, import_sales_rows, parse_sales_import_workbook
from sale_stock import apply_sale_stock, void_sale
from audit import log_action
from sockets import notify_admin_and_branch, notify_bell
from utils import (
    BOTTLE_UNITS, PAYMENT_METHODS, base_code_from_sku, business_today, PRODUCT_UNITS, SALE_TYPES, ValidationError, consume_form_token,
    issue_form_token, parse_non_negative_int, parse_optional_text, parse_past_date,
    parse_positive_decimal, parse_positive_int, percent_change,
)

bp = Blueprint("branch", __name__, url_prefix="/branch")

# Bucket-size options for the "Ledger movement" trend chart on the Reports
# page (see reports_data() below). Kept in sync with admin.py's
# _TREND_GRANULARITIES — same fixed, non-user-supplied SQL fragments, just
# duplicated here rather than imported since admin.py and branch.py don't
# otherwise share code.
_TREND_GRANULARITIES = {
    "daily": {
        "trunc": "DATE(sml.created_at)",
        "window": "INTERVAL 14 DAY",
    },
    "weekly": {
        "trunc": "DATE(DATE_SUB(sml.created_at, INTERVAL WEEKDAY(sml.created_at) DAY))",
        "window": "INTERVAL 12 WEEK",
    },
    "monthly": {
        "trunc": "DATE(DATE_FORMAT(sml.created_at, '%Y-%m-01'))",
        "window": "INTERVAL 12 MONTH",
    },
    "yearly": {
        "trunc": "DATE(DATE_FORMAT(sml.created_at, '%Y-01-01'))",
        "window": "INTERVAL 5 YEAR",
    },
}


def _branch_id():
    return session["branch_id"]


# ---------------------------------------------------------------- dashboard
@bp.route("/")
@branch_required
def dashboard():
    bid = _branch_id()
    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (bid,),
    )
    # BULK (Bulk/Refill) is left out: branches can't request it from
    # HQ (see request_stock()), so flagging it as "reorder" is noise.
    low_stock = [row for row in inventory
                 if row["unit"] in BOTTLE_UNITS and row["stock_qty"] <= row["reorder_level"]]
    # "SKUs carried" counts by base code, same as admin's Products page
    # (A1-85ML, A1-50ML and A1-BULK are one SKU).
    sku_count = len({base_code_from_sku(row["sku"], row["unit"]) for row in inventory})

    # Suggested reorder quantity: top back up to the same "full" reference
    # the dashboard/branch_stock fill-bar visualizations use elsewhere
    # (reorder_level * 3), never less than 1. Purely a starting point —
    # see the Reorder button below, which pre-fills Request Stock's cart
    # with this but leaves it fully editable there before submitting.
    for row in low_stock:
        row["suggested_qty"] = max(
            (row["reorder_level"] * 3) - row["stock_qty"], 1)
        # Only bottle sizes can be requested from HQ (see request_stock()).
        row["requestable"] = row["unit"] in BOTTLE_UNITS

    # Pre-built querystring for "Reorder all low stock" — repeated sku/qty
    # pairs that request_stock.html's JS reads with URLSearchParams.getAll()
    # to seed the cart on load. Built here (not in the template) since a
    # list-valued url_for() kwarg produces the same repeated-key querystring
    # but doing it as plain urlencode keeps the two sku[]/qty[] lists
    # trivially guaranteed to line up positionally.
    reorder_all_url = None
    reorderable = [row for row in low_stock if row["requestable"]]
    if reorderable:
        pairs = [("sku", row["sku"]) for row in reorderable] + \
            [("qty", row["suggested_qty"]) for row in reorderable]
        reorder_all_url = url_for(
            "branch.request_stock") + "?" + urlencode(pairs)

    # A "request" is now a multi-item delivery (see stock_request_items) —
    # this dashboard widget only needs the header plus a couple of totals,
    # not the line-item detail itself (that's what the receipt is for).
    pending_requests = query(
        """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                  COALESCE(SUM(GREATEST(sri.requested_qty - COALESCE(sri.received_qty, 0), 0)), 0) AS total_remaining
           FROM stock_requests sr
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
            WHERE sr.branch_id = %s AND sr.status IN ('Pending', 'Partially Fulfilled', 'In Transit')
           GROUP BY sr.request_id, sr.delivery_number, sr.status, sr.requested_at
           ORDER BY sr.requested_at DESC""",
        (bid,),
    )

    today_sales = query(
        """SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.branch_id = %s AND DATE(s.sold_at) = CURDATE()""",
        (bid,), fetchone=True,
    )

    # Trend arrow on the Today's sales tile: today's revenue vs.
    # yesterday up to this same time of day, so a morning isn't
    # compared against a whole finished day.
    yesterday_revenue = query(
        """SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales WHERE branch_id = %s
             AND sold_at >= CURDATE() - INTERVAL 1 DAY AND sold_at < NOW() - INTERVAL 1 DAY""",
        (bid,), fetchone=True,
    )["revenue"]
    today_sales["trend"] = percent_change(today_sales["revenue"], yesterday_revenue)

    return render_template(
        "branch/dashboard.html",
        inventory=inventory, low_stock=low_stock,
        pending_requests=pending_requests, today_sales=today_sales,
        reorder_all_url=reorder_all_url, sku_count=sku_count,
    )


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


# ---------------------------------------------------------------- stock requests list
@bp.route("/stock-requests")
@branch_required
def requests_list():
    """Full history of this branch's deliveries, with status tabs — the
    branch-scoped counterpart to admin's requests_list(). Request Stock
    keeps its own short "recent" preview (see request_stock() below);
    this is the page that link points to for the complete, filterable
    history, same relationship as Record Sale -> Sales History.
    """
    bid = _branch_id()
    status_filter = request.args.get("status", "all")
    sql = """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at,
                    COUNT(sri.item_id) AS item_count,
                    COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                    COALESCE(SUM(sri.requested_qty * sri.unit_price), 0) AS total_value,
                    COALESCE(SUM(sri.received_qty), 0) AS total_received,
                    COALESCE(SUM(sri.damaged_qty), 0) AS total_damaged,
                    COALESCE(SUM(GREATEST(sri.requested_qty - COALESCE(sri.received_qty, 0), 0)), 0) AS total_remaining
             FROM stock_requests sr
             LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
             WHERE sr.branch_id = %s"""
    params = [bid]
    if status_filter != "all":
        sql += " AND sr.status = %s"
        params.append(status_filter)
    sql += """ GROUP BY sr.request_id
               ORDER BY FIELD(sr.status,'Pending','Partially Fulfilled','In Transit','Fulfilled','Rejected'), sr.requested_at DESC"""
    stock_requests = query(sql, tuple(params))
    return render_template("branch/requests.html", stock_requests=stock_requests, status_filter=status_filter)


# ---------------------------------------------------------------- request stock
@bp.route("/request-stock", methods=["GET", "POST"])
@branch_required
def request_stock():
    """Send a delivery request to HQ.

    A request is now one delivery that can carry several different
    products at once (see stock_request_items) rather than one product
    per request. The form submits parallel arrays — sku[] and
    requested_qty[] — one pair per line the branch added to its cart in
    the UI. Each line is priced at that product's current reference
    price at the moment of submission, snapshotted onto the line item so
    it never silently changes later if HQ updates the catalog price.
    """
    bid = _branch_id()
    if request.method == "POST":
        if not consume_form_token("request_stock"):
            flash(
                "This delivery request already went through, or the form expired — check your open requests below before resending.", "error")
            return redirect(url_for("branch.request_stock"))

        skus = request.form.getlist("sku[]")
        raw_qtys = request.form.getlist("requested_qty[]")

        if not skus or len(skus) != len(raw_qtys):
            flash("Add at least one product to the delivery.", "error")
            return redirect(url_for("branch.request_stock"))

        # Merge duplicate SKUs (defensive against a tampered/duplicated
        # submission — the cart UI itself never produces duplicates) and
        # validate every quantity before touching the database.
        line_qty = {}
        try:
            for sku, raw_qty in zip(skus, raw_qtys):
                sku = (sku or "").strip()
                if not sku:
                    continue
                qty = parse_positive_int(raw_qty, "Quantity")
                line_qty[sku] = line_qty.get(sku, 0) + qty
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("branch.request_stock"))

        if not line_qty:
            flash("Add at least one product to the delivery.", "error")
            return redirect(url_for("branch.request_stock"))

        # Only bottled products can be requested — BULK (Bulk/Refill)
        # stock isn't shipped to branches this way. Enforced here, not
        # just by the product dropdown, so a tampered form can't slip a
        # BULK SKU through; it reads as "not available to request".
        skus_list = list(line_qty.keys())
        placeholders = ", ".join(["%s"] * len(skus_list))
        unit_placeholders = ", ".join(["%s"] * len(BOTTLE_UNITS))
        price_rows = query(
            f"""SELECT sku, price FROM products
                WHERE sku IN ({placeholders}) AND unit IN ({unit_placeholders})""",
            tuple(skus_list) + BOTTLE_UNITS,
        )
        price_by_sku = {row["sku"]: row["price"] for row in price_rows}
        if any(sku not in price_by_sku for sku in skus_list):
            flash(
                "One or more selected products are no longer available to request.", "error")
            return redirect(url_for("branch.request_stock"))

        delivery_number = None
        try:
            with transaction() as conn:
                cur = conn.cursor(dictionary=True)
                # delivery_number is UNIQUE + NOT NULL but its real value
                # (DR-<request_id>) depends on the row's own auto-increment
                # id, which only exists after the insert. A random
                # placeholder here satisfies the constraint for the instant
                # before it's overwritten below, without weakening it.
                cur.execute(
                    "INSERT INTO stock_requests (branch_id, delivery_number) VALUES (%s, %s)",
                    (bid, uuid.uuid4().hex[:20]),
                )
                request_id = cur.lastrowid
                delivery_number = f"DR-{request_id:06d}"
                cur.execute(
                    "UPDATE stock_requests SET delivery_number = %s WHERE request_id = %s",
                    (delivery_number, request_id),
                )
                for sku, qty in line_qty.items():
                    cur.execute(
                        """INSERT INTO stock_request_items (request_id, sku, requested_qty, unit_price)
                           VALUES (%s, %s, %s, %s)""",
                        (request_id, sku, qty, price_by_sku[sku]),
                    )
                cur.close()
            notify_admin_and_branch(bid, "requests")
            item_word = "item" if len(line_qty) == 1 else "items"
            notify_bell(
                f"{session.get('branch_name') or 'A branch'} requested delivery {delivery_number} "
                f"({len(line_qty)} {item_word}).",
                room="admin",
            )
            flash(
                f"Delivery {delivery_number} sent to HQ ({len(line_qty)} {item_word}).", "success")
        except Exception:
            current_app.logger.exception(
                "request_stock failed for branch_id=%s", bid)
            flash("Couldn't send this request — please try again.", "error")
        return redirect(url_for("branch.request_stock"))

    # Bottle sizes only — see the matching check in the POST branch above.
    products_list = query(
        f"""SELECT sku, item_name, variant, unit, price FROM products
            WHERE unit IN ({", ".join(["%s"] * len(BOTTLE_UNITS))})
            ORDER BY item_name""",
        BOTTLE_UNITS,
    )
    # Only requests still in play, so whoever is filling in a new request
    # can see what's already on its way and avoid asking twice. The full
    # record lives on the History tab (branch.requests_list).
    open_statuses = ("Pending", "Partially Fulfilled", "In Transit")
    open_requests = query(
        """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                  COALESCE(SUM(GREATEST(sri.requested_qty - COALESCE(sri.received_qty, 0), 0)), 0) AS total_remaining,
                  COALESCE(SUM(sri.requested_qty * sri.unit_price), 0) AS total_value
           FROM stock_requests sr
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
           WHERE sr.branch_id = %s AND sr.status IN (%s, %s, %s)
           GROUP BY sr.request_id, sr.delivery_number, sr.status, sr.requested_at
           ORDER BY sr.requested_at DESC LIMIT 5""",
        (bid, *open_statuses),
    )
    open_count = query(
        "SELECT COUNT(*) AS c FROM stock_requests WHERE branch_id = %s AND status IN (%s, %s, %s)",
        (bid, *open_statuses),
        fetchone=True,
    )["c"]
    return render_template(
        "branch/request_stock.html", products=products_list,
        open_requests=open_requests, open_count=open_count,
        form_token=issue_form_token("request_stock"),
    )


# ---------------------------------------------------------------- receive stock
@bp.route("/receive-stock", methods=["GET", "POST"])
@branch_required
def receive_stock():
    """Confirm one dispatch shipment; a request can have later shipments."""
    bid = _branch_id()
    if request.method == "POST":
        shipment_id = request.form.get("shipment_id")
        item_ids = request.form.getlist("shipment_item_id[]")
        raw_received = request.form.getlist("received_qty[]")
        raw_damaged = request.form.getlist("damaged_qty[]")

        if (not shipment_id or not item_ids or len(set(item_ids)) != len(item_ids)
                or len(item_ids) != len(raw_received) or len(item_ids) != len(raw_damaged)):
            flash(
                "Couldn't read that shipment's items — please refresh and try again.", "error")
            return redirect(url_for("branch.receive_stock"))

        try:
            received_by_item = {}
            damaged_by_item = {}
            for item_id, raw_r, raw_d in zip(item_ids, raw_received, raw_damaged):
                received_by_item[item_id] = parse_non_negative_int(
                    raw_r, "Received quantity")
                damaged_by_item[item_id] = parse_non_negative_int(
                    raw_d, "Damaged quantity")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("branch.receive_stock"))

        total_shortfall = 0
        delivery_number = None
        request_id = None
        try:
            with transaction() as conn:
                cur = conn.cursor(dictionary=True)
                cur.execute(
                    """SELECT ss.*, sr.request_id, sr.branch_id, sr.delivery_number
                       FROM stock_request_shipments ss
                       JOIN stock_requests sr ON sr.request_id = ss.request_id
                       WHERE ss.shipment_id = %s AND sr.branch_id = %s AND ss.status = 'In Transit'
                       FOR UPDATE""",
                    (shipment_id, bid),
                )
                shipment = cur.fetchone()
                if not shipment:
                    cur.close()
                    raise TransactionAborted(
                        "That shipment isn't awaiting receipt.")
                request_id = shipment["request_id"]
                delivery_number = shipment["delivery_number"]

                cur.execute(
                    """SELECT ssi.*, sri.item_id, sri.sku
                       FROM stock_request_shipment_items ssi
                       JOIN stock_request_items sri ON sri.item_id = ssi.request_item_id
                       WHERE ssi.shipment_id = %s FOR UPDATE""",
                    (shipment_id,),
                )
                item_rows = {str(r["shipment_item_id"]): r for r in cur.fetchall()}

                if set(item_ids) != set(item_rows.keys()):
                    cur.close()
                    raise TransactionAborted(
                        "That shipment's items don't match — please refresh and try again.")

                # Every per-item check below MUST raise rather than
                # `return` — see TransactionAborted's docstring in db.py.
                # A `return` here after an earlier item in this same loop
                # has already had its cur.execute() writes run would still
                # let transaction() commit those partial writes, since a
                # bare return doesn't count as an exceptional exit from
                # the `with` block.
                for item_id, item in item_rows.items():
                    received_qty = received_by_item[item_id]
                    damaged_qty = damaged_by_item[item_id]
                    dispatched = item["dispatched_qty"]
                    if received_qty + damaged_qty > dispatched:
                        cur.close()
                        raise TransactionAborted(
                            "Received + damaged can't exceed dispatched for one of the items.")

                    cur.execute(
                        """UPDATE stock_request_shipment_items
                           SET received_qty = %s, damaged_qty = %s WHERE shipment_item_id = %s""",
                        (received_qty, damaged_qty, item["shipment_item_id"]),
                    )
                    cur.execute(
                        """UPDATE stock_request_items
                           SET received_qty = COALESCE(received_qty, 0) + %s,
                               damaged_qty = COALESCE(damaged_qty, 0) + %s,
                               dispatched_qty = COALESCE(dispatched_qty, 0)
                           WHERE item_id = %s""",
                        (received_qty, damaged_qty, item["item_id"]),
                    )

                    if received_qty > 0:
                        cur.execute(
                            """INSERT INTO branch_inventory (branch_id, sku, stock_qty)
                               VALUES (%s, %s, %s)
                               ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
                            (bid, item["sku"], received_qty),
                        )
                        cur.execute(
                            "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s",
                            (bid, item["sku"]),
                        )
                        after_qty = cur.fetchone()["stock_qty"]
                        before_qty = after_qty - received_qty
                        cur.execute(
                            """INSERT INTO stock_movement_logs
                               (branch_id, sku, change_qty, movement_type, notes,
                                created_by_user_id, reference_type, reference_id, before_qty, after_qty)
                               VALUES (%s, %s, %s, 'RECEIPT', %s, %s, 'STOCK_REQUEST', %s, %s, %s)""",
                             (bid, item["sku"], received_qty, f"Receipt for delivery {delivery_number}, shipment #{shipment_id}",
                             session.get("user_id"), request_id, before_qty, after_qty),
                        )
                    if damaged_qty > 0:
                        cur.execute(
                            """INSERT INTO stock_movement_logs
                               (branch_id, sku, change_qty, movement_type, notes,
                                created_by_user_id, reference_type, reference_id)
                                VALUES (%s, %s, %s, 'DAMAGE', %s, %s, 'STOCK_REQUEST', %s)""",
                            (bid, item["sku"], -damaged_qty,
                              f"{damaged_qty} unit(s) damaged in transit, delivery {delivery_number}, shipment #{shipment_id}",
                             session.get("user_id"), request_id),
                        )

                    shortfall = dispatched - received_qty - damaged_qty
                    if shortfall > 0:
                        total_shortfall += shortfall
                        # This is the actual audit trail entry the Receive
                        # Shipment page promises HQ will see. Previously
                        # this was only a flash message that vanished
                        # after a few seconds — nothing was written to
                        # the ledger.
                        cur.execute(
                            """INSERT INTO stock_movement_logs
                               (branch_id, sku, change_qty, movement_type, notes,
                                created_by_user_id, reference_type, reference_id)
                                VALUES (%s, %s, %s, 'ADJUSTMENT', %s, %s, 'STOCK_REQUEST', %s)""",
                            (bid, item["sku"], -shortfall,
                             f"{shortfall} unit(s) dispatched but not received or reported damaged "
                              f"— delivery {delivery_number}, shipment #{shipment_id}, flagged for HQ follow-up",
                             session.get("user_id"), request_id),
                        )

                cur.execute(
                    "UPDATE stock_request_shipments SET status = 'Received', received_at = CURRENT_TIMESTAMP WHERE shipment_id = %s",
                    (shipment_id,),
                )
                cur.execute(
                    """SELECT COUNT(*) AS remaining_lines
                       FROM stock_request_items
                       WHERE request_id = %s AND COALESCE(received_qty, 0) < requested_qty""",
                    (request_id,),
                )
                remaining_lines = cur.fetchone()["remaining_lines"]
                request_status = "Partially Fulfilled" if remaining_lines else "Fulfilled"
                cur.execute(
                    "UPDATE stock_requests SET status = %s WHERE request_id = %s",
                    (request_status, request_id),
                )
                cur.close()
        except TransactionAborted as err:
            flash(str(err), "error")
            return redirect(url_for("branch.receive_stock"))
        except Exception:
            current_app.logger.exception(
                "receive_stock failed for shipment_id=%s", shipment_id)
            flash("Couldn't confirm this receipt — please try again.", "error")
            return redirect(url_for("branch.receive_stock"))

        notify_admin_and_branch(
            bid, ["requests", "inventory", "movement_logs"])
        branch_label = session.get("branch_name") or "A branch"
        if total_shortfall > 0:
            notify_bell(
                f"{branch_label} received {delivery_number} — {total_shortfall} unit(s) unaccounted for.",
                room="admin", level="warning",
            )
        else:
            notify_bell(f"{branch_label} received delivery {delivery_number}.",
                        room="admin", level="success")
        if total_shortfall > 0:
            flash(
                f"Shipment #{shipment_id} for {delivery_number} confirmed. {total_shortfall} unit(s) were short and flagged; "
                "the remaining request quantity stays open for another dispatch.", "warning")
        else:
            flash(f"Shipment #{shipment_id} for {delivery_number} confirmed and inventory updated.", "success")
        return redirect(url_for("branch.receive_stock"))

    item_rows = query(
        """SELECT ss.shipment_id, ss.request_id, ss.dispatched_at, sr.delivery_number, sr.requested_at,
                  ssi.shipment_item_id, ssi.dispatched_qty,
                  sri.item_id, sri.sku, p.item_name, p.unit
           FROM stock_request_shipments ss
           JOIN stock_requests sr ON sr.request_id = ss.request_id
           JOIN stock_request_shipment_items ssi ON ssi.shipment_id = ss.shipment_id
           JOIN stock_request_items sri ON sri.item_id = ssi.request_item_id
           JOIN products p ON sri.sku = p.sku
           WHERE sr.branch_id = %s AND ss.status = 'In Transit'
           ORDER BY ss.dispatched_at, p.item_name""",
        (bid,),
    )
    in_transit = []
    by_shipment = {}
    for row in item_rows:
        sid = row["shipment_id"]
        entry = by_shipment.get(sid)
        if entry is None:
            entry = {
                "shipment_id": sid,
                "request_id": row["request_id"],
                "delivery_number": row["delivery_number"],
                "requested_at": row["requested_at"],
                "dispatched_at": row["dispatched_at"],
                "line_items": [],
            }
            by_shipment[sid] = entry
            in_transit.append(entry)
        entry["line_items"].append(row)

    return render_template("branch/receive_stock.html", in_transit=in_transit)


# ---------------------------------------------------------------- goods-received receipt
@bp.route("/receive-stock/<int:request_id>/receipt")
@branch_required
def receipt(request_id):
    """Downloadable PDF for a request this branch has confirmed as received.

    Sourced straight from the stock_requests row and the movement-ledger
    entries receive_stock() wrote — see receipts.py. Scoped to this
    branch's own request_id via branch_id, same as every other page here.
    """
    pdf_buffer, req = build_receipt_pdf(request_id, branch_id=_branch_id())
    if pdf_buffer is None:
        abort(404)
    return send_file(
        pdf_buffer, mimetype="application/pdf",
        as_attachment=True, download_name=f"GR-{request_id:06d}.pdf",
    )


# ---------------------------------------------------------------- record sale
@bp.route("/record-sale/<int:sale_id>/receipt")
@branch_required
def sale_receipt(sale_id):
    """Downloadable PDF receipt for a single sale/refill this branch
    recorded — see receipts.py. Scoped to this branch's own sale_id,
    same convention as the goods-received receipt() above.
    """
    pdf_buffer, sale = build_sale_receipt_pdf(sale_id, branch_id=_branch_id())
    if pdf_buffer is None:
        abort(404)
    return send_file(
        pdf_buffer, mimetype="application/pdf",
        as_attachment=True, download_name=f"SR-{sale_id:06d}.pdf",
    )


@bp.route("/record-sale", methods=["GET", "POST"])
@branch_required
def record_sale():
    """Record a Sale or a Refill.

    A Sale is the normal case (customer takes a bottle). A Refill is a
    customer bringing their own bottle back and only paying for
    product — both consume stock the same way, but are usually charged
    a different amount, so the price is always typed in here rather
    than pulled from the catalog automatically.

    payment_method covers anyone taking product now without paying
    cash — an employee against their own pay, or a customer buying on
    store credit ("utang") — see buyer_name on the sales table (free
    text, not tied to a login account; buyer_user_id is a separate,
    currently-unused FK).
    """
    bid = _branch_id()
    if request.method == "POST":
        if not consume_form_token("branch_record_sale"):
            flash(
                "This sale already went through, or the form expired — check Sales History before recording it again.", "error")
            return redirect(url_for("branch.record_sale"))

        sku = request.form.get("sku")
        sale_type = request.form.get("sale_type")
        payment_method = request.form.get("payment_method")
        raw_buyer = request.form.get("buyer_name", "").strip()

        try:
            qty = parse_positive_int(
                request.form.get("qty_sold"), "Quantity sold")
            # Strictly positive, not just non-negative, for Sale/Refill —
            # a ₱0 "Sale" would otherwise be a way to move stock out with
            # zero revenue and no record of it being a freebie/giveaway.
            # Freebie is the explicit way to log that instead — always
            # ₱0, whatever was actually typed into the field (it's
            # read-only client-side, but never trust that alone).
            if sale_type == "Freebie":
                unit_price = decimal.Decimal("0")
            else:
                unit_price = parse_positive_decimal(
                    request.form.get("unit_price"), "Price charged")
            customer_name = parse_optional_text(
                request.form.get("customer_name"), "Customer name", 120)
            customer_address = parse_optional_text(
                request.form.get("customer_address"), "Customer address", 255)
            # Lets a sale that actually happened earlier be logged under
            # that date instead of today — blank defaults to today, same
            # as the old always-"now" behavior.
            sold_at = parse_past_date(
                request.form.get("sold_at"), "Sale date")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("branch.record_sale"))

        if sale_type not in SALE_TYPES:
            flash("Select whether this is a sale or a freebie.", "error")
            return redirect(url_for("branch.record_sale"))
        if sale_type == "Refill":
            # Refills pour from bulk, and bulk is only ever held at HQ.
            flash("Refills are only recorded at HQ — branches don't hold bulk stock.", "error")
            return redirect(url_for("branch.record_sale"))
        # Freebie is never actually paid for — payment_method only means
        # something for a real charge (Cash vs. Credit), so it's forced
        # to Cash here rather than exposed as its own choice, whatever
        # the form happened to submit.
        if sale_type == "Freebie":
            payment_method = "Cash"
        if payment_method not in PAYMENT_METHODS:
            flash("Select a payment method.", "error")
            return redirect(url_for("branch.record_sale"))
        if not sku:
            flash("Select a product to sell.", "error")
            return redirect(url_for("branch.record_sale"))

        buyer_name = None
        if payment_method == "Credit":
            if not raw_buyer:
                flash("Enter who this credit sale is for.", "error")
                return redirect(url_for("branch.record_sale"))
            if len(raw_buyer) > 120:
                flash("Name is too long (max 120 characters).", "error")
                return redirect(url_for("branch.record_sale"))
            # Free-text name, not a login lookup — see admin.py's
            # record_sale() for the same change and reasoning.
            buyer_name = raw_buyer

        try:
            with transaction() as conn:
                cur = conn.cursor(dictionary=True)
                cur.execute("SELECT sku, item_name, unit FROM products WHERE sku = %s", (sku,))
                product = cur.fetchone()
                if not product:
                    raise TransactionAborted("That product no longer exists.")
                cur.execute(
                    """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sale_type, payment_method,
                                          buyer_name, customer_name, customer_address, sold_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (bid, sku, qty, unit_price, sale_type,
                     payment_method, buyer_name, customer_name, customer_address, sold_at),
                )
                if sale_type == "Freebie":
                    notes = "Freebie / giveaway"
                else:
                    notes = "Point-of-sale" if payment_method == "Cash" else f"Credit — {buyer_name}"
                # Row-locks this branch/SKU's stock (a concurrent sale of the
                # same SKU waits), blocks if there isn't enough, deducts and
                # logs it — see sale_stock.py.
                apply_sale_stock(cur, sale_id=cur.lastrowid, branch_id=bid, product=product,
                                 qty=qty, sale_type=sale_type, notes=notes,
                                 user_id=session.get("user_id"))
                cur.close()
            notify_admin_and_branch(
                bid, ["inventory", "sales", "movement_logs"])
            flash(f"{sale_type} recorded.", "success")
        except TransactionAborted as err:
            flash(str(err), "error")
        except Exception:
            current_app.logger.exception(
                "record_sale failed for branch_id=%s sku=%s", bid, sku)
            flash("Couldn't record that sale — please try again.", "error")
        return redirect(url_for("branch.record_sale"))

    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.category, p.unit, p.price, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND bi.stock_qty > 0 ORDER BY p.item_name""",
        (bid,),
    )
    bulk_rate = query(
        "SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1", fetchone=True,
    )["rate_per_ml"]
    recent_sales = query(
        """SELECT s.*, p.item_name, p.unit, COALESCE(s.buyer_name, bu.username) AS buyer_username
           FROM sales s JOIN products p ON s.sku = p.sku
           LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 10""",
        (bid,),
    )
    employees = query(
        "SELECT user_id, username, role FROM users WHERE is_active = TRUE ORDER BY username"
    )
    # Every distinct customer name logged at this branch, each paired
    # with the address from that name's *first* sale (see schema.sql's
    # comment on sales.customer_address) — feeds the Customer name
    # autocomplete/autofill on the form below.
    customers = query(
        """SELECT s.customer_name AS name, s.customer_address AS address
           FROM sales s
           JOIN (
               SELECT customer_name, MIN(sold_at) AS first_sold_at
               FROM sales
               WHERE branch_id = %s AND customer_name IS NOT NULL AND customer_name <> ''
               GROUP BY customer_name
           ) first_seen
             ON first_seen.customer_name = s.customer_name AND first_seen.first_sold_at = s.sold_at
           WHERE s.branch_id = %s
           ORDER BY s.customer_name""",
        (bid, bid),
    )
    return render_template(
        "branch/record_sale.html", inventory=inventory, recent_sales=recent_sales, employees=employees,
        customers=customers, bulk_rate=bulk_rate, form_token=issue_form_token("branch_record_sale"),
        import_form_token=issue_form_token("branch_import_sales"),
    )


# ---------------------------------------------------------------- bulk import (Excel)
@bp.route("/record-sale/import-template")
@branch_required
def sales_import_template():
    """Downloadable blank workbook for the "Import from Excel" button
    below — see sales_import.build_sales_import_template().
    """
    bid = _branch_id()
    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, p.price, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (bid,),
    )
    buf = build_sales_import_template(inventory)
    return send_file(
        buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="sales-import-template.xlsx",
    )


@bp.route("/record-sale/import", methods=["POST"])
@branch_required
def import_sales():
    """Bulk-insert every row of an uploaded, filled-in copy of the
    template above in one go, instead of using Record Sale one line at
    a time. See sales_import.import_sales_rows() for the all-or-nothing
    transaction this runs — either the whole file lands, or none of it
    does, with every problem reported at once.
    """
    bid = _branch_id()
    if not consume_form_token("branch_import_sales"):
        flash(
            "This import already went through, or the form expired — try uploading again.", "error")
        return redirect(url_for("branch.record_sale"))

    upload = request.files.get("import_file")
    if not upload or not upload.filename:
        flash("Choose an Excel file to import.", "error")
        return redirect(url_for("branch.record_sale"))

    try:
        raw_rows = parse_sales_import_workbook(upload)
        inserted = import_sales_rows(bid, raw_rows, session.get("user_id"))
        notify_admin_and_branch(bid, ["inventory", "sales", "movement_logs"])
        flash(
            f"Imported {inserted} sale{'s' if inserted != 1 else ''} from the file.", "success")
    except (ValidationError, TransactionAborted) as err:
        for line in str(err).split("\n"):
            if line.strip():
                flash(line, "error")
    except Exception:
        current_app.logger.exception(
            "sales import failed for branch_id=%s", bid)
        flash("Couldn't import that file — please check the format and try again.", "error")
    return redirect(url_for("branch.record_sale"))


# ---------------------------------------------------------------- sales history
@bp.route("/sales-history")
@branch_required
def sales_history():
    bid = _branch_id()
    sales = query(
        # voidable: branch staff can void only what was recorded today.
        """SELECT s.*, p.item_name, p.variant, p.unit, DATE(s.recorded_at) = CURDATE() AS voidable
           FROM sales s JOIN products p ON s.sku = p.sku
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 200""",
        (bid,),
    )
    totals = query(
        """SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END),0) AS units,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END),0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku WHERE s.branch_id = %s""",
        (bid,), fetchone=True,
    )
    return render_template("branch/sales_history.html", sales=sales, totals=totals)


# ---------------------------------------------------------------- customers
@bp.route("/customers")
@branch_required
def customers():
    """Repeat-customer directory for this branch, built entirely from
    `sales.customer_name` / `customer_address` — there's no separate
    customers table. Address is taken from each name's *first* recorded
    sale, same convention as the Record Sale autocomplete's data-customers
    payload (see record_sale() below and schema.sql's comment on
    sales.customer_address). Walk-ins (blank customer_name) are excluded
    since there's nothing to group them by.
    """
    bid = _branch_id()
    rows = query(
        """SELECT s.customer_name AS name, s.customer_address AS address,
                  agg.purchase_count, agg.total_units, agg.total_bulk_ml, agg.total_spent, agg.last_purchase_at
           FROM (
               SELECT sa.customer_name,
                      COUNT(*) AS purchase_count,
                      -- Bulk sales store qty_sold in mL: kept apart from bottles.
                      COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_units,
                      COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                      COALESCE(SUM(sa.qty_sold * sa.unit_price), 0) AS total_spent,
                      MIN(sa.sold_at) AS first_sold_at,
                      MAX(sa.sold_at) AS last_purchase_at
               FROM sales sa
               JOIN products p ON p.sku = sa.sku
               WHERE sa.branch_id = %s AND sa.customer_name IS NOT NULL AND sa.customer_name <> ''
               GROUP BY sa.customer_name
           ) agg
           JOIN sales s
             ON s.customer_name = agg.customer_name AND s.sold_at = agg.first_sold_at AND s.branch_id = %s
           ORDER BY agg.total_spent DESC""",
        (bid, bid),
    )
    totals = query(
        """SELECT COUNT(DISTINCT customer_name) AS customer_count,
                  COALESCE(SUM(qty_sold * unit_price), 0) AS total_named_revenue
           FROM sales WHERE branch_id = %s AND customer_name IS NOT NULL AND customer_name <> ''""",
        (bid,), fetchone=True,
    )
    return render_template("branch/customers.html", rows=rows, totals=totals)


# ---------------------------------------------------------------- credit purchases
@bp.route("/credit-purchases")
@branch_required
def credit_purchases():
    """Credit leaderboard for this branch — who's taken how much product
    on credit, whether an employee against their own pay or a customer
    on store credit ("utang"), for reconciliation/collection. This is
    NOT "who rang up the sale": there's no such column on `sales` (see
    record_sale()'s comment on buyer_name/buyer_user_id above), only who
    the product was taken *for* on a Credit sale. Ordinary Cash sales
    aren't attributable to any one person and are excluded. Settled
    credit (see sale_stock.settle_credit(), used from the Scan Receipt
    page's "Mark as paid") drops off here once cleared.
    """
    bid = _branch_id()
    rows = query(
        """SELECT COALESCE(s.buyer_name, bu.username) AS buyer_name,
                  COUNT(*) AS transaction_count,
                  COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS total_units,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS total_amount,
                  MAX(s.sold_at) AS last_taken_at
           FROM sales s
           JOIN products p ON p.sku = s.sku
           LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
           WHERE s.branch_id = %s AND s.payment_method = 'Credit' AND s.credit_settled_at IS NULL
           GROUP BY COALESCE(s.buyer_name, bu.username)
           ORDER BY total_amount DESC""",
        (bid,),
    )
    totals = query(
        """SELECT COUNT(*) AS transaction_count,
                  COALESCE(SUM(qty_sold * unit_price), 0) AS total_amount
           FROM sales WHERE branch_id = %s AND payment_method = 'Credit' AND credit_settled_at IS NULL""",
        (bid,), fetchone=True,
    )
    return render_template("branch/credit_purchases.html", rows=rows, totals=totals)


# ---------------------------------------------------------------- reports
@bp.route("/reports")
@branch_required
def reports():
    report_types = [
        {"key": key, "label": meta.get(
            "branch_label", meta["label"]), "windowed": meta["windowed"]}
        for key, meta in REPORT_TYPES.items() if meta["branch"]
    ]
    return render_template(
        "branch/reports.html", report_types=report_types, unit_choices=PRODUCT_UNITS,
        current_month=business_today().strftime("%Y-%m"),
    )


@bp.route("/reports/generate")
@branch_required
def generate_report():
    """Download a filtered report as PDF or Excel, scoped to this branch.

    branch_scope is always this signed-in branch's own branch_id — any
    branch_id present in the querystring is ignored by reports.py, so
    editing the URL can't pull another branch's data.
    """
    report_type = request.args.get("type", "")
    fmt = request.args.get("format", "pdf")
    meta = REPORT_TYPES.get(report_type)
    if meta is None or not meta["branch"] or fmt not in ("pdf", "xlsx"):
        abort(404)

    bid = _branch_id()
    filters = parse_report_filters(request.args)
    report = get_report(
        report_type, filters, branch_scope=bid,
        actor_label=f"{session.get('branch_name')} — {session.get('username')}",
    )

    if report["row_count"] == 0:
        flash(
            f"No data matches the selected filters for {report['title']}.", "warning")
        return redirect(url_for("branch.reports"))

    stamp = business_today().isoformat()
    if fmt == "xlsx":
        buf = render_report_excel(report)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        buf = render_report_pdf(report)
        mimetype = "application/pdf"
    return send_file(
        buf, mimetype=mimetype, as_attachment=True,
        download_name=f"{report_type}-{stamp}.{fmt}",
    )


@bp.route("/api/reports-data")
@branch_required
def reports_data():
    """Chart data for the branch Reports page — everything here is scoped
    to this signed-in branch's own branch_id, same as every query above.
    There's no branch_id in the querystring to trust or ignore.
    """
    bid = _branch_id()

    # Unit figures below count bottles only: a bulk product's qty_sold
    # (and stock_qty) is mL, which would swamp the bottle counts if added
    # in. Bulk volume is reported separately as bulk_ml where it matters.
    by_variant = query(
        """SELECT p.variant, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM products p
           LEFT JOIN sales s ON p.sku = s.sku AND s.branch_id = %s
           WHERE p.unit <> 'BULK'
           GROUP BY p.variant""",
        (bid,),
    )

    # Daily units sold for the last 14 days — the branch-scoped stand-in
    # for the admin page's "units sold by branch" chart, which doesn't
    # make sense when there's only one branch to look at.
    sales_trend = query(
        """SELECT DATE(s.sold_at) AS day, COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units_sold
           FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.branch_id = %s AND s.sold_at >= NOW() - INTERVAL 14 DAY
           GROUP BY DATE(s.sold_at) ORDER BY day""",
        (bid,),
    )

    top_products = query(
        """SELECT p.item_name, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM sales s JOIN products p ON s.sku = p.sku
           WHERE s.branch_id = %s AND p.unit <> 'BULK'
           GROUP BY p.sku, p.item_name
           ORDER BY units_sold DESC LIMIT 8""",
        (bid,),
    )

    stock_by_variant = query(
        """SELECT p.variant, COALESCE(SUM(bi.stock_qty), 0) AS total_stock
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND p.unit <> 'BULK'
           GROUP BY p.variant""",
        (bid,),
    )

    granularity = request.args.get("granularity", "daily")
    trend_bucket = _TREND_GRANULARITIES.get(
        granularity, _TREND_GRANULARITIES["daily"])
    # Bottles and bulk mL are different units — kept apart the same way
    # sales/stock totals are split elsewhere, rather than summed into
    # one meaningless number (see admin.reports_data()'s movement_trend).
    movement_trend = query(
        f"""SELECT {trend_bucket['trunc']} AS day, sml.movement_type,
                   COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN ABS(sml.change_qty) ELSE 0 END), 0) AS units,
                   COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN ABS(sml.change_qty) ELSE 0 END), 0) AS bulk_ml
            FROM stock_movement_logs sml
            JOIN products p ON p.sku = sml.sku
            WHERE sml.branch_id = %s AND sml.created_at >= NOW() - {trend_bucket['window']}
            GROUP BY {trend_bucket['trunc']}, sml.movement_type ORDER BY day""",
        (bid,),
    )

    # Cash sales vs. employee purchases deducted from salary, and plain
    # sales vs. refills — both scoped to this branch only.
    payment_breakdown = query(
        """SELECT s.payment_method, COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units_sold, COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.branch_id = %s GROUP BY s.payment_method""",
        (bid,),
    )
    sale_type_breakdown = query(
        """SELECT s.sale_type, COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units_sold, COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.branch_id = %s GROUP BY s.sale_type""",
        (bid,),
    )

    totals = query(
        """SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS units, COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku WHERE s.branch_id = %s""",
        (bid,), fetchone=True,
    )

    for row in sales_trend:
        row["day"] = row["day"].isoformat()
    for row in movement_trend:
        row["day"] = row["day"].isoformat()

    return jsonify(
        by_variant=by_variant, sales_trend=sales_trend, top_products=top_products,
        stock_by_variant=stock_by_variant, movement_trend=movement_trend,
        payment_breakdown=payment_breakdown, sale_type_breakdown=sale_type_breakdown, totals=totals,
    )


# ---------------------------------------------------------------- void a sale
@bp.route("/sales/<int:sale_id>/void", methods=["POST"])
@branch_required
def void_sale_route(sale_id):
    """Branch staff may void their own branch's sales recorded today (a
    counter mistake); older ones go through HQ. See sale_stock.void_sale()."""
    bid = _branch_id()
    back = redirect(url_for("branch.sales_history"))
    reason = (request.form.get("reason") or "").strip()[:255]
    if not reason:
        flash("Give a reason for voiding this sale.", "error")
        return back
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            sale = void_sale(cur, sale_id=sale_id, reason=reason,
                             user_id=session.get("user_id"), username=session.get("username"),
                             branch_id=bid, today_only=True)
            cur.close()
    except TransactionAborted as err:
        flash(str(err), "error")
        return back
    except Exception:
        current_app.logger.exception("branch void sale %s failed", sale_id)
        flash("Couldn't void that sale — please try again.", "error")
        return back
    notify_admin_and_branch(bid, ["inventory", "sales", "movement_logs"])
    log_action("void_sale", target=f"sale #{sale_id} — {sale['item_name']}",
               details=f"{session.get('branch_name', 'branch')} · {sale['sale_type']} × {sale['qty_sold']} · {reason}"[:255])
    flash(f"Voided the {sale['sale_type'].lower()} of {sale['item_name']} — its stock was returned.", "success")
    return back
