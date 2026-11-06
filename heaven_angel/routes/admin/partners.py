
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
)

from heaven_angel.routes.admin._common import bp


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
        _scheme="https",
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
    portal.py's _match_or_create_partner(): a repeat inquiry from a
    known email/phone no longer overwrites the partner's name/contact/
    address above (see partners.html), so this page is where the full
    story — including whatever changed between visits — actually lives.
    partners.last_inquiry_at/inquiry_count are just a rollup of what's
    queried here, kept in sync on every new inquiry.
    """
    partner = query("SELECT * FROM partners WHERE partner_id = %s",
                    (partner_id,), fetchone=True)
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
