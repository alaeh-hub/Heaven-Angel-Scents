"""Public partner portal — no login required.

Distributors and resellers reach this from a plain link (or the "Browse
our packages" link on the sign-in screen), browse the active packages
HQ is currently offering, and submit an inquiry on one. Submitting an
inquiry:

  1. Saves it permanently to partner_inquiries (see schema.sql) — this
     is the history admins review on the Partner Inquiries page.
  2. Matches an existing partners row by email/phone, or creates a new
     one, and links it to the inquiry — see _find_or_create_partner().
  3. Emails HQ a notification (best-effort — see mailer.py). A failed
     or unconfigured mailer never loses the inquiry itself, since the
     save in step 1 already happened.

Nothing here requires @login_required/@admin_required — nothing under
this blueprint should, since the whole point is that a prospect
doesn't have (and shouldn't need) an account to reach it.
"""
import decimal

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from db import execute, query
from mailer import send_partner_inquiry_email
from sockets import notify_admin, notify_bell
from utils import PARTNER_TYPES, ValidationError

bp = Blueprint("portal", __name__, url_prefix="/partner-portal")


def _package_value(discount_percent, reference_total):
    """Same discount math as admin.py's _package_value() — duplicated
    rather than imported to keep this public blueprint decoupled from
    the admin-only one; if that ever becomes annoying to keep in sync,
    it's a two-line function to promote into a shared module."""
    reference_total = decimal.Decimal(reference_total)
    discount_percent = decimal.Decimal(discount_percent)
    discounted_total = reference_total * \
        (decimal.Decimal("1") - (discount_percent / decimal.Decimal("100")))
    return reference_total, discounted_total


def _find_or_create_partner(partner_type, company_name, contact_person, phone, email, address):
    """Match an inquiry to an existing partner by email, then phone, so
    the same distributor/reseller inquiring more than once doesn't pile
    up duplicate partner rows. No match -> create a new partner from
    what they entered, same shape as admin.py's "Add a partner" form.

    Returns the partner_id either way.
    """
    partner = None
    if email:
        partner = query("SELECT partner_id FROM partners WHERE email = %s",
                         (email,), fetchone=True)
    if not partner and phone:
        partner = query("SELECT partner_id FROM partners WHERE phone = %s",
                         (phone,), fetchone=True)

    if partner:
        return partner["partner_id"]

    partner_id, _ = execute(
        """INSERT INTO partners
               (partner_type, partner_name, contact_person, phone, email, address, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (partner_type, company_name, contact_person, phone, email, address,
         "Added automatically from a partner portal package inquiry."),
    )
    notify_admin(["partners"])
    return partner_id


@bp.route("/packages")
def packages():
    """Public catalog of active packages, optionally filtered to just
    Distributor- or Reseller-scoped ones (packages scoped 'Both' always
    show either way)."""
    scope_filter = request.args.get("scope", "all")
    where_extra = ""
    params = ()
    if scope_filter in PARTNER_TYPES:
        where_extra = "AND (pkg.partner_scope = 'Both' OR pkg.partner_scope = %s)"
        params = (scope_filter,)

    package_rows = query(
        f"""SELECT pkg.*, COALESCE(SUM(pi.qty * p.price), 0) AS reference_total
            FROM packages pkg
            LEFT JOIN package_items pi ON pi.package_id = pkg.package_id
            LEFT JOIN products p ON p.sku = pi.sku
            WHERE pkg.is_active = TRUE {where_extra}
            GROUP BY pkg.package_id
            ORDER BY pkg.created_at DESC""",
        params,
    )

    package_list = []
    for row in package_rows:
        reference_total, discounted_total = _package_value(
            row["discount_percent"], row["reference_total"])
        row["reference_total"] = reference_total
        row["discounted_total"] = discounted_total
        row["items"] = query(
            """SELECT p.item_name, p.variant, p.unit, pi.qty
               FROM package_items pi JOIN products p ON pi.sku = p.sku
               WHERE pi.package_id = %s ORDER BY p.item_name""",
            (row["package_id"],),
        )
        package_list.append(row)

    return render_template(
        "public/packages.html",
        package_list=package_list, scope_filter=scope_filter, partner_types=PARTNER_TYPES,
    )


@bp.route("/packages/<int:package_id>/inquire", methods=["POST"])
def inquire(package_id):
    return_scope = request.form.get("return_scope", "all")

    pkg = query(
        "SELECT package_id, package_name, discount_percent FROM packages "
        "WHERE package_id = %s AND is_active = TRUE",
        (package_id,), fetchone=True,
    )
    if not pkg:
        flash("That package is no longer available.", "error")
        return redirect(url_for("portal.packages", scope=return_scope))

    partner_type = request.form.get("partner_type", "").strip()
    company_name = request.form.get("company_name", "").strip()
    contact_person = request.form.get("contact_person", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    email = request.form.get("email", "").strip() or None
    address = request.form.get("address", "").strip() or None
    message = request.form.get("message", "").strip() or None

    if partner_type not in PARTNER_TYPES:
        flash("Select whether you're a distributor or a reseller.", "error")
        return redirect(url_for("portal.packages", scope=return_scope))
    if not company_name:
        flash("Business / company name is required.", "error")
        return redirect(url_for("portal.packages", scope=return_scope))
    if not phone and not email:
        flash("Please provide at least a phone number or an email so we can reach you.", "error")
        return redirect(url_for("portal.packages", scope=return_scope))
    if len(message or "") > 500:
        flash("Message is too long — please keep it under 500 characters.", "error")
        return redirect(url_for("portal.packages", scope=return_scope))

    partner_id = _find_or_create_partner(
        partner_type, company_name, contact_person, phone, email, address)

    package_snapshot = f"{pkg['package_name']} ({pkg['discount_percent']}% off)"
    inquiry_id, _ = execute(
        """INSERT INTO partner_inquiries
               (package_id, partner_id, partner_type, company_name, contact_person,
                phone, email, address, message, package_name_snapshot)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (package_id, partner_id, partner_type, company_name, contact_person,
         phone, email, address, message, package_snapshot),
    )

    email_sent = send_partner_inquiry_email(
        package_name=pkg["package_name"], partner_type=partner_type, company_name=company_name,
        contact_person=contact_person, phone=phone, email=email, address=address, message=message,
    )
    if email_sent:
        execute(
            "UPDATE partner_inquiries SET email_sent = TRUE WHERE inquiry_id = %s",
            (inquiry_id,),
        )

    notify_admin(["partners", "partner_inquiries"])
    notify_bell(
        f"New {partner_type.lower()} inquiry from {company_name} — {pkg['package_name']}",
        room="admin", level="success",
    )

    flash("Thanks! Your inquiry has been sent — our team will reach out shortly.", "success")
    return redirect(url_for("portal.packages", scope=return_scope))
