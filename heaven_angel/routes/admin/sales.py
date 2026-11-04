
from flask import (
    abort, current_app, flash, redirect, render_template,
    request, send_file, session, url_for,
)

from heaven_angel.db import TransactionAborted, query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.services.receipts import build_sale_receipt_pdf
from heaven_angel.services.sales_import import build_sales_import_template, import_sales_rows, parse_sales_import_workbook
from heaven_angel.services.sale_stock import void_sale
from heaven_angel.sockets import notify_admin, notify_admin_and_branch
from heaven_angel.utils import (
    ValidationError,
    consume_form_token, issue_form_token,
)

from heaven_angel.services import sale_entry
from heaven_angel.routes.admin._common import HQ_BRANCH_ID, bp


# ---------------------------------------------------------------- record sale (HQ direct)
@bp.route("/record-sale/<int:sale_id>/receipt")
@admin_required
def sale_receipt(sale_id):
    """Downloadable PDF receipt for a single sale/refill — see receipts.py.

    No branch_id scoping here since HQ Admin can see every branch (in
    practice this is only ever called for HQ/Main Branch's own sales,
    recorded on this same page).
    """
    pdf_buffer, sale = build_sale_receipt_pdf(sale_id)
    if pdf_buffer is None:
        abort(404)
    return send_file(
        pdf_buffer, mimetype="application/pdf",
        as_attachment=True, download_name=f"SR-{sale_id:06d}.pdf",
    )


@bp.route("/record-sale", methods=["GET", "POST"])
@admin_required
def record_sale():
    """HQ selling straight from the warehouse (branch_id=1) — walk-in
    sales, refills, or a Credit sale (an employee against their own pay,
    or a customer buying on store credit/"utang"). Mirrors
    branch.record_sale(); the only difference is HQ can't produce here,
    only sell/refill from whatever the warehouse already has on hand
    (see production() for adding stock).

    There's no branch price to fall back to anymore — the HQ price is
    only ever a suggested starting point; the actual amount charged is
    typed in on every sale/refill.
    """
    if request.method == "POST":
        if not consume_form_token("admin_record_sale"):
            flash(
                "This sale already went through, or the form expired — check the sales list before recording it again.", "error")
            return redirect(url_for("admin.record_sale"))

        try:
            sale = sale_entry.parse_sale_form(
                request.form, qty_label="Quantity",
                sale_type_prompt="Select whether this is a sale, a refill, or a freebie.",
                product_prompt="Select a product.")
        except sale_entry.SaleFormError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.record_sale"))

        try:
            sale_entry.record_sale(
                sale, branch_id=HQ_BRANCH_ID, user_id=session.get("user_id"), note_suffix=" (HQ)")
            notify_admin(["inventory", "sales", "movement_logs"])
            flash(f"{sale.sale_type} recorded.", "success")
        except TransactionAborted as err:
            flash(str(err), "error")
        except Exception:
            current_app.logger.exception(
                "admin record_sale failed for sku=%s", sale.sku)
            flash("Couldn't record that — please try again.", "error")
        return redirect(url_for("admin.record_sale"))

    # bulk_ml: HQ's stock of each bottle size's scent bulk (same base
    # code, unit BULK — see sale_stock.refill_source()). A Refill pours
    # from that, so a bottle size can be refilled even with no bottles
    # left, as long as its scent's bulk is there.
    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.category, p.unit, p.price, bi.stock_qty,
                  COALESCE(bb.stock_qty, 0) AS bulk_ml
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           LEFT JOIN branch_inventory bb
             ON p.unit <> 'BULK' AND bb.branch_id = bi.branch_id
            AND bb.sku = CONCAT(LEFT(p.sku, CHAR_LENGTH(p.sku) - CHAR_LENGTH(p.unit) - 1), '-BULK')
           WHERE bi.branch_id = %s AND (bi.stock_qty > 0 OR bb.stock_qty > 0)
           ORDER BY p.item_name""",
        (HQ_BRANCH_ID,),
    )
    bulk_rate = query(
        "SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1", fetchone=True,
    )["rate_per_ml"]
    # s.* already carries sales.sku (the FK column) — no need to
    # re-select p.sku separately; it's used below for the Item column's
    # SKU + Name display.
    recent_sales = query(
        """SELECT s.*, p.item_name, p.unit, COALESCE(s.buyer_name, bu.username) AS buyer_username
           FROM sales s JOIN products p ON s.sku = p.sku
           LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 10""",
        (HQ_BRANCH_ID,),
    )
    employees = query(
        "SELECT user_id, username, role FROM users WHERE is_active = TRUE ORDER BY username"
    )
    # Every distinct customer name logged at HQ/Main Branch, each paired
    # with the address from that name's *first* sale (see schema.sql's
    # comment on sales.customer_address) — feeds the Customer name
    # autocomplete/autofill on the form below. Mirrors branch.record_sale().
    customers = query(
        """SELECT s.customer_name AS name, s.customer_address AS address
           FROM sales s
           JOIN (
               SELECT customer_name, MIN(sold_at) AS first_sold_at
               FROM sales
               WHERE branch_id = %s AND customer_name IS NOT NULL AND customer_name <> ''
               GROUP BY customer_name
           ) first_seen
             ON first_seen.customer_name = s.customer_name AND first_seen.first_sold_at = s.sold_at
           WHERE s.branch_id = %s
           ORDER BY s.customer_name""",
        (HQ_BRANCH_ID, HQ_BRANCH_ID),
    )
    return render_template(
        "admin/record_sale.html", inventory=inventory, recent_sales=recent_sales, employees=employees,
        customers=customers, bulk_rate=bulk_rate, form_token=issue_form_token(
            "admin_record_sale"),
        import_form_token=issue_form_token("admin_import_sales"),
    )


# ---------------------------------------------------------------- bulk import (Excel)
@bp.route("/record-sale/import-template")
@admin_required
def sales_import_template():
    """Downloadable blank workbook for the "Import from Excel" button
    below — see sales_import.build_sales_import_template().
    """
    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, p.price, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (HQ_BRANCH_ID,),
    )
    buf = build_sales_import_template(inventory)
    return send_file(
        buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="sales-import-template.xlsx",
    )


@bp.route("/record-sale/import", methods=["POST"])
@admin_required
def import_sales():
    """Bulk-insert every row of an uploaded, filled-in copy of the
    template above in one go. Mirrors branch.import_sales() — see
    sales_import.import_sales_rows() for the all-or-nothing transaction
    this runs against the HQ warehouse (branch_id=1).
    """
    if not consume_form_token("admin_import_sales"):
        flash(
            "This import already went through, or the form expired — try uploading again.", "error")
        return redirect(url_for("admin.record_sale"))

    upload = request.files.get("import_file")
    if not upload or not upload.filename:
        flash("Choose an Excel file to import.", "error")
        return redirect(url_for("admin.record_sale"))

    try:
        raw_rows = parse_sales_import_workbook(upload)
        inserted = import_sales_rows(
            HQ_BRANCH_ID, raw_rows, session.get("user_id"))
        notify_admin(["inventory", "sales", "movement_logs"])
        flash(
            f"Imported {inserted} sale{'s' if inserted != 1 else ''} from the file.", "success")
    except (ValidationError, TransactionAborted) as err:
        for line in str(err).split("\n"):
            if line.strip():
                flash(line, "error")
    except Exception:
        current_app.logger.exception("admin sales import failed")
        flash("Couldn't import that file — please check the format and try again.", "error")
    return redirect(url_for("admin.record_sale"))


# ---------------------------------------------------------------- customer directory (company-wide)
@bp.route("/customers")
@admin_required
def customers():
    """Company-wide repeat-customer directory, aggregated across every
    branch — the admin counterpart to branch.customers().

    Same underlying data as the branch-scoped version: there's no
    separate customers table, this is built entirely from
    `sales.customer_name` / `customer_address`, grouped by name. The
    difference here is scope: a customer who has bought from more than
    one branch is folded into a single row instead of appearing once
    per branch, with a "Branches" column showing where they've shopped
    and the branch of their most recent purchase.

    Address is taken from the earliest sale on file for that name
    fleet-wide (same "first sale wins" convention as the branch page
    and Record Sale's autocomplete — see schema.sql's comment on
    sales.customer_address). Walk-ins (blank customer_name) are
    excluded since there's nothing to group them by. Matching is by
    exact customer_name text, same as everywhere else this field is
    used — two different spellings of the same person's name are
    treated as two different customers, same limitation the branch
    page and Record Sale autocomplete already have.
    """
    rows = query(
        """SELECT s.customer_name AS name, s.customer_address AS address,
                  agg.branch_count, agg.branches, agg.purchase_count,
                  agg.total_units, agg.total_bulk_ml, agg.total_spent, agg.last_purchase_at,
                  last_b.branch_name AS last_branch_name
           FROM (
               SELECT sa.customer_name,
                      COUNT(DISTINCT sa.branch_id) AS branch_count,
                      GROUP_CONCAT(DISTINCT b.branch_name ORDER BY b.branch_name SEPARATOR ', ') AS branches,
                      COUNT(*) AS purchase_count,
                      -- Bulk sales store qty_sold in mL, so they're totalled
                      -- separately rather than added to the bottle count.
                      COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_units,
                      COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                      COALESCE(SUM(sa.qty_sold * sa.unit_price), 0) AS total_spent,
                      MIN(sa.sold_at) AS first_sold_at,
                      MAX(sa.sold_at) AS last_purchase_at
               FROM sales sa
               JOIN branches b ON sa.branch_id = b.branch_id
               JOIN products p ON p.sku = sa.sku
               WHERE sa.customer_name IS NOT NULL AND sa.customer_name <> ''
               GROUP BY sa.customer_name
           ) agg
           JOIN sales s
             ON s.customer_name = agg.customer_name AND s.sold_at = agg.first_sold_at
           JOIN sales last_sale
             ON last_sale.customer_name = agg.customer_name AND last_sale.sold_at = agg.last_purchase_at
           JOIN branches last_b ON last_sale.branch_id = last_b.branch_id
           ORDER BY agg.total_spent DESC"""
    )

    totals = query(
        """SELECT COUNT(DISTINCT customer_name) AS customer_count,
                  COALESCE(SUM(qty_sold * unit_price), 0) AS total_named_revenue
           FROM sales WHERE customer_name IS NOT NULL AND customer_name <> ''""", fetchone=True,
    )
    multi_branch_count = sum(1 for r in rows if r["branch_count"] > 1)

    return render_template(
        "admin/customers.html", rows=rows, totals=totals,
        multi_branch_count=multi_branch_count,
    )


# ---------------------------------------------------------------- credit purchases
@bp.route("/credit-purchases")
@admin_required
def credit_purchases():
    """Outstanding Credit across every branch — who still owes what,
    HQ-wide. Mirrors branch.credit_purchases() but grouped by branch +
    buyer (so the same name at two branches stays two rows) and filterable
    by branch. Settled credit (see sale_stock.settle_credit(), used from
    the Scan Receipt page's "Mark as paid") drops off once cleared.
    """
    branch_filter = request.args.get("branch_id", "all")
    branches = query("SELECT branch_id, branch_name, is_hq FROM branches ORDER BY is_hq DESC, branch_name")

    sql = """SELECT s.branch_id, b.branch_name,
                    COALESCE(s.buyer_name, bu.username) AS buyer_name,
                    COUNT(*) AS transaction_count,
                    COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS total_units,
                    COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                    COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS total_amount,
                    MAX(s.sold_at) AS last_taken_at
             FROM sales s
             JOIN products p ON p.sku = s.sku
             JOIN branches b ON b.branch_id = s.branch_id
             LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
             WHERE s.payment_method = 'Credit' AND s.credit_settled_at IS NULL"""
    params = []
    if branch_filter.isdigit():
        sql += " AND s.branch_id = %s"
        params.append(int(branch_filter))
    sql += """ GROUP BY s.branch_id, b.branch_name, COALESCE(s.buyer_name, bu.username)
               ORDER BY total_amount DESC"""
    rows = query(sql, tuple(params))

    totals_sql = """SELECT COUNT(*) AS transaction_count,
                            COALESCE(SUM(qty_sold * unit_price), 0) AS total_amount
                     FROM sales WHERE payment_method = 'Credit' AND credit_settled_at IS NULL"""
    totals_params = ()
    if branch_filter.isdigit():
        totals_sql += " AND branch_id = %s"
        totals_params = (int(branch_filter),)
    totals = query(totals_sql, totals_params, fetchone=True)

    return render_template(
        "admin/credit_purchases.html", rows=rows, totals=totals,
        branches=branches, branch_filter=branch_filter,
    )


# ---------------------------------------------------------------- sales history / void
@bp.route("/sales-history")
@admin_required
def sales_history():
    """Every location's sales (HQ and branches), newest first — the place
    HQ voids a sale from. Recent voids are listed underneath as the
    permanent record of what was removed and why (sale_voids)."""
    branch_filter = request.args.get("branch_id", "all")
    branches = query("SELECT branch_id, branch_name, is_hq FROM branches ORDER BY is_hq DESC, branch_name")
    sql = """SELECT s.*, b.branch_name, b.is_hq, p.item_name, p.variant, p.unit,
                    COALESCE(s.buyer_name, bu.username) AS buyer_username
             FROM sales s
             JOIN branches b ON b.branch_id = s.branch_id
             JOIN products p ON p.sku = s.sku
             LEFT JOIN users bu ON s.buyer_user_id = bu.user_id"""
    params = ()
    if branch_filter.isdigit():
        sql += " WHERE s.branch_id = %s"
        params = (int(branch_filter),)
    sql += " ORDER BY s.sold_at DESC, s.sale_id DESC LIMIT 300"
    sales = query(sql, params)
    voids = query(
        """SELECT v.*, b.branch_name FROM sale_voids v JOIN branches b ON b.branch_id = v.branch_id
           ORDER BY v.voided_at DESC LIMIT 50"""
    )
    return render_template("admin/sales_history.html", sales=sales, voids=voids,
                           branches=branches, branch_filter=branch_filter)


@bp.route("/sales/<int:sale_id>/void", methods=["POST"])
@admin_required
def void_sale_route(sale_id):
    """HQ can void any location's sale — see sale_stock.void_sale()."""
    # Only ever back to an admin page (never an arbitrary URL from the form).
    next_url = request.form.get("next") or ""
    if not (next_url.startswith("/admin/") and not next_url.startswith("//")):
        next_url = url_for("admin.sales_history")
    back = redirect(next_url)
    reason = (request.form.get("reason") or "").strip()[:255]
    if not reason:
        flash("Give a reason for voiding this sale.", "error")
        return back
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            sale = void_sale(cur, sale_id=sale_id, reason=reason,
                             user_id=session.get("user_id"), username=session.get("username"))
            cur.close()
    except TransactionAborted as err:
        flash(str(err), "error")
        return back
    except Exception:
        current_app.logger.exception("void sale %s failed", sale_id)
        flash("Couldn't void that sale — please try again.", "error")
        return back
    notify_admin_and_branch(sale["branch_id"], ["inventory", "sales", "movement_logs"])
    log_action("void_sale", target=f"sale #{sale_id} — {sale['item_name']}",
               details=f"{sale['sale_type']} × {sale['qty_sold']} · {reason}"[:255])
    flash(f"Voided the {sale['sale_type'].lower()} of {sale['item_name']} — its stock was returned.", "success")
    return back
