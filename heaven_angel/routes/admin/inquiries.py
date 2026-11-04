
from flask import (
    current_app, flash, redirect, render_template,
    request, session, url_for,
)

from heaven_angel.db import TransactionAborted, execute, query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_admin

from heaven_angel.routes.admin._common import HQ_BRANCH_ID, bp


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
INQUIRY_STATUSES = ("New", "Contacted", "Follow-up",
                    "On Hold", "Closed", "Declined")


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

    # Each inquiry's snapshotted package contents next to what HQ has on
    # hand right now — what the Fulfill dialog shows (and what still needs
    # producing before a Closed order can ship).
    items_by_inquiry = _inquiry_items_with_stock([i["inquiry_id"] for i in inquiries])
    for inq in inquiries:
        inq["items"] = items_by_inquiry.get(inq["inquiry_id"], [])
        inq["short_count"] = sum(1 for it in inq["items"] if it["short"] > 0)

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
               COALESCE(SUM(CASE WHEN status = 'Declined' THEN 1 ELSE 0 END), 0) AS declined_count,
               COALESCE(SUM(CASE WHEN status = 'Closed' AND fulfilled_at IS NULL
                                  AND package_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS to_fulfill_count
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

    company_name = None
    stock_returned = False
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            try:
                # Serialize status changes with fulfillment and other
                # status changes. The fulfilled check must use this locked
                # row, or two requests can both return the same stock.
                cur.execute(
                    """SELECT company_name, fulfilled_at FROM partner_inquiries
                       WHERE inquiry_id = %s FOR UPDATE""",
                    (inquiry_id,),
                )
                inquiry = cur.fetchone()
                if not inquiry:
                    raise TransactionAborted("That inquiry no longer exists.")

                company_name = inquiry["company_name"]
                if inquiry["fulfilled_at"] and new_status != "Closed":
                    _return_inquiry_stock(cur, inquiry_id, company_name)
                    cur.execute(
                        """UPDATE partner_inquiries
                           SET status = %s, fulfilled_at = NULL, fulfilled_by_user_id = NULL,
                               closed_at = CASE WHEN %s = 'Closed' THEN COALESCE(closed_at, NOW()) ELSE NULL END
                           WHERE inquiry_id = %s""",
                        (new_status, new_status, inquiry_id),
                    )
                    stock_returned = True
                else:
                    cur.execute(
                        """UPDATE partner_inquiries
                           SET status = %s,
                               closed_at = CASE WHEN %s = 'Closed' THEN COALESCE(closed_at, NOW()) ELSE NULL END
                           WHERE inquiry_id = %s""",
                        (new_status, new_status, inquiry_id),
                    )
            finally:
                cur.close()
    except TransactionAborted as err:
        flash(str(err), "error")
        return redirect(url_for("admin.partner_inquiries", status=return_status))
    except Exception:
        current_app.logger.exception("Failed to update inquiry %s", inquiry_id)
        flash("Couldn't update that inquiry — please try again.", "error")
        return redirect(url_for("admin.partner_inquiries", status=return_status))

    if stock_returned:
        notify_admin(["partner_inquiries", "inventory", "movement_logs"])
        details = f"{new_status} — fulfilled stock returned to HQ"
        flash(f"Marked {company_name}'s inquiry as {new_status} and returned "
              "its items to HQ stock.", "success")
    else:
        notify_admin(["partner_inquiries"])
        details = new_status
        flash(f"Marked {company_name}'s inquiry as {new_status}.", "success")
    log_action("update_inquiry_status", target=company_name, details=details)
    return redirect(url_for("admin.partner_inquiries", status=return_status))


def _inquiry_items_with_stock(inquiry_ids):
    """{inquiry_id: [item, ...]} — each inquiry's snapshotted package
    contents (partner_inquiry_items) with HQ's current stock and how many
    are still short (need - on hand, never below 0)."""
    if not inquiry_ids:
        return {}
    placeholders = ",".join(["%s"] * len(inquiry_ids))
    rows = query(
        f"""SELECT pii.inquiry_id, pii.sku, pii.item_name, pii.unit, pii.qty,
                   COALESCE(bi.stock_qty, 0) AS hq_stock
            FROM partner_inquiry_items pii
            LEFT JOIN branch_inventory bi ON bi.branch_id = %s AND bi.sku = pii.sku
            WHERE pii.inquiry_id IN ({placeholders})
            ORDER BY pii.item_name""",
        (HQ_BRANCH_ID, *inquiry_ids),
    )
    out = {}
    for r in rows:
        r["short"] = max(r["qty"] - r["hq_stock"], 0)
        r["qty_unit"] = "mL" if r["unit"] == "BULK" else ""
        out.setdefault(r["inquiry_id"], []).append(r)
    return out


def _return_inquiry_stock(cur, inquiry_id, company_name):
    """Put a fulfilled inquiry's items back into HQ stock (PACKAGE_RETURN).
    Runs inside the caller's transaction, on its cursor."""
    cur.execute(
        "SELECT sku, qty FROM partner_inquiry_items WHERE inquiry_id = %s", (inquiry_id,))
    for item in cur.fetchall():
        cur.execute(
            """INSERT INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
            (HQ_BRANCH_ID, item["sku"], item["qty"]),
        )
        cur.execute(
            "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s",
            (HQ_BRANCH_ID, item["sku"]),
        )
        after_qty = cur.fetchone()["stock_qty"]
        cur.execute(
            """INSERT INTO stock_movement_logs
               (branch_id, sku, change_qty, movement_type, notes,
                created_by_user_id, reference_type, reference_id, before_qty, after_qty)
               VALUES (%s, %s, %s, 'PACKAGE_RETURN', %s, %s, 'PARTNER_INQUIRY', %s, %s, %s)""",
            (HQ_BRANCH_ID, item["sku"], item["qty"],
             f"Returned from {company_name}'s package order (reopened)",
             session.get("user_id"), inquiry_id, after_qty - item["qty"], after_qty),
        )


@bp.route("/partners/inquiries/<int:inquiry_id>/fulfill", methods=["POST"])
@admin_required
def fulfill_inquiry(inquiry_id):
    """Ship a Closed package order: take its snapshotted items out of HQ
    stock (PACKAGE_ORDER). Order -> produce -> fulfill — blocked, with
    the exact shortfall per item, until Production has made enough, so
    HQ stock never goes negative."""
    return_status = request.form.get("return_status", "all")
    back = redirect(url_for("admin.partner_inquiries", status=return_status))
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT * FROM partner_inquiries WHERE inquiry_id = %s FOR UPDATE", (inquiry_id,))
            inq = cur.fetchone()
            if not inq:
                raise TransactionAborted("That inquiry no longer exists.")
            if inq["status"] != "Closed":
                raise TransactionAborted("Only a Closed inquiry (a confirmed order) can be fulfilled.")
            if inq["fulfilled_at"]:
                raise TransactionAborted("That order has already been fulfilled.")
            cur.execute(
                """SELECT sku, item_name, unit, qty FROM partner_inquiry_items
                   WHERE inquiry_id = %s ORDER BY item_name FOR UPDATE""",
                (inquiry_id,),
            )
            items = cur.fetchall()
            if not items:
                raise TransactionAborted("This inquiry has no package items to fulfill.")

            # Lock and check every line first, so the message lists every
            # shortfall at once instead of one per attempt.
            stock, short = {}, []
            for item in items:
                cur.execute(
                    "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE",
                    (HQ_BRANCH_ID, item["sku"]),
                )
                row = cur.fetchone()
                have = row["stock_qty"] if row else 0
                stock[item["sku"]] = have
                if have < item["qty"]:
                    unit = " mL" if item["unit"] == "BULK" else ""
                    short.append(f"{item['item_name']} ({item['unit']}): need {item['qty']}{unit}, "
                                 f"HQ has {have}{unit}")
            if short:
                raise TransactionAborted(
                    "Not enough HQ stock to fulfill this order — produce the shortfall on "
                    "Production Log first. " + "; ".join(short) + ".")

            for item in items:
                before_qty = stock[item["sku"]]
                after_qty = before_qty - item["qty"]
                cur.execute(
                    "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
                    (after_qty, HQ_BRANCH_ID, item["sku"]),
                )
                cur.execute(
                    """INSERT INTO stock_movement_logs
                       (branch_id, sku, change_qty, movement_type, notes,
                        created_by_user_id, reference_type, reference_id, before_qty, after_qty)
                       VALUES (%s, %s, %s, 'PACKAGE_ORDER', %s, %s, 'PARTNER_INQUIRY', %s, %s, %s)""",
                    (HQ_BRANCH_ID, item["sku"], -item["qty"],
                     f"Package order for {inq['company_name']}",
                     session.get("user_id"), inquiry_id, before_qty, after_qty),
                )
            cur.execute(
                """UPDATE partner_inquiries SET fulfilled_at = NOW(), fulfilled_by_user_id = %s
                   WHERE inquiry_id = %s""",
                (session.get("user_id"), inquiry_id),
            )
            cur.close()
    except TransactionAborted as err:
        flash(str(err), "error")
        return back
    except Exception:
        current_app.logger.exception("Failed to fulfill inquiry %s", inquiry_id)
        flash("Couldn't fulfill that order — please try again.", "error")
        return back

    notify_admin(["partner_inquiries", "inventory", "movement_logs"])
    log_action("fulfill_package_order", target=inq["company_name"],
               details=f"{len(items)} item(s) from HQ stock")
    flash(f"Fulfilled {inq['company_name']}'s package order — {len(items)} item(s) taken "
          f"out of HQ stock.", "success")
    return back


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
