from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from db import execute, query
from decorators import branch_required

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
        """SELECT p.sku, p.item_name, p.variant, p.price, bi.stock_qty, bi.reorder_level
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
        qty = int(request.form.get("requested_qty", 0))
        if qty <= 0:
            flash("Requested quantity must be greater than zero.", "error")
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
        received_qty = int(request.form.get("received_qty", 0))
        damaged_qty = int(request.form.get("damaged_qty", 0))

        req = query(
            "SELECT * FROM stock_requests WHERE request_id = %s AND branch_id = %s AND status = 'In Transit'",
            (request_id, bid), fetchone=True,
        )
        if not req:
            flash("That shipment isn't awaiting receipt.", "error")
            return redirect(url_for("branch.receive_stock"))

        dispatched = req["dispatched_qty"] or 0
        if received_qty + damaged_qty > dispatched:
            flash("Received + damaged can't exceed the dispatched quantity.", "error")
            return redirect(url_for("branch.receive_stock"))

        execute(
            """UPDATE stock_requests SET status = 'Fulfilled', received_qty = %s, damaged_qty = %s
               WHERE request_id = %s""",
            (received_qty, damaged_qty, request_id),
        )
        if received_qty > 0:
            execute(
                """INSERT INTO branch_inventory (branch_id, sku, stock_qty)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
                (bid, req["sku"], received_qty),
            )
            execute(
                """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes)
                   VALUES (%s, %s, %s, 'RECEIPT', %s)""",
                (bid, req["sku"], received_qty, f"Receipt for request #{request_id}"),
            )
        if damaged_qty > 0:
            execute(
                """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes)
                   VALUES (%s, %s, 0, 'DAMAGE', %s)""",
                (bid, req["sku"], f"{damaged_qty} unit(s) damaged in transit, request #{request_id}"),
            )
        shortfall = dispatched - received_qty - damaged_qty
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
        qty = int(request.form.get("qty_sold", 0))

        stock_row = query(
            "SELECT bi.stock_qty, p.price FROM branch_inventory bi JOIN products p ON bi.sku = p.sku "
            "WHERE bi.branch_id = %s AND bi.sku = %s",
            (bid, sku), fetchone=True,
        )
        if qty <= 0:
            flash("Quantity sold must be greater than zero.", "error")
        elif not stock_row or stock_row["stock_qty"] < qty:
            flash("Not enough stock on hand for that sale.", "error")
        else:
            execute(
                "INSERT INTO sales (branch_id, sku, qty_sold, unit_price) VALUES (%s, %s, %s, %s)",
                (bid, sku, qty, stock_row["price"]),
            )
            execute(
                "UPDATE branch_inventory SET stock_qty = stock_qty - %s WHERE branch_id = %s AND sku = %s",
                (qty, bid, sku),
            )
            execute(
                """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes)
                   VALUES (%s, %s, %s, 'SALE', 'Point-of-sale')""",
                (bid, sku, -qty),
            )
            flash("Sale recorded.", "success")
        return redirect(url_for("branch.record_sale"))

    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.price, bi.stock_qty
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
