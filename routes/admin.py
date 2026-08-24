import datetime

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, send_file, session, url_for,
)
from werkzeug.security import generate_password_hash

from db import execute, query, transaction
from decorators import admin_required
from audit import log_action
from receipts import build_receipt_pdf
from reports import REPORT_TYPES, get_report, parse_report_filters, render_report_excel, render_report_pdf
from sockets import notify_admin, notify_admin_and_branch, notify_all, notify_bell
from utils import ValidationError, generate_temp_password, parse_non_negative_decimal, parse_positive_int

bp = Blueprint("admin", __name__, url_prefix="/admin")

HQ_BRANCH_ID = 1

# Bucket-size options for the "Ledger movement" trend chart on the
# Reports page (see reports_data() below). Every `trunc` expression is
# wrapped so it evaluates to an actual DATE, not a formatted string —
# DATE_FORMAT() on its own returns text, and wrapping it back in DATE()
# keeps all four options returning the same Python type (datetime.date)
# so reports_data() can call .isoformat() on the result the same way
# regardless of which option was picked, with no extra branching.
_TREND_GRANULARITIES = {
    "daily": {
        "trunc": "DATE(created_at)",
        "window": "INTERVAL 14 DAY",
    },
    "weekly": {
        # WEEKDAY() is 0 for Monday, so this rounds each timestamp back
        # to the Monday that starts its week.
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

    # All-time, across every branch — qty_sold/unit_price on `sales`
    # already carries everything this needs, no extra table required.
    top_sellers = query(
        """SELECT p.sku, p.item_name, p.variant,
                  SUM(s.qty_sold) AS total_units,
                  SUM(s.qty_sold * s.unit_price) AS total_revenue
           FROM sales s
           JOIN products p ON p.sku = s.sku
           GROUP BY p.sku, p.item_name, p.variant
           ORDER BY total_units DESC LIMIT 3"""
    )

    return render_template(
        "admin/dashboard.html",
        stats=stats, low_stock=low_stock,
        recent_requests=recent_requests, recent_activity=recent_activity,
        top_sellers=top_sellers,
    )


# ---------------------------------------------------------------- products
@bp.route("/products", methods=["GET", "POST"])
@admin_required
def products():
    if request.method == "POST":
        sku = request.form.get("sku", "").strip().upper()
        item_name = request.form.get("item_name", "").strip()
        variant = request.form.get("variant")

        try:
            price = parse_non_negative_decimal(request.form.get("price"), "Price")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.products"))

        if not sku or not item_name or variant not in ("Male", "Female", "Unisex"):
            flash("Please fill in every field with a valid value.", "error")
        else:
            try:
                with transaction() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO products (sku, item_name, variant, price) VALUES (%s, %s, %s, %s)",
                        (sku, item_name, variant, price),
                    )
                    # Give every existing branch (and HQ) a zero-stock row so
                    # it shows up everywhere. branch_price is left NULL,
                    # meaning the branch uses this HQ price until an admin
                    # sets an override on the Branch Stock page. Both writes
                    # happen in one transaction so a crash partway through
                    # can't leave the product created with no inventory rows.
                    cur.execute("SELECT branch_id FROM branches")
                    branch_ids = [row[0] for row in cur.fetchall()]
                    for branch_id in branch_ids:
                        cur.execute(
                            "INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, 0)",
                            (branch_id, sku),
                        )
                    cur.close()
                notify_all(["products", "inventory"])
                log_action("add_product", target=sku, details=f"{item_name} ({variant}) — ₱{price:,.2f}")
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
    product = query("SELECT item_name, is_active FROM products WHERE sku = %s", (sku,), fetchone=True)
    execute("UPDATE products SET is_active = NOT is_active WHERE sku = %s", (sku,))
    notify_all(["products", "inventory"])
    if product:
        new_status = "Discontinued" if product["is_active"] else "Reactivated"
        log_action("toggle_product", target=sku, details=f"{product['item_name']} — {new_status}")
        if new_status == "Discontinued":
            notify_bell(f"{product['item_name']} ({sku}) was discontinued.", level="warning")
        else:
            notify_bell(f"{product['item_name']} ({sku}) is available again.", level="success")
    flash("Product status updated.", "success")
    return redirect(url_for("admin.products"))


# ---------------------------------------------------------------- production
@bp.route("/production", methods=["GET", "POST"])
@admin_required
def production():
    if request.method == "POST":
        sku = request.form.get("sku")
        batch_code = request.form.get("batch_code", "").strip() or None
        try:
            qty = parse_positive_int(request.form.get("qty_produced"), "Units produced")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.production"))

        try:
            with transaction() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO production_logs (sku, batch_code, qty_produced) VALUES (%s, %s, %s)",
                    (sku, batch_code, qty),
                )
                cur.execute(
                    """INSERT INTO branch_inventory (branch_id, sku, stock_qty)
                       VALUES (%s, %s, %s)
                       ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
                    (HQ_BRANCH_ID, sku, qty),
                )
                cur.execute(
                    "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s",
                    (HQ_BRANCH_ID, sku),
                )
                row = cur.fetchone()
                after_qty = row[0] if row else None
                before_qty = (after_qty - qty) if after_qty is not None else None
                cur.execute(
                    """INSERT INTO stock_movement_logs
                       (branch_id, sku, change_qty, movement_type, notes,
                        created_by_user_id, reference_type, before_qty, after_qty)
                       VALUES (%s, %s, %s, 'PRODUCTION', %s, %s, 'PRODUCTION_LOG', %s, %s)""",
                    (HQ_BRANCH_ID, sku, qty, f"Batch {batch_code}" if batch_code else "Production run",
                     session.get("user_id"), before_qty, after_qty),
                )
                cur.close()
            notify_admin(["production", "inventory", "movement_logs"])
            flash(f"Logged {qty} units produced and added to HQ warehouse stock.", "success")
        except Exception:
            current_app.logger.exception("production logging failed for sku=%s", sku)
            flash("Couldn't log this production run — please try again.", "error")
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
                with transaction() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO branches (branch_name, location) VALUES (%s, %s)", (name, location)
                    )
                    new_branch_id = cur.lastrowid
                    cur.execute("SELECT sku FROM products")
                    skus = [row[0] for row in cur.fetchall()]
                    for sku in skus:
                        cur.execute(
                            "INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, 0)",
                            (new_branch_id, sku),
                        )
                    cur.close()
                notify_admin("branches")
                log_action("add_branch", target=name, details=location or None)
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


# ---------------------------------------------------------------- branch stock & pricing
@bp.route("/branch-stock")
@admin_required
def branch_stock():
    branch_filter = request.args.get("branch_id", "all")
    branch_list = query("SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")

    rows = []
    if branch_filter != "all":
        rows = query(
            """SELECT b.branch_id, b.branch_name, p.sku, p.item_name, p.variant,
                      p.price AS base_price, bi.branch_price,
                      COALESCE(bi.branch_price, p.price) AS effective_price,
                      bi.stock_qty, bi.reorder_level
               FROM branch_inventory bi
               JOIN branches b ON bi.branch_id = b.branch_id
               JOIN products p ON bi.sku = p.sku
               WHERE b.is_hq = FALSE AND p.is_active = TRUE AND b.branch_id = %s
               ORDER BY p.item_name""",
            (branch_filter,),
        )

    totals = query(
        """SELECT b.branch_id, b.branch_name, COALESCE(SUM(bi.stock_qty), 0) AS total_stock
           FROM branches b
           LEFT JOIN branch_inventory bi ON b.branch_id = bi.branch_id
           LEFT JOIN products p ON bi.sku = p.sku AND p.is_active = TRUE
           WHERE b.is_hq = FALSE
           GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )

    return render_template(
        "admin/branch_stock.html",
        rows=rows, branch_list=branch_list, branch_filter=branch_filter, totals=totals,
    )


@bp.route("/branch-stock/price", methods=["POST"])
@admin_required
def update_branch_price():
    branch_id = request.form.get("branch_id")
    sku = request.form.get("sku")
    raw_price = request.form.get("branch_price", "").strip()

    if raw_price == "":
        execute(
            "UPDATE branch_inventory SET branch_price = NULL WHERE branch_id = %s AND sku = %s",
            (branch_id, sku),
        )
        notify_admin_and_branch(branch_id, "inventory")
        flash("Branch price cleared — this branch now uses the HQ price.", "success")
    else:
        try:
            new_price = parse_non_negative_decimal(raw_price, "Branch price")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.branch_stock", branch_id=branch_id))

        execute(
            "UPDATE branch_inventory SET branch_price = %s WHERE branch_id = %s AND sku = %s",
            (new_price, branch_id, sku),
        )
        notify_admin_and_branch(branch_id, "inventory")
        flash("Branch price updated.", "success")

    return redirect(url_for("admin.branch_stock", branch_id=branch_id))


# ---------------------------------------------------------------- user accounts
@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role")
        branch_id = request.form.get("branch_id") or None

        # For a Branch account, branch_id must be both present AND
        # actually point at a real, non-HQ branch. Without the second
        # check, a hand-crafted POST (bypassing the dropdown, which only
        # ever lists non-HQ branches) could set branch_id=1 — HQ's own
        # branch row — and create a "Branch" account that sees and can
        # sell against the HQ warehouse through the ordinary branch UI,
        # which every branch-side query trusts blindly via session.
        valid_branch = None
        if role == "Branch" and branch_id:
            valid_branch = query(
                "SELECT branch_id FROM branches WHERE branch_id = %s AND is_hq = FALSE",
                (branch_id,), fetchone=True,
            )

        if role not in ("Admin", "Branch"):
            flash("Select a valid role.", "error")
        elif role == "Branch" and not branch_id:
            flash("Select a branch for this Branch account.", "error")
        elif role == "Branch" and not valid_branch:
            flash("Select a valid branch.", "error")
        elif not username or len(password) < 8:
            flash("Username is required and password must be at least 8 characters.", "error")
        else:
            try:
                execute(
                    """INSERT INTO users (username, password_hash, role, branch_id, must_change_password)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (username, generate_password_hash(password), role, branch_id if role == "Branch" else None),
                )
                notify_admin("users")
                branch_detail = f", branch_id={branch_id}" if role == "Branch" else ""
                log_action("create_account", target=username, details=f"role={role}{branch_detail}")
                flash(f"Account '{username}' created. They'll be asked to set a new password at first login.", "success")
            except Exception:
                flash(f"Username '{username}' is already taken.", "error")
        return redirect(url_for("admin.users"))

    accounts = query(
        """SELECT u.user_id, u.username, u.role, u.is_active, u.created_at, b.branch_name
           FROM users u LEFT JOIN branches b ON u.branch_id = b.branch_id
           ORDER BY u.role, b.branch_name"""
    )
    branch_list = query("SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")
    # pop(), not get() — this must only ever be readable once. If it's
    # left in the session, refreshing this page (or hitting back/forward)
    # would keep re-showing a temporary password that may already have
    # been handed to the staff member and could be stale or reused.
    temp_password_reveal = session.pop("temp_password_reveal", None)
    return render_template(
        "admin/users.html", accounts=accounts, branch_list=branch_list,
        temp_password_reveal=temp_password_reveal,
    )


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    if user_id == session.get("user_id"):
        flash("You can't deactivate your own account.", "error")
    else:
        target = query("SELECT username, is_active FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        execute("UPDATE users SET is_active = NOT is_active WHERE user_id = %s", (user_id,))
        notify_admin("users")
        if target:
            new_status = "Deactivated" if target["is_active"] else "Reactivated"
            log_action("toggle_user", target=target["username"], details=new_status)
        flash("Account status updated.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_user_password(user_id):
    target = query("SELECT username FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    if not target:
        flash("Account not found.", "error")
        return redirect(url_for("admin.users"))

    temp_password = generate_temp_password()
    execute(
        "UPDATE users SET password_hash = %s, must_change_password = TRUE WHERE user_id = %s",
        (generate_password_hash(temp_password), user_id),
    )
    # Hand the plaintext password to the one-shot reveal modal in
    # users.html rather than putting it in a flash message. Flash
    # messages render as plain banner text mixed in with every other
    # message on the page — this way it only ever appears once, inside
    # the dedicated modal that already exists for it (copy button,
    # "won't be shown again" framing), and users() pops it from the
    # session immediately after reading it so a page refresh or back
    # button can't bring it back.
    session["temp_password_reveal"] = {"username": target["username"], "password": temp_password}
    log_action("reset_password", target=target["username"])
    flash(
        f"Password for '{target['username']}' has been reset. "
        "They'll be asked to set a new password the next time they sign in.",
        "success",
    )
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
    raw_qty = request.form.get("dispatched_qty")
    try:
        dispatched_qty = parse_positive_int(raw_qty, "Dispatched quantity") if raw_qty not in (None, "") else None
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.requests_list"))

    qty_to_dispatch = None
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            # Lock both the request and the HQ inventory row for the rest
            # of this transaction, so two admins dispatching at the same
            # moment can't both pass the "enough HQ stock?" check and
            # jointly over-dispatch below zero.
            cur.execute("SELECT * FROM stock_requests WHERE request_id = %s FOR UPDATE", (request_id,))
            req = cur.fetchone()
            if not req or req["status"] != "Pending":
                cur.close()
                flash("This request can no longer be dispatched.", "error")
                return redirect(url_for("admin.requests_list"))

            qty_to_dispatch = dispatched_qty if dispatched_qty is not None else req["requested_qty"]

            cur.execute(
                "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE",
                (HQ_BRANCH_ID, req["sku"]),
            )
            hq_row = cur.fetchone()
            if not hq_row or hq_row["stock_qty"] < qty_to_dispatch:
                cur.close()
                flash("Not enough HQ warehouse stock to dispatch that quantity.", "error")
                return redirect(url_for("admin.requests_list"))

            before_qty = hq_row["stock_qty"]
            after_qty = before_qty - qty_to_dispatch

            cur.execute(
                "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
                (after_qty, HQ_BRANCH_ID, req["sku"]),
            )
            cur.execute(
                "UPDATE stock_requests SET status = 'In Transit', dispatched_qty = %s WHERE request_id = %s",
                (qty_to_dispatch, request_id),
            )
            cur.execute(
                """INSERT INTO stock_movement_logs
                   (branch_id, sku, change_qty, movement_type, notes,
                    created_by_user_id, reference_type, reference_id, before_qty, after_qty)
                   VALUES (%s, %s, %s, 'DISPATCH', %s, %s, 'STOCK_REQUEST', %s, %s, %s)""",
                (HQ_BRANCH_ID, req["sku"], -qty_to_dispatch, f"Dispatched to request #{request_id}",
                 session.get("user_id"), request_id, before_qty, after_qty),
            )
            cur.close()
        notify_admin_and_branch(req["branch_id"], ["requests", "inventory", "movement_logs"])
        flash(f"Dispatched {qty_to_dispatch} units — now in transit to the branch.", "success")
    except Exception:
        current_app.logger.exception("dispatch_request failed for request_id=%s", request_id)
        flash("Couldn't dispatch this request — please try again.", "error")
    return redirect(url_for("admin.requests_list"))


@bp.route("/requests/<int:request_id>/receipt")
@admin_required
def request_receipt(request_id):
    """Downloadable PDF for any branch's Fulfilled request — see receipts.py.

    No branch_id scoping here since HQ Admin can see every branch.
    """
    pdf_buffer, req = build_receipt_pdf(request_id)
    if pdf_buffer is None:
        abort(404)
    return send_file(
        pdf_buffer, mimetype="application/pdf",
        as_attachment=True, download_name=f"GR-{request_id:06d}.pdf",
    )


@bp.route("/requests/<int:request_id>/reject", methods=["POST"])
@admin_required
def reject_request(request_id):
    req = query("SELECT branch_id FROM stock_requests WHERE request_id = %s", (request_id,), fetchone=True)
    execute(
        "UPDATE stock_requests SET status = 'Rejected' WHERE request_id = %s AND status = 'Pending'",
        (request_id,),
    )
    if req:
        notify_admin_and_branch(req["branch_id"], "requests")
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


# ---------------------------------------------------------------- audit log
@bp.route("/audit-log")
@admin_required
def audit_log():
    """Non-inventory admin activity — account/product/branch changes.

    Separate from the Movement Ledger above on purpose: that ledger is
    strictly stock quantity events (production/dispatch/receipt/sale/
    damage), while this covers everything else an admin can do that
    isn't a stock movement. See audit.py for how entries get written.
    """
    logs = query(
        """SELECT action_id, actor_username, action, target, details, created_at
           FROM admin_actions
           ORDER BY created_at DESC LIMIT 300"""
    )
    return render_template("admin/audit_log.html", logs=logs)


# ---------------------------------------------------------------- reports
@bp.route("/reports")
@admin_required
def reports():
    branch_list = query("SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")
    report_types = [
        {"key": key, "label": meta["label"], "windowed": meta["windowed"]}
        for key, meta in REPORT_TYPES.items() if meta["admin"]
    ]
    return render_template("admin/reports.html", branch_list=branch_list, report_types=report_types)


@bp.route("/reports/generate")
@admin_required
def generate_report():
    """Download a filtered report as PDF or Excel — see reports.py.

    Any admin can pull any branch's data here (or "all branches"), same
    as every other admin page; there's no branch scoping to apply.
    """
    report_type = request.args.get("type", "")
    fmt = request.args.get("format", "pdf")
    meta = REPORT_TYPES.get(report_type)
    if meta is None or not meta["admin"] or fmt not in ("pdf", "xlsx"):
        abort(404)

    filters = parse_report_filters(request.args)
    report = get_report(report_type, filters, branch_scope=None, actor_label=f"HQ Admin — {session.get('username')}")

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
@admin_required
def reports_data():
    by_variant = query(
        """SELECT p.variant, COALESCE(SUM(s.qty_sold), 0) AS units_sold
           FROM products p LEFT JOIN sales s ON p.sku = s.sku
           GROUP BY p.variant"""
    )

    # Revenue is a straight SUM of qty_sold * unit_price per sale row. Because
    # unit_price was captured at the moment each sale happened (the branch's
    # effective price at that time), this stays accurate even though branches
    # charge different prices from HQ and from each other.
    by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(s.qty_sold), 0) AS units_sold,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b LEFT JOIN sales s ON b.branch_id = s.branch_id
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )

    # Average discount/markup each branch is currently running vs. the HQ price,
    # based on active SKUs that have a branch_price override set.
    branch_pricing = query(
        """SELECT b.branch_name,
                  AVG(CASE WHEN bi.branch_price IS NOT NULL AND p.price > 0
                           THEN (p.price - bi.branch_price) / p.price * 100
                           ELSE 0 END) AS avg_discount_pct
           FROM branches b
           JOIN branch_inventory bi ON b.branch_id = bi.branch_id
           JOIN products p ON bi.sku = p.sku
           WHERE b.is_hq = FALSE AND p.is_active = TRUE
           GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )

    # Bucket size for the "Ledger movement" trend chart. Same allow-list
    # pattern reports.py uses elsewhere: the value picked here is never
    # user text going into SQL, it's a key into a fixed dict, and only
    # the fixed SQL fragment that key maps to gets interpolated — so
    # this stays just as injection-safe as a plain %s placeholder would,
    # while letting the WHERE/GROUP BY expressions differ per option
    # (parameterizing a GROUP BY expression itself isn't possible with
    # a plain %s placeholder).
    granularity = request.args.get("granularity", "daily")
    trend_bucket = _TREND_GRANULARITIES.get(granularity, _TREND_GRANULARITIES["daily"])

    movement_trend = query(
        f"""SELECT {trend_bucket['trunc']} AS day, movement_type, SUM(ABS(change_qty)) AS total
            FROM stock_movement_logs
            WHERE created_at >= NOW() - {trend_bucket['window']}
            GROUP BY {trend_bucket['trunc']}, movement_type ORDER BY day"""
    )
    stock_by_branch = query(
        """SELECT b.branch_name, SUM(bi.stock_qty) AS total_stock
           FROM branch_inventory bi JOIN branches b ON bi.branch_id = b.branch_id
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )

    # Combined totals across every branch, regardless of each branch's price.
    totals = query(
        """SELECT COALESCE(SUM(qty_sold), 0) AS units, COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales""",
        fetchone=True,
    )

    for row in movement_trend:
        row["day"] = row["day"].isoformat()

    return jsonify(
        by_variant=by_variant, by_branch=by_branch, branch_pricing=branch_pricing,
        movement_trend=movement_trend, stock_by_branch=stock_by_branch, totals=totals,
    )
