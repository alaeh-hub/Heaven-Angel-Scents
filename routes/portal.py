"""Public partner portal — no login required.

Distributors and resellers reach this from a private link HQ shares
with them directly — see PARTNER_PORTAL_SLUG in config.py. It is
deliberately NOT linked anywhere in the signed-in app or on the login
screen: the slug in the URL is the only thing standing between "public
internet" and "sees our packages", so the link itself has to stay out
of anything a stranger could stumble onto. An admin can always copy
the current link from the Partners page (see routes/admin.py's
partners()).

Flow, on purpose kept to a single page per package:

  1. /packages             — browse active packages, "View Package" per card.
  2. /packages/<id>        — full package detail: every product in it (with
                             photo, variant, unit, qty), pricing, and an
                             "Inquire About This Package" button that opens
                             an inquiry form right there (no extra page).
  3. POST .../inquire      — submitting that form:
       a. Saves the inquiry permanently to partner_inquiries (see
          schema.sql) — the history admins review on the Partner
          Inquiries page.
       b. Matches an existing partners row by email/phone, or creates a
          new one, and links it to the inquiry — see
          _find_or_create_partner().
       c. Emails HQ a notification (best-effort — see mailer.py). A
          failed or unconfigured mailer never loses the inquiry itself,
          since the save in step (a) already happened.
       d. Pushes a realtime event so every open HQ Admin tab's "Partner
          Inquiries" sidebar badge updates immediately, and a bell
          notification for the new lead — see sockets.py.

Nothing here requires @login_required/@admin_required — nothing under
this blueprint should, since the whole point is that a prospect
doesn't have (and shouldn't need) an account to reach it. The slug
check below is what stands in for authentication instead.
"""
import decimal
import secrets

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from db import execute, query
from mailer import send_partner_inquiry_email
from sockets import notify_admin, notify_bell
from utils import (
    PARTNER_TYPES, ValidationError, parse_email, parse_phone, parse_required_text,
)

bp = Blueprint("portal", __name__, url_prefix="/partner-portal")


def _verify_slug(slug):
    """Gate every route in this blueprint behind the configured secret
    slug (see PARTNER_PORTAL_SLUG in config.py). A wrong or missing
    slug 404s exactly like a page that doesn't exist — it never reveals
    that a portal lives at this path at all.

    secrets.compare_digest avoids leaking the real slug's length/prefix
    through response-timing differences.
    """
    configured = current_app.config.get("PARTNER_PORTAL_SLUG", "")
    if not configured or not secrets.compare_digest(slug, configured):
        abort(404)


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

    On a match, the existing partner's name/type/contact/address are
    now left ALONE — the first submission is what "wins" the partner
    record on the Partners page, permanently. Every inquiry's own
    submitted details (which may differ — a different name, a typo
    fixed, a new contact person) still live forever, unedited, in
    partner_inquiries; see admin.partner_detail for that full history
    per partner. Only last_inquiry_at/inquiry_count on the matched
    partner move on every inquiry, since those are just a rollup of
    that same history (see schema.sql's partners table comment).

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
        execute(
            """UPDATE partners
                   SET last_inquiry_at = CURRENT_TIMESTAMP, inquiry_count = inquiry_count + 1
               WHERE partner_id = %s""",
            (partner["partner_id"],),
        )
        notify_admin(["partners"])
        return partner["partner_id"]

    partner_id, _ = execute(
        """INSERT INTO partners
               (partner_type, partner_name, contact_person, phone, email, address, notes,
                last_inquiry_at, inquiry_count)
           VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, 1)""",
        (partner_type, company_name, contact_person, phone, email, address,
         "Added automatically from a partner portal package inquiry."),
    )
    notify_admin(["partners"])
    return partner_id


def _active_package_or_none(package_id):
    return query(
        "SELECT * FROM packages WHERE package_id = %s AND is_active = TRUE",
        (package_id,), fetchone=True,
    )


@bp.route("/<slug>/packages")
def packages(slug):
    """Public catalog of active packages, optionally filtered to just
    Distributor- or Reseller-scoped ones (packages scoped 'Both' always
    show either way). Each card links to the package's own detail page
    ("View Package") — inquiring happens there, not from this list."""
    _verify_slug(slug)

    scope_filter = request.args.get("scope", "all")
    where_extra = ""
    params = ()
    if scope_filter in PARTNER_TYPES:
        where_extra = "AND (pkg.partner_scope = 'Both' OR pkg.partner_scope = %s)"
        params = (scope_filter,)

    package_rows = query(
        f"""SELECT pkg.*, COUNT(pi.package_item_id) AS item_count,
                   COALESCE(SUM(pi.qty * p.price), 0) AS reference_total
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
        package_list.append(row)

    return render_template(
        "public/packages.html",
        package_list=package_list, scope_filter=scope_filter,
        partner_types=PARTNER_TYPES, slug=slug,
    )


@bp.route("/<slug>/packages/<int:package_id>")
def package_detail(slug, package_id):
    """One package's full detail: every product in it (photo, variant,
    unit, qty per set), pricing, and the inquiry form — all on this one
    page, so "View Package" -> "Inquire" never leaves it.

    A missing/inactive package_id (deactivated by an admin, or — since
    Step 2 — auto-deleted after its last product was removed) redirects
    back to the package list with an explanation instead of a bare 404.
    A distributor/reseller reaching this from an old bookmark or a
    shared link has no way to know a package disappeared; a dead-end
    error page is a worse experience than just landing back on what's
    currently available.
    """
    _verify_slug(slug)
    pkg = _active_package_or_none(package_id)
    if not pkg:
        flash("That package is no longer available — here's what's currently on offer.", "error")
        return redirect(url_for("portal.packages", slug=slug))

    items = query(
        """SELECT p.item_name, p.variant, p.unit, p.image_path, p.price, pi.qty
           FROM package_items pi JOIN products p ON pi.sku = p.sku
           WHERE pi.package_id = %s ORDER BY p.item_name""",
        (package_id,),
    )
    reference_total = sum(
        (decimal.Decimal(i["qty"]) * decimal.Decimal(i["price"])
         for i in items),
        decimal.Decimal("0"),
    )
    reference_total, discounted_total = _package_value(
        pkg["discount_percent"], reference_total)

    return render_template(
        "public/package_detail.html",
        pkg=pkg, items=items, reference_total=reference_total, discounted_total=discounted_total,
        partner_types=PARTNER_TYPES, slug=slug,
    )


@bp.route("/<slug>/packages/<int:package_id>/inquire", methods=["POST"])
def inquire(slug, package_id):
    _verify_slug(slug)
    detail_redirect = redirect(
        url_for("portal.package_detail", slug=slug, package_id=package_id))

    pkg = query(
        "SELECT package_id, package_name, discount_percent FROM packages "
        "WHERE package_id = %s AND is_active = TRUE",
        (package_id,), fetchone=True,
    )
    if not pkg:
        flash("That package is no longer available.", "error")
        return redirect(url_for("portal.packages", slug=slug))

    partner_type = request.form.get("partner_type", "").strip()
    address = request.form.get("address", "").strip() or None
    message = request.form.get("message", "").strip() or None

    # Every field below is required now except address and message — a
    # Distributor is filling this in on behalf of a business, a Reseller
    # on behalf of themselves (see the "You are a" toggle on the
    # template, which relabels "Business / company name" to "Your
    # full name" for a Reseller — resellers don't necessarily have a
    # registered company). Either way the underlying column is still
    # company_name; only the label/placeholder changes per type.
    try:
        if partner_type not in PARTNER_TYPES:
            raise ValidationError("Select whether you're a distributor or a reseller.")

        name_field_label = "Business / company name" if partner_type == "Distributor" else "Your full name"
        company_name = parse_required_text(
            request.form.get("company_name"), name_field_label, max_length=150
        )
        contact_person = parse_required_text(
            request.form.get("contact_person"), "Contact person", max_length=100
        )
        phone = parse_phone(request.form.get("phone"))
        email = parse_email(request.form.get("email"))
        if len(message or "") > 500:
            raise ValidationError("Message is too long — please keep it under 500 characters.")
    except ValidationError as err:
        flash(str(err), "error")
        return detail_redirect

    partner_id = _find_or_create_partner(
        partner_type, company_name, contact_person, phone, email, address)

    # order_amount snapshots what this partner would actually pay for the
    # package right now — same reference-total-then-discount math as
    # package_detail() above, computed fresh here rather than trusting a
    # hidden form field (which the visitor's browser could tamper with).
    # Frozen permanently at insert time, same "snapshot, don't recompute
    # later" philosophy as package_name_snapshot itself — see schema.sql's
    # note on partner_inquiries.order_amount for why this only counts once
    # an admin marks the inquiry Closed.
    item_totals = query(
        """SELECT COALESCE(SUM(pi.qty * p.price), 0) AS reference_total
           FROM package_items pi JOIN products p ON p.sku = pi.sku
           WHERE pi.package_id = %s""",
        (package_id,), fetchone=True,
    )
    _, order_amount = _package_value(
        pkg["discount_percent"], item_totals["reference_total"])

    package_snapshot = f"{pkg['package_name']} ({pkg['discount_percent']}% off)"
    inquiry_id, _ = execute(
        """INSERT INTO partner_inquiries
               (package_id, partner_id, partner_type, company_name, contact_person,
                phone, email, address, message, package_name_snapshot, order_amount)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (package_id, partner_id, partner_type, company_name, contact_person,
         phone, email, address, message, package_snapshot, order_amount),
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

    # Realtime: every open HQ Admin tab's "Partner Inquiries" sidebar
    # badge (and the Partners page, since a new partner may have just
    # been created) updates immediately — see main.js's initRealtime(),
    # which already refetches that badge on the "partner_inquiries" scope.
    notify_admin(["partners", "partner_inquiries"])
    notify_bell(
        f"New {partner_type.lower()} inquiry from {company_name} — {pkg['package_name']}",
        room="admin", level="success",
    )

    flash("Thanks! Your inquiry has been sent — our team will reach out shortly.", "success")
    return detail_redirect
