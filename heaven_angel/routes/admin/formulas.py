import decimal

from flask import (
    flash, redirect, render_template,
    request, url_for,
)

from heaven_angel.db import execute, query
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_all
from heaven_angel.utils import (
    FORMULA_UNITS, ValidationError,
    parse_non_negative_decimal, parse_positive_decimal, parse_positive_int,
)

from heaven_angel.routes.admin._common import bp


def load_formula_items_by_unit():
    """Every packaging size's reference packaging-materials list (see
    unit_formula_items in schema.sql), keyed by unit, with each line's
    cost computed LIVE from the material's current cost_per_unit — never
    trusted from unit_formula_items.line_cost itself, so editing a
    material's cost is reflected immediately. Shared by formulas() (to
    edit/display the recipe) and bulk_batches() (to fold a size's
    packaging total into a batch's own reference cost-per-bottle).
    """
    rows = query(
        """SELECT ufi.formula_item_id, ufi.unit, ufi.material_id, ufi.qty_per_unit,
                  rm.material_name, rm.unit AS material_unit, rm.cost_per_unit
           FROM unit_formula_items ufi
           JOIN raw_materials rm ON rm.material_id = ufi.material_id
           ORDER BY ufi.unit, rm.material_name"""
    )
    items_by_unit = {u: [] for u in FORMULA_UNITS}
    for row in rows:
        row["line_cost"] = (row["qty_per_unit"] * row["cost_per_unit"]).quantize(
            decimal.Decimal("0.0001"))
        items_by_unit.setdefault(row["unit"], []).append(row)
    return items_by_unit


# ---------------------------------------------------------------- unit formulas (COGS)
@bp.route("/formulas")
@admin_required
def formulas():
    """Cost-of-goods settings: the Bulk/Refill rate per mL (unchanged —
    see bulk_rate_settings/save_bulk_rate() below), a flat base cost per
    Bottled packaging size (see unit_cogs_settings in schema.sql) — this
    is what production() actually charges a Bottled run — plus a
    reference-only packaging materials formula per size (see
    unit_formula_items/formula_items_by_unit() and
    add_formula_item()/delete_formula_item() below). The formula's
    computed total is only ever a reference for setting the base cost
    above by hand; nothing here feeds production()'s cogs_per_unit
    automatically. Each size's own cost-per-new-bottle/cost-per-refill
    reference is pulled straight from whichever active bulk batch(es)
    actually targeted that size at creation (see create_bulk_batch()'s
    target_unit/target_bottle_qty) — a size nobody has targeted yet
    shows no reference, rather than a generic cross-batch guess.
    """
    bulk_rate = query(
        "SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1", fetchone=True,
    )["rate_per_ml"]
    # Same idea as the per-size reference below, but for the one shared
    # Bulk/Refill rate: averaged all-in cost per mL (total_cost ÷
    # total_volume_ml — same figure the Bulk Batches page itself shows
    # as each batch's "Cost / mL", see all_in_cost_per_ml there) across
    # every active batch (remaining_ml > 0), so the two pages always
    # agree on what a batch "costs per mL" rather than this one quietly
    # using the liquid-only cost_per_ml column instead. Purely
    # informational, same as the Bottled reference above — Save rate
    # still has to be clicked by hand.
    active_batches_for_rate = query(
        "SELECT total_cost, total_volume_ml FROM bulk_batches WHERE remaining_ml > 0")
    reference_rate_per_ml = None
    reference_rate_batch_count = len(active_batches_for_rate)
    if active_batches_for_rate:
        all_in_rates = [
            b["total_cost"] / b["total_volume_ml"]
            for b in active_batches_for_rate if b["total_volume_ml"]
        ]
        if all_in_rates:
            reference_rate_per_ml = (
                sum(all_in_rates, decimal.Decimal("0")) / len(all_in_rates)
            ).quantize(decimal.Decimal("0.0001"))
    cogs_rows = query(
        "SELECT unit, base_cost_per_unit FROM unit_cogs_settings")
    cogs_by_unit = {r["unit"]: r["base_cost_per_unit"] for r in cogs_rows}
    formula_items_by_unit = load_formula_items_by_unit()
    # Reference numbers now come from actual batches that picked THIS
    # size as their target (see create_bulk_batch()'s target_unit/
    # target_bottle_qty) — same real cost_per_new_bottle/
    # cost_per_refill_bottle split the Bulk Batches page computes for
    # that batch, averaged if more than one active batch targets the
    # same size. A size nobody has ever targeted yet (or whose only
    # batches are already depleted) gets no reference at all, instead of
    # a generic cross-batch/formula estimate that isn't really about
    # that size.
    target_batches = query(
        """SELECT batch_id, target_unit, target_bottle_qty, total_cost
           FROM bulk_batches
           WHERE remaining_ml > 0 AND target_unit IS NOT NULL AND target_bottle_qty > 0"""
    )
    liquid_subtotal_by_batch = {}
    if target_batches:
        placeholders = ",".join(["%s"] * len(target_batches))
        liquid_rows = query(
            f"""SELECT batch_id, SUM(line_cost) AS liquid_subtotal
                FROM bulk_batch_materials
                WHERE is_packaging = FALSE AND batch_id IN ({placeholders})
                GROUP BY batch_id""",
            tuple(b["batch_id"] for b in target_batches),
        )
        liquid_subtotal_by_batch = {
            r["batch_id"]: r["liquid_subtotal"] for r in liquid_rows}

    refs_by_unit = {}
    for b in target_batches:
        liquid_subtotal = liquid_subtotal_by_batch.get(
            b["batch_id"], decimal.Decimal("0"))
        cost_per_new = (b["total_cost"] / b["target_bottle_qty"]).quantize(
            decimal.Decimal("0.01"))
        cost_per_refill = (liquid_subtotal / b["target_bottle_qty"]).quantize(
            decimal.Decimal("0.01"))
        refs_by_unit.setdefault(b["target_unit"], []).append(
            (cost_per_new, cost_per_refill))

    # FORMULA_UNITS is PRODUCT_UNITS minus BULK — every bottle size gets a
    # row here (defaulting to 0 if somehow missing) so admins always see
    # the full picture.
    units = []
    for u in FORMULA_UNITS:
        formula_total = sum(
            (i["line_cost"] for i in formula_items_by_unit.get(u, [])),
            decimal.Decimal("0"),
        )
        refs = refs_by_unit.get(u)
        reference_cost_per_new = None
        reference_cost_per_refill = None
        reference_batch_count = len(refs) if refs else 0
        if refs:
            reference_cost_per_new = (
                sum((r[0] for r in refs), decimal.Decimal("0")) / len(refs)
            ).quantize(decimal.Decimal("0.01"))
            reference_cost_per_refill = (
                sum((r[1] for r in refs), decimal.Decimal("0")) / len(refs)
            ).quantize(decimal.Decimal("0.01"))
        units.append({
            "unit": u,
            "base_cost_per_unit": cogs_by_unit.get(u, decimal.Decimal("0")),
            "formula_items": formula_items_by_unit.get(u, []),
            "formula_total": formula_total,
            "reference_cost_per_new": reference_cost_per_new,
            "reference_cost_per_refill": reference_cost_per_refill,
            "reference_batch_count": reference_batch_count,
        })
    materials_list = query(
        """SELECT material_id, material_name, unit, cost_per_unit
           FROM raw_materials ORDER BY material_name"""
    )

    return render_template(
        "admin/formulas.html",
        units=units,
        bulk_rate=bulk_rate,
        reference_rate_per_ml=reference_rate_per_ml,
        reference_rate_batch_count=reference_rate_batch_count,
        materials=materials_list,
    )


@bp.route("/formulas/add-item", methods=["POST"])
@admin_required
def add_formula_item():
    """Add (or update) one material's line in a packaging size's
    reference packaging-materials formula (see unit_formula_items in
    schema.sql and this route's own comment on formula_items_by_unit()).
    Purely a reference breakdown — production() keeps charging whatever
    flat base cost is set on unit_cogs_settings, unaffected by this.
    """
    unit = request.form.get("unit")
    if unit not in FORMULA_UNITS:
        flash("Select a valid packaging size.", "error")
        return redirect(url_for("admin.formulas"))

    try:
        material_id = parse_positive_int(
            request.form.get("material_id"), "Material")
        qty_per_unit = parse_positive_decimal(
            request.form.get("qty_per_unit"), "Quantity per unit")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.formulas"))

    material = query(
        "SELECT material_name, cost_per_unit FROM raw_materials WHERE material_id = %s",
        (material_id,), fetchone=True,
    )
    if not material:
        flash("That material no longer exists.", "error")
        return redirect(url_for("admin.formulas"))

    line_cost = (qty_per_unit * material["cost_per_unit"]).quantize(
        decimal.Decimal("0.0001"))
    execute(
        """INSERT INTO unit_formula_items (unit, material_id, qty_per_unit, line_cost)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE qty_per_unit = VALUES(qty_per_unit), line_cost = VALUES(line_cost)""",
        (unit, material_id, qty_per_unit, line_cost),
    )
    notify_all(["materials"])
    log_action("add_formula_item", target=unit,
               details=f"{material['material_name']} x {qty_per_unit:g}/unit -> ₱{line_cost:,.4f}")
    flash(f"{unit} packaging formula updated.", "success")
    return redirect(url_for("admin.formulas"))


@bp.route("/formulas/delete-item", methods=["POST"])
@admin_required
def delete_formula_item():
    """Remove one material line from a packaging size's reference
    packaging-materials formula (see unit_formula_items in schema.sql)."""
    try:
        formula_item_id = parse_positive_int(
            request.form.get("formula_item_id"), "Formula item")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.formulas"))

    execute("DELETE FROM unit_formula_items WHERE formula_item_id = %s",
            (formula_item_id,))
    notify_all(["materials"])
    log_action("delete_formula_item", target=str(formula_item_id))
    flash("Removed from the packaging formula.", "success")
    return redirect(url_for("admin.formulas"))


@bp.route("/formulas/save-bulk-rate", methods=["POST"])
@admin_required
def save_bulk_rate():
    """Update the single shared ₱-per-mL rate every Bulk/Refill product is
    produced/sold at (see bulk_rate_settings in schema.sql). Production
    still lets this be overridden per batch (raw material costs
    fluctuate); Record Sale always uses this rate as-is.
    """
    try:
        rate = parse_non_negative_decimal(
            request.form.get("rate_per_ml"), "Rate per mL")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.formulas"))

    execute("UPDATE bulk_rate_settings SET rate_per_ml = %s WHERE id = 1", (rate,))
    notify_all(["materials"])
    log_action("save_bulk_rate", details=f"₱{rate:.4f}/mL")
    flash(f"Bulk/Refill rate set to ₱{rate:.4f} per mL.", "success")
    return redirect(url_for("admin.formulas"))


@bp.route("/formulas/save-unit-cogs", methods=["POST"])
@admin_required
def save_unit_cogs():
    """Set the flat base cost of goods for one Bottled packaging size
    (see unit_cogs_settings in schema.sql) — this is what production()
    logs as cogs_per_unit for that size, independent of whichever bulk
    batch a run happens to draw from (a batch's own real cost/mL stays
    informational, shown on the Bulk Batches page only).
    """
    unit = request.form.get("unit")
    if unit not in FORMULA_UNITS:
        flash("Select a valid packaging size.", "error")
        return redirect(url_for("admin.formulas"))

    try:
        base_cost = parse_non_negative_decimal(
            request.form.get("base_cost_per_unit"), "Base cost per unit")
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.formulas"))

    execute(
        """INSERT INTO unit_cogs_settings (unit, base_cost_per_unit) VALUES (%s, %s)
           ON DUPLICATE KEY UPDATE base_cost_per_unit = VALUES(base_cost_per_unit)""",
        (unit, base_cost),
    )
    notify_all(["materials"])
    log_action("save_unit_cogs", target=unit,
               details=f"₱{base_cost:,.4f}/unit")
    flash(f"{unit} base cost of goods set to ₱{base_cost:,.4f} per unit.", "success")
    return redirect(url_for("admin.formulas"))
