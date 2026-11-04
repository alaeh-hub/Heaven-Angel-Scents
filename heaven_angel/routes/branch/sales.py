
from flask import abort, current_app, flash, redirect, render_template, request, send_file, session, url_for

from heaven_angel.db import TransactionAborted, query, transaction
from heaven_angel.decorators import branch_required
from heaven_angel.services.receipts import build_sale_receipt_pdf
from heaven_angel.services.sales_import import build_sales_import_template, import_sales_rows, parse_sales_import_workbook
from heaven_angel.services.sale_stock import void_sale
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_admin_and_branch
from heaven_angel.utils import (
    ValidationError, consume_form_token,
    issue_form_token,
)

from heaven_angel.services import sale_entry
from heaven_angel.routes.branch._common import _branch_id, bp


# ---------------------------------------------------------------- record sale
@bp.route("/record-sale/<int:sale_id>/receipt")
@branch_required
def sale_receipt(sale_id):
    """Downloadable PDF receipt for a single sale/refill this branch
    recorded — see receipts.py. Scoped to this branch's own sale_id,
    same convention as the goods-received receipt() above.
    """
    pdf_buffer, sale = build_sale_receipt_pdf(sale_id, branch_id=_branch_id())
    if pdf_buffer is None:
        abort(404)
    return send_file(
        pdf_buffer, mimetype="application/pdf",
        as_attachment=True, download_name=f"SR-{sale_id:06d}.pdf",
    )


@bp.route("/record-sale", methods=["GET", "POST"])
@branch_required
def record_sale():
    """Record a Sale or a Refill.

    A Sale is the normal case (customer takes a bottle). A Refill is a
    customer bringing their own bottle back and only paying for
    product — both consume stock the same way, but are usually charged
    a different amount, so the price is always typed in here rather
    than pulled from the catalog automatically.

    payment_method covers anyone taking product now without paying
    cash — an employee against their own pay, or a customer buying on
    store credit ("utang") — see buyer_name on the sales table (free
    text, not tied to a login account; buyer_user_id is a separate,
    currently-unused FK).
    """
    bid = _branch_id()
    if request.method == "POST":
        if not consume_form_token("branch_record_sale"):
            flash(
                "This sale already went through, or the form expired — check Sales History before recording it again.", "error")
            return redirect(url_for("branch.record_sale"))

        try:
            sale = sale_entry.parse_sale_form(
                request.form, qty_label="Quantity sold",
                sale_type_prompt="Select whether this is a sale or a freebie.",
                product_prompt="Select a product to sell.",
                allow_refill=False,
                # Refills pour from bulk, and bulk is only ever held at HQ.
                refill_error="Refills are only recorded at HQ — branches don't hold bulk stock.")
        except sale_entry.SaleFormError as err:
            flash(str(err), "error")
            return redirect(url_for("branch.record_sale"))

        try:
            sale_entry.record_sale(sale, branch_id=bid, user_id=session.get("user_id"))
            notify_admin_and_branch(
                bid, ["inventory", "sales", "movement_logs"])
            flash(f"{sale.sale_type} recorded.", "success")
        except TransactionAborted as err:
            flash(str(err), "error")
        except Exception:
            current_app.logger.exception(
                "record_sale failed for branch_id=%s sku=%s", bid, sale.sku)
            flash("Couldn't record that sale — please try again.", "error")
        return redirect(url_for("branch.record_sale"))

    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.category, p.unit, p.price, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND bi.stock_qty > 0 ORDER BY p.item_name""",
        (bid,),
    )
    bulk_rate = query(
        "SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1", fetchone=True,
    )["rate_per_ml"]
    recent_sales = query(
        """SELECT s.*, p.item_name, p.unit, COALESCE(s.buyer_name, bu.username) AS buyer_username
           FROM sales s JOIN products p ON s.sku = p.sku
           LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 10""",
        (bid,),
    )
    employees = query(
        "SELECT user_id, username, role FROM users WHERE is_active = TRUE ORDER BY username"
    )
    # Every distinct customer name logged at this branch, each paired
    # with the address from that name's *first* sale (see schema.sql's
    # comment on sales.customer_address) — feeds the Customer name
    # autocomplete/autofill on the form below.
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
        (bid, bid),
    )
    return render_template(
        "branch/record_sale.html", inventory=inventory, recent_sales=recent_sales, employees=employees,
        customers=customers, bulk_rate=bulk_rate, form_token=issue_form_token("branch_record_sale"),
        import_form_token=issue_form_token("branch_import_sales"),
    )


# ---------------------------------------------------------------- bulk import (Excel)
@bp.route("/record-sale/import-template")
@branch_required
def sales_import_template():
    """Downloadable blank workbook for the "Import from Excel" button
    below — see sales_import.build_sales_import_template().
    """
    bid = _branch_id()
    inventory = query(
        """SELECT p.sku, p.item_name, p.variant, p.unit, p.price, bi.stock_qty
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (bid,),
    )
    buf = build_sales_import_template(inventory)
    return send_file(
        buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="sales-import-template.xlsx",
    )


@bp.route("/record-sale/import", methods=["POST"])
@branch_required
def import_sales():
    """Bulk-insert every row of an uploaded, filled-in copy of the
    template above in one go, instead of using Record Sale one line at
    a time. See sales_import.import_sales_rows() for the all-or-nothing
    transaction this runs — either the whole file lands, or none of it
    does, with every problem reported at once.
    """
    bid = _branch_id()
    if not consume_form_token("branch_import_sales"):
        flash(
            "This import already went through, or the form expired — try uploading again.", "error")
        return redirect(url_for("branch.record_sale"))

    upload = request.files.get("import_file")
    if not upload or not upload.filename:
        flash("Choose an Excel file to import.", "error")
        return redirect(url_for("branch.record_sale"))

    try:
        raw_rows = parse_sales_import_workbook(upload)
        inserted = import_sales_rows(bid, raw_rows, session.get("user_id"))
        notify_admin_and_branch(bid, ["inventory", "sales", "movement_logs"])
        flash(
            f"Imported {inserted} sale{'s' if inserted != 1 else ''} from the file.", "success")
    except (ValidationError, TransactionAborted) as err:
        for line in str(err).split("\n"):
            if line.strip():
                flash(line, "error")
    except Exception:
        current_app.logger.exception(
            "sales import failed for branch_id=%s", bid)
        flash("Couldn't import that file — please check the format and try again.", "error")
    return redirect(url_for("branch.record_sale"))


# ---------------------------------------------------------------- sales history
@bp.route("/sales-history")
@branch_required
def sales_history():
    bid = _branch_id()
    sales = query(
        # voidable: branch staff can void only what was recorded today.
        """SELECT s.*, p.item_name, p.variant, p.unit, DATE(s.recorded_at) = CURDATE() AS voidable
           FROM sales s JOIN products p ON s.sku = p.sku
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 200""",
        (bid,),
    )
    totals = query(
        """SELECT COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END),0) AS units,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END),0) AS bulk_ml,
                  COALESCE(SUM(s.qty_sold*s.unit_price),0) AS revenue
           FROM sales s JOIN products p ON p.sku = s.sku WHERE s.branch_id = %s""",
        (bid,), fetchone=True,
    )
    return render_template("branch/sales_history.html", sales=sales, totals=totals)


# ---------------------------------------------------------------- customers
@bp.route("/customers")
@branch_required
def customers():
    """Repeat-customer directory for this branch, built entirely from
    `sales.customer_name` / `customer_address` — there's no separate
    customers table. Address is taken from each name's *first* recorded
    sale, same convention as the Record Sale autocomplete's data-customers
    payload (see record_sale() below and schema.sql's comment on
    sales.customer_address). Walk-ins (blank customer_name) are excluded
    since there's nothing to group them by.
    """
    bid = _branch_id()
    rows = query(
        """SELECT s.customer_name AS name, s.customer_address AS address,
                  agg.purchase_count, agg.total_units, agg.total_bulk_ml, agg.total_spent, agg.last_purchase_at
           FROM (
               SELECT sa.customer_name,
                      COUNT(*) AS purchase_count,
                      -- Bulk sales store qty_sold in mL: kept apart from bottles.
                      COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_units,
                      COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                      COALESCE(SUM(sa.qty_sold * sa.unit_price), 0) AS total_spent,
                      MIN(sa.sold_at) AS first_sold_at,
                      MAX(sa.sold_at) AS last_purchase_at
               FROM sales sa
               JOIN products p ON p.sku = sa.sku
               WHERE sa.branch_id = %s AND sa.customer_name IS NOT NULL AND sa.customer_name <> ''
               GROUP BY sa.customer_name
           ) agg
           JOIN sales s
             ON s.customer_name = agg.customer_name AND s.sold_at = agg.first_sold_at AND s.branch_id = %s
           ORDER BY agg.total_spent DESC""",
        (bid, bid),
    )
    totals = query(
        """SELECT COUNT(DISTINCT customer_name) AS customer_count,
                  COALESCE(SUM(qty_sold * unit_price), 0) AS total_named_revenue
           FROM sales WHERE branch_id = %s AND customer_name IS NOT NULL AND customer_name <> ''""",
        (bid,), fetchone=True,
    )
    return render_template("branch/customers.html", rows=rows, totals=totals)


# ---------------------------------------------------------------- credit purchases
@bp.route("/credit-purchases")
@branch_required
def credit_purchases():
    """Credit leaderboard for this branch — who's taken how much product
    on credit, whether an employee against their own pay or a customer
    on store credit ("utang"), for reconciliation/collection. This is
    NOT "who rang up the sale": there's no such column on `sales` (see
    record_sale()'s comment on buyer_name/buyer_user_id above), only who
    the product was taken *for* on a Credit sale. Ordinary Cash sales
    aren't attributable to any one person and are excluded. Settled
    credit (see sale_stock.settle_credit(), used from the Scan Receipt
    page's "Mark as paid") drops off here once cleared.
    """
    bid = _branch_id()
    rows = query(
        """SELECT COALESCE(s.buyer_name, bu.username) AS buyer_name,
                  COUNT(*) AS transaction_count,
                  COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS total_units,
                  COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS total_amount,
                  MAX(s.sold_at) AS last_taken_at
           FROM sales s
           JOIN products p ON p.sku = s.sku
           LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
           WHERE s.branch_id = %s AND s.payment_method = 'Credit' AND s.credit_settled_at IS NULL
           GROUP BY COALESCE(s.buyer_name, bu.username)
           ORDER BY total_amount DESC""",
        (bid,),
    )
    totals = query(
        """SELECT COUNT(*) AS transaction_count,
                  COALESCE(SUM(qty_sold * unit_price), 0) AS total_amount
           FROM sales WHERE branch_id = %s AND payment_method = 'Credit' AND credit_settled_at IS NULL""",
        (bid,), fetchone=True,
    )
    return render_template("branch/credit_purchases.html", rows=rows, totals=totals)


# ---------------------------------------------------------------- void a sale
@bp.route("/sales/<int:sale_id>/void", methods=["POST"])
@branch_required
def void_sale_route(sale_id):
    """Branch staff may void their own branch's sales recorded today (a
    counter mistake); older ones go through HQ. See sale_stock.void_sale()."""
    bid = _branch_id()
    back = redirect(url_for("branch.sales_history"))
    reason = (request.form.get("reason") or "").strip()[:255]
    if not reason:
        flash("Give a reason for voiding this sale.", "error")
        return back
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            sale = void_sale(cur, sale_id=sale_id, reason=reason,
                             user_id=session.get("user_id"), username=session.get("username"),
                             branch_id=bid, today_only=True)
            cur.close()
    except TransactionAborted as err:
        flash(str(err), "error")
        return back
    except Exception:
        current_app.logger.exception("branch void sale %s failed", sale_id)
        flash("Couldn't void that sale — please try again.", "error")
        return back
    notify_admin_and_branch(bid, ["inventory", "sales", "movement_logs"])
    log_action("void_sale", target=f"sale #{sale_id} — {sale['item_name']}",
               details=f"{session.get('branch_name', 'branch')} · {sale['sale_type']} × {sale['qty_sold']} · {reason}"[:255])
    flash(f"Voided the {sale['sale_type'].lower()} of {sale['item_name']} — its stock was returned.", "success")
    return back
