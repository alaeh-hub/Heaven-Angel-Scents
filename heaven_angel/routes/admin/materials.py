
from flask import (
    current_app, flash, redirect, render_template,
    request, session, url_for,
)

from heaven_angel.db import TransactionAborted, execute, query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_all
from heaven_angel.utils import (
    MATERIAL_UNITS, ValidationError,
    parse_non_negative_decimal, parse_optional_id,
    parse_past_date, parse_positive_decimal,
)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- materials
@bp.route("/materials", methods=["GET", "POST"])
@admin_required
def materials():
    if request.method == "POST":
        material_name = request.form.get("material_name", "").strip()
        unit = request.form.get("unit")
        purchase_mode = request.form.get("purchase_mode", "Package")
        if purchase_mode not in ("Package", "Individual"):
            purchase_mode = "Package"
        receipt_number = request.form.get("receipt_number", "").strip() or None
        if receipt_number and len(receipt_number) > 60:
            flash("Receipt number must be under 60 characters.", "error")
            return redirect(url_for("admin.materials"))

        try:
            if not material_name or unit not in MATERIAL_UNITS:
                raise ValidationError(
                    "Please fill in every field with a valid value.")
            # Package vs Individual is purely descriptive now — both modes
            # require a real quantity (e.g. 5 individually-purchased
            # pieces is package_qty=5, purchase_mode='Individual').
            package_qty = parse_positive_decimal(
                request.form.get("package_qty"), "Quantity")
            package_cost = parse_non_negative_decimal(
                request.form.get("package_cost"), "Cost")
            supplier_id = parse_optional_id(
                request.form.get("supplier_id"), "Supplier")
            # Lets a material bought a while ago be logged under the date it
            # was actually purchased instead of today — blank defaults to
            # today, same as the old always-"now" behavior.
            purchased_at = parse_past_date(
                request.form.get("purchased_at"), "Purchase date")
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
                       (material_name, unit, purchase_mode, package_qty, package_cost,
                        receipt_number, cost_per_unit, stock_qty, supplier_id, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (material_name, unit, purchase_mode, package_qty, package_cost,
                 receipt_number, cost_per_unit, package_qty, supplier_id, purchased_at),
            )
            notify_all(["materials"])
            log_action("add_material", target=material_name,
                       details=f"{package_qty:g} {unit} for ₱{package_cost:,.2f} — ₱{cost_per_unit:,.4f}/{unit.lower()}"
                       + (f" — receipt {receipt_number}" if receipt_number else ""))
            flash(
                f"{material_name} added at ₱{cost_per_unit:,.4f} per {unit.lower()}.", "success")
        except Exception:
            current_app.logger.exception(
                "add material failed for material_name=%s", material_name)
            flash(
                f"'{material_name}' already exists in the materials list.", "error")
        return redirect(url_for("admin.materials"))

    materials_list = query(
        """SELECT rm.*, s.supplier_name
           FROM raw_materials rm
           LEFT JOIN suppliers s ON rm.supplier_id = s.supplier_id
           ORDER BY rm.material_name"""
    )

    # Suppliers directory — merged onto this same page (Materials and
    # Suppliers used to be separate tabs, but there was nothing on
    # either that the other didn't already need: the Materials form's
    # supplier picker duplicated this exact list, and this table is
    # purely rolled up from raw_materials rows entered above). `suppliers`
    # doubles as the Add/Edit material modals' supplier <select> options.
    suppliers_list = query(
        """SELECT s.supplier_id, s.supplier_name, s.contact_person, s.phone, s.email,
                  s.address, s.notes, s.created_at,
                  COUNT(DISTINCT rm.material_id) AS material_count,
                  COALESCE(SUM(rm.package_cost), 0) AS total_spent,
                  MAX(CASE WHEN COALESCE(re.last_restock_at, rm.created_at) > rm.created_at
                           THEN re.last_restock_at ELSE rm.created_at END) AS last_purchase_at
           FROM suppliers s
           LEFT JOIN raw_materials rm ON rm.supplier_id = s.supplier_id
           LEFT JOIN (
               SELECT material_id, MAX(purchased_at) AS last_restock_at
               FROM material_restock_events GROUP BY material_id
           ) re ON re.material_id = rm.material_id
           GROUP BY s.supplier_id, s.supplier_name, s.contact_person, s.phone, s.email,
                    s.address, s.notes, s.created_at
           ORDER BY total_spent DESC, s.supplier_name"""
    )
    # One row per supplier/material. Lifetime quantity and cost remain on
    # raw_materials; dated restock events add accurate purchase counts.
    material_rows = query(
        """SELECT rm.supplier_id, rm.material_name, rm.unit,
                  1 + COALESCE(re.restock_count, 0) AS purchase_count,
                  rm.package_qty AS total_qty,
                  rm.package_cost AS total_cost
           FROM raw_materials rm
           LEFT JOIN (
               SELECT material_id, COUNT(*) AS restock_count
               FROM material_restock_events GROUP BY material_id
           ) re ON re.material_id = rm.material_id
           WHERE rm.supplier_id IS NOT NULL
           ORDER BY rm.material_name"""
    )
    materials_by_supplier = {}
    for row in material_rows:
        materials_by_supplier.setdefault(row["supplier_id"], []).append({
            "material_name": row["material_name"],
            "unit": row["unit"],
            "purchase_count": row["purchase_count"],
            "total_qty": float(row["total_qty"]),
            "total_cost": float(row["total_cost"]),
        })
    for s in suppliers_list:
        s["materials_supplied"] = materials_by_supplier.get(
            s["supplier_id"], [])

    supplier_totals = query(
        """SELECT (SELECT COUNT(*) FROM suppliers) AS supplier_count,
                  COALESCE(SUM(rm.package_cost), 0) AS total_supplied_spend,
                  (SELECT COUNT(*) FROM raw_materials WHERE supplier_id IS NULL) AS unassigned_material_count
           FROM raw_materials rm WHERE rm.supplier_id IS NOT NULL""",
        fetchone=True,
    )

    return render_template(
        "admin/materials.html",
        materials=materials_list,
        suppliers=suppliers_list,
        supplier_totals=supplier_totals,
        unit_choices=MATERIAL_UNITS,
    )


@bp.route("/materials/edit", methods=["POST"])
@admin_required
def edit_material():
    material_id = request.form.get("material_id")

    # Checked up front, same convention as edit_product(): a stale or
    # tampered material_id would otherwise let the UPDATE below silently
    # match zero rows while this route still flashes a success message,
    # as if the edit had actually landed somewhere.
    existing = query(
        "SELECT unit FROM raw_materials WHERE material_id = %s", (material_id,), fetchone=True
    )
    if not existing:
        flash("That material no longer exists.", "error")
        return redirect(url_for("admin.materials"))

    material_name = request.form.get("material_name", "").strip()
    unit = request.form.get("unit")
    purchase_mode = request.form.get("purchase_mode", "Package")
    if purchase_mode not in ("Package", "Individual"):
        purchase_mode = "Package"
    receipt_number = request.form.get("receipt_number", "").strip() or None
    if receipt_number and len(receipt_number) > 60:
        flash("Receipt number must be under 60 characters.", "error")
        return redirect(url_for("admin.materials"))

    try:
        if not material_name or unit not in MATERIAL_UNITS:
            raise ValidationError(
                "Please fill in every field with a valid value.")
        if unit != existing["unit"]:
            raise ValidationError(
                "A material's unit cannot be changed after creation. Add a separate material with the correct unit.")
        package_qty = parse_positive_decimal(
            request.form.get("package_qty"), "Quantity")
        package_cost = parse_non_negative_decimal(
            request.form.get("package_cost"), "Cost")
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
    # carry any cost of their own (see production()), so this recompute
    # has no effect on past usage history the way it used to.
    cost_per_unit = package_cost / package_qty

    try:
        execute(
            """UPDATE raw_materials
               SET material_name = %s, purchase_mode = %s, package_qty = %s,
                   package_cost = %s, receipt_number = %s, cost_per_unit = %s, supplier_id = %s
               WHERE material_id = %s""",
            (material_name, purchase_mode, package_qty, package_cost,
             receipt_number, cost_per_unit, supplier_id, material_id),
        )
        notify_all(["materials"])
        log_action("edit_material", target=material_name,
                   details=f"{package_qty:g} {unit} for ₱{package_cost:,.2f} — ₱{cost_per_unit:,.4f}/{unit.lower()}"
                   + (f" — receipt {receipt_number}" if receipt_number else ""))
        flash(
            f"{material_name} updated at ₱{cost_per_unit:,.4f} per {unit.lower()}.", "success")
    except Exception:
        current_app.logger.exception(
            "edit material failed for material_id=%s material_name=%s", material_id, material_name)
        flash(f"'{material_name}' already exists in the materials list.", "error")
    return redirect(url_for("admin.materials"))


@bp.route("/materials/restock", methods=["POST"])
@admin_required
def restock_material():
    """Record buying more of an existing material — adds to stock_qty and
    rolls the new purchase into the running package_qty/package_cost
    totals, so cost_per_unit becomes a weighted average. A separate dated
    event preserves each restock for supplier and date-range history.
    Kept
    separate from edit_material(), which stays a plain correction tool
    for the record's own fields and never touches stock_qty — Add
    Material can't be resubmitted for the same name (material_name is
    unique), so this is what "buying more of something already on file"
    means now.
    """
    material_id = request.form.get("material_id")

    try:
        qty = parse_positive_decimal(request.form.get("qty"), "Quantity")
        cost = parse_non_negative_decimal(request.form.get("cost"), "Cost")
        purchased_at = parse_past_date(request.form.get("purchased_at"), "Purchase date")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.materials"))

    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """SELECT material_id, material_name, unit, package_qty, package_cost, supplier_id
                   FROM raw_materials WHERE material_id = %s FOR UPDATE""",
                (material_id,),
            )
            existing = cur.fetchone()
            if not existing:
                raise TransactionAborted("That material no longer exists.")
            new_package_qty = existing["package_qty"] + qty
            new_package_cost = existing["package_cost"] + cost
            new_cost_per_unit = new_package_cost / new_package_qty
            cur.execute(
                """UPDATE raw_materials
                   SET package_qty = %s, package_cost = %s, cost_per_unit = %s,
                       stock_qty = stock_qty + %s
                   WHERE material_id = %s""",
                (new_package_qty, new_package_cost, new_cost_per_unit, qty, material_id),
            )
            supplier_name = None
            if existing["supplier_id"] is not None:
                cur.execute("SELECT supplier_name FROM suppliers WHERE supplier_id = %s",
                            (existing["supplier_id"],))
                supplier_row = cur.fetchone()
                supplier_name = supplier_row["supplier_name"] if supplier_row else None
            cur.execute(
                """INSERT INTO material_restock_events
                       (material_id, material_name_snapshot, unit_snapshot, qty, cost,
                        supplier_id, supplier_name_snapshot, purchased_at, created_by_user_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (material_id, existing["material_name"], existing["unit"], qty, cost,
                 existing["supplier_id"], supplier_name, purchased_at, session.get("user_id")),
            )
            cur.close()
    except TransactionAborted as err:
        flash(str(err), "error")
        return redirect(url_for("admin.materials"))
    except Exception:
        current_app.logger.exception("restock failed for material_id=%s", material_id)
        flash("Couldn't record this restock — please try again.", "error")
        return redirect(url_for("admin.materials"))
    notify_all(["materials"])
    log_action("restock_material", target=existing["material_name"],
               details=f"+{qty:g} for ₱{cost:,.2f} — now ₱{new_cost_per_unit:,.4f}/unit avg")
    flash(
        f"{existing['material_name']} restocked — +{qty:g} in stock.", "success")
    return redirect(url_for("admin.materials"))


# ---------------------------------------------------------------- suppliers
# Suppliers no longer has its own tab/page — it's merged onto the
# Materials page (see materials() above), which was the only place these
# add/edit forms were ever posted from anyway once the two pages became
# one. Both routes always redirect back to admin.materials now.
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
        # Lets a supplier that's been on hand for a while be logged as of
        # when they were actually onboarded — blank defaults to today.
        added_on = parse_past_date(
            request.form.get("added_on"), "Date added")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.materials"))

    try:
        execute(
            """INSERT INTO suppliers (supplier_name, contact_person, phone, email, address, notes, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (supplier_name, contact_person, phone, email, address, notes, added_on),
        )
        notify_all(["materials"])
        log_action("add_supplier", target=supplier_name,
                   details=contact_person or None)
        flash(f"Supplier '{supplier_name}' added.", "success")
    except Exception:
        current_app.logger.exception(
            "add supplier failed for supplier_name=%s", supplier_name)
        flash(f"'{supplier_name}' already exists in the suppliers list.", "error")
    return redirect(url_for("admin.materials"))


@bp.route("/materials/suppliers/edit", methods=["POST"])
@admin_required
def edit_supplier():
    supplier_id = request.form.get("supplier_id")

    # Same reasoning as edit_material()'s existence check: without this,
    # a stale/tampered supplier_id makes the UPDATE below match zero
    # rows while still flashing success, as if it had actually landed.
    existing = query(
        "SELECT 1 FROM suppliers WHERE supplier_id = %s", (supplier_id,), fetchone=True
    )
    if not existing:
        flash("That supplier no longer exists.", "error")
        return redirect(url_for("admin.materials"))

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
        current_app.logger.exception(
            "edit supplier failed for supplier_id=%s supplier_name=%s", supplier_id, supplier_name)
        flash(f"'{supplier_name}' already exists in the suppliers list.", "error")
    return redirect(url_for("admin.materials"))


@bp.route("/suppliers")
@admin_required
def suppliers():
    """Old standalone Suppliers tab — now merged onto the Materials page
    (see materials() above). Kept as a redirect so any old bookmark or
    link still lands somewhere sensible instead of 404ing."""
    return redirect(url_for("admin.materials"))


# ---------------------------------------------------------------- capital
# The /capital route and admin/capital.html are removed — "Total Capital"
# is no longer a manually-logged ledger. It's now derived as
# SUM(package_cost) over raw_materials (see the dashboard() and
# reports_data() routes above), i.e. it always equals what's been spent
# on material packages. There's nothing left to log here; manage that
# spend from the Materials page instead.
