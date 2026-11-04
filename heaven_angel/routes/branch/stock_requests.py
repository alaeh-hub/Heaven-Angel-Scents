import uuid

from flask import abort, current_app, flash, redirect, render_template, request, send_file, session, url_for

from heaven_angel.db import TransactionAborted, query, transaction
from heaven_angel.decorators import branch_required
from heaven_angel.services.receipts import build_receipt_pdf
from heaven_angel.sockets import notify_admin_and_branch, notify_bell
from heaven_angel.utils import (
    BOTTLE_UNITS, ValidationError, consume_form_token,
    issue_form_token, parse_non_negative_int, parse_positive_int,
)

from heaven_angel.routes.branch._common import _branch_id, bp


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


def _parse_receipt_form(form):
    """Read the Receive Shipment form. Returns (shipment_id, item_ids, received_by_item,
    damaged_by_item); raises ValidationError with a message to flash as-is."""
    shipment_id = form.get("shipment_id")
    item_ids = form.getlist("shipment_item_id[]")
    raw_received = form.getlist("received_qty[]")
    raw_damaged = form.getlist("damaged_qty[]")

    if (not shipment_id or not item_ids or len(set(item_ids)) != len(item_ids)
            or len(item_ids) != len(raw_received) or len(item_ids) != len(raw_damaged)):
        raise ValidationError("Couldn't read that shipment's items — please refresh and try again.")

    received_by_item = {}
    damaged_by_item = {}
    for item_id, raw_r, raw_d in zip(item_ids, raw_received, raw_damaged):
        received_by_item[item_id] = parse_non_negative_int(raw_r, "Received quantity")
        damaged_by_item[item_id] = parse_non_negative_int(raw_d, "Damaged quantity")
    return shipment_id, item_ids, received_by_item, damaged_by_item


def _post_item_receipt(cur, *, bid, user_id, shipment, item, received_qty, damaged_qty):
    """Write one shipment line's receipt: the counts, the branch's stock and the
    movement-log entries (receipt, damage, and any unexplained shortfall).
    Returns the line's shortfall. Raises TransactionAborted if it doesn't add up."""
    shipment_id = shipment["shipment_id"]
    request_id = shipment["request_id"]
    delivery_number = shipment["delivery_number"]
    dispatched = item["dispatched_qty"]
    if received_qty + damaged_qty > dispatched:
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
             user_id, request_id, before_qty, after_qty),
        )
    if damaged_qty > 0:
        cur.execute(
            """INSERT INTO stock_movement_logs
               (branch_id, sku, change_qty, movement_type, notes,
                created_by_user_id, reference_type, reference_id)
               VALUES (%s, %s, %s, 'DAMAGE', %s, %s, 'STOCK_REQUEST', %s)""",
            (bid, item["sku"], -damaged_qty,
             f"{damaged_qty} unit(s) damaged in transit, delivery {delivery_number}, shipment #{shipment_id}",
             user_id, request_id),
        )

    shortfall = dispatched - received_qty - damaged_qty
    if shortfall > 0:
        # The audit-trail entry the Receive Shipment page promises HQ will
        # see — written to the ledger, not just flashed to the screen.
        cur.execute(
            """INSERT INTO stock_movement_logs
               (branch_id, sku, change_qty, movement_type, notes,
                created_by_user_id, reference_type, reference_id)
               VALUES (%s, %s, %s, 'ADJUSTMENT', %s, %s, 'STOCK_REQUEST', %s)""",
            (bid, item["sku"], -shortfall,
             f"{shortfall} unit(s) dispatched but not received or reported damaged "
             f"— delivery {delivery_number}, shipment #{shipment_id}, flagged for HQ follow-up",
             user_id, request_id),
        )
    return shortfall


def _confirm_shipment_receipt(bid, user_id, shipment_id, item_ids, received_by_item, damaged_by_item):
    """Confirm one In Transit shipment in a single transaction. Returns
    (delivery_number, total_shortfall); raises TransactionAborted with a message to flash."""
    total_shortfall = 0
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
            raise TransactionAborted("That shipment isn't awaiting receipt.")
        request_id = shipment["request_id"]

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
            raise TransactionAborted("That shipment's items don't match — please refresh and try again.")

        # Every per-item check MUST raise rather than `return` — see
        # TransactionAborted's docstring in db.py. A `return` after an
        # earlier item in this loop has already had its writes run would
        # still let transaction() commit those partial writes, since a bare
        # return doesn't count as an exceptional exit from the `with` block.
        for item_id, item in item_rows.items():
            total_shortfall += _post_item_receipt(
                cur, bid=bid, user_id=user_id, shipment=shipment, item=item,
                received_qty=received_by_item[item_id], damaged_qty=damaged_by_item[item_id])

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
    return shipment["delivery_number"], total_shortfall


def _in_transit_shipments(bid):
    """This branch's In Transit shipments, each with its line items."""
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
    return in_transit


@bp.route("/receive-stock", methods=["GET", "POST"])
@branch_required
def receive_stock():
    """Confirm one dispatch shipment; a request can have later shipments."""
    bid = _branch_id()
    if request.method == "POST":
        try:
            shipment_id, item_ids, received_by_item, damaged_by_item = _parse_receipt_form(request.form)
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("branch.receive_stock"))

        try:
            delivery_number, total_shortfall = _confirm_shipment_receipt(
                bid, session.get("user_id"), shipment_id, item_ids, received_by_item, damaged_by_item)
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
            flash(
                f"Shipment #{shipment_id} for {delivery_number} confirmed. {total_shortfall} unit(s) were short and flagged; "
                "the remaining request quantity stays open for another dispatch.", "warning")
        else:
            notify_bell(f"{branch_label} received delivery {delivery_number}.",
                        room="admin", level="success")
            flash(f"Shipment #{shipment_id} for {delivery_number} confirmed and inventory updated.", "success")
        return redirect(url_for("branch.receive_stock"))

    return render_template("branch/receive_stock.html", in_transit=_in_transit_shipments(bid))


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
