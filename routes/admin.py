from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from db import execute, query
from decorators import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

HQ_BRANCH_ID = 1


# ---------------------------------------------------------------- dashboard
@bp.route("/")
@admin_required
def dashboard():
    stats = {
        "sku_count": query("SELECT COUNT(*) c FROM products WHERE is_active = TRUE", fetchone=True)["c"],
        "branch_count": query("SELECT COUNT(*) c FROM branches WHERE is_hq = FALSE", fetchone=True)["c"],
        "pending_requests": query(
            "SELECT COUNT(*) c FROM stock_requests WHERE status = 'Pending'", fetchone=True
        )["c"],
        "low_stock_count": query(
            """SELECT COUNT(*) c FROM branch_inventory bi
               JOIN branches b ON bi.branch_id = b.branch_id
               WHERE b.is_hq = FALSE AND bi.stock_qty <= bi.reorder_level""",
            fetchone=True,
        )["c"],
    }

    low_stock = query(
        """SELECT b.branch_name, p.item_name, p.sku, bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi
           JOIN branches b ON bi.branch_id = b.branch_id
           JOIN products p ON bi.sku = p.sku
           WHERE b.is_hq = FALSE AND bi.stock_qty <= bi.reorder_level
           ORDER BY bi.stock_qty ASC LIMIT 8"""
    )

    recent_requests = query(
        """SELECT sr.request_id, b.branch_name, p.item_name, sr.requested_qty, sr.status, sr.requested_at
           FROM stock_requests sr
           JOIN branches b ON sr.branch_id = b.branch_id
           JOIN products p ON sr.sku = p.sku
           ORDER BY sr.requested_at DESC LIMIT 6"""
    )

    recent_activity = query(
        """SELECT sml.created_at, b.branch_name, p.item_name, sml.change_qty, sml.movement_type
           FROM stock_movement_logs sml
           JOIN branches b ON sml.branch_id = b.branch_id
           JOIN products p ON sml.sku = p.sku
           ORDER BY sml.created_at DESC LIMIT 8"""
    )

    return render_template(
        "admin/dashboard.html",
        stats=stats, low_stock=low_stock,
        recent_requests=recent_requests, recent_activity=recent_activity,
    )


# ---------------------------------------------------------------- products
@bp.route("/products", methods=["GET", "POST"])
@admin_required
def products():
    if request.method == "POST":
        sku = request.form.get("sku", "").strip().upper()
        item_name = request.form.get("item_name", "").strip()
        variant = request.form.get("variant")
        price = request.form.get("price", "0")

        if not sku or not item_name or variant not in ("Male", "Female", "Unisex"):
            flash("Please fill in every field with a valid value.", "error")
        else:
            try:
                execute(
                    "INSERT INTO products (sku, item_name, variant, price) VALUES (%s, %s, %s, %s)",
                    (sku, item_name, variant, price),
                )
                # Give every existing branch (and HQ) a zero-stock row so it shows up everywhere
                branches = query("SELECT branch_id FROM branches")
                for b in branches:
                    execute(
                        "INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, 0)",
                        (b["branch_id"], sku),
                    )
                flash(f"{item_name} ({sku}) added to the catalog.", "success")
            except Exception:
                flash(f"SKU '{sku}' already exists.", "error")
        return redirect(url_for("admin.products"))

    catalog = query(
        """SELECT p.*, COALESCE(SUM(bi.stock_qty), 0) AS total_stock
           FROM products p
           LEFT JOIN branch_inventory bi ON p.sku = bi.sku
           GROUP BY p.sku ORDER BY p.item_name"""
    )
    return render_template("admin/products.html", catalog=catalog)


@bp.route("/products/<sku>/toggle", methods=["POST"])
@admin_required
def toggle_product(sku):
    execute("UPDATE products SET is_active = NOT is_active WHERE sku = %s", (sku,))
    flash("Product status updated.", "success")
    return redirect(url_for("admin.products"))


# ---------------------------------------------------------------- production
@bp.route("/production", methods=["GET", "POST"])
@admin_required
def production():
    if request.method == "POST":
        sku = request.form.get("sku")
        qty = int(request.form.get("qty_produced", 0))
        batch_code = request.form.get("batch_code", "").strip() or None

        if qty <= 0:
            flash("Quantity produced must be greater than zero.", "error")
        else:
            execute(
                "INSERT INTO production_logs (sku, batch_code, qty_produced) VALUES (%s, %s, %s)",
                (sku, batch_code, qty),
            )
            execute(
                """INSERT INTO branch_inventory (branch_id, sku, stock_qty)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
                (HQ_BRANCH_ID, sku, qty),
            )
            execute(
                """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes)
                   VALUES (%s, %s, %s, 'PRODUCTION', %s)""",
                (HQ_BRANCH_ID, sku, qty, f"Batch {batch_code}" if batch_code else "Production run"),
            )
            flash(f"Logged {qty} units produced and added to HQ warehouse stock.", "success")
        return redirect(url_for("admin.production"))

    products_list = query("SELECT sku, item_name FROM products WHERE is_active = TRUE ORDER BY item_name")
    logs = query(
        """SELECT pl.*, p.item_name FROM production_logs pl
           JOIN products p ON pl.sku = p.sku
           ORDER BY pl.produced_at DESC LIMIT 40"""
    )
    hq_stock = query(
        """SELECT p.sku, p.item_name, p.variant, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (HQ_BRANCH_ID,),
    )
    return render_template("admin/production.html", products=products_list, logs=logs, hq_stock=hq_stock)


# ---------------------------------------------------------------- branches
@bp.route("/branches", methods=["GET", "POST"])
@admin_required
def branches():
    if request.method == "POST":
        name = request.form.get("branch_name", "").strip()
        location = request.form.get("location", "").strip()
        if not name:
            flash("Branch name is required.", "error")
        else:
            try:
                bid, _ = execute(
                    "INSERT INTO branches (branch_name, location) VALUES (%s, %s)", (name, location)
                )
                products_list = query("SELECT sku FROM products")
                for p in products_list:
                    execute(
                        "INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, 0)",
                        (bid, p["sku"]),
                    )
                flash(f"{name} added.", "success")
            except Exception:
                flash("A branch with that name already exists.", "error")
        return redirect(url_for("admin.branches"))

    branch_list = query(
        """SELECT b.*, COUNT(DISTINCT u.user_id) AS user_count
           FROM branches b LEFT JOIN users u ON b.branch_id = u.branch_id
           WHERE b.is_hq = FALSE GROUP BY b.branch_id ORDER BY b.branch_name"""
    )
    return render_template("admin/branches.html", branch_list=branch_list)


# ---------------------------------------------------------------- user accounts
@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role")
        branch_id = request.form.get("branch_id") or None

        if role == "Branch" and not branch_id:
            flash("Select a branch for this Branch account.", "error")
        elif not username or len(password) < 8:
            flash("Username is required and password must be at least 8 characters.", "error")
        else:
            try:
                execute(
                    """INSERT INTO users (username, password_hash, role, branch_id)
                       VALUES (%s, %s, %s, %s)""",
                    (username, generate_password_hash(password), role, branch_id if role == "Branch" else None),
                )
                flash(f"Account '{username}' created.", "success")
            except Exception:
                flash(f"Username '{username}' is already taken.", "error")
        return redirect(url_for("admin.users"))

    accounts = query(
        """SELECT u.user_id, u.username, u.role, u.is_active, u.created_at, b.branch_name
           FROM users u LEFT JOIN branches b ON u.branch_id = b.branch_id
           ORDER BY u.role, b.branch_name"""
    )
    branch_list = query("SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")
    return render_template("admin/users.html", accounts=accounts, branch_list=branch_list)


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    if user_id == session.get("user_id"):
        flash("You can't deactivate your own account.", "error")
    else:
        execute("UPDATE users SET is_active = NOT is_active WHERE user_id = %s", (user_id,))
        flash("Account status updated.", "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------- stock requests
@bp.route("/requests")
@admin_required
def requests_list():
    status_filter = request.args.get("status", "all")
    sql = """SELECT sr.*, b.branch_name, p.item_name, p.variant
              FROM stock_requests sr
              JOIN branches b ON sr.branch_id = b.branch_id
              JOIN products p ON sr.sku = p.sku"""
    params = ()
    if status_filter != "all":
        sql += " WHERE sr.status = %s"
        params = (status_filter,)
    sql += " ORDER BY FIELD(sr.status,'Pending','In Transit','Fulfilled','Rejected'), sr.requested_at DESC"
    stock_requests = query(sql, params)
    return render_template("admin/requests.html", stock_requests=stock_requests, status_filter=status_filter)


@bp.route("/requests/<int:request_id>/dispatch", methods=["POST"])
@admin_required
def dispatch_request(request_id):
    req = query("SELECT * FROM stock_requests WHERE request_id = %s", (request_id,), fetchone=True)
    if not req or req["status"] != "Pending":
        flash("This request can no longer be dispatched.", "error")
        return redirect(url_for("admin.requests_list"))

    dispatched_qty = int(request.form.get("dispatched_qty", req["requested_qty"]))
    hq_row = query(
        "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s",
        (HQ_BRANCH_ID, req["sku"]), fetchone=True,
    )
    if not hq_row or hq_row["stock_qty"] < dispatched_qty:
        flash("Not enough HQ warehouse stock to dispatch that quantity.", "error")
        return redirect(url_for("admin.requests_list"))

    execute(
        "UPDATE branch_inventory SET stock_qty = stock_qty - %s WHERE branch_id = %s AND sku = %s",
        (dispatched_qty, HQ_BRANCH_ID, req["sku"]),
    )
    execute(
        "UPDATE stock_requests SET status = 'In Transit', dispatched_qty = %s WHERE request_id = %s",
        (dispatched_qty, request_id),
    )
    execute(
        """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes)
           VALUES (%s, %s, %s, 'DISPATCH', %s)""",
        (HQ_BRANCH_ID, req["sku"], -dispatched_qty, f"Dispatched to request #{request_id}"),
    )
    flash(f"Dispatched {dispatched_qty} units — now in transit to the branch.", "success")
    return redirect(url_for("admin.requests_list"))


@bp.route("/requests/<int:request_id>/reject", methods=["POST"])
@admin_required
def reject_request(request_id):
    execute(
        "UPDATE stock_requests SET status = 'Rejected' WHERE request_id = %s AND status = 'Pending'",
        (request_id,),
    )
    flash("Request rejected.", "success")
    return redirect(url_for("admin.requests_list"))


# ---------------------------------------------------------------- ledger
@bp.route("/movement-logs")
@admin_required
def movement_logs():
    branch_filter = request.args.get("branch_id", "all")
    sql = """SELECT sml.*, b.branch_name, p.item_name
              FROM stock_movement_logs sml
              JOIN branches b ON sml.branch_id = b.branch_id
              JOIN products p ON sml.sku = p.sku"""
    params = ()
    if branch_filter != "all":
        sql += " WHERE sml.branch_id = %s"
        params = (branch_filter,)
    sql += " ORDER BY sml.created_at DESC LIMIT 200"
    logs = query(sql, params)
    branch_list = query("SELECT branch_id, branch_name FROM branches ORDER BY branch_name")
    return render_template("admin/movement_logs.html", logs=logs, branch_list=branch_list, branch_filter=branch_filter)


# ---------------------------------------------------------------- reports
@bp.route("/reports")
@admin_required
def reports():
    return render_template("admin/reports.html")


@bp.route("/api/reports-data")
@admin_required
def reports_data():
    by_variant = query(
        """SELECT p.variant, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM products p LEFT JOIN sales s ON p.sku = s.sku
           GROUP BY p.variant"""
    )
    by_branch = query(
        """SELECT b.branch_name, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM branches b LEFT JOIN sales s ON b.branch_id = s.branch_id
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )
    movement_trend = query(
        """SELECT DATE(created_at) AS day, movement_type, SUM(ABS(change_qty)) AS total
           FROM stock_movement_logs
           WHERE created_at >= NOW() - INTERVAL 14 DAY
           GROUP BY DATE(created_at), movement_type ORDER BY day"""
    )
    stock_by_branch = query(
        """SELECT b.branch_name, SUM(bi.stock_qty) AS total_stock
           FROM branch_inventory bi JOIN branches b ON bi.branch_id = b.branch_id
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )

    for row in movement_trend:
        row["day"] = row["day"].isoformat()

    return jsonify(
        by_variant=by_variant, by_branch=by_branch,
        movement_trend=movement_trend, stock_by_branch=stock_by_branch,
    )
