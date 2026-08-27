import datetime
import uuid

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from db import execute, query, transaction
from decorators import branch_required
from receipts import build_receipt_pdf
from reports import REPORT_TYPES, get_report, parse_report_filters, render_report_excel, render_report_pdf
from sockets import notify_admin_and_branch
from utils import PAYMENT_METHODS, PRODUCT_UNITS, SALE_TYPES, ValidationError, parse_non_negative_decimal, parse_non_negative_int, parse_positive_int

bp = Blueprint("branch", __name__, url_prefix="/branch")

# Bucket-size options for the "Ledger movement" trend chart on the Reports
# page (see reports_data() below). Kept in sync with admin.py's
# _TREND_GRANULARITIES — same fixed, non-user-supplied SQL fragments, just
# duplicated here rather than imported since admin.py and branch.py don't
# otherwise share code.
_TREND_GRANULARITIES = {
    "daily": {
        "trunc": "DATE(created_at)",
        "window": "INTERVAL 14 DAY",
    },
    "weekly": {
        "trunc": "DATE(DATE_SUB(created_at, INTERVAL WEEKDAY(created_at) DAY))",
        "window": "INTERVAL 12 WEEK",
    },
    "monthly": {
        "trunc": "DATE(DATE_FORMAT(created_at, '%Y-%m-01'))",
        "window": "INTERVAL 12 MONTH",
    },
    "yearly": {
        "trunc": "DATE(DATE_FORMAT(created_at, '%Y-01-01'))",
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
        """SELECT p.sku, p.item_name, p.variant, bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (bid,),
    )
    low_stock = [row for row in inventory if row["stock_qty"]
                 <= row["reorder_level"]]

    # A "request" is now a multi-item delivery (see stock_request_items) —
    # this dashboard widget only needs the header plus a couple of totals,
    # not the line-item detail itself (that's what the receipt is for).
    pending_requests = query(
        """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty
           FROM stock_requests sr
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
           WHERE sr.branch_id = %s AND sr.status IN ('Pending', 'In Transit')
           GROUP BY sr.request_id, sr.delivery_number, sr.status, sr.requested_at
           ORDER BY sr.requested_at DESC""",
        (bid,),
    )

    today_sales = query(
        """SELECT COALESCE(SUM(qty_sold), 0) AS units, COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales WHERE branch_id = %s AND DATE(sold_at) = CURDATE()""",
        (bid,), fetchone=True,
    )

    return render_template(
        "branch/dashboard.html",
        inventory=inventory, low_stock=low_stock,
        pending_requests=pending_requests, today_sales=today_sales,
    )


# ---------------------------------------------------------------- inventory
@bp.route("/inventory")
@branch_required
def inventory():
    bid = _branch_id()
    rows = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, p.price,
                  bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s
           ORDER BY p.item_name""",
        (bid,),
    )
    return render_template("branch/inventory.html", rows=rows, unit_choices=PRODUCT_UNITS)


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

        skus_list = list(line_qty.keys())
        placeholders = ", ".join(["%s"] * len(skus_list))
        price_rows = query(
            f"SELECT sku, price FROM products WHERE sku IN ({placeholders})",
            tuple(skus_list),
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
            flash(
                f"Delivery {delivery_number} sent to HQ ({len(line_qty)} {item_word}).", "success")
        except Exception:
            current_app.logger.exception(
                "request_stock failed for branch_id=%s", bid)
            flash("Couldn't send this request — please try again.", "error")
        return redirect(url_for("branch.request_stock"))

    products_list = query(
        """SELECT sku, item_name, variant, unit, price FROM products
           ORDER BY item_name""")
    # One row per delivery, with item count / total qty / total value
    # rolled up from stock_request_items — the line-item breakdown itself
    # only ever needs to be seen on the receipt (once Fulfilled).
    history = query(
        """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                  COALESCE(SUM(sri.requested_qty * sri.unit_price), 0) AS total_value
           FROM stock_requests sr
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
           WHERE sr.branch_id = %s
           GROUP BY sr.request_id, sr.delivery_number, sr.status, sr.requested_at
           ORDER BY sr.requested_at DESC LIMIT 25""",
        (bid,),
    )
    return render_template("branch/request_stock.html", products=products_list, history=history)


# ---------------------------------------------------------------- receive stock
@bp.route("/receive-stock", methods=["GET", "POST"])
@branch_required
def receive_stock():
    """Confirm what actually arrived for a whole delivery in one go.

    The form submits one request_id plus parallel arrays — item_id[],
    received_qty[], damaged_qty[] — covering every line item on that
    delivery, so a multi-product shipment is confirmed as a single
    transaction instead of one form per product.
    """
    bid = _branch_id()
    if request.method == "POST":
        request_id = request.form.get("request_id")
        item_ids = request.form.getlist("item_id[]")
        raw_received = request.form.getlist("received_qty[]")
        raw_damaged = request.form.getlist("damaged_qty[]")

        if not request_id or not item_ids or len(item_ids) != len(raw_received) or len(item_ids) != len(raw_damaged):
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
        try:
            with transaction() as conn:
                cur = conn.cursor(dictionary=True)
                # Lock the delivery header for the duration of the
                # transaction so it can't be confirmed twice in parallel.
                cur.execute(
                    """SELECT * FROM stock_requests
                       WHERE request_id = %s AND branch_id = %s AND status = 'In Transit'
                       FOR UPDATE""",
                    (request_id, bid),
                )
                req = cur.fetchone()
                if not req:
                    cur.close()
                    flash("That shipment isn't awaiting receipt.", "error")
                    return redirect(url_for("branch.receive_stock"))
                delivery_number = req["delivery_number"]

                cur.execute(
                    "SELECT * FROM stock_request_items WHERE request_id = %s FOR UPDATE",
                    (request_id,),
                )
                item_rows = {str(r["item_id"]): r for r in cur.fetchall()}

                if set(item_ids) != set(item_rows.keys()):
                    cur.close()
                    flash(
                        "That shipment's items don't match — please refresh and try again.", "error")
                    return redirect(url_for("branch.receive_stock"))

                for item_id, item in item_rows.items():
                    received_qty = received_by_item[item_id]
                    damaged_qty = damaged_by_item[item_id]
                    dispatched = item["dispatched_qty"] or 0
                    if received_qty + damaged_qty > dispatched:
                        cur.close()
                        flash(
                            "Received + damaged can't exceed dispatched for one of the items.", "error")
                        return redirect(url_for("branch.receive_stock"))

                    cur.execute(
                        "UPDATE stock_request_items SET received_qty = %s, damaged_qty = %s WHERE item_id = %s",
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
                            (bid, item["sku"], received_qty, f"Receipt for delivery {delivery_number}",
                             session.get("user_id"), request_id, before_qty, after_qty),
                        )
                    if damaged_qty > 0:
                        cur.execute(
                            """INSERT INTO stock_movement_logs
                               (branch_id, sku, change_qty, movement_type, notes,
                                created_by_user_id, reference_type, reference_id)
                               VALUES (%s, %s, 0, 'DAMAGE', %s, %s, 'STOCK_REQUEST', %s)""",
                            (bid, item["sku"],
                             f"{damaged_qty} unit(s) damaged in transit, delivery {delivery_number}",
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
                               VALUES (%s, %s, 0, 'ADJUSTMENT', %s, %s, 'STOCK_REQUEST', %s)""",
                            (bid, item["sku"],
                             f"{shortfall} unit(s) dispatched but not received or reported damaged "
                             f"— delivery {delivery_number}, flagged for HQ follow-up",
                             session.get("user_id"), request_id),
                        )

                cur.execute(
                    "UPDATE stock_requests SET status = 'Fulfilled' WHERE request_id = %s",
                    (request_id,),
                )
                cur.close()
        except Exception:
            current_app.logger.exception(
                "receive_stock failed for request_id=%s", request_id)
            flash("Couldn't confirm this receipt — please try again.", "error")
            return redirect(url_for("branch.receive_stock"))

        notify_admin_and_branch(
            bid, ["requests", "inventory", "movement_logs"])
        if total_shortfall > 0:
            flash(
                f"Delivery {delivery_number} received. Note: {total_shortfall} unit(s) unaccounted for "
                "— flagged in the ledger.", "warning")
        else:
            flash(
                f"Delivery {delivery_number} receipt confirmed and inventory updated.", "success")
        return redirect(url_for("branch.receive_stock"))

    item_rows = query(
        """SELECT sr.request_id, sr.delivery_number, sr.requested_at,
                  sri.item_id, sri.sku, sri.requested_qty, sri.dispatched_qty,
                  p.item_name, p.unit
           FROM stock_requests sr
           JOIN stock_request_items sri ON sri.request_id = sr.request_id
           JOIN products p ON sri.sku = p.sku
           WHERE sr.branch_id = %s AND sr.status = 'In Transit'
           ORDER BY sr.requested_at, p.item_name""",
        (bid,),
    )
    # Group the flat item rows into one entry per delivery for the
    # template. NOTE: this key is deliberately called "line_items", not
    # "items" — a plain dict already has a built-in .items() method, and
    # Jinja's dotted access (d.items) resolves to that bound method
    # before it ever tries d["items"], which breaks any |length/loop use
    # on it. Naming it "line_items" (or "keys"/"values"/"update", etc.)
    # sidesteps that collision instead of relying on everyone remembering
    # to use bracket access in the template.
    in_transit = []
    by_request = {}
    for row in item_rows:
        rid = row["request_id"]
        entry = by_request.get(rid)
        if entry is None:
            entry = {
                "request_id": rid,
                "delivery_number": row["delivery_number"],
                "requested_at": row["requested_at"],
                "line_items": [],
            }
            by_request[rid] = entry
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
@bp.route("/record-sale", methods=["GET", "POST"])
@branch_required
def record_sale():
    """Record a Sale or a Refill.

    A Sale is the normal case (customer takes a bottle). A Refill is a
    customer bringing their own bottle back and only paying for
    product — both consume stock the same way, but are usually charged
    a different amount, so the price is always typed in here rather
    than pulled from the catalog automatically.

    payment_method covers employees who take product for themselves
    where the cost is deducted from their salary instead of paid in
    cash — see buyer_user_id on the sales table.
    """
    bid = _branch_id()
    if request.method == "POST":
        sku = request.form.get("sku")
        sale_type = request.form.get("sale_type")
        payment_method = request.form.get("payment_method")
        raw_buyer = request.form.get("buyer_user_id", "").strip()

        try:
            qty = parse_positive_int(
                request.form.get("qty_sold"), "Quantity sold")
            unit_price = parse_non_negative_decimal(
                request.form.get("unit_price"), "Price charged")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("branch.record_sale"))

        if sale_type not in SALE_TYPES:
            flash("Select whether this is a sale or a refill.", "error")
            return redirect(url_for("branch.record_sale"))
        if payment_method not in PAYMENT_METHODS:
            flash("Select a payment method.", "error")
            return redirect(url_for("branch.record_sale"))
        if not sku:
            flash("Select a product to sell.", "error")
            return redirect(url_for("branch.record_sale"))

        # AFTER
        buyer_name = None
        if payment_method == "Salary Deduction":
            if not raw_buyer:
                flash("Enter which employee this salary deduction applies to.", "error")
                return redirect(url_for("branch.record_sale"))
            if len(raw_buyer) > 120:
                flash("Employee name is too long (max 120 characters).", "error")
                return redirect(url_for("branch.record_sale"))
            # Free-text name, not a login lookup — see admin.py's
            # record_sale() for the same change and reasoning.
            buyer_name = raw_buyer

        try:
            with transaction() as conn:
                cur = conn.cursor(dictionary=True)
                # Row-lock this branch/SKU for the rest of the transaction.
                # A second, concurrent sale of the same SKU has to wait
                # here until this one commits or rolls back, so two
                # cashiers can no longer both "see" the last unit as
                # available and both sell it.
                cur.execute(
                    "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE",
                    (bid, sku),
                )
                stock_row = cur.fetchone()
                if not stock_row:
                    cur.close()
                    flash("That product isn't stocked at this branch.", "error")
                    return redirect(url_for("branch.record_sale"))
                # AFTER
                # See admin.py's record_sale() for the reasoning: a Refill
                # doesn't draw down countable branch_inventory the way a
                # Sale does — it's still a real sales row (counts toward
                # revenue and "today's sales") and still gets its own
                # ledger entry, just with a zero stock change.
                is_refill = sale_type == "Refill"

                if not is_refill and stock_row["stock_qty"] < qty:
                    cur.close()
                    flash("Not enough stock on hand for that sale.", "error")
                    return redirect(url_for("branch.record_sale"))

                before_qty = stock_row["stock_qty"]
                after_qty = before_qty if is_refill else before_qty - qty

                cur.execute(
                    """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sale_type, payment_method, buyer_name)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (bid, sku, qty, unit_price, sale_type,
                     payment_method, buyer_name),
                )
                if not is_refill:
                    cur.execute(
                        "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
                        (after_qty, bid, sku),
                    )
                movement_type = "SALE" if sale_type == "Sale" else "REFILL"
                notes = "Point-of-sale" if payment_method == "Cash" else f"Salary deduction — {buyer_name}"
                if is_refill:
                    notes += " · no stock deducted (refill)"
                cur.execute(
                    """INSERT INTO stock_movement_logs
                       (branch_id, sku, change_qty, movement_type, notes,
                        created_by_user_id, reference_type, before_qty, after_qty)
                       VALUES (%s, %s, %s, %s, %s, %s, 'SALE', %s, %s)""",
                    (bid, sku, 0 if is_refill else -qty, movement_type, notes,
                     session.get("user_id"), before_qty, after_qty),
                )
                cur.close()
            notify_admin_and_branch(
                bid, ["inventory", "sales", "movement_logs"])
            flash(f"{sale_type} recorded.", "success")
        except Exception:
            current_app.logger.exception(
                "record_sale failed for branch_id=%s sku=%s", bid, sku)
            flash("Couldn't record that sale — please try again.", "error")
        return redirect(url_for("branch.record_sale"))

    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, p.price, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND bi.stock_qty > 0 ORDER BY p.item_name""",
        (bid,),
    )
    recent_sales = query(
        """SELECT s.*, p.item_name, COALESCE(s.buyer_name, bu.username) AS buyer_username
           FROM sales s JOIN products p ON s.sku = p.sku
           LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 10""",
        (bid,),
    )
    employees = query(
        "SELECT user_id, username, role FROM users WHERE is_active = TRUE ORDER BY username"
    )
    return render_template(
        "branch/record_sale.html", inventory=inventory, recent_sales=recent_sales, employees=employees,
    )


# ---------------------------------------------------------------- sales history
@bp.route("/sales-history")
@branch_required
def sales_history():
    bid = _branch_id()
    sales = query(
        """SELECT s.*, p.item_name, p.variant FROM sales s JOIN products p ON s.sku = p.sku
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 200""",
        (bid,),
    )
    totals = query(
        """SELECT COALESCE(SUM(qty_sold),0) AS units, COALESCE(SUM(qty_sold*unit_price),0) AS revenue
           FROM sales WHERE branch_id = %s""",
        (bid,), fetchone=True,
    )
    return render_template("branch/sales_history.html", sales=sales, totals=totals)


# ---------------------------------------------------------------- reports
@bp.route("/reports")
@branch_required
def reports():
    report_types = [
        {"key": key, "label": meta.get(
            "branch_label", meta["label"]), "windowed": meta["windowed"]}
        for key, meta in REPORT_TYPES.items() if meta["branch"]
    ]
    return render_template("branch/reports.html", report_types=report_types, unit_choices=PRODUCT_UNITS)


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

    stamp = datetime.date.today().isoformat()
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

    by_variant = query(
        """SELECT p.variant, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM products p
           LEFT JOIN sales s ON p.sku = s.sku AND s.branch_id = %s
           GROUP BY p.variant""",
        (bid,),
    )

    # Daily units sold for the last 14 days — the branch-scoped stand-in
    # for the admin page's "units sold by branch" chart, which doesn't
    # make sense when there's only one branch to look at.
    sales_trend = query(
        """SELECT DATE(sold_at) AS day, COALESCE(SUM(qty_sold), 0) AS units_sold
           FROM sales
           WHERE branch_id = %s AND sold_at >= NOW() - INTERVAL 14 DAY
           GROUP BY DATE(sold_at) ORDER BY day""",
        (bid,),
    )

    top_products = query(
        """SELECT p.item_name, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM sales s JOIN products p ON s.sku = p.sku
           WHERE s.branch_id = %s
           GROUP BY p.sku, p.item_name
           ORDER BY units_sold DESC LIMIT 8""",
        (bid,),
    )

    stock_by_variant = query(
        """SELECT p.variant, COALESCE(SUM(bi.stock_qty), 0) AS total_stock
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s
           GROUP BY p.variant""",
        (bid,),
    )

    granularity = request.args.get("granularity", "daily")
    trend_bucket = _TREND_GRANULARITIES.get(
        granularity, _TREND_GRANULARITIES["daily"])
    movement_trend = query(
        f"""SELECT {trend_bucket['trunc']} AS day, movement_type, SUM(ABS(change_qty)) AS total
            FROM stock_movement_logs
            WHERE branch_id = %s AND created_at >= NOW() - {trend_bucket['window']}
            GROUP BY {trend_bucket['trunc']}, movement_type ORDER BY day""",
        (bid,),
    )

    # Cash sales vs. employee purchases deducted from salary, and plain
    # sales vs. refills — both scoped to this branch only.
    payment_breakdown = query(
        """SELECT payment_method, COALESCE(SUM(qty_sold), 0) AS units_sold,
                  COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales WHERE branch_id = %s GROUP BY payment_method""",
        (bid,),
    )
    sale_type_breakdown = query(
        """SELECT sale_type, COALESCE(SUM(qty_sold), 0) AS units_sold,
                  COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales WHERE branch_id = %s GROUP BY sale_type""",
        (bid,),
    )

    totals = query(
        """SELECT COALESCE(SUM(qty_sold), 0) AS units, COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales WHERE branch_id = %s""",
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
