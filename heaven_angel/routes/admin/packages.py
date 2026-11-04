import decimal

from flask import (
    abort, current_app, flash, redirect, render_template,
    request, url_for,
)

from heaven_angel.db import execute, query
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_all
from heaven_angel.utils import (
    PARTNER_TYPES,
    ValidationError,
    parse_non_negative_decimal, parse_positive_int,
)

from heaven_angel.services.finance import package_value
from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- packages (distributor/reseller bundles)
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
        log_action("add_package", target=name,
                   details=f"{discount}% off, {scope}")
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
        reference_total, discounted_total = package_value(
            row["discount_percent"], row["reference_total"])
        row["reference_total"] = reference_total
        row["discounted_total"] = discounted_total
        package_list.append(row)

    return render_template(
        "admin/packages.html", package_list=package_list, partner_types=PARTNER_TYPES,
    )


# Packages hold finished bottles only; Bulk/Refill stock (category, and
# its BULK packaging size, see utils.PRODUCT_CATEGORIES) is left out.
_BOTTLED_ONLY = "category <> 'Bulk/Refill' AND unit <> 'BULK'"


@bp.route("/packages/<int:package_id>")
@admin_required
def package_detail(package_id):
    pkg = query("SELECT * FROM packages WHERE package_id = %s",
                (package_id,), fetchone=True)
    if not pkg:
        abort(404)

    items = query(
        """SELECT pi.package_item_id, pi.sku, pi.qty, p.item_name, p.variant, p.unit, p.price
           FROM package_items pi JOIN products p ON pi.sku = p.sku
           WHERE pi.package_id = %s ORDER BY p.item_name""",
        (package_id,),
    )
    reference_total = sum(
        (decimal.Decimal(i["qty"]) * decimal.Decimal(i["price"])
         for i in items), decimal.Decimal("0")
    )
    reference_total, discounted_total = package_value(
        pkg["discount_percent"], reference_total)

    # Bottled products only: packages are bundles of finished bottles,
    # so Bulk/Refill stock never shows up as an option here (and
    # add_package_item() refuses it too). Sent to the page as JSON for
    # its searchable product field, sorted so each scent's sizes sit
    # together, smallest first.
    existing_skus = {i["sku"] for i in items}
    catalog = query(
        f"""SELECT sku, item_name, variant, unit, price FROM products
            WHERE {_BOTTLED_ONLY}
            ORDER BY item_name, variant, FIELD(unit, '3ML', '10ML', '50ML', '85ML')""")
    available_products = [
        {**p, "price": float(p["price"])} for p in catalog if p["sku"] not in existing_skus
    ]

    return render_template(
        "admin/package_detail.html",
        pkg=pkg, items=items, reference_total=reference_total, discounted_total=discounted_total,
        available_products=available_products, partner_types=PARTNER_TYPES,
    )


@bp.route("/packages/<int:package_id>/edit", methods=["POST"])
@admin_required
def edit_package(package_id):
    pkg = query("SELECT package_name FROM packages WHERE package_id = %s",
                (package_id,), fetchone=True)
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
    log_action("edit_package", target=name,
               details=f"{discount}% off, {scope}")
    flash("Package updated.", "success")
    return redirect(url_for("admin.packages"))


@bp.route("/packages/<int:package_id>/items", methods=["POST"])
@admin_required
def add_package_item(package_id):
    pkg = query("SELECT package_name FROM packages WHERE package_id = %s",
                (package_id,), fetchone=True)
    if not pkg:
        abort(404)

    sku = request.form.get("sku", "").strip()
    product = query(f"SELECT sku FROM products WHERE sku = %s AND {_BOTTLED_ONLY}",
                    (sku,), fetchone=True)

    try:
        qty = parse_positive_int(request.form.get("qty"), "Quantity")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.package_detail", package_id=package_id))

    if not product:
        flash("Select a bottled product from the list. Bulk/Refill products can't go in a package.", "error")
        return redirect(url_for("admin.package_detail", package_id=package_id))

    try:
        execute(
            "INSERT INTO package_items (package_id, sku, qty) VALUES (%s, %s, %s)",
            (package_id, sku, qty),
        )
    except Exception:
        current_app.logger.exception(
            "add_package_item failed for package_id=%s sku=%s", package_id, sku)
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
    pkg = query("SELECT package_name FROM packages WHERE package_id = %s",
                (package_id,), fetchone=True)
    if not pkg:
        abort(404)

    item_id = request.form.get("package_item_id")
    # Same reasoning as reject_request()/edit_material(): without checking
    # rowcount, a stale page (item already removed by another admin, or a
    # double-submit) would DELETE zero rows but still fall through and
    # flash "Product removed" as if it had actually happened.
    _, rowcount = execute(
        "DELETE FROM package_items WHERE package_item_id = %s AND package_id = %s",
        (item_id, package_id),
    )
    if rowcount == 0:
        flash("That product is no longer in this package.", "error")
        return redirect(url_for("admin.package_detail", package_id=package_id))

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
        log_action(
            "edit_package", target=pkg["package_name"], details="Deleted — last product removed")
        flash(
            f"\"{pkg['package_name']}\" had no products left, so it was deleted.", "success")
        return redirect(url_for("admin.packages"))

    notify_all(["packages"])
    flash("Product removed from package.", "success")
    return redirect(url_for("admin.package_detail", package_id=package_id))
