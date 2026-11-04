import decimal
from dataclasses import dataclass

from flask import (
    current_app, flash, redirect, render_template,
    request, session, url_for,
)

from heaven_angel.db import TransactionAborted, query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_admin, notify_all
from heaven_angel.utils import (
    BATCH_VOLUME_UNITS, BOTTLE_UNITS, ML_PER_BATCH_UNIT, MATERIAL_UNITS, ValidationError,
    base_code_from_sku, bottle_size_ml, compatible_material_units, consume_form_token, convert_material_qty,
    issue_form_token,
    parse_non_negative_decimal, parse_optional_id,
    parse_optional_text, parse_positive_decimal, parse_positive_int,
)

from heaven_angel.routes.admin.formulas import load_formula_items_by_unit
from heaven_angel.routes.admin._common import HQ_BRANCH_ID, bp


@dataclass(frozen=True)
class _ProductionRun:
    sku: str
    batch_code: str | None
    qty: int
    is_bulk: bool
    bulk_batch_id: int | None   # Bottled runs only
    bottle_ml: object           # Bottled runs only: mL of bulk one bottle takes
    cogs_per_unit: decimal.Decimal
    total_cogs: decimal.Decimal


def _plan_production_run(form):
    """Validate the Log Production form and work out the run's cost.

    Raises ValidationError with a message to flash as-is. Checks run in a
    fixed order: units, product, then the bulk rate / bulk batch.
    """
    sku = form.get("sku")
    batch_code = form.get("batch_code", "").strip() or None
    qty = parse_positive_int(form.get("qty_produced"), "Units produced")

    product = query(
        "SELECT sku, item_name, unit, category FROM products WHERE sku = %s",
        (sku,), fetchone=True,
    )
    if not product:
        raise ValidationError("Select a valid product.")

    is_bulk = product["category"] == "Bulk/Refill"
    bulk_batch_id = None
    bottle_ml = None
    if is_bulk:
        # Bulk/Refill has no formula/batch link at all — qty_produced here
        # is a custom mL amount, and cost is that mL amount x a rate typed in
        # for THIS batch (material costs fluctuate, so it's not pulled
        # read-only from bulk_rate_settings the way Record Sale's price is —
        # see that table's own comment in schema.sql).
        cogs_per_unit = parse_non_negative_decimal(
            form.get("rate_per_ml"), "Rate per mL").quantize(decimal.Decimal("0.0001"))
    else:
        # A Bottled run still requires picking a bulk batch — that's what
        # enforces "materials go into a batch before they're bottled" and is
        # what gets deducted when the run is recorded. But the cost logged is
        # a flat, hand-set base price per packaging size (see
        # unit_cogs_settings in schema.sql and save_unit_cogs()), not derived
        # from the batch's own cost/ml — the batch already recorded its real
        # materials cost when it was made, so re-deriving a per-bottle cost
        # here would just be double bookkeeping.
        bulk_batch_id = parse_positive_int(form.get("bulk_batch_id"), "Bulk batch")
        bottle_ml = bottle_size_ml(product["unit"])
        cogs_setting = query(
            "SELECT base_cost_per_unit FROM unit_cogs_settings WHERE unit = %s",
            (product["unit"],), fetchone=True,
        )
        cogs_per_unit = (
            cogs_setting["base_cost_per_unit"] if cogs_setting else decimal.Decimal("0")
        ).quantize(decimal.Decimal("0.0001"))
    total_cogs = (cogs_per_unit * qty).quantize(decimal.Decimal("0.01"))
    return _ProductionRun(sku, batch_code, qty, is_bulk, bulk_batch_id, bottle_ml, cogs_per_unit, total_cogs)


def _record_production_run(run, user_id):
    """Write the run in one transaction: deduct the bulk batch (Bottled), log
    the run, add HQ warehouse stock, and log the movement and cost of goods.
    Raises TransactionAborted with a message to flash."""
    with transaction() as conn:
        cur = conn.cursor(dictionary=True)

        if not run.is_bulk:
            # Locked and checked/deducted inside the transaction, not before
            # it, so two concurrent runs against the same batch can't both
            # pass a stale remaining_ml check.
            cur.execute(
                "SELECT batch_id, remaining_ml FROM bulk_batches WHERE batch_id = %s FOR UPDATE",
                (run.bulk_batch_id,),
            )
            batch = cur.fetchone()
            if not batch:
                raise TransactionAborted("Select a valid bulk batch.")
            needed_ml = run.bottle_ml * run.qty
            if batch["remaining_ml"] < needed_ml:
                raise TransactionAborted(
                    f"That batch only has {batch['remaining_ml']:.3f} mL left — "
                    f"this run needs {needed_ml:.3f} mL."
                )
            cur.execute(
                "UPDATE bulk_batches SET remaining_ml = remaining_ml - %s WHERE batch_id = %s",
                (needed_ml, run.bulk_batch_id),
            )

        cur.execute(
            "INSERT INTO production_logs (sku, batch_code, qty_produced) VALUES (%s, %s, %s)",
            (run.sku, run.batch_code, run.qty),
        )
        log_id = cur.lastrowid
        cur.execute(
            """INSERT INTO branch_inventory (branch_id, sku, stock_qty)
               VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
            (HQ_BRANCH_ID, run.sku, run.qty),
        )
        cur.execute(
            "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s",
            (HQ_BRANCH_ID, run.sku),
        )
        row = cur.fetchone()
        after_qty = row["stock_qty"] if row else None
        before_qty = (after_qty - run.qty) if after_qty is not None else None
        cur.execute(
            """INSERT INTO stock_movement_logs
               (branch_id, sku, change_qty, movement_type, notes,
                created_by_user_id, reference_type, before_qty, after_qty)
               VALUES (%s, %s, %s, 'PRODUCTION', %s, %s, 'PRODUCTION_LOG', %s, %s)""",
            (HQ_BRANCH_ID, run.sku, run.qty,
             f"Batch {run.batch_code}" if run.batch_code else "Production run",
             user_id, before_qty, after_qty),
        )
        cur.execute(
            """INSERT INTO cogs_logs
               (sku, production_log_id, bulk_batch_id, qty_produced, cogs_per_unit, total_cogs,
                created_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (run.sku, log_id, run.bulk_batch_id, run.qty, run.cogs_per_unit, run.total_cogs, user_id),
        )
        cur.close()


def _production_page_data():
    # Each Bottled product carries its packaging size's flat base cost (see
    # unit_cogs_settings in schema.sql) so the form can preview the cost of
    # goods a run will log before it's submitted.
    products_list = query(
        """SELECT p.sku, p.item_name, p.unit, p.category,
                  COALESCE(ucs.base_cost_per_unit, 0) AS cogs_per_unit
           FROM products p
           LEFT JOIN unit_cogs_settings ucs ON ucs.unit = p.unit
           ORDER BY p.item_name"""
    )
    # Bottled sizes must pick one of these to log a run against — only
    # batches that still have something left to give are worth offering.
    # Only remaining_ml matters here; cost of goods comes from
    # unit_cogs_settings, not the batch's own cost_per_ml.
    active_batches = query(
        """SELECT batch_id, batch_code, scent_name, remaining_ml
           FROM bulk_batches WHERE remaining_ml > 0
           ORDER BY scent_name, created_at DESC"""
    )
    # cogs_logs.production_log_id is one-to-one with a run going forward, but
    # is grouped/summed here rather than joined plain in case an older run
    # picked up more than one batch back when "Log material usage" let an
    # admin link any run by hand.
    logs = query(
        """SELECT pl.*, p.item_name, p.unit, p.category,
                  cogs.cogs_per_unit, cogs.total_cogs
           FROM production_logs pl
           JOIN products p ON pl.sku = p.sku
           LEFT JOIN (
               SELECT production_log_id,
                      SUM(total_cogs) AS total_cogs,
                      AVG(cogs_per_unit) AS cogs_per_unit
               FROM cogs_logs
               WHERE production_log_id IS NOT NULL
               GROUP BY production_log_id
           ) cogs ON cogs.production_log_id = pl.log_id
           ORDER BY pl.produced_at DESC LIMIT 40"""
    )
    hq_stock = query(
        """SELECT p.sku, p.item_name, p.variant, p.category, p.unit, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (HQ_BRANCH_ID,),
    )
    # Counted by base code, same as the Products page.
    for row in hq_stock:
        row["base_code"] = base_code_from_sku(row["sku"], row["unit"])
    return {"products": products_list, "logs": logs, "hq_stock": hq_stock,
            "active_batches": active_batches}


@bp.route("/production", methods=["GET", "POST"])
@admin_required
def production():
    """Logging a production run also logs its cost of goods in the same
    step (see cogs_logs in schema.sql).

    Bulk/Refill (`category`) is unchanged: no fixed size, costed at a
    hand-typed rate per mL for this batch (see bulk_rate_settings).

    Every other (Bottled) size still requires picking a specific bulk
    batch (see bulk_batches/bulk_batch_materials in schema.sql) — its
    remaining_ml is checked and deducted by qty_produced x this size's
    mL, which is what enforces a size only being bottled once its
    materials have actually gone into a batch first. The cost logged
    (cogs_per_unit), though, is a flat base price set per packaging size
    (see unit_cogs_settings/save_unit_cogs()) — not derived from the
    batch's own real cost_per_ml, since a batch already accounted for its
    real materials cost when it was made; re-deriving a per-bottle cost
    from it here would just be double bookkeeping. material_usage_logs is
    no longer written from this route — that audit trail is now
    bulk_batch_materials, captured once when the batch itself was
    created.
    """
    if request.method == "POST":
        if not consume_form_token("log_production"):
            flash(
                "This run was already logged, or the form expired — check the log below before resending.", "error")
            return redirect(url_for("admin.production"))

        try:
            run = _plan_production_run(request.form)
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.production"))

        try:
            _record_production_run(run, session.get("user_id"))
            notify_admin(["production", "inventory", "movement_logs"])
            notify_all(["materials"])
            unit_label = "mL" if run.is_bulk else "units"
            flash(
                f"Logged {run.qty} {unit_label} produced — ₱{run.total_cogs:,.2f} in cost of goods — and added to HQ warehouse stock.",
                "success",
            )
        except TransactionAborted as err:
            flash(str(err), "error")
        except Exception:
            current_app.logger.exception(
                "production logging failed for sku=%s", run.sku)
            flash("Couldn't log this production run — please try again.", "error")
        return redirect(url_for("admin.production"))

    return render_template(
        "admin/production.html", **_production_page_data(),
        form_token=issue_form_token("log_production"),
    )


# ---------------------------------------------------------------- bulk batches
@bp.route("/bulk-batches")
@admin_required
def bulk_batches():
    """The step between raw materials and bottling: mix specific raw
    materials at specific quantities into one bulk batch (e.g. 500ml or
    1 gallon — see bulk_batches/bulk_batch_materials in schema.sql), then
    bottle out of it later (see production()). This page shows every
    batch's real cost (from what was actually used to make it) and
    remaining mL, and, for each fixed bottle size, how many more of that
    size it can still fill — "how many 85ml bottles can this batch still
    make" answered right here — plus a reference "all-in cost per bottle"
    (this batch's liquid cost/mL x the size's mL, plus that size's
    packaging formula total — see formula_items_by_unit()). What a
    bottled run actually gets charged, though, is the flat base price set
    per size on the Formulas page (see unit_cogs_settings) — this page's
    own cost/mL, and the per-bottle figure computed from it, are both
    informational only, not what production() logs.
    """
    materials_list = query(
        """SELECT material_id, material_name, unit, stock_qty, cost_per_unit
           FROM raw_materials ORDER BY material_name"""
    )
    batch_rows = query(
        """SELECT batch_id, batch_code, scent_name, input_qty, input_unit,
                  total_volume_ml, total_cost, cost_per_ml, remaining_ml,
                  notes, target_unit, target_bottle_qty, created_at
           FROM bulk_batches ORDER BY created_at DESC"""
    )
    ingredient_rows = query(
        """SELECT bbm.batch_id, bbm.qty_used, bbm.qty_used_unit, bbm.cost_per_unit_snapshot,
                  bbm.line_cost, bbm.is_packaging,
                  rm.material_name, rm.unit AS material_unit
           FROM bulk_batch_materials bbm
           JOIN raw_materials rm ON rm.material_id = bbm.material_id
           ORDER BY bbm.is_packaging, rm.material_name"""
    )
    ingredients_by_batch = {}
    for row in ingredient_rows:
        ingredients_by_batch.setdefault(row["batch_id"], []).append(row)

    # Packaging total per size (see formula_items_by_unit()) — folded
    # into each batch's own liquid cost/mL below into one reference
    # "all-in cost per bottle" figure. Still purely informational: what a
    # production run actually gets charged is the flat base price on
    # unit_cogs_settings, not either of these (see production()'s own
    # comment for why). Also embedded below (formula_items_by_unit) for
    # the create-batch form's own live "this will also add ..." preview
    # when a target bottle size is picked.
    formula_items_by_unit = load_formula_items_by_unit()
    formula_totals = {
        unit: sum((i["line_cost"] for i in items), decimal.Decimal("0"))
        for unit, items in formula_items_by_unit.items()
    }

    batches = []
    for b in batch_rows:
        # A batch with a target bottle size already had THAT size's
        # packaging bought and deducted at creation (target_bottle_qty
        # worth — see create_bulk_batch()). Any mL still left over is only
        # ever going to refill that same size, never a different one, and
        # a refill needs no new bottle/sticker/seal — so the yield grid
        # here shows just that one size, liquid-only (matches
        # cost_per_refill_bottle below), instead of every fixed size with
        # packaging folded in as if it were a fresh bottle.
        if b["target_unit"]:
            unit = b["target_unit"]
            size_ml = bottle_size_ml(unit)
            liquid_cost = (b["cost_per_ml"] * size_ml).quantize(
                decimal.Decimal("0.0001"))
            yields = [{
                "unit": unit,
                "bottle_count": int(b["remaining_ml"] // size_ml),
                "liquid_cost": liquid_cost,
                "packaging_cost": decimal.Decimal("0"),
                "total_cost_per_bottle": liquid_cost.quantize(decimal.Decimal("0.01")),
                "refill_only": True,
            }]
        else:
            yields = []
            for unit in BOTTLE_UNITS:
                size_ml = bottle_size_ml(unit)
                liquid_cost = (b["cost_per_ml"] * size_ml).quantize(
                    decimal.Decimal("0.0001"))
                packaging_cost = formula_totals.get(unit, decimal.Decimal("0"))
                yields.append({
                    "unit": unit,
                    "bottle_count": int(b["remaining_ml"] // size_ml),
                    "liquid_cost": liquid_cost,
                    "packaging_cost": packaging_cost,
                    "total_cost_per_bottle": (liquid_cost + packaging_cost).quantize(
                        decimal.Decimal("0.01")),
                    "refill_only": False,
                })
        b["yields"] = yields
        b["depleted"] = b["remaining_ml"] <= 0
        ingredients = ingredients_by_batch.get(b["batch_id"], [])
        b["ingredients"] = ingredients

        # All-in price per mL — total_cost (liquid + whatever packaging
        # was folded in for a target size) ÷ total_volume_ml. Shown to
        # the user as "the" cost per mL (see bulk_batches.html's table
        # and details overlay). Deliberately a SEPARATE figure from the
        # stored cost_per_ml column, which stays liquid-only on purpose
        # — yields/refill math above and formulas()'s cross-batch
        # average both depend on cost_per_ml meaning liquid-only, so
        # packaging is folded in here at read time instead of changing
        # what's stored.
        b["all_in_cost_per_ml"] = (
            b["total_cost"] / b["total_volume_ml"]).quantize(decimal.Decimal("0.000001")) \
            if b["total_volume_ml"] else decimal.Decimal("0")

        # This batch's own real plan, when a target bottle size was
        # picked at creation (see create_bulk_batch()) — liquid/packaging
        # subtotals split straight from this batch's own materials (not
        # the generic per-size reference above), same SUMIF(Category=...)
        # split the "Timpla Costing" spreadsheet does. Cost per NEW
        # bottle is liquid+packaging / yield; cost per REFILL is
        # liquid-only / yield (a refill doesn't need a new bottle/sticker/
        # seal) — matching that spreadsheet's own two result rows.
        if b["target_unit"] and b["target_bottle_qty"]:
            liquid_subtotal = sum(
                (i["line_cost"] for i in ingredients if not i["is_packaging"]),
                decimal.Decimal("0"))
            packaging_subtotal = sum(
                (i["line_cost"] for i in ingredients if i["is_packaging"]),
                decimal.Decimal("0"))
            b["liquid_subtotal"] = liquid_subtotal
            b["packaging_subtotal"] = packaging_subtotal
            b["cost_per_new_bottle"] = (
                b["total_cost"] / b["target_bottle_qty"]).quantize(decimal.Decimal("0.01"))
            b["cost_per_refill_bottle"] = (
                liquid_subtotal / b["target_bottle_qty"]).quantize(decimal.Decimal("0.01"))
        batches.append(b)

    return render_template(
        "admin/bulk_batches.html",
        materials=materials_list,
        batches=batches,
        batch_units=BATCH_VOLUME_UNITS,
        # Embedded as JSON for the create-batch form's live yield preview
        # (see bulk_batches.html's own script) — the same math this route
        # runs server-side per existing batch above, run client-side
        # against whatever's being typed in before it's ever submitted.
        bottle_ml_map={u: float(bottle_size_ml(u)) for u in BOTTLE_UNITS},
        ml_per_batch_unit={u: float(v) for u, v in ML_PER_BATCH_UNIT.items()},
        # Which packaging materials (and how much of each per bottle) a
        # target size would fold into this batch — drives the create-batch
        # form's own live preview of what picking that size adds (see
        # bulk_batches.html's own script). Same live-computed source as
        # the Formulas page itself (formula_items_by_unit()).
        formula_items_by_unit={
            unit: [
                {
                    "material_id": i["material_id"],
                    "material_name": i["material_name"],
                    "qty_per_unit": float(i["qty_per_unit"]),
                    "material_unit": i["material_unit"],
                    "cost_per_unit": float(i["cost_per_unit"]),
                }
                for i in items
            ]
            for unit, items in formula_items_by_unit.items()
        },
        # Which units the "Qty used" field can be logged in per material
        # unit (e.g. a Gallon-stocked material can be used in mL/L/Gallon)
        # — drives the per-line unit <select> in the add-material row (see
        # utils.compatible_material_units).
        material_unit_options={
            u: compatible_material_units(u) for u in MATERIAL_UNITS},
        form_token=issue_form_token("create_bulk_batch"),
    )


@dataclass(frozen=True)
class _BatchPlan:
    scent_name: str
    batch_code: str | None
    input_qty: decimal.Decimal
    input_unit: str
    notes: str | None
    target_unit: str | None
    target_bottle_qty: int | None
    total_volume_ml: decimal.Decimal
    items: list                     # (material_id, qty_used, qty_unit) per liquid ingredient


def _plan_bulk_batch(form):
    """Validate the Create Batch form. Raises ValidationError with a message to flash as-is."""
    scent_name = form.get("scent_name", "").strip()
    batch_code = form.get("batch_code", "").strip() or None
    input_unit = form.get("input_unit")
    target_unit = form.get("target_unit") or None

    if not scent_name:
        raise ValidationError("Scent / batch name is required.")
    if input_unit not in BATCH_VOLUME_UNITS:
        raise ValidationError("Select a valid batch size unit.")
    if target_unit is not None and target_unit not in BOTTLE_UNITS:
        raise ValidationError("Select a valid target bottle size.")
    input_qty = parse_positive_decimal(form.get("input_qty"), "Batch size")
    notes = parse_optional_text(form.get("notes"), "Notes", max_length=255)

    items = []
    seen_material_ids = set()
    for raw_id, raw_qty, qty_unit in zip(form.getlist("material_id[]"), form.getlist("qty_used[]"),
                                         form.getlist("qty_unit[]")):
        material_id = parse_optional_id(raw_id, "Material")
        if material_id is None:
            continue
        qty_used = parse_positive_decimal(raw_qty, "Quantity used")
        if qty_unit not in MATERIAL_UNITS:
            raise ValidationError("Select a valid unit for the quantity used.")
        if material_id in seen_material_ids:
            raise ValidationError("Each material can only appear once in a batch.")
        seen_material_ids.add(material_id)
        items.append((material_id, qty_used, qty_unit))
    if not items:
        raise ValidationError("Add at least one material used in this batch.")

    total_volume_ml = (input_qty * ML_PER_BATCH_UNIT[input_unit]).quantize(decimal.Decimal("0.001"))

    # Bottle count for a target size is auto-computed from this batch's own
    # volume — however many whole bottles of that size the batch can
    # physically fill (total_volume_ml // bottle_size_ml). The grand
    # total/cost-per-new-bottle/cost-per-refill math is unchanged — it just
    # divides by whatever this comes out to.
    target_bottle_qty = None
    if target_unit:
        target_bottle_qty = int(total_volume_ml // bottle_size_ml(target_unit))
        if target_bottle_qty < 1:
            raise ValidationError(
                f"This batch isn't big enough to fill even one {target_unit} bottle — "
                "leave the target size blank, or enter a bigger batch.")
    return _BatchPlan(scent_name, batch_code, input_qty, input_unit, notes, target_unit,
                      target_bottle_qty, total_volume_ml, items)


def _price_liquid_lines(cur, items):
    """Lock and stock-check each liquid ingredient. Returns
    (materials_by_id, line_rows, liquid_subtotal); raises TransactionAborted."""
    placeholders = ",".join(["%s"] * len(items))
    cur.execute(
        f"""SELECT material_id, material_name, unit, cost_per_unit, stock_qty
            FROM raw_materials WHERE material_id IN ({placeholders}) FOR UPDATE""",
        tuple(i[0] for i in items),
    )
    materials_by_id = {row["material_id"]: row for row in cur.fetchall()}

    liquid_subtotal = decimal.Decimal("0")
    line_rows = []
    for material_id, qty_used, qty_unit in items:
        material = materials_by_id.get(material_id)
        if not material:
            raise TransactionAborted("One of the selected materials no longer exists.")
        if qty_unit not in compatible_material_units(material["unit"]):
            raise TransactionAborted(
                f"{qty_unit} isn't a valid quantity unit for {material['material_name']} "
                f"({material['unit']})."
            )
        # qty_used/cost_per_unit_snapshot are both kept in qty_unit — the unit
        # actually typed in, which may be finer than the material's own
        # purchase unit (e.g. mL logged against a material stocked by the
        # Gallon) — see utils.compatible_material_units()/convert_material_qty()
        # and migration 39 in schema.sql for why line_cost still just equals
        # qty_used * cost_per_unit_snapshot either way.
        cost_per_qty_unit = material["cost_per_unit"] * convert_material_qty(
            decimal.Decimal("1"), qty_unit, material["unit"])
        native_qty_used = convert_material_qty(qty_used, qty_unit, material["unit"])
        if material["stock_qty"] < native_qty_used:
            raise TransactionAborted(
                f"Not enough {material['material_name']} in stock — "
                f"{material['stock_qty']:g} {material['unit'].lower()} left, "
                f"{qty_used:g} {qty_unit.lower()} needed."
            )
        line_cost = (qty_used * cost_per_qty_unit).quantize(decimal.Decimal("0.0001"))
        liquid_subtotal += line_cost
        line_rows.append(
            (material_id, qty_used, qty_unit, cost_per_qty_unit, line_cost, native_qty_used, False))
    return materials_by_id, line_rows, liquid_subtotal


def _price_packaging_lines(cur, plan, materials_by_id):
    """Fold the target size's packaging formula into the same batch (see
    create_bulk_batch()'s docstring and migration 43 in schema.sql). Locked
    (FOR UPDATE) and stock-checked like the liquid materials, driven by the
    volume-derived target_bottle_qty. Returns (line_rows, packaging_subtotal)."""
    packaging_subtotal = decimal.Decimal("0")
    line_rows = []
    if not (plan.target_unit and plan.target_bottle_qty):
        return line_rows, packaging_subtotal
    cur.execute(
        """SELECT rm.material_id, rm.material_name, rm.unit, rm.cost_per_unit, rm.stock_qty,
                  ufi.qty_per_unit
           FROM unit_formula_items ufi
           JOIN raw_materials rm ON rm.material_id = ufi.material_id
           WHERE ufi.unit = %s FOR UPDATE""",
        (plan.target_unit,),
    )
    packaging_materials = cur.fetchall()
    overlap_ids = {
        material["material_id"] for material in packaging_materials
        if material["material_id"] in materials_by_id
    }
    if overlap_ids:
        names = ", ".join(sorted(materials_by_id[mid]["material_name"] for mid in overlap_ids))
        raise TransactionAborted(
            f"{names} can't be both a batch ingredient and packaging for "
            f"{plan.target_unit}. Remove it from one of the two."
        )

    for material in packaging_materials:
        qty_used = (material["qty_per_unit"] * plan.target_bottle_qty).quantize(decimal.Decimal("0.001"))
        if material["stock_qty"] < qty_used:
            raise TransactionAborted(
                f"Not enough {material['material_name']} in stock to package "
                f"{plan.target_bottle_qty} {plan.target_unit} bottle(s) — "
                f"{material['stock_qty']:g} {material['unit'].lower()} left, "
                f"{qty_used:g} needed."
            )
        line_cost = (qty_used * material["cost_per_unit"]).quantize(decimal.Decimal("0.0001"))
        packaging_subtotal += line_cost
        line_rows.append(
            (material["material_id"], qty_used, material["unit"],
             material["cost_per_unit"], line_cost, qty_used, True))
    return line_rows, packaging_subtotal


def _create_bulk_batch_records(plan, user_id):
    """Write the batch in one transaction: its totals, one row per material line,
    and the stock deducted from each material. Returns (total_cost, cost_per_ml);
    raises TransactionAborted."""
    with transaction() as conn:
        cur = conn.cursor(dictionary=True)
        materials_by_id, line_rows, liquid_subtotal = _price_liquid_lines(cur, plan.items)
        packaging_rows, packaging_subtotal = _price_packaging_lines(cur, plan, materials_by_id)
        line_rows += packaging_rows

        liquid_subtotal = liquid_subtotal.quantize(decimal.Decimal("0.01"))
        packaging_subtotal = packaging_subtotal.quantize(decimal.Decimal("0.01"))
        total_cost = liquid_subtotal + packaging_subtotal
        # Cost per mL is, and stays, a LIQUID-only rate — packaging is
        # piece-counted, not mL-counted, so mixing it in here would make this
        # figure meaningless everywhere else it's read (production()'s
        # preview, the Bulk Batches yield grid).
        cost_per_ml = (liquid_subtotal / plan.total_volume_ml).quantize(decimal.Decimal("0.000001"))

        cur.execute(
            """INSERT INTO bulk_batches
               (batch_code, scent_name, input_qty, input_unit, total_volume_ml,
                total_cost, cost_per_ml, remaining_ml, notes, target_unit,
                target_bottle_qty, created_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (plan.batch_code, plan.scent_name, plan.input_qty, plan.input_unit, plan.total_volume_ml,
             total_cost, cost_per_ml, plan.total_volume_ml, plan.notes, plan.target_unit,
             plan.target_bottle_qty, user_id),
        )
        batch_id = cur.lastrowid

        for (material_id, qty_used, qty_unit, cost_per_unit_snapshot, line_cost,
             native_qty_used, is_packaging) in line_rows:
            cur.execute(
                """INSERT INTO bulk_batch_materials
                   (batch_id, material_id, qty_used, qty_used_unit, cost_per_unit_snapshot,
                    line_cost, is_packaging)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (batch_id, material_id, qty_used, qty_unit,
                 cost_per_unit_snapshot, line_cost, is_packaging),
            )
            cur.execute(
                "UPDATE raw_materials SET stock_qty = stock_qty - %s WHERE material_id = %s",
                (native_qty_used, material_id),
            )
        cur.close()
    return total_cost, cost_per_ml


@bp.route("/bulk-batches/create", methods=["POST"])
@admin_required
def create_bulk_batch():
    """Mix specific raw materials at specific quantities into one new
    bulk batch — deducts stock_qty from each material used (this is what
    actually enforces materials being in stock before they're used) and
    computes the batch's real liquid cost / cost per mL from what was
    used, not a hand-typed guess (see bulk_batches/bulk_batch_materials in
    schema.sql).

    Picking a target bottle size (optional) also folds that size's
    packaging formula (see unit_formula_items/formula_items_by_unit())
    into this SAME batch — qty = target_bottle_qty (auto-computed from
    this batch's own volume — total_volume_ml // bottle_size_ml, frozen
    at creation) x each packaging material's qty_per_unit. Those
    packaging lines deduct stock and add to total_cost exactly like the
    liquid ones, just tagged is_packaging — matching how the business's
    own "Timpla Costing" spreadsheet bundles a batch's bottles/stickers/
    seals into the same total as its liquid ingredients, bought for the
    whole batch up front. Leave target size blank to keep the old
    liquid-only behavior.
    """
    if not consume_form_token("create_bulk_batch"):
        flash("This batch was already logged, or the form expired — check the list below before resending.", "error")
        return redirect(url_for("admin.bulk_batches"))

    try:
        plan = _plan_bulk_batch(request.form)
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.bulk_batches"))

    try:
        total_cost, cost_per_ml = _create_bulk_batch_records(plan, session.get("user_id"))
        notify_admin(["production", "materials"])
        notify_all(["materials"])
        log_action("create_bulk_batch", target=plan.scent_name,
                   details=f"{plan.input_qty:g} {plan.input_unit} — ₱{total_cost:,.2f} total (₱{cost_per_ml:,.6f}/mL liquid)"
                   + (f", {plan.target_bottle_qty} x {plan.target_unit} packaged" if plan.target_unit else "")
                   + (f" — batch {plan.batch_code}" if plan.batch_code else ""))
        flash(
            f"Batch logged — {plan.total_volume_ml:g} mL at ₱{total_cost:,.2f} total.", "success")
    except TransactionAborted as err:
        flash(str(err), "error")
    except Exception:
        current_app.logger.exception(
            "create bulk batch failed for scent_name=%s", plan.scent_name)
        flash("Couldn't log this batch — please try again.", "error")
    return redirect(url_for("admin.bulk_batches"))
