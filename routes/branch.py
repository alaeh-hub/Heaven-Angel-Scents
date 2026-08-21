from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from db import execute, query, transaction
from decorators import branch_required
from utils import ValidationError, parse_non_negative_int, parse_positive_int

bp = Blueprint("branch", __name__, url_prefix="/branch")


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
           WHERE bi.branch_id = %s AND p.is_active = TRUE ORDER BY p.item_name""",
        (bid,),
    )
    low_stock = [row for row in inventory if row["stock_qty"] <= row["reorder_level"]]

    pending_requests = query(
        """SELECT sr.request_id, p.item_name, sr.requested_qty, sr.dispatched_qty, sr.status, sr.requested_at
           FROM stock_requests sr JOIN products p ON sr.sku = p.sku
           WHERE sr.branch_id = %s AND sr.status IN ('Pending', 'In Transit')
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
        """SELECT p.sku, p.item_name, p.variant, p.price AS base_price,
                  COALESCE(bi.branch_price, p.price) AS price, bi.branch_price,
                  bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND p.is_active = TRUE ORDER BY p.item_name""",
        (bid,),
    )
    return render_template("branch/inventory.html", rows=rows)


# ---------------------------------------------------------------- request stock
@bp.route("/request-stock", methods=["GET", "POST"])
@branch_required
def request_stock():
    bid = _branch_id()
    if request.method == "POST":
        sku = request.form.get("sku")
        try:
            qty = parse_positive_int(request.form.get("requested_qty"), "Requested quantity")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("branch.request_stock"))

        if not sku:
            flash("Select a product to request.", "error")
        else:
            execute(
                "INSERT INTO stock_requests (branch_id, sku, requested_qty) VALUES (%s, %s, %s)",
                (bid, sku, qty),
            )
            flash("Stock request sent to HQ.", "success")
        return redirect(url_for("branch.request_stock"))

    products_list = query("SELECT sku, item_name, variant FROM products WHERE is_active = TRUE ORDER BY item_name")
    history = query(
        """SELECT sr.*, p.item_name FROM stock_requests sr JOIN products p ON sr.sku = p.sku
           WHERE sr.branch_id = %s ORDER BY sr.requested_at DESC LIMIT 25""",
        (bid,),
    )
    return render_template("branch/request_stock.html", products=products_list, history=history)


# ---------------------------------------------------------------- receive stock
@bp.route("/receive-stock", methods=["GET", "POST"])
@branch_required
def receive_stock():
    bid = _branch_id()
    if request.method == "POST":
        request_id = request.form.get("request_id")
        try:
            received_qty = parse_non_negative_int(request.form.get("received_qty"), "Received quantity")
            damaged_qty = parse_non_negative_int(request.form.get("damaged_qty"), "Damaged quantity")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("branch.receive_stock"))

        shortfall = 0
        try:
            with transaction() as conn:
                cur = conn.cursor(dictionary=True)
                # Lock this request row for the duration of the transaction
                # so a shipment can't be confirmed twice in parallel.
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

                dispatched = req["dispatched_qty"] or 0
                if received_qty + damaged_qty > dispatched:
                    cur.close()
                    flash("Received + damaged can't exceed the dispatched quantity.", "error")
                    return redirect(url_for("branch.receive_stock"))

                cur.execute(
                    """UPDATE stock_requests SET status = 'Fulfilled', received_qty = %s, damaged_qty = %s
                       WHERE request_id = %s""",
                    (received_qty, damaged_qty, request_id),
                )

                if received_qty > 0:
                    cur.execute(
                        """INSERT INTO branch_inventory (branch_id, sku, stock_qty)
                           VALUES (%s, %s, %s)
                           ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
                        (bid, req["sku"], received_qty),
                    )
                    cur.execute(
                        "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s",
                        (bid, req["sku"]),
                    )
                    after_qty = cur.fetchone()["stock_qty"]
                    before_qty = after_qty - received_qty
                    cur.execute(
                        """INSERT INTO stock_movement_logs
                           (branch_id, sku, change_qty, movement_type, notes,
                            created_by_user_id, reference_type, reference_id, before_qty, after_qty)
                           VALUES (%s, %s, %s, 'RECEIPT', %s, %s, 'STOCK_REQUEST', %s, %s, %s)""",
                        (bid, req["sku"], received_qty, f"Receipt for request #{request_id}",
                         session.get("user_id"), request_id, before_qty, after_qty),
                    )
                if damaged_qty > 0:
                    cur.execute(
                        """INSERT INTO stock_movement_logs
                           (branch_id, sku, change_qty, movement_type, notes,
                            created_by_user_id, reference_type, reference_id)
                           VALUES (%s, %s, 0, 'DAMAGE', %s, %s, 'STOCK_REQUEST', %s)""",
                        (bid, req["sku"], f"{damaged_qty} unit(s) damaged in transit, request #{request_id}",
                         session.get("user_id"), request_id),
                    )

                shortfall = dispatched - received_qty - damaged_qty
                if shortfall > 0:
                    # This is the actual audit trail entry the Receive
                    # Shipment page promises HQ will see. Previously this
                    # was only a flash message that vanished after a few
                    # seconds — nothing was ever written to the ledger.
                    cur.execute(
                        """INSERT INTO stock_movement_logs
                           (branch_id, sku, change_qty, movement_type, notes,
                            created_by_user_id, reference_type, reference_id)
                           VALUES (%s, %s, 0, 'ADJUSTMENT', %s, %s, 'STOCK_REQUEST', %s)""",
                        (bid, req["sku"],
                         f"{shortfall} unit(s) dispatched but not received or reported damaged "
                         f"— request #{request_id}, flagged for HQ follow-up",
                         session.get("user_id"), request_id),
                    )
                cur.close()
        except Exception:
            current_app.logger.exception("receive_stock failed for request_id=%s", request_id)
            flash("Couldn't confirm this receipt — please try again.", "error")
            return redirect(url_for("branch.receive_stock"))

        if shortfall > 0:
            flash(f"Received recorded. Note: {shortfall} unit(s) unaccounted for — flagged in the ledger.", "warning")
        else:
            flash("Shipment receipt confirmed and inventory updated.", "success")
        return redirect(url_for("branch.receive_stock"))

    in_transit = query(
        """SELECT sr.*, p.item_name FROM stock_requests sr JOIN products p ON sr.sku = p.sku
           WHERE sr.branch_id = %s AND sr.status = 'In Transit' ORDER BY sr.requested_at""",
        (bid,),
    )
    return render_template("branch/receive_stock.html", in_transit=in_transit)


# ---------------------------------------------------------------- record sale
@bp.route("/record-sale", methods=["GET", "POST"])
@branch_required
def record_sale():
    bid = _branch_id()
    if request.method == "POST":
        sku = request.form.get("sku")
        try:
            qty = parse_positive_int(request.form.get("qty_sold"), "Quantity sold")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("branch.record_sale"))

        if not sku:
            flash("Select a product to sell.", "error")
            return redirect(url_for("branch.record_sale"))

        try:
            with transaction() as conn:
                cur = conn.cursor(dictionary=True)
                # Row-lock this branch/SKU for the rest of the transaction.
                # A second, concurrent sale of the same SKU has to wait
                # here until this one commits or rolls back, so two
                # cashiers can no longer both "see" the last unit as
                # available and both sell it.
                cur.execute(
                    """SELECT bi.stock_qty, COALESCE(bi.branch_price, p.price) AS price
                       FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
                       WHERE bi.branch_id = %s AND bi.sku = %s
                       FOR UPDATE""",
                    (bid, sku),
                )
                stock_row = cur.fetchone()
                if not stock_row:
                    cur.close()
                    flash("That product isn't stocked at this branch.", "error")
                    return redirect(url_for("branch.record_sale"))
                if stock_row["stock_qty"] < qty:
                    cur.close()
                    flash("Not enough stock on hand for that sale.", "error")
                    return redirect(url_for("branch.record_sale"))

                before_qty = stock_row["stock_qty"]
                after_qty = before_qty - qty

                cur.execute(
                    "INSERT INTO sales (branch_id, sku, qty_sold, unit_price) VALUES (%s, %s, %s, %s)",
                    (bid, sku, qty, stock_row["price"]),
                )
                cur.execute(
                    "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
                    (after_qty, bid, sku),
                )
                cur.execute(
                    """INSERT INTO stock_movement_logs
                       (branch_id, sku, change_qty, movement_type, notes,
                        created_by_user_id, reference_type, before_qty, after_qty)
                       VALUES (%s, %s, %s, 'SALE', 'Point-of-sale', %s, 'SALE', %s, %s)""",
                    (bid, sku, -qty, session.get("user_id"), before_qty, after_qty),
                )
                cur.close()
            flash("Sale recorded.", "success")
        except Exception:
            current_app.logger.exception("record_sale failed for branch_id=%s sku=%s", bid, sku)
            flash("Couldn't record that sale — please try again.", "error")
        return redirect(url_for("branch.record_sale"))

    inventory = query(
        """SELECT p.sku, p.item_name, p.variant,
                  COALESCE(bi.branch_price, p.price) AS price, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND p.is_active = TRUE AND bi.stock_qty > 0 ORDER BY p.item_name""",
        (bid,),
    )
    recent_sales = query(
        """SELECT s.*, p.item_name FROM sales s JOIN products p ON s.sku = p.sku
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 10""",
        (bid,),
    )
    return render_template("branch/record_sale.html", inventory=inventory, recent_sales=recent_sales)


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
