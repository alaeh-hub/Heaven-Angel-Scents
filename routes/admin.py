import datetime
import decimal
import os
import uuid

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, send_file, session, url_for,
)
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from db import execute, query, transaction
from decorators import admin_required
from audit import log_action
from receipts import build_receipt_pdf
from reports import REPORT_TYPES, get_report, parse_report_filters, render_report_excel, render_report_pdf
from sockets import notify_admin, notify_admin_and_branch, notify_all, notify_bell
from utils import (
    MATERIAL_UNITS, PARTNER_TYPES, PAYMENT_METHODS, PRODUCT_UNITS, SALE_TYPES, ValidationError,
    build_sku, generate_temp_password, parse_base_code, parse_non_negative_decimal,
    parse_non_negative_int, parse_optional_id, parse_positive_decimal, parse_positive_int,
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

    # Best-selling packages — same "only Closed counts as a real sale"
    # rule as everywhere else package revenue is computed (see
    # partners()'s docstring and schema.sql's note on
    # partner_inquiries.order_amount). Joined through packages so a
    # package that's since been deleted (package_id set NULL on the
    # inquiry) drops out here rather than showing as a nameless row —
    # its revenue still counts in the totals below, just not in this
    # per-package breakdown.
    top_packages = query(
        """SELECT pkg.package_id, pkg.package_name,
                  COUNT(pinq.inquiry_id) AS order_count,
                  COALESCE(SUM(pinq.order_amount), 0) AS total_revenue
           FROM partner_inquiries pinq
           JOIN packages pkg ON pkg.package_id = pinq.package_id
           WHERE pinq.status = 'Closed'
           GROUP BY pkg.package_id, pkg.package_name
           ORDER BY total_revenue DESC LIMIT 3"""
    )

    # Top partners by package sales — same Closed-only rule. Joined
    # through partners for the same reason as above (a since-deleted
    # partner drops out of this breakdown, not out of the totals).
    top_partners = query(
        """SELECT p.partner_id, p.partner_name, p.partner_type,
                  COUNT(pinq.inquiry_id) AS order_count,
                  COALESCE(SUM(pinq.order_amount), 0) AS total_spent
           FROM partner_inquiries pinq
           JOIN partners p ON p.partner_id = pinq.partner_id
           WHERE pinq.status = 'Closed'
           GROUP BY p.partner_id, p.partner_name, p.partner_type
           ORDER BY total_spent DESC LIMIT 3"""
    )

    # Business-level financials: total revenue across branch sales AND
    # closed package orders, and total spent on raw materials, so a
    # simple gross profit can be shown at a glance. See reports_data()
    # below for the same figures broken out into charts on the Reports
    # page.
    #
    # package_sales is only ever Closed inquiries' order_amount — see
    # the note above top_packages. It's folded into total_revenue (and
    # therefore profit) the same way branch sales are, since a closed
    # package order is real money in exactly the same sense.
    #
    # "Capital" is no longer its own ledger (see schema.sql's note on
    # capital_contributions being removed) — it's now just how much has
    # been spent buying raw material packages, SUM(package_cost) over
    # raw_materials. That's the same number as materials_cost below; the
    # dashboard keeps the "Total Capital" label (financials.capital) for
    # that stat tile, it just now shows this figure instead of a
    # manually-logged one.
    total_materials_cost = query(
        "SELECT COALESCE(SUM(package_cost), 0) AS v FROM raw_materials", fetchone=True
    )["v"]
    branch_sales_revenue = query(
        "SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales", fetchone=True
    )["v"]
    package_sales_revenue = query(
        "SELECT COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries WHERE status = 'Closed'",
        fetchone=True,
    )["v"]
    total_revenue = branch_sales_revenue + package_sales_revenue
    financials = {
        "capital": total_materials_cost,
        "revenue": total_revenue,
        "branch_sales_revenue": branch_sales_revenue,
        "package_sales_revenue": package_sales_revenue,
        "materials_cost": total_materials_cost,
        "profit": total_revenue - total_materials_cost,
    }

    return render_template(
        "admin/dashboard.html",
        stats=stats, low_stock=low_stock,
        recent_requests=recent_requests, recent_activity=recent_activity,
        top_sellers=top_sellers, top_packages=top_packages, top_partners=top_partners,
        financials=financials,
    )


# ---------------------------------------------------------------- product images
# Uploaded product photos are stored under <static>/uploads/products/ and
# referenced from products.image_path as a path relative to the static
# folder (e.g. "uploads/products/<uuid>.jpg"), so they can be rendered
# anywhere with url_for('static', filename=...).
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
PRODUCT_IMAGE_SUBDIR = "uploads/products"


def _save_product_image(file_storage):
    """Validate and persist an uploaded product image. Returns the
    image_path to store on the product row, or None if no file was
    actually chosen (the field is optional). Raises ValidationError on
    an unsupported file type.
    """
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(
            "Product image must be a JPG, PNG, or WEBP file.")

    upload_dir = os.path.join(
        current_app.static_folder, *PRODUCT_IMAGE_SUBDIR.split("/"))
    os.makedirs(upload_dir, exist_ok=True)

    # Random filename — never trust/re-use the uploader's own filename
    # beyond checking its extension, and this also sidesteps any
    # collision between products.
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(upload_dir, stored_name))
    return f"{PRODUCT_IMAGE_SUBDIR}/{stored_name}"


def _delete_product_image(image_path):
    """Best-effort removal of a product image file that's being replaced
    or cleared. Never raises — a missing/already-gone file shouldn't
    block the request that's replacing it."""
    if not image_path:
        return
    full_path = os.path.join(current_app.static_folder, image_path)
    try:
        if os.path.isfile(full_path):
            os.remove(full_path)
    except OSError:
        pass


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
            # Optional — validated and saved to disk here so a bad file
            # type is caught before we touch the database at all.
            image_path = _save_product_image(request.files.get("image"))
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.products"))

        try:
            with transaction() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO products (sku, item_name, variant, unit, price, image_path) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (sku, item_name, variant, unit, price, image_path),
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
            # The SKU insert failed (almost always a duplicate base
            # code + unit) — don't leave an orphaned image file behind
            # for a product row that was never created.
            if image_path:
                _delete_product_image(image_path)
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
    # Checkbox only rendered (and only meaningful) when the product
    # already has an image — see products.html.
    remove_image = request.form.get("remove_image") == "1"

    product = query(
        "SELECT sku, item_name, unit, image_path FROM products WHERE sku = %s", (sku,), fetchone=True
    )
    if not product:
        flash("That product no longer exists.", "error")
        return redirect(url_for("admin.products"))

    try:
        price = parse_non_negative_decimal(request.form.get("price"), "Price")
        if not item_name or variant not in ("Male", "Female", "Unisex"):
            raise ValidationError(
                "Please fill in every field with a valid value.")
        # Optional — a new file replaces the existing image. Validated
        # and saved here so a bad file type is caught before the row
        # is touched at all.
        new_image_path = _save_product_image(request.files.get("image"))
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.products"))

    old_image_path = product["image_path"]
    if new_image_path:
        image_path = new_image_path
    elif remove_image:
        image_path = None
    else:
        image_path = old_image_path

    # unit is intentionally not editable here — it's baked into the SKU
    # itself (see build_sku() above), so changing it on an existing row
    # would leave the SKU claiming a size the row no longer has. To
    # change a product's size, add it again under the same base code
    # with the new unit — that gets its own SKU, same as any other size.
    execute(
        "UPDATE products SET item_name = %s, variant = %s, price = %s, image_path = %s WHERE sku = %s",
        (item_name, variant, price, image_path, sku),
    )
    # Only delete the old file once the row has been updated to point
    # elsewhere (or nowhere) — never delete first, so a crash mid-request
    # can't leave the row referencing a file that's already gone.
    if old_image_path and old_image_path != image_path:
        _delete_product_image(old_image_path)

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
                      p.price AS hq_price, p.image_path, bi.stock_qty, bi.reorder_level
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
    #
    # Revenue here is branch sales AND closed package orders combined —
    # same "Closed only counts as a sale" rule used everywhere else
    # package revenue is computed (see admin.partners()'s docstring and
    # schema.sql's note on partner_inquiries.order_amount). units/
    # package_order_count are kept as separate figures rather than added
    # together, since "a unit sold at a branch" and "a package order" are
    # not the same kind of count.
    branch_totals = query(
        """SELECT COALESCE(SUM(qty_sold), 0) AS units, COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales""",
        fetchone=True,
    )
    package_totals = query(
        """SELECT COUNT(*) AS order_count, COALESCE(SUM(order_amount), 0) AS revenue
           FROM partner_inquiries WHERE status = 'Closed'""",
        fetchone=True,
    )
    totals = {
        "units": branch_totals["units"],
        "revenue": branch_totals["revenue"] + package_totals["revenue"],
        "branch_revenue": branch_totals["revenue"],
        "package_revenue": package_totals["revenue"],
        "package_order_count": package_totals["order_count"],
    }

    # Business-level financials.
    # - capital: no longer a logged ledger (see schema.sql's note on
    #   capital_contributions being removed) — it's SUM(package_cost)
    #   over raw_materials, i.e. everything ever spent buying material
    #   packages. Always all-time, paired with all-time revenue on the
    #   Revenue vs. Capital chart, same as before.
    # - revenue_windowed / materials_cost / profit: scoped to the same
    #   window as the granularity switch, for the Revenue, Materials &
    #   Profit chart. Profit is a simple gross figure — revenue (branch
    #   sales + closed package orders) minus what's been spent on raw
    #   materials in that window — it doesn't subtract other costs
    #   (rent, payroll, etc.), which the app doesn't currently track.
    #   materials_cost here is windowed by raw_materials.created_at (when
    #   a material package was logged/added), not by usage — usage no
    #   longer carries a cost at all, see material_usage_logs.
    #
    #   Package orders are windowed by partner_inquiries.created_at,
    #   same as everything else windowed here — the moment of inquiry,
    #   not the (untracked) moment an admin later marks it Closed. A
    #   package inquired within the window but closed after it falls
    #   outside; one inquired earlier but closed inside the window still
    #   counts, same limitation the rest of this window-based reporting
    #   already has for anything without its own "completed_at" column.
    capital_total = query(
        "SELECT COALESCE(SUM(package_cost), 0) AS v FROM raw_materials", fetchone=True
    )["v"]
    windowed_branch_revenue = query(
        f"SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales WHERE sold_at >= NOW() - {window_sql}",
        fetchone=True,
    )["v"]
    windowed_package_revenue = query(
        f"""SELECT COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries
            WHERE status = 'Closed' AND created_at >= NOW() - {window_sql}""",
        fetchone=True,
    )["v"]
    windowed_revenue = windowed_branch_revenue + windowed_package_revenue
    windowed_materials_cost = query(
        f"SELECT COALESCE(SUM(package_cost), 0) AS v FROM raw_materials WHERE created_at >= NOW() - {window_sql}",
        fetchone=True,
    )["v"]
    financials = {
        "capital": capital_total,
        "revenue_windowed": windowed_revenue,
        "branch_revenue_windowed": windowed_branch_revenue,
        "package_revenue_windowed": windowed_package_revenue,
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


# ---------------------------------------------------------------- materials
@bp.route("/materials", methods=["GET", "POST"])
@admin_required
def materials():
    if request.method == "POST":
        material_name = request.form.get("material_name", "").strip()
        unit = request.form.get("unit")

        try:
            if not material_name or unit not in MATERIAL_UNITS:
                raise ValidationError(
                    "Please fill in every field with a valid value.")
            package_qty = parse_positive_decimal(
                request.form.get("package_qty"), "Package quantity")
            package_cost = parse_non_negative_decimal(
                request.form.get("package_cost"), "Package cost")
            supplier_id = parse_optional_id(
                request.form.get("supplier_id"), "Supplier")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.materials"))

        if supplier_id is not None:
            supplier_exists = query(
                "SELECT 1 FROM suppliers WHERE supplier_id = %s", (supplier_id,), fetchone=True
            )
            if not supplier_exists:
                flash("Select a valid supplier.", "error")
                return redirect(url_for("admin.materials"))

        cost_per_unit = package_cost / package_qty

        try:
            execute(
                """INSERT INTO raw_materials
                       (material_name, unit, package_qty, package_cost, cost_per_unit, stock_qty, supplier_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (material_name, unit, package_qty,
                 package_cost, cost_per_unit, package_qty, supplier_id),
            )
            notify_all(["materials"])
            log_action("add_material", target=material_name,
                       details=f"{package_qty:g} {unit} for ₱{package_cost:,.2f} — ₱{cost_per_unit:,.4f}/{unit.lower()}")
            flash(
                f"{material_name} added at ₱{cost_per_unit:,.4f} per {unit.lower()}.", "success")
        except Exception:
            flash(
                f"'{material_name}' already exists in the materials list.", "error")
        return redirect(url_for("admin.materials"))

    materials_list = query(
        """SELECT rm.*, s.supplier_name
           FROM raw_materials rm
           LEFT JOIN suppliers s ON rm.supplier_id = s.supplier_id
           ORDER BY rm.material_name"""
    )
    suppliers_list = query(
        """SELECT s.*, COUNT(rm.material_id) AS material_count
           FROM suppliers s LEFT JOIN raw_materials rm ON rm.supplier_id = s.supplier_id
           GROUP BY s.supplier_id ORDER BY s.supplier_name"""
    )
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
    # Total spent on materials is now purely what's been paid for material
    # packages — SUM(package_cost) over raw_materials — not anything
    # derived from usage. See schema.sql's note on raw_materials for why.
    totals = query(
        "SELECT COALESCE(SUM(package_cost), 0) AS total_spent FROM raw_materials",
        fetchone=True,
    )
    return render_template(
        "admin/materials.html",
        materials=materials_list,
        suppliers=suppliers_list,
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
            raise ValidationError(
                "Please fill in every field with a valid value.")
        package_qty = parse_positive_decimal(
            request.form.get("package_qty"), "Package quantity")
        package_cost = parse_non_negative_decimal(
            request.form.get("package_cost"), "Package cost")
        supplier_id = parse_optional_id(
            request.form.get("supplier_id"), "Supplier")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.materials"))

    if supplier_id is not None:
        supplier_exists = query(
            "SELECT 1 FROM suppliers WHERE supplier_id = %s", (supplier_id,), fetchone=True
        )
        if not supplier_exists:
            flash("Select a valid supplier.", "error")
            return redirect(url_for("admin.materials"))

    # cost_per_unit is recomputed from the new package figures purely as
    # a reference figure shown on the page — usage entries no longer
    # carry any cost of their own (see log_material_usage()), so this
    # recompute has no effect on past usage history the way it used to.
    # stock_qty is deliberately left untouched here: editing a material's
    # purchase details isn't the same as restocking it.
    cost_per_unit = package_cost / package_qty

    try:
        execute(
            """UPDATE raw_materials
               SET material_name = %s, unit = %s, package_qty = %s, package_cost = %s,
                   cost_per_unit = %s, supplier_id = %s
               WHERE material_id = %s""",
            (material_name, unit, package_qty, package_cost,
             cost_per_unit, supplier_id, material_id),
        )
        notify_all(["materials"])
        log_action("edit_material", target=material_name,
                   details=f"{package_qty:g} {unit} for ₱{package_cost:,.2f} — ₱{cost_per_unit:,.4f}/{unit.lower()}")
        flash(
            f"{material_name} updated at ₱{cost_per_unit:,.4f} per {unit.lower()}.", "success")
    except Exception:
        flash(f"'{material_name}' already exists in the materials list.", "error")
    return redirect(url_for("admin.materials"))


@bp.route("/materials/log-usage", methods=["POST"])
@admin_required
def log_material_usage():
    """Log that some quantity of a material was used — a plain
    quantity record, nothing else. No cost is computed or stored here;
    "total materials spent" is tracked purely at purchase time now (see
    raw_materials.package_cost), not re-derived every time material is
    withdrawn. The only side effect on raw_materials is stock_qty going
    down by qty_used, same as a sale deducting branch_inventory.stock_qty.
    """
    material_id = request.form.get("material_id")
    production_log_id = request.form.get("production_log_id") or None
    notes = request.form.get("notes", "").strip() or None

    material = query(
        "SELECT material_name, unit FROM raw_materials WHERE material_id = %s",
        (material_id,), fetchone=True,
    )
    if not material:
        flash("Select a valid material.", "error")
        return redirect(url_for("admin.materials"))

    try:
        qty_used = parse_positive_decimal(
            request.form.get("qty_used"), "Quantity used")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.materials"))

    # Lock the material row for the check-then-deduct below, same
    # pattern as stock deductions elsewhere in this file (e.g.
    # dispatch_request) — so two usage entries logged for the same
    # material at the same moment can't jointly push stock_qty negative.
    with transaction() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT stock_qty FROM raw_materials WHERE material_id = %s FOR UPDATE",
            (material_id,),
        )
        row = cur.fetchone()
        if not row or qty_used > float(row["stock_qty"]):
            cur.close()
            on_hand = row["stock_qty"] if row else 0
            flash(
                f"Only {on_hand:g} {material['unit'].lower()} of "
                f"{material['material_name']} is on hand — can't log more than that as used.",
                "error",
            )
            return redirect(url_for("admin.materials"))

        cur.execute(
            """INSERT INTO material_usage_logs
               (material_id, production_log_id, qty_used, notes, created_by_user_id)
               VALUES (%s, %s, %s, %s, %s)""",
            (material_id, production_log_id, qty_used, notes, session.get("user_id")),
        )
        cur.execute(
            "UPDATE raw_materials SET stock_qty = stock_qty - %s WHERE material_id = %s",
            (qty_used, material_id),
        )
        cur.close()

    notify_all(["materials"])
    log_action("log_material_usage", target=material["material_name"],
               details=f"{qty_used:g} {material['unit'].lower()}")
    flash(
        f"Logged {qty_used:g} {material['unit'].lower()} of {material['material_name']} used.", "success")
    return redirect(url_for("admin.materials"))


# ---------------------------------------------------------------- suppliers
@bp.route("/materials/suppliers/add", methods=["POST"])
@admin_required
def add_supplier():
    supplier_name = request.form.get("supplier_name", "").strip()
    contact_person = request.form.get("contact_person", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    email = request.form.get("email", "").strip() or None
    address = request.form.get("address", "").strip() or None
    notes = request.form.get("notes", "").strip() or None

    if not supplier_name:
        flash("Supplier name is required.", "error")
        return redirect(url_for("admin.materials"))
    if len(supplier_name) > 100:
        flash("Supplier name is too long (max 100 characters).", "error")
        return redirect(url_for("admin.materials"))

    try:
        execute(
            """INSERT INTO suppliers (supplier_name, contact_person, phone, email, address, notes)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (supplier_name, contact_person, phone, email, address, notes),
        )
        notify_all(["materials"])
        log_action("add_supplier", target=supplier_name,
                   details=contact_person or None)
        flash(f"Supplier '{supplier_name}' added.", "success")
    except Exception:
        flash(f"'{supplier_name}' already exists in the suppliers list.", "error")
    return redirect(url_for("admin.materials"))


@bp.route("/materials/suppliers/edit", methods=["POST"])
@admin_required
def edit_supplier():
    supplier_id = request.form.get("supplier_id")
    supplier_name = request.form.get("supplier_name", "").strip()
    contact_person = request.form.get("contact_person", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    email = request.form.get("email", "").strip() or None
    address = request.form.get("address", "").strip() or None
    notes = request.form.get("notes", "").strip() or None

    if not supplier_name:
        flash("Supplier name is required.", "error")
        return redirect(url_for("admin.materials"))
    if len(supplier_name) > 100:
        flash("Supplier name is too long (max 100 characters).", "error")
        return redirect(url_for("admin.materials"))

    try:
        execute(
            """UPDATE suppliers
               SET supplier_name = %s, contact_person = %s, phone = %s, email = %s, address = %s, notes = %s
               WHERE supplier_id = %s""",
            (supplier_name, contact_person, phone,
             email, address, notes, supplier_id),
        )
        notify_all(["materials"])
        log_action("edit_supplier", target=supplier_name,
                   details=contact_person or None)
        flash(f"Supplier '{supplier_name}' updated.", "success")
    except Exception:
        flash(f"'{supplier_name}' already exists in the suppliers list.", "error")
    return redirect(url_for("admin.materials"))


# ---------------------------------------------------------------- capital
# The /capital route and admin/capital.html are removed — "Total Capital"
# is no longer a manually-logged ledger. It's now derived as
# SUM(package_cost) over raw_materials (see the dashboard() and
# reports_data() routes above), i.e. it always equals what's been spent
# on material packages. There's nothing left to log here; manage that
# spend from the Materials page instead.


# ---------------------------------------------------------------- partners (distributors & resellers)
@bp.route("/partners", methods=["GET", "POST"])
@admin_required
def partners():
    """Distributors and resellers HQ sells to in bulk, outside the retail
    branch network. This page is the partner directory — who they are
    and how to reach them.

    Partners don't "invest" in the old sense — there's no way to log a
    manual contribution here anymore. What used to be "Total invested"
    is now "Total package sales": the sum of order_amount across every
    inquiry this partner has made that's marked Closed (i.e. an admin
    has confirmed the order actually went through and was received —
    see partner_inquiries.status and its migration note in schema.sql).
    New/Contacted inquiries are leads, not sales, so they're excluded.

    partner_investments is no longer read here at all — nothing has
    written to it since packages/inquiries replaced manual investment
    logging, so it would only ever show stale, frozen figures alongside
    numbers that are actually still growing.
    """
    if request.method == "POST":
        return_type = request.form.get("return_type", "all")
        partner_type = request.form.get("partner_type", "").strip()
        name = request.form.get("partner_name", "").strip()
        contact_person = request.form.get("contact_person", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        email = request.form.get("email", "").strip() or None
        address = request.form.get("address", "").strip() or None
        notes = request.form.get("notes", "").strip() or None

        if partner_type not in PARTNER_TYPES:
            flash("Select whether this is a Distributor or a Reseller.", "error")
        elif not name:
            flash("Partner name is required.", "error")
        else:
            execute(
                """INSERT INTO partners
                       (partner_type, partner_name, contact_person, phone, email, address, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (partner_type, name, contact_person, phone, email, address, notes),
            )
            notify_all(["partners"])
            log_action("add_partner", target=name, details=partner_type)
            flash(f"{name} added as a {partner_type.lower()}.", "success")
        return redirect(url_for("admin.partners", type=return_type))

    type_filter = request.args.get("type", "all")
    where_sql = ""
    params = ()
    if type_filter in PARTNER_TYPES:
        where_sql = "WHERE p.partner_type = %s"
        params = (type_filter,)

    # order_amount is only ever counted once an inquiry is Closed — see
    # the docstring above and schema.sql's note on
    # partner_inquiries.order_amount. The AND sits inside the LEFT JOIN
    # (not a WHERE) so a partner with zero Closed orders still appears
    # in the list with 0 sales, rather than disappearing entirely.
    partner_list = query(
        f"""SELECT p.*,
                   COALESCE(SUM(pinq.order_amount), 0) AS total_invested,
                   COUNT(pinq.inquiry_id) AS investment_count,
                   MAX(pinq.created_at) AS last_investment_at
            FROM partners p
            LEFT JOIN partner_inquiries pinq
                ON pinq.partner_id = p.partner_id AND pinq.status = 'Closed'
            {where_sql}
            GROUP BY p.partner_id
            ORDER BY p.partner_name""",
        params,
    )

    totals = query(
        """SELECT
               COALESCE(SUM(pinq.order_amount), 0) AS total_all,
               COALESCE(SUM(CASE WHEN p.partner_type = 'Distributor' THEN pinq.order_amount ELSE 0 END), 0)
                   AS total_distributor,
               COALESCE(SUM(CASE WHEN p.partner_type = 'Reseller' THEN pinq.order_amount ELSE 0 END), 0)
                   AS total_reseller
           FROM partner_inquiries pinq
           JOIN partners p ON pinq.partner_id = p.partner_id
           WHERE pinq.status = 'Closed'""",
        fetchone=True,
    )
    counts = query(
        """SELECT
               COALESCE(SUM(CASE WHEN partner_type = 'Distributor' THEN 1 ELSE 0 END), 0)
                   AS distributor_count,
               COALESCE(SUM(CASE WHEN partner_type = 'Reseller' THEN 1 ELSE 0 END), 0)
                   AS reseller_count
           FROM partners""",
        fetchone=True,
    )

    portal_link = url_for(
        "portal.packages", slug=current_app.config.get("PARTNER_PORTAL_SLUG", ""), _external=True,
    )

    return render_template(
        "admin/partners.html",
        partner_list=partner_list, type_filter=type_filter,
        totals=totals, counts=counts, partner_types=PARTNER_TYPES,
        portal_link=portal_link,
    )


@bp.route("/partners/<int:partner_id>")
@admin_required
def partner_detail(partner_id):
    """One partner's full inquiry history — every package inquiry they've
    ever submitted through the public portal, in the exact form they
    submitted it in. This is the permanent record referenced in
    portal.py's _find_or_create_partner(): a repeat inquiry from a
    known email/phone no longer overwrites the partner's name/contact/
    address above (see partners.html), so this page is where the full
    story — including whatever changed between visits — actually lives.
    partners.last_inquiry_at/inquiry_count are just a rollup of what's
    queried here, kept in sync on every new inquiry.
    """
    partner = query("SELECT * FROM partners WHERE partner_id = %s", (partner_id,), fetchone=True)
    if not partner:
        abort(404)

    inquiries = query(
        """SELECT * FROM partner_inquiries
           WHERE partner_id = %s ORDER BY created_at DESC""",
        (partner_id,),
    )

    return render_template(
        "admin/partner_detail.html", partner=partner, inquiries=inquiries,
    )


# NOTE: partners no longer "invest" — a distributor/reseller's money comes
# from the packages they order and Close (see the Packages tab and
# partners()'s "Total invested" query above, which sums order_amount on
# Closed partner_inquiries rows). The old POST /admin/partners/investment
# route has been removed, and partner_investments is no longer read
# anywhere in this file — the table itself is left in place purely so any
# pre-existing rows in it aren't destructively dropped, but nothing
# reads or writes it anymore.


# ---------------------------------------------------------------- packages (distributor/reseller bundles)
def _package_value(discount_percent, reference_total):
    """Apply a package's discount_percent to a reference total, returning
    (reference_total, discounted_total) as Decimals. Shared by the list
    and detail views so the math can't drift between them."""
    reference_total = decimal.Decimal(reference_total)
    discount_percent = decimal.Decimal(discount_percent)
    discounted_total = reference_total * (decimal.Decimal("1") - (discount_percent / decimal.Decimal("100")))
    return reference_total, discounted_total


@bp.route("/packages", methods=["GET", "POST"])
@admin_required
def packages():
    """Bundles of products HQ offers distributors/resellers at a discount.

    Creating a package here only sets its shell (name, discount, who it's
    for) — products are added one at a time on its own detail page (see
    package_detail() below), the same two-step shape as adding a branch
    then assigning it stock.
    """
    if request.method == "POST":
        name = request.form.get("package_name", "").strip()
        description = request.form.get("description", "").strip() or None
        scope = request.form.get("partner_scope", "Both")
        if scope not in ("Both",) + PARTNER_TYPES:
            scope = "Both"

        try:
            discount = parse_non_negative_decimal(
                request.form.get("discount_percent") or 0, "Discount")
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.packages"))

        if discount > 100:
            flash("Discount can't exceed 100%.", "error")
            return redirect(url_for("admin.packages"))
        if not name:
            flash("Package name is required.", "error")
            return redirect(url_for("admin.packages"))

        new_id, _ = execute(
            """INSERT INTO packages (package_name, description, partner_scope, discount_percent)
               VALUES (%s, %s, %s, %s)""",
            (name, description, scope, discount),
        )
        notify_all(["packages"])
        log_action("add_package", target=name, details=f"{discount}% off, {scope}")
        flash(f"{name} created — add products to it below.", "success")
        return redirect(url_for("admin.package_detail", package_id=new_id))

    package_rows = query(
        """SELECT pkg.*, COUNT(pi.package_item_id) AS item_count,
                  COALESCE(SUM(pi.qty * p.price), 0) AS reference_total
           FROM packages pkg
           LEFT JOIN package_items pi ON pi.package_id = pkg.package_id
           LEFT JOIN products p ON p.sku = pi.sku
           GROUP BY pkg.package_id
           ORDER BY pkg.created_at DESC"""
    )
    package_list = []
    for row in package_rows:
        reference_total, discounted_total = _package_value(row["discount_percent"], row["reference_total"])
        row["reference_total"] = reference_total
        row["discounted_total"] = discounted_total
        package_list.append(row)

    return render_template(
        "admin/packages.html", package_list=package_list, partner_types=PARTNER_TYPES,
    )


@bp.route("/packages/<int:package_id>")
@admin_required
def package_detail(package_id):
    pkg = query("SELECT * FROM packages WHERE package_id = %s", (package_id,), fetchone=True)
    if not pkg:
        abort(404)

    items = query(
        """SELECT pi.package_item_id, pi.sku, pi.qty, p.item_name, p.variant, p.unit, p.price
           FROM package_items pi JOIN products p ON pi.sku = p.sku
           WHERE pi.package_id = %s ORDER BY p.item_name""",
        (package_id,),
    )
    reference_total = sum(
        (decimal.Decimal(i["qty"]) * decimal.Decimal(i["price"]) for i in items), decimal.Decimal("0")
    )
    reference_total, discounted_total = _package_value(pkg["discount_percent"], reference_total)

    existing_skus = {i["sku"] for i in items}
    catalog = query("SELECT sku, item_name, variant, unit, price FROM products ORDER BY item_name")
    available_products = [p for p in catalog if p["sku"] not in existing_skus]

    return render_template(
        "admin/package_detail.html",
        pkg=pkg, items=items, reference_total=reference_total, discounted_total=discounted_total,
        available_products=available_products, partner_types=PARTNER_TYPES,
    )


@bp.route("/packages/<int:package_id>/edit", methods=["POST"])
@admin_required
def edit_package(package_id):
    pkg = query("SELECT package_name FROM packages WHERE package_id = %s", (package_id,), fetchone=True)
    if not pkg:
        abort(404)

    name = request.form.get("package_name", "").strip()
    description = request.form.get("description", "").strip() or None
    scope = request.form.get("partner_scope", "Both")
    if scope not in ("Both",) + PARTNER_TYPES:
        scope = "Both"
    is_active = request.form.get("is_active") == "1"

    try:
        discount = parse_non_negative_decimal(
            request.form.get("discount_percent") or 0, "Discount")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.package_detail", package_id=package_id))

    if discount > 100:
        flash("Discount can't exceed 100%.", "error")
        return redirect(url_for("admin.package_detail", package_id=package_id))
    if not name:
        flash("Package name is required.", "error")
        return redirect(url_for("admin.package_detail", package_id=package_id))

    execute(
        """UPDATE packages SET package_name = %s, description = %s, partner_scope = %s,
               discount_percent = %s, is_active = %s WHERE package_id = %s""",
        (name, description, scope, discount, is_active, package_id),
    )
    notify_all(["packages"])
    log_action("edit_package", target=name, details=f"{discount}% off, {scope}")
    flash("Package updated.", "success")
    return redirect(url_for("admin.package_detail", package_id=package_id))


@bp.route("/packages/<int:package_id>/items", methods=["POST"])
@admin_required
def add_package_item(package_id):
    pkg = query("SELECT package_name FROM packages WHERE package_id = %s", (package_id,), fetchone=True)
    if not pkg:
        abort(404)

    sku = request.form.get("sku", "").strip()
    product = query("SELECT sku FROM products WHERE sku = %s", (sku,), fetchone=True)

    try:
        qty = parse_positive_int(request.form.get("qty"), "Quantity")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.package_detail", package_id=package_id))

    if not product:
        flash("Select a valid product.", "error")
        return redirect(url_for("admin.package_detail", package_id=package_id))

    try:
        execute(
            "INSERT INTO package_items (package_id, sku, qty) VALUES (%s, %s, %s)",
            (package_id, sku, qty),
        )
    except Exception:
        flash(
            "That product is already in this package — remove it first if you need to change its quantity.",
            "error",
        )
        return redirect(url_for("admin.package_detail", package_id=package_id))

    notify_all(["packages"])
    flash("Product added to package.", "success")
    return redirect(url_for("admin.package_detail", package_id=package_id))


@bp.route("/packages/<int:package_id>/items/remove", methods=["POST"])
@admin_required
def remove_package_item(package_id):
    pkg = query("SELECT package_name FROM packages WHERE package_id = %s", (package_id,), fetchone=True)
    if not pkg:
        abort(404)

    item_id = request.form.get("package_item_id")
    execute(
        "DELETE FROM package_items WHERE package_item_id = %s AND package_id = %s",
        (item_id, package_id),
    )

    remaining = query(
        "SELECT COUNT(*) c FROM package_items WHERE package_id = %s", (package_id,), fetchone=True
    )
    if remaining["c"] == 0:
        # An empty package can't be ordered/inquired about anyway — the
        # confirm dialog on package_detail.html already warned the admin
        # this exact removal would empty it out, so deleting it here
        # (rather than leaving a dangling zero-product shell around) is
        # the outcome they already agreed to.
        execute("DELETE FROM packages WHERE package_id = %s", (package_id,))
        notify_all(["packages"])
        log_action("edit_package", target=pkg["package_name"], details="Deleted — last product removed")
        flash(f"\"{pkg['package_name']}\" had no products left, so it was deleted.", "success")
        return redirect(url_for("admin.packages"))

    notify_all(["packages"])
    flash("Product removed from package.", "success")
    return redirect(url_for("admin.package_detail", package_id=package_id))


# ---------------------------------------------------------------- partner inquiries
# History of every inquiry a distributor/reseller has submitted through
# the public partner portal (see routes/portal.py + partner_inquiries in
# schema.sql). This is the "Partner Inquiries" page referenced in
# portal.py's module docstring — read-only except for two admin-only
# fields layered on top of the permanent record: `status`, a triage
# pipeline an admin can move through while following up, and `remarks`,
# a free-text internal note (e.g. "on hold, still deciding" or "follow
# up next week") that's independent of status and never shown to the
# partner. Nothing about what was actually *submitted* is ever edited;
# see the note on partner_inquiries in schema.sql for why — it's a
# permanent record.
INQUIRY_STATUSES = ("New", "Contacted", "Follow-up", "On Hold", "Closed", "Declined")


@bp.route("/partners/inquiries")
@admin_required
def partner_inquiries():
    status_filter = request.args.get("status", "all")
    sql = "SELECT * FROM partner_inquiries"
    params = ()
    if status_filter in INQUIRY_STATUSES:
        sql += " WHERE status = %s"
        params = (status_filter,)
    sql += " ORDER BY created_at DESC LIMIT 300"
    inquiries = query(sql, params)

    # in_progress_count groups Follow-up and On Hold together for the
    # stat tile — both mean "not new, not decided yet", just for a
    # different reason (needs a nudge vs. the partner asked to wait).
    # declined_count doesn't get its own tile; it's called out in the
    # Closed tile's footer instead so the row of tiles stays at four.
    counts = query(
        """SELECT
               COUNT(*) AS total_count,
               COALESCE(SUM(CASE WHEN status = 'New' THEN 1 ELSE 0 END), 0) AS new_count,
               COALESCE(SUM(CASE WHEN status = 'Contacted' THEN 1 ELSE 0 END), 0) AS contacted_count,
               COALESCE(SUM(CASE WHEN status IN ('Follow-up', 'On Hold') THEN 1 ELSE 0 END), 0) AS in_progress_count,
               COALESCE(SUM(CASE WHEN status = 'Closed' THEN 1 ELSE 0 END), 0) AS closed_count,
               COALESCE(SUM(CASE WHEN status = 'Declined' THEN 1 ELSE 0 END), 0) AS declined_count
           FROM partner_inquiries""",
        fetchone=True,
    )

    portal_link = url_for(
        "portal.packages", slug=current_app.config.get("PARTNER_PORTAL_SLUG", ""), _external=True,
    )

    return render_template(
        "admin/partner_inquiries.html",
        inquiries=inquiries, status_filter=status_filter, counts=counts,
        inquiry_statuses=INQUIRY_STATUSES, portal_link=portal_link,
    )


@bp.route("/partners/inquiries/<int:inquiry_id>/status", methods=["POST"])
@admin_required
def update_inquiry_status(inquiry_id):
    return_status = request.form.get("return_status", "all")
    new_status = request.form.get("status")

    if new_status not in INQUIRY_STATUSES:
        flash("Select a valid status.", "error")
        return redirect(url_for("admin.partner_inquiries", status=return_status))

    inquiry = query(
        "SELECT company_name FROM partner_inquiries WHERE inquiry_id = %s",
        (inquiry_id,), fetchone=True,
    )
    if not inquiry:
        flash("That inquiry no longer exists.", "error")
        return redirect(url_for("admin.partner_inquiries", status=return_status))

    execute(
        "UPDATE partner_inquiries SET status = %s WHERE inquiry_id = %s",
        (new_status, inquiry_id),
    )
    notify_admin(["partner_inquiries"])
    log_action("update_inquiry_status", target=inquiry["company_name"], details=new_status)
    flash(f"Marked {inquiry['company_name']}'s inquiry as {new_status}.", "success")
    return redirect(url_for("admin.partner_inquiries", status=return_status))


@bp.route("/partners/inquiries/<int:inquiry_id>/remarks", methods=["POST"])
@admin_required
def update_inquiry_remarks(inquiry_id):
    """Save (or clear) an admin's internal note on one inquiry.

    Independent of `status` — a note like "on hold, still comparing
    packages" can sit alongside any status and gets edited in place
    (no history of past remarks is kept, unlike the inquiry's own
    submitted fields). Never shown to the partner; this only ever
    appears inside the signed-in admin app.
    """
    return_status = request.form.get("return_status", "all")
    remarks = request.form.get("remarks", "").strip()

    if len(remarks) > 1000:
        flash("Remarks must be under 1000 characters.", "error")
        return redirect(url_for("admin.partner_inquiries", status=return_status))

    inquiry = query(
        "SELECT company_name FROM partner_inquiries WHERE inquiry_id = %s",
        (inquiry_id,), fetchone=True,
    )
    if not inquiry:
        flash("That inquiry no longer exists.", "error")
        return redirect(url_for("admin.partner_inquiries", status=return_status))

    execute(
        "UPDATE partner_inquiries SET remarks = %s WHERE inquiry_id = %s",
        (remarks or None, inquiry_id),
    )
    notify_admin(["partner_inquiries"])
    log_action(
        "update_inquiry_remarks", target=inquiry["company_name"],
        details=(remarks[:100] if remarks else "Remarks cleared"),
    )
    flash(f"Remarks saved for {inquiry['company_name']}'s inquiry.", "success")
    return redirect(url_for("admin.partner_inquiries", status=return_status))
