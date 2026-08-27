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
from utils import (
    MATERIAL_UNITS, PAYMENT_METHODS, PRODUCT_UNITS, SALE_TYPES, ValidationError, build_sku,
    generate_temp_password, parse_base_code, parse_non_negative_decimal, parse_non_negative_int,
    parse_positive_decimal, parse_positive_int,
)

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
        "sku_count": query("SELECT COUNT(*) c FROM products", fetchone=True)["c"],
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
        """SELECT sr.request_id, sr.delivery_number, b.branch_name, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty
           FROM stock_requests sr
           JOIN branches b ON sr.branch_id = b.branch_id
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
           GROUP BY sr.request_id
           ORDER BY sr.requested_at DESC LIMIT 6"""
    )

    # Same exclusion as movement_logs() below: a delivery's own
    # DISPATCH/RECEIPT/DAMAGE/ADJUSTMENT rows (reference_type=
    # 'STOCK_REQUEST') are left off this preview too, so it doesn't show
    # one SKU out of a multi-item delivery as if that were the whole
    # story — that detail belongs on the delivery's own page/receipt.
    recent_activity = query(
        """SELECT sml.created_at, b.branch_name, p.item_name, sml.change_qty, sml.movement_type
           FROM stock_movement_logs sml
           JOIN branches b ON sml.branch_id = b.branch_id
           JOIN products p ON sml.sku = p.sku
           WHERE (sml.reference_type IS NULL OR sml.reference_type != 'STOCK_REQUEST')
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

    # Business-level financials: total capital ever put in (see
    # capital_contributions — a running ledger, not a single value),
    # total revenue across every sale, and total spent on raw
    # materials, so a simple gross profit can be shown at a glance.
    # See reports_data() below for the same figures broken out into
    # charts on the Reports page.
    total_capital = query(
        "SELECT COALESCE(SUM(amount), 0) AS v FROM capital_contributions", fetchone=True
    )["v"]
    total_revenue = query(
        "SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales", fetchone=True
    )["v"]
    total_materials_cost = query(
        "SELECT COALESCE(SUM(line_cost), 0) AS v FROM material_usage_logs", fetchone=True
    )["v"]
    financials = {
        "capital": total_capital,
        "revenue": total_revenue,
        "materials_cost": total_materials_cost,
        "profit": total_revenue - total_materials_cost,
    }

    return render_template(
        "admin/dashboard.html",
        stats=stats, low_stock=low_stock,
        recent_requests=recent_requests, recent_activity=recent_activity,
        top_sellers=top_sellers, financials=financials,
    )


# ---------------------------------------------------------------- products
@bp.route("/products", methods=["GET", "POST"])
@admin_required
def products():
    if request.method == "POST":
        item_name = request.form.get("item_name", "").strip()
        variant = request.form.get("variant")
        unit = request.form.get("unit")

        try:
            base_code = parse_base_code(request.form.get("base_code"))
            price = parse_non_negative_decimal(
                request.form.get("price"), "Price")
            if not item_name or variant not in ("Male", "Female", "Unisex") or unit not in PRODUCT_UNITS:
                raise ValidationError(
                    "Please fill in every field with a valid value.")
            # The base code is reusable across sizes (e.g. base 'A1' + unit
            # '85ML' and base 'A1' + unit '15ML' both come from the same
            # admin-entered code) — build_sku() is what actually makes each
            # one a distinct SKU/row, with its own price and stock.
            sku = build_sku(base_code, unit)
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.products"))

        try:
            with transaction() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO products (sku, item_name, variant, unit, price) VALUES (%s, %s, %s, %s, %s)",
                    (sku, item_name, variant, unit, price),
                )
                # Give every existing branch (and HQ) a zero-stock row so
                # it shows up everywhere. Both writes happen in one
                # transaction so a crash partway through can't leave the
                # product created with no inventory rows.
                cur.execute("SELECT branch_id FROM branches")
                branch_ids = [row[0] for row in cur.fetchall()]
                for branch_id in branch_ids:
                    cur.execute(
                        "INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, 0)",
                        (branch_id, sku),
                    )
                cur.close()
            notify_all(["products", "inventory"])
            log_action("add_product", target=sku,
                       details=f"{item_name} ({variant}, {unit}) — ₱{price:,.2f}")
            flash(f"{item_name} — {unit} ({sku}) added to the catalog.", "success")
        except Exception:
            flash(f"'{base_code}' already has a {unit} entry (SKU {sku}).", "error")
        return redirect(url_for("admin.products"))

    catalog = query(
        """SELECT p.*, COALESCE(SUM(bi.stock_qty), 0) AS total_stock
           FROM products p
           LEFT JOIN branch_inventory bi ON p.sku = bi.sku
           GROUP BY p.sku ORDER BY p.item_name"""
    )
    return render_template("admin/products.html", catalog=catalog, unit_choices=PRODUCT_UNITS)


@bp.route("/products/edit", methods=["POST"])
@admin_required
def edit_product():
    sku = request.form.get("sku")
    item_name = request.form.get("item_name", "").strip()
    variant = request.form.get("variant")

    product = query("SELECT sku, item_name, unit FROM products WHERE sku = %s", (sku,), fetchone=True)
    if not product:
        flash("That product no longer exists.", "error")
        return redirect(url_for("admin.products"))

    try:
        price = parse_non_negative_decimal(request.form.get("price"), "Price")
        if not item_name or variant not in ("Male", "Female", "Unisex"):
            raise ValidationError("Please fill in every field with a valid value.")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.products"))

    # unit is intentionally not editable here — it's baked into the SKU
    # itself (see build_sku() above), so changing it on an existing row
    # would leave the SKU claiming a size the row no longer has. To
    # change a product's size, add it again under the same base code
    # with the new unit — that gets its own SKU, same as any other size.
    execute(
        "UPDATE products SET item_name = %s, variant = %s, price = %s WHERE sku = %s",
        (item_name, variant, price, sku),
    )
    notify_all(["products", "inventory"])
    log_action("edit_product", target=sku,
               details=f"{item_name} ({variant}, {product['unit']}) — ₱{price:,.2f}")
    flash(f"{item_name} ({sku}) updated.", "success")
    return redirect(url_for("admin.products"))


# ---------------------------------------------------------------- production
@bp.route("/production", methods=["GET", "POST"])
@admin_required
def production():
    if request.method == "POST":
        sku = request.form.get("sku")
        batch_code = request.form.get("batch_code", "").strip() or None
        try:
            qty = parse_positive_int(request.form.get(
                "qty_produced"), "Units produced")
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
                before_qty = (
                    after_qty - qty) if after_qty is not None else None
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
            flash(
                f"Logged {qty} units produced and added to HQ warehouse stock.", "success")
        except Exception:
            current_app.logger.exception(
                "production logging failed for sku=%s", sku)
            flash("Couldn't log this production run — please try again.", "error")
        return redirect(url_for("admin.production"))

    products_list = query(
        "SELECT sku, item_name, unit FROM products ORDER BY item_name")
    logs = query(
        """SELECT pl.*, p.item_name, p.unit FROM production_logs pl
           JOIN products p ON pl.sku = p.sku
           ORDER BY pl.produced_at DESC LIMIT 40"""
    )
    hq_stock = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, bi.stock_qty
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
                        "INSERT INTO branches (branch_name, location) VALUES (%s, %s)", (
                            name, location)
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


# ---------------------------------------------------------------- branch stock
@bp.route("/branch-stock")
@admin_required
def branch_stock():
    """Read-only view of stock levels per branch.

    There's no per-branch price to set anymore — every branch sells at
    the HQ price on the product catalog (see products()). This page is
    purely about stock levels now.
    """
    branch_filter = request.args.get("branch_id", "all")
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")

    rows = []
    if branch_filter != "all":
        rows = query(
            """SELECT b.branch_id, b.branch_name, p.sku, p.item_name, p.variant, p.unit,
                      p.price AS hq_price, bi.stock_qty, bi.reorder_level
               FROM branch_inventory bi
               JOIN branches b ON bi.branch_id = b.branch_id
               JOIN products p ON bi.sku = p.sku
               WHERE b.is_hq = FALSE AND b.branch_id = %s
               ORDER BY p.item_name""",
            (branch_filter,),
        )

    totals = query(
        """SELECT b.branch_id, b.branch_name, COALESCE(SUM(bi.stock_qty), 0) AS total_stock
           FROM branches b
           LEFT JOIN branch_inventory bi ON b.branch_id = bi.branch_id
           LEFT JOIN products p ON bi.sku = p.sku
           WHERE b.is_hq = FALSE
           GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )

    return render_template(
        "admin/branch_stock.html",
        rows=rows, branch_list=branch_list, branch_filter=branch_filter, totals=totals,
        unit_choices=PRODUCT_UNITS,
    )


# ---------------------------------------------------------------- record sale (HQ direct)
@bp.route("/record-sale", methods=["GET", "POST"])
@admin_required
def record_sale():
    """HQ selling straight from the warehouse (branch_id=1) — walk-in
    sales, refills, or an employee taking product with the cost deducted
    from their salary. Mirrors branch.record_sale(); the only difference
    is HQ can't produce here, only sell/refill from whatever the
    warehouse already has on hand (see production() for adding stock).

    There's no branch price to fall back to anymore — the HQ price is
    only ever a suggested starting point; the actual amount charged is
    typed in on every sale/refill.
    """
    if request.method == "POST":
        sku = request.form.get("sku")
        sale_type = request.form.get("sale_type")
        payment_method = request.form.get("payment_method")
        raw_buyer = request.form.get("buyer_user_id", "").strip()

        try:
            qty = parse_positive_int(request.form.get("qty_sold"), "Quantity")
            unit_price = parse_non_negative_decimal(
                request.form.get("unit_price"), "Price charged")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.record_sale"))

        if sale_type not in SALE_TYPES:
            flash("Select whether this is a sale or a refill.", "error")
            return redirect(url_for("admin.record_sale"))
        if payment_method not in PAYMENT_METHODS:
            flash("Select a payment method.", "error")
            return redirect(url_for("admin.record_sale"))
        if not sku:
            flash("Select a product.", "error")
            return redirect(url_for("admin.record_sale"))

        # AFTER
        buyer_name = None
        if payment_method == "Salary Deduction":
            if not raw_buyer:
                flash("Enter which employee this salary deduction applies to.", "error")
                return redirect(url_for("admin.record_sale"))
            if len(raw_buyer) > 120:
                flash("Employee name is too long (max 120 characters).", "error")
                return redirect(url_for("admin.record_sale"))
            # This is now a plain free-text name, not a lookup against real
            # login accounts — HQ handles reconciling it against payroll
            # themselves. Whatever the admin types is what's recorded.
            buyer_name = raw_buyer

        try:
            with transaction() as conn:
                cur = conn.cursor(dictionary=True)
                # Row-lock HQ's own stock for this SKU for the rest of the
                # transaction — same reasoning as branch.record_sale().
                cur.execute(
                    "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE",
                    (HQ_BRANCH_ID, sku),
                )
                stock_row = cur.fetchone()
                if not stock_row:
                    cur.close()
                    flash("That product isn't stocked at the HQ warehouse.", "error")
                    return redirect(url_for("admin.record_sale"))

                is_refill = sale_type == "Refill"

                if not is_refill and stock_row["stock_qty"] < qty:
                    cur.close()
                    flash("Not enough HQ warehouse stock on hand for that.", "error")
                    return redirect(url_for("admin.record_sale"))

                before_qty = stock_row["stock_qty"]
                after_qty = before_qty if is_refill else before_qty - qty

                # AFTER
                cur.execute(
                    """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sale_type, payment_method, buyer_name)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (HQ_BRANCH_ID, sku, qty, unit_price,
                     sale_type, payment_method, buyer_name),
                )
                if not is_refill:
                    cur.execute(
                        "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
                        (after_qty, HQ_BRANCH_ID, sku),
                    )
                movement_type = "SALE" if sale_type == "Sale" else "REFILL"
                # AFTER
                notes = "Point-of-sale (HQ)" if payment_method == "Cash" else f"Salary deduction — {buyer_name}"
                if is_refill:
                    notes += " · no stock deducted (refill)"
                cur.execute(
                    """INSERT INTO stock_movement_logs
                       (branch_id, sku, change_qty, movement_type, notes,
                        created_by_user_id, reference_type, before_qty, after_qty)
                       VALUES (%s, %s, %s, %s, %s, %s, 'SALE', %s, %s)""",
                    (HQ_BRANCH_ID, sku, 0 if is_refill else -qty, movement_type, notes,
                     session.get("user_id"), before_qty, after_qty),
                )
                cur.close()
            notify_admin(["inventory", "sales", "movement_logs"])
            flash(f"{sale_type} recorded.", "success")
        except Exception:
            current_app.logger.exception(
                "admin record_sale failed for sku=%s", sku)
            flash("Couldn't record that — please try again.", "error")
        return redirect(url_for("admin.record_sale"))

    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, p.price, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND bi.stock_qty > 0 ORDER BY p.item_name""",
        (HQ_BRANCH_ID,),
    )
    recent_sales = query(
        """SELECT s.*, p.item_name, COALESCE(s.buyer_name, bu.username) AS buyer_username
           FROM sales s JOIN products p ON s.sku = p.sku
           LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 10""",
        (HQ_BRANCH_ID,),
    )
    employees = query(
        "SELECT user_id, username, role FROM users WHERE is_active = TRUE ORDER BY username"
    )
    return render_template(
        "admin/record_sale.html", inventory=inventory, recent_sales=recent_sales, employees=employees,
    )


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
            flash(
                "Username is required and password must be at least 8 characters.", "error")
        else:
            try:
                execute(
                    """INSERT INTO users (username, password_hash, role, branch_id, must_change_password)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (username, generate_password_hash(password),
                     role, branch_id if role == "Branch" else None),
                )
                notify_admin("users")
                branch_detail = f", branch_id={branch_id}" if role == "Branch" else ""
                log_action("create_account", target=username,
                           details=f"role={role}{branch_detail}")
                flash(
                    f"Account '{username}' created. They'll be asked to set a new password at first login.", "success")
            except Exception:
                flash(f"Username '{username}' is already taken.", "error")
        return redirect(url_for("admin.users"))

    accounts = query(
        """SELECT u.user_id, u.username, u.role, u.is_active, u.created_at, b.branch_name
           FROM users u LEFT JOIN branches b ON u.branch_id = b.branch_id
           ORDER BY u.role, b.branch_name"""
    )
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")
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
        target = query(
            "SELECT username, is_active FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        execute(
            "UPDATE users SET is_active = NOT is_active WHERE user_id = %s", (user_id,))
        notify_admin("users")
        if target:
            new_status = "Deactivated" if target["is_active"] else "Reactivated"
            log_action("toggle_user",
                       target=target["username"], details=new_status)
        flash("Account status updated.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_user_password(user_id):
    target = query("SELECT username FROM users WHERE user_id = %s",
                   (user_id,), fetchone=True)
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
    session["temp_password_reveal"] = {
        "username": target["username"], "password": temp_password}
    log_action("reset_password", target=target["username"])
    flash(
        f"Password for '{target['username']}' has been reset. "
        "They'll be asked to set a new password the next time they sign in.",
        "success",
    )
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------- stock requests
#
# A stock request is now a *delivery* — one branch-submitted cart that can
# carry any number of different SKUs (see stock_requests + the new
# stock_request_items table in schema.sql). The Requests list therefore
# shows one row per delivery (Delivery #, item count, total value) rather
# than one row per product; the actual line items only ever show up on
# the delivery's own review/dispatch page and on its receipt PDF.
@bp.route("/requests")
@admin_required
def requests_list():
    status_filter = request.args.get("status", "all")
    sql = """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at, sr.branch_id,
                    b.branch_name,
                    COUNT(sri.item_id) AS item_count,
                    COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                    COALESCE(SUM(sri.requested_qty * sri.unit_price), 0) AS total_value,
                    COALESCE(SUM(sri.received_qty), 0) AS total_received,
                    COALESCE(SUM(sri.damaged_qty), 0) AS total_damaged
              FROM stock_requests sr
              JOIN branches b ON sr.branch_id = b.branch_id
              LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id"""
    params = ()
    if status_filter != "all":
        sql += " WHERE sr.status = %s"
        params = (status_filter,)
    sql += " GROUP BY sr.request_id ORDER BY FIELD(sr.status,'Pending','In Transit','Fulfilled','Rejected'), sr.requested_at DESC"
    stock_requests = query(sql, params)
    return render_template("admin/requests.html", stock_requests=stock_requests, status_filter=status_filter)


@bp.route("/requests/<int:request_id>")
@admin_required
def review_request(request_id):
    """Line-item breakdown of one delivery — the only place its individual
    products/quantities/prices are shown on the admin side (besides the
    receipt, once Fulfilled). Pending deliveries get an editable dispatch
    quantity per item; every other status is a read-only breakdown.
    """
    req = query(
        """SELECT sr.*, b.branch_name
           FROM stock_requests sr JOIN branches b ON sr.branch_id = b.branch_id
           WHERE sr.request_id = %s""",
        (request_id,), fetchone=True,
    )
    if not req:
        abort(404)
    items = query(
        """SELECT sri.*, p.item_name, p.variant, p.unit
           FROM stock_request_items sri JOIN products p ON sri.sku = p.sku
           WHERE sri.request_id = %s ORDER BY p.item_name""",
        (request_id,),
    )
    total_value = sum(
        (item["requested_qty"] * item["unit_price"]) for item in items)
    return render_template("admin/request_detail.html", req=req, items=items, total_value=total_value)


@bp.route("/requests/<int:request_id>/dispatch", methods=["POST"])
@admin_required
def dispatch_request(request_id):
    """Dispatch some or all items on a Pending delivery in one shot.

    Comes from the per-item quantity inputs on request_detail.html —
    item_id[] / dispatched_qty[] pairs, one per line on the delivery.
    Defaults to the full requested_qty per item if the admin didn't touch
    a field, same as the old single-item flow defaulted to requested_qty
    when dispatched_qty was left blank.
    """
    item_ids = request.form.getlist("item_id[]")
    raw_qtys = request.form.getlist("dispatched_qty[]")

    if not item_ids:
        flash("Nothing to dispatch.", "error")
        return redirect(url_for("admin.review_request", request_id=request_id))

    try:
        requested_dispatch = {}
        for raw_id, raw_qty in zip(item_ids, raw_qtys):
            requested_dispatch[int(raw_id)] = parse_non_negative_int(
                raw_qty, "Dispatched quantity")
    except (TypeError, ValueError, ValidationError) as err:
        flash(str(err) if isinstance(err, ValidationError)
              else "Invalid item in dispatch request.", "error")
        return redirect(url_for("admin.review_request", request_id=request_id))

    if all(qty == 0 for qty in requested_dispatch.values()):
        flash("Dispatch at least one item.", "error")
        return redirect(url_for("admin.review_request", request_id=request_id))

    req = None
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            # Lock the request row, then every one of its line items, then
            # every HQ stock row they touch — all for the rest of this
            # transaction — so two admins dispatching the same delivery (or
            # two deliveries that both draw on the same SKU) at the same
            # moment can't jointly over-dispatch HQ stock below zero.
            cur.execute(
                "SELECT * FROM stock_requests WHERE request_id = %s FOR UPDATE", (request_id,))
            req = cur.fetchone()
            if not req or req["status"] != "Pending":
                cur.close()
                flash("This request can no longer be dispatched.", "error")
                return redirect(url_for("admin.requests_list"))

            cur.execute(
                "SELECT * FROM stock_request_items WHERE request_id = %s FOR UPDATE",
                (request_id,),
            )
            items_by_id = {row["item_id"]: row for row in cur.fetchall()}

            for item_id, qty in requested_dispatch.items():
                item = items_by_id.get(item_id)
                if item is None:
                    cur.close()
                    flash("Invalid item in dispatch request.", "error")
                    return redirect(url_for("admin.review_request", request_id=request_id))
                if qty > item["requested_qty"]:
                    cur.close()
                    flash(
                        "Dispatched quantity can't exceed what was requested.", "error")
                    return redirect(url_for("admin.review_request", request_id=request_id))

            dispatched_any = False
            for item_id, qty in requested_dispatch.items():
                item = items_by_id[item_id]
                if qty == 0:
                    cur.execute(
                        "UPDATE stock_request_items SET dispatched_qty = 0 WHERE item_id = %s",
                        (item_id,),
                    )
                    continue

                cur.execute(
                    "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE",
                    (HQ_BRANCH_ID, item["sku"]),
                )
                hq_row = cur.fetchone()
                if not hq_row or hq_row["stock_qty"] < qty:
                    cur.close()
                    flash(
                        f"Not enough HQ warehouse stock to dispatch {item['sku']}.", "error")
                    return redirect(url_for("admin.review_request", request_id=request_id))

                before_qty = hq_row["stock_qty"]
                after_qty = before_qty - qty
                cur.execute(
                    "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
                    (after_qty, HQ_BRANCH_ID, item["sku"]),
                )
                cur.execute(
                    "UPDATE stock_request_items SET dispatched_qty = %s WHERE item_id = %s",
                    (qty, item_id),
                )
                cur.execute(
                    """INSERT INTO stock_movement_logs
                       (branch_id, sku, change_qty, movement_type, notes,
                        created_by_user_id, reference_type, reference_id, before_qty, after_qty)
                       VALUES (%s, %s, %s, 'DISPATCH', %s, %s, 'STOCK_REQUEST', %s, %s, %s)""",
                    (HQ_BRANCH_ID, item["sku"], -qty, f"Dispatched on {req['delivery_number']}",
                     session.get("user_id"), request_id, before_qty, after_qty),
                )
                dispatched_any = True

            if not dispatched_any:
                cur.close()
                flash("Dispatch at least one item.", "error")
                return redirect(url_for("admin.review_request", request_id=request_id))

            cur.execute(
                "UPDATE stock_requests SET status = 'In Transit' WHERE request_id = %s",
                (request_id,),
            )
            cur.close()
        notify_admin_and_branch(
            req["branch_id"], ["requests", "inventory", "movement_logs"])
        flash(
            f"{req['delivery_number']} dispatched — now in transit to the branch.", "success")
    except Exception:
        current_app.logger.exception(
            "dispatch_request failed for request_id=%s", request_id)
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
    req = query("SELECT branch_id, delivery_number FROM stock_requests WHERE request_id = %s",
                (request_id,), fetchone=True)
    execute(
        "UPDATE stock_requests SET status = 'Rejected' WHERE request_id = %s AND status = 'Pending'",
        (request_id,),
    )
    if req:
        notify_admin_and_branch(req["branch_id"], "requests")
        flash(f"{req['delivery_number']} rejected.", "success")
    else:
        flash("Request rejected.", "success")
    return redirect(url_for("admin.requests_list"))


# ---------------------------------------------------------------- ledger
@bp.route("/movement-logs")
@admin_required
def movement_logs():
    """Production/Sale/Refill activity only.

    Stock-request-driven movements (DISPATCH, RECEIPT, DAMAGE,
    ADJUSTMENT — every row tagged reference_type='STOCK_REQUEST' by
    dispatch_request() / receive_stock()) are deliberately excluded
    here. A delivery can carry any number of SKUs at once now, so one
    row here — one branch, one item, one qty — would misrepresent it
    and just duplicate what the delivery's own review page and its
    receipt PDF already show in full, correctly grouped under one
    delivery number. See requests_list()/review_request() and
    receipts.py for that history instead.
    """
    branch_filter = request.args.get("branch_id", "all")
    sql = """SELECT sml.*, b.branch_name, p.item_name
              FROM stock_movement_logs sml
              JOIN branches b ON sml.branch_id = b.branch_id
              JOIN products p ON sml.sku = p.sku
              WHERE (sml.reference_type IS NULL OR sml.reference_type != 'STOCK_REQUEST')"""
    params = []
    if branch_filter != "all":
        sql += " AND sml.branch_id = %s"
        params.append(branch_filter)
    sql += " ORDER BY sml.created_at DESC LIMIT 200"
    logs = query(sql, tuple(params))
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches ORDER BY branch_name")
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
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")
    report_types = [
        {"key": key, "label": meta["label"], "windowed": meta["windowed"]}
        for key, meta in REPORT_TYPES.items() if meta["admin"]
    ]
    return render_template(
        "admin/reports.html", branch_list=branch_list, report_types=report_types, unit_choices=PRODUCT_UNITS,
    )


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
    report = get_report(report_type, filters, branch_scope=None,
                        actor_label=f"HQ Admin — {session.get('username')}")

    if report["row_count"] == 0:
        flash(
            f"No data matches the selected filters for {report['title']}.", "warning")
        return redirect(url_for("admin.reports"))

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

    # Bucket size — and time window — shared by three charts: Ledger
    # movement (bucketed by day/week/month/year), and Revenue by branch
    # + Revenue/Materials/Profit (not bucketed, just totaled within the
    # same window, e.g. "last 14 days" for daily). Same allow-list
    # pattern reports.py uses elsewhere: the value picked here is never
    # user text going into SQL, it's a key into a fixed dict, and only
    # the fixed SQL fragment that key maps to gets interpolated — so
    # this stays just as injection-safe as a plain %s placeholder would,
    # while letting the WHERE/GROUP BY expressions differ per option
    # (parameterizing a GROUP BY expression itself isn't possible with
    # a plain %s placeholder).
    granularity = request.args.get("granularity", "daily")
    trend_bucket = _TREND_GRANULARITIES.get(
        granularity, _TREND_GRANULARITIES["daily"])
    window_sql = trend_bucket["window"]

    movement_trend = query(
        f"""SELECT {trend_bucket['trunc']} AS day, movement_type, SUM(ABS(change_qty)) AS total
            FROM stock_movement_logs
            WHERE created_at >= NOW() - {window_sql}
            GROUP BY {trend_bucket['trunc']}, movement_type ORDER BY day"""
    )

    # Revenue by branch, windowed to match the granularity switch (e.g.
    # last 14 days for "Daily") rather than all-time — the filter lives
    # in the JOIN condition, not WHERE, so a branch with zero sales in
    # the window still shows up with a 0 bar instead of disappearing.
    by_branch_revenue = query(
        f"""SELECT b.branch_name,
                   COALESCE(SUM(s.qty_sold), 0) AS units_sold,
                   COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
            FROM branches b
            LEFT JOIN sales s ON b.branch_id = s.branch_id AND s.sold_at >= NOW() - {window_sql}
            WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )

    stock_by_branch = query(
        """SELECT b.branch_name, SUM(bi.stock_qty) AS total_stock
           FROM branch_inventory bi JOIN branches b ON bi.branch_id = b.branch_id
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )

    # Combined totals across every branch, regardless of each branch's price.
    # All-time — feeds the top stat tiles and the Revenue vs. Capital chart,
    # neither of which are windowed by the granularity switch.
    totals = query(
        """SELECT COALESCE(SUM(qty_sold), 0) AS units, COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales""",
        fetchone=True,
    )

    # Business-level financials.
    # - capital: a running ledger (see capital_contributions — an admin
    #   can log a new contribution any time, not just once); always
    #   all-time, paired with all-time revenue on the Revenue vs.
    #   Capital chart.
    # - revenue_windowed / materials_cost / profit: scoped to the same
    #   window as the granularity switch, for the Revenue, Materials &
    #   Profit chart. Profit is a simple gross figure — revenue minus
    #   what's been spent on raw materials in that window — it doesn't
    #   subtract other costs (rent, payroll, etc.), which the app
    #   doesn't currently track.
    capital_total = query(
        "SELECT COALESCE(SUM(amount), 0) AS v FROM capital_contributions", fetchone=True
    )["v"]
    windowed_revenue = query(
        f"SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales WHERE sold_at >= NOW() - {window_sql}",
        fetchone=True,
    )["v"]
    windowed_materials_cost = query(
        f"SELECT COALESCE(SUM(line_cost), 0) AS v FROM material_usage_logs WHERE created_at >= NOW() - {window_sql}",
        fetchone=True,
    )["v"]
    financials = {
        "capital": capital_total,
        "revenue_windowed": windowed_revenue,
        "materials_cost": windowed_materials_cost,
        "profit": float(windowed_revenue) - float(windowed_materials_cost),
    }

    for row in movement_trend:
        row["day"] = row["day"].isoformat()

    return jsonify(
        by_variant=by_variant, by_branch=by_branch, by_branch_revenue=by_branch_revenue,
        movement_trend=movement_trend, stock_by_branch=stock_by_branch, totals=totals,
        financials=financials,
    )


@bp.route("/materials", methods=["GET", "POST"])
@admin_required
def materials():
    if request.method == "POST":
        material_name = request.form.get("material_name", "").strip()
        unit = request.form.get("unit")

        try:
            if not material_name or unit not in MATERIAL_UNITS:
                raise ValidationError("Please fill in every field with a valid value.")
            package_qty = parse_positive_decimal(request.form.get("package_qty"), "Package quantity")
            package_cost = parse_non_negative_decimal(request.form.get("package_cost"), "Package cost")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.materials"))

        cost_per_unit = package_cost / package_qty

        try:
            execute(
                """INSERT INTO raw_materials (material_name, unit, package_qty, package_cost, cost_per_unit)
                   VALUES (%s, %s, %s, %s, %s)""",
                (material_name, unit, package_qty, package_cost, cost_per_unit),
            )
            notify_all(["materials"])
            log_action("add_material", target=material_name,
                       details=f"{package_qty:g} {unit} for ₱{package_cost:,.2f} — ₱{cost_per_unit:,.4f}/{unit.lower()}")
            flash(f"{material_name} added at ₱{cost_per_unit:,.4f} per {unit.lower()}.", "success")
        except Exception:
            flash(f"'{material_name}' already exists in the materials list.", "error")
        return redirect(url_for("admin.materials"))

    materials_list = query(
        "SELECT * FROM raw_materials ORDER BY material_name")
    recent_production = query(
        """SELECT pl.log_id, pl.produced_at, pl.batch_code, p.item_name, p.unit
           FROM production_logs pl JOIN products p ON pl.sku = p.sku
           ORDER BY pl.produced_at DESC LIMIT 40"""
    )
    usage_logs = query(
        """SELECT mul.*, rm.material_name, rm.unit,
                  pl.batch_code, p.item_name AS production_item_name
           FROM material_usage_logs mul
           JOIN raw_materials rm ON mul.material_id = rm.material_id
           LEFT JOIN production_logs pl ON mul.production_log_id = pl.log_id
           LEFT JOIN products p ON pl.sku = p.sku
           ORDER BY mul.created_at DESC LIMIT 40"""
    )
    totals = query(
        "SELECT COALESCE(SUM(line_cost), 0) AS total_spent FROM material_usage_logs",
        fetchone=True,
    )
    return render_template(
        "admin/materials.html",
        materials=materials_list,
        recent_production=recent_production,
        usage_logs=usage_logs,
        totals=totals,
        unit_choices=MATERIAL_UNITS,
    )


@bp.route("/materials/edit", methods=["POST"])
@admin_required
def edit_material():
    material_id = request.form.get("material_id")
    material_name = request.form.get("material_name", "").strip()
    unit = request.form.get("unit")

    try:
        if not material_name or unit not in MATERIAL_UNITS:
            raise ValidationError("Please fill in every field with a valid value.")
        package_qty = parse_positive_decimal(request.form.get("package_qty"), "Package quantity")
        package_cost = parse_non_negative_decimal(request.form.get("package_cost"), "Package cost")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.materials"))

    # cost_per_unit is recomputed from the new package figures, but this
    # only affects usage entries logged from now on — past entries keep
    # the unit_cost_snapshot they were logged with (see log_material_usage()).
    cost_per_unit = package_cost / package_qty

    try:
        execute(
            """UPDATE raw_materials
               SET material_name = %s, unit = %s, package_qty = %s, package_cost = %s, cost_per_unit = %s
               WHERE material_id = %s""",
            (material_name, unit, package_qty, package_cost, cost_per_unit, material_id),
        )
        notify_all(["materials"])
        log_action("edit_material", target=material_name,
                   details=f"{package_qty:g} {unit} for ₱{package_cost:,.2f} — ₱{cost_per_unit:,.4f}/{unit.lower()}")
        flash(f"{material_name} updated at ₱{cost_per_unit:,.4f} per {unit.lower()}.", "success")
    except Exception:
        flash(f"'{material_name}' already exists in the materials list.", "error")
    return redirect(url_for("admin.materials"))


@bp.route("/materials/log-usage", methods=["POST"])
@admin_required
def log_material_usage():
    material_id = request.form.get("material_id")
    production_log_id = request.form.get("production_log_id") or None
    notes = request.form.get("notes", "").strip() or None

    material = query(
        "SELECT material_name, unit, cost_per_unit FROM raw_materials WHERE material_id = %s",
        (material_id,), fetchone=True,
    )
    if not material:
        flash("Select a valid material.", "error")
        return redirect(url_for("admin.materials"))

    try:
        qty_used = parse_positive_decimal(request.form.get("qty_used"), "Quantity used")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.materials"))

    unit_cost_snapshot = material["cost_per_unit"]
    line_cost = round(float(qty_used) * float(unit_cost_snapshot), 2)

    execute(
        """INSERT INTO material_usage_logs
           (material_id, production_log_id, qty_used, unit_cost_snapshot, line_cost, notes, created_by_user_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (material_id, production_log_id, qty_used, unit_cost_snapshot, line_cost, notes, session.get("user_id")),
    )
    notify_all(["materials"])
    log_action("log_material_usage", target=material["material_name"],
               details=f"{qty_used:g} {material['unit'].lower()} — ₱{line_cost:,.2f}")
    flash(f"Logged {qty_used:g} {material['unit'].lower()} of {material['material_name']} — ₱{line_cost:,.2f}.", "success")
    return redirect(url_for("admin.materials"))


# ---------------------------------------------------------------- capital
@bp.route("/capital", methods=["GET", "POST"])
@admin_required
def capital():
    """Business capital, as a running ledger rather than a single value.

    An admin can log a new contribution any time — an initial injection,
    then later top-ups — and the business's "total capital" is always
    just the sum of every row here. Nothing is edited or deleted after
    the fact; see the note on capital_contributions in schema.sql.
    """
    if request.method == "POST":
        note = request.form.get("note", "").strip() or None
        try:
            amount = parse_positive_decimal(request.form.get("amount"), "Amount")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.capital"))

        execute(
            """INSERT INTO capital_contributions (amount, note, contributed_by_user_id)
               VALUES (%s, %s, %s)""",
            (amount, note, session.get("user_id")),
        )
        notify_all(["capital"])
        log_action(
            "add_capital", target=session.get("username"),
            details=f"₱{amount:,.2f}" + (f" — {note}" if note else ""),
        )
        flash(f"Logged ₱{amount:,.2f} in capital.", "success")
        return redirect(url_for("admin.capital"))

    contributions = query(
        """SELECT cc.*, u.username
           FROM capital_contributions cc
           LEFT JOIN users u ON cc.contributed_by_user_id = u.user_id
           ORDER BY cc.created_at DESC"""
    )
    totals = query(
        "SELECT COALESCE(SUM(amount), 0) AS total_capital FROM capital_contributions",
        fetchone=True,
    )
    return render_template(
        "admin/capital.html", contributions=contributions, totals=totals,
    )
