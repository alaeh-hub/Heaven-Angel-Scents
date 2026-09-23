"""Public partner portal — no login required.

Distributors and resellers reach this from a private link HQ shares
with them directly — see PARTNER_PORTAL_SLUG in config.py. It is
deliberately NOT linked anywhere in the signed-in app or on the login
screen: the slug in the URL is the only thing standing between "public
internet" and "sees our packages", so the link itself has to stay out
of anything a stranger could stumble onto. An admin can always copy
the current link from the Partners page (see routes/admin.py's
partners()).

The pages themselves are a React app (see public-site/ at the repo
root). This blueprint only does two things for it:

  * Serves the built app's index.html at the page URLs below (after
    the slug check), so the private link HQ hands out never changed:
      /packages             — browse active packages.
      /packages/<id>        — one package's full detail + inquiry form.
      /products             — the full product catalog.
    React Router takes over from there in the browser.
  * Exposes the JSON API that app reads and writes, under /api/:
      GET  /api/packages              — the package list (?scope= filter).
      GET  /api/packages/<id>         — one package, its products, a few
                                        other packages, and a CSRF token.
      GET  /api/products              — the catalog (?gender=, ?page=).
      POST /api/packages/<id>/inquire — submitting the inquiry form:
       a. Saves the inquiry permanently to partner_inquiries (see
          schema.sql) — the history admins review on the Partner
          Inquiries page.
       b. Matches an existing partners row by email/phone, or creates a
          new one, and links it to the inquiry — see
          _find_or_create_partner().
       c. Emails HQ a notification, off the request thread (best-effort —
          see mailer.py and _send_inquiry_notification_async() below). A
          failed or unconfigured mailer never loses the inquiry itself,
          since the save in step (a) already happened, and a slow SMTP
          server never holds up the visitor's response either.
       d. Pushes a realtime event so every open HQ Admin tab's "Partner
          Inquiries" sidebar badge updates immediately, and a bell
          notification for the new lead — see sockets.py.

Nothing here requires @login_required/@admin_required — nothing under
this blueprint should, since the whole point is that a prospect
doesn't have (and shouldn't need) an account to reach it. The slug
check below is what stands in for authentication instead. That also
means this blueprint is reachable by anyone who has (or guesses) the
slug, with no login to throttle via the usual account-lockout path —
see the rate limit on inquire() below.
"""
import decimal
import os
import secrets
import threading

from flask import Blueprint, abort, current_app, jsonify, request, send_file, url_for
from flask_wtf.csrf import generate_csrf

from db import execute, query
from extensions import limiter
from mailer import send_partner_inquiry_email
from sockets import notify_admin, notify_bell
from utils import (
    PARTNER_TYPES, ValidationError, parse_email, parse_optional_text, parse_phone,
    parse_required_text, product_avatar,
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


def _send_inquiry_notification_async(app, inquiry_id, mail_kwargs):
    """Send the HQ notification email (and record email_sent) on a
    background thread, off the request/response path.

    inquire() below is public, unauthenticated, and — even rate-limited —
    can still be hit repeatedly by anyone who has the portal slug.
    mailer.py's own smtplib call has a 10s timeout; running that inline,
    on the same thread/worker that's handling the HTTP request, meant a
    burst of submissions (malicious or just a flaky mail server) could
    tie up every worker for up to 10s apiece before any of them could
    respond — a self-inflicted denial of service on top of the extra
    DB writes. The inquiry row is already saved by inquire() before this
    is spawned, so a slow, failed, or unconfigured mailer here can never
    lose the inquiry itself — same contract mailer.py already documents,
    just moved off the request thread too.

    Needs its own Flask app context: mailer.py reads current_app.config/
    current_app.logger, and db.get_db()'s cached connection lives on
    Flask's `g`, which is per app-context (i.e. per thread here) rather
    than shared — so this thread transparently gets its own DB
    connection the first time execute() is called inside it.
    """
    with app.app_context():
        try:
            email_sent = send_partner_inquiry_email(**mail_kwargs)
            if email_sent:
                execute(
                    "UPDATE partner_inquiries SET email_sent = TRUE WHERE inquiry_id = %s",
                    (inquiry_id,),
                )
        except Exception:
            app.logger.exception(
                "Background partner-inquiry notification failed for inquiry_id=%s", inquiry_id)


def _money(value):
    """Decimal -> float rounded to centavos, for JSON. Display-only: the
    stored order_amount on an inquiry is still computed server-side in
    inquire() below, never taken from anything the browser sends back."""
    return float(decimal.Decimal(value).quantize(decimal.Decimal("0.01")))


def _package_summary(row, item_count=None):
    """One package as the React app sees it — the same fields the old
    Jinja templates read, with totals already discounted via
    _package_value()."""
    reference_total, discounted_total = _package_value(
        row["discount_percent"], row["reference_total"])
    return {
        "package_id": row["package_id"],
        "package_name": row["package_name"],
        "description": row["description"],
        "partner_scope": row["partner_scope"],
        "discount_percent": float(row["discount_percent"]),
        "item_count": int(row["item_count"] if item_count is None else item_count),
        "reference_total": _money(reference_total),
        "discounted_total": _money(discounted_total),
    }


def _serve_public_site():
    """Hand the browser the built React app (public-site/, built into
    static/public-site/ by `npm run build`). Served no-cache so a fresh
    build is picked up on the next page load; the hashed JS/CSS files it
    references are ordinary static files and cache normally."""
    index_path = os.path.join(current_app.static_folder, "public-site", "index.html")
    if not os.path.isfile(index_path):
        current_app.logger.error(
            "Partner portal front end is not built: %s is missing. Run `npm install` and "
            "`npm run build` inside public-site/ (or use `npm run dev` there while developing).",
            index_path,
        )
        return (
            "The partner portal front end hasn't been built yet. "
            "Run `npm install && npm run build` inside public-site/.",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )
    response = send_file(index_path, max_age=0)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/<slug>/packages")
def packages(slug):
    """Page URL for the package list — see _serve_public_site(). Still
    the URL admin.py's Partners page copies for HQ to share."""
    _verify_slug(slug)
    return _serve_public_site()


@bp.route("/<slug>/packages/<int:package_id>")
def package_detail(slug, package_id):
    """Page URL for one package. A missing/inactive package is handled
    in the browser (the API below 404s and the app sends the visitor
    back to the list with an explanation), so this always serves the
    app once the slug checks out."""
    _verify_slug(slug)
    return _serve_public_site()


@bp.route("/<slug>/products")
def products_page(slug):
    """Page URL for the full product catalog ("View all" under the
    collection strip) — see _serve_public_site()."""
    _verify_slug(slug)
    return _serve_public_site()


PRODUCTS_PER_PAGE = 12
PRODUCT_GENDERS = ("Male", "Female", "Unisex")
# Smallest bottle first, bulk last, whatever order the SKUs were added in.
_UNIT_ORDER = "FIELD(unit, '3ML', '10ML', '50ML', '85ML', 'BULK')"


@bp.route("/<slug>/api/products")
def api_products(slug):
    """Every scent in the catalog, one entry per product name + gender
    (the SKU table has a row per size, so sizes are folded into a list),
    optionally filtered by gender and paginated.

    ?gender=Male|Female|Unisex (anything else = all), ?page=N (1-based,
    clamped to the last page). Also returns per-gender counts for the
    filter tabs.
    """
    _verify_slug(slug)

    gender = request.args.get("gender", "all")
    if gender not in PRODUCT_GENDERS:
        gender = "all"
    where = "WHERE variant = %s" if gender != "all" else ""
    params = (gender,) if gender != "all" else ()

    count_rows = query(
        """SELECT variant, COUNT(*) AS c
           FROM (SELECT item_name, variant FROM products GROUP BY item_name, variant) grouped
           GROUP BY variant"""
    )
    counts = {g: 0 for g in PRODUCT_GENDERS}
    for row in count_rows:
        counts[row["variant"]] = int(row["c"])
    counts["all"] = sum(counts.values())

    total = counts[gender]
    pages = max(1, -(-total // PRODUCTS_PER_PAGE))
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)

    rows = query(
        f"""SELECT item_name, variant,
                   GROUP_CONCAT(DISTINCT unit ORDER BY {_UNIT_ORDER} SEPARATOR ',') AS units,
                   MAX(image_path) AS image_path
            FROM products {where}
            GROUP BY item_name, variant
            ORDER BY item_name, variant
            LIMIT %s OFFSET %s""",
        params + (PRODUCTS_PER_PAGE, (page - 1) * PRODUCTS_PER_PAGE),
    )

    return jsonify(
        products=[
            {
                "item_name": r["item_name"],
                "variant": r["variant"],
                "sizes": [u for u in (r["units"] or "").split(",") if u],
                "image_url": url_for("static", filename=r["image_path"]) if r["image_path"] else None,
            }
            for r in rows
        ],
        gender=gender,
        counts=counts,
        page=page,
        pages=pages,
        per_page=PRODUCTS_PER_PAGE,
        total=total,
    )


@bp.route("/<slug>/api/packages")
def api_packages(slug):
    """Active packages, optionally filtered to just Distributor- or
    Reseller-scoped ones (packages scoped 'Both' always show either
    way)."""
    _verify_slug(slug)

    scope_filter = request.args.get("scope", "all")
    where_extra = ""
    params = ()
    if scope_filter in PARTNER_TYPES:
        where_extra = "AND (pkg.partner_scope = 'Both' OR pkg.partner_scope = %s)"
        params = (scope_filter,)
    else:
        scope_filter = "all"

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

    return jsonify(
        packages=[_package_summary(row) for row in package_rows],
        scope=scope_filter,
        partner_types=list(PARTNER_TYPES),
    )


@bp.route("/<slug>/api/packages/<int:package_id>")
def api_package_detail(slug, package_id):
    """One package's full detail: every product in it (photo, variant,
    unit, qty per set), pricing, a few other packages, and the CSRF
    token the inquiry form sends back.

    A missing/inactive package_id (deactivated by an admin, or
    auto-deleted after its last product was removed) is a 404 with a
    visitor-facing message — the app shows it and returns to the
    package list rather than a dead-end error page, since a
    distributor/reseller following an old bookmark or a shared link has
    no way to know a package disappeared.
    """
    _verify_slug(slug)
    pkg = _active_package_or_none(package_id)
    if not pkg:
        return jsonify(
            error="That package is no longer available. Here's what's on offer right now."), 404

    items = query(
        """SELECT p.item_name, p.variant, p.unit, p.image_path, p.price, pi.qty
           FROM package_items pi JOIN products p ON pi.sku = p.sku
           WHERE pi.package_id = %s ORDER BY p.item_name""",
        (package_id,),
    )
    pkg["reference_total"] = sum(
        (decimal.Decimal(i["qty"]) * decimal.Decimal(i["price"])
         for i in items),
        decimal.Decimal("0"),
    )

    # A handful of other active packages (same visibility rule as the
    # main list — scoped to this package's own partner_scope, or 'Both'
    # packages, so a Distributor-only visitor never sees a Reseller-only
    # suggestion and vice versa) so a visitor who lands directly on this
    # page via a shared link isn't stuck with nowhere else to look.
    other_rows = query(
        """SELECT pkg2.*, COUNT(pi.package_item_id) AS item_count,
                  COALESCE(SUM(pi.qty * p.price), 0) AS reference_total
           FROM packages pkg2
           LEFT JOIN package_items pi ON pi.package_id = pkg2.package_id
           LEFT JOIN products p ON p.sku = pi.sku
           WHERE pkg2.is_active = TRUE AND pkg2.package_id != %s
             AND (pkg2.partner_scope = 'Both' OR pkg2.partner_scope = %s OR %s = 'Both')
           GROUP BY pkg2.package_id
           ORDER BY pkg2.created_at DESC
           LIMIT 3""",
        (package_id, pkg["partner_scope"], pkg["partner_scope"]),
    )

    return jsonify(
        package=_package_summary(pkg, item_count=len(items)),
        items=[
            {
                "item_name": i["item_name"],
                "variant": i["variant"],
                "unit": i["unit"],
                "qty": int(i["qty"]),
                "image_url": url_for("static", filename=i["image_path"]) if i["image_path"] else None,
                "initials": product_avatar(i["item_name"])["initials"],
            }
            for i in items
        ],
        other_packages=[_package_summary(row) for row in other_rows],
        partner_types=list(PARTNER_TYPES),
        # Sent back as the X-CSRFToken header on the inquiry POST — see
        # CSRFProtect in app.py. Tied to the session cookie this same
        # response sets.
        csrf_token=generate_csrf(),
    )


@bp.route("/<slug>/api/packages/<int:package_id>/inquire", methods=["POST"])
# This is the only write (and the only endpoint that fans out to email +
# a DB row per hit) anywhere in the unauthenticated portal blueprint —
# there's no login to throttle abuse through the way auth.login() does
# ("10 per minute"), so it gets its own limit here instead. Two windows,
# same style as AI_CHAT_RATE_LIMIT in config.py: tight enough to blunt a
# scripted flood of fake inquiries, loose enough that a real distributor
# fumbling the form a few times in a row (or several people behind the
# same office/shared IP) never gets blocked.
@limiter.limit("5 per minute;30 per day")
def inquire(slug, package_id):
    """Takes a JSON body with the inquiry form's fields. Responds 201
    {"message"} on success, or 4xx {"error"} with a message to show the
    visitor."""
    _verify_slug(slug)

    pkg = query(
        "SELECT package_id, package_name, discount_percent FROM packages "
        "WHERE package_id = %s AND is_active = TRUE",
        (package_id,), fetchone=True,
    )
    if not pkg:
        return jsonify(error="That package is no longer available."), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Something went wrong sending your inquiry. Please try again."), 400

    partner_type = str(data.get("partner_type") or "").strip()
    message = str(data.get("message") or "").strip() or None

    # Every field below is required except address and message — a
    # Distributor is filling this in on behalf of a business, a Reseller
    # on behalf of themselves (the form relabels "Business / company
    # name" to "Your full name" for a Reseller — resellers don't
    # necessarily have a registered company). Either way the underlying
    # column is still company_name; only the label changes per type.
    try:
        if partner_type not in PARTNER_TYPES:
            raise ValidationError("Select whether you're a distributor or a reseller.")

        name_field_label = "Business / company name" if partner_type == "Distributor" else "Your full name"
        company_name = parse_required_text(
            data.get("company_name"), name_field_label, max_length=150
        )
        contact_person = parse_required_text(
            data.get("contact_person"), "Contact person", max_length=100
        )
        phone = parse_phone(data.get("phone"))
        email = parse_email(data.get("email"))
        address = parse_optional_text(
            data.get("address"), "Address", max_length=255
        )
        if len(message or "") > 500:
            raise ValidationError("Message is too long. Please keep it under 500 characters.")
    except ValidationError as err:
        return jsonify(error=str(err)), 400

    partner_id = _find_or_create_partner(
        partner_type, company_name, contact_person, phone, email, address)

    # order_amount snapshots what this partner would actually pay for the
    # package right now — same reference-total-then-discount math as
    # api_package_detail() above, computed fresh here rather than
    # trusting anything the browser sends. Frozen permanently at insert
    # time, same "snapshot, don't recompute later" philosophy as
    # package_name_snapshot itself — see schema.sql's note on
    # partner_inquiries.order_amount for why this only counts once an
    # admin marks the inquiry Closed.
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

    # Off the request thread — see _send_inquiry_notification_async()'s
    # docstring above. The inquiry row is already committed at this
    # point, so the visitor's own response below no longer waits on
    # mailer.py's smtplib call (up to a 10s timeout) at all.
    threading.Thread(
        target=_send_inquiry_notification_async,
        args=(
            current_app._get_current_object(),
            inquiry_id,
            dict(
                package_name=pkg["package_name"], partner_type=partner_type, company_name=company_name,
                contact_person=contact_person, phone=phone, email=email, address=address, message=message,
            ),
        ),
        daemon=True,
    ).start()

    # Realtime: every open HQ Admin tab's "Partner Inquiries" sidebar
    # badge (and the Partners page, since a new partner may have just
    # been created) updates immediately — see main.js's initRealtime(),
    # which already refetches that badge on the "partner_inquiries" scope.
    notify_admin(["partners", "partner_inquiries"])
    notify_bell(
        f"New {partner_type.lower()} inquiry from {company_name} — {pkg['package_name']}",
        room="admin", level="success",
    )

    return jsonify(message="Thanks! Your inquiry has been sent. Our team will reach out shortly."), 201
