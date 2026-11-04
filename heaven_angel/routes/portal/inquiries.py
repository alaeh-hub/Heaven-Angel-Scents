import contextlib
import decimal
import hashlib
import threading

from flask import current_app, jsonify, request

from heaven_angel.db import execute, get_db, query, transaction
from heaven_angel.services.finance import package_value
from heaven_angel.extensions import limiter
from heaven_angel.services.mailer import send_partner_inquiry_email
from heaven_angel.sockets import notify_admin, notify_bell
from heaven_angel.utils import (
    PARTNER_TYPES, ValidationError, parse_email, parse_optional_text, parse_phone,
    parse_required_text,
)

from heaven_angel.routes.portal._common import _verify_slug, bp


class _PartnerLockUnavailable(Exception):
    """Raised when an identity lock cannot be acquired before its timeout."""


def _partner_lock_name(kind, identity):
    normalized = (identity or "").strip().casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    # MySQL limits named locks to 64 bytes; this prefix is 10 bytes.
    return f"partner:{kind}:{digest[:54]}"


@contextlib.contextmanager
def _identity_locks(email, phone, package_id):
    """Hold MySQL named locks (GET_LOCK/RELEASE_LOCK) for everything that
    must not race while one inquiry is saved: the submitter's email and
    phone (so two near-simultaneous inquiries from the same brand-new
    person don't each create a partner row, a check-then-act race) and
    their email + package (so a double-tap can't save the inquiry twice).

    The locks are held for the whole block, which includes the commit:
    releasing them any earlier would let a second request run its
    SELECT before the first one's rows are visible. Taken in a stable
    order so two requests with overlapping identities can't deadlock.
    """
    lock_keys = sorted({
        _partner_lock_name(kind, value)
        for kind, value in (
            ("e", email),
            ("p", phone),
            ("d", f"{email}|{package_id or ''}"),
        )
        if value
    })
    acquired = []
    try:
        for lock_key in lock_keys:
            row = query("SELECT GET_LOCK(%s, 5) AS ok", (lock_key,), fetchone=True)
            if not row or not row["ok"]:
                raise _PartnerLockUnavailable(
                    "Couldn't safely match this inquiry to a partner yet.")
            acquired.append(lock_key)
        yield
    finally:
        for lock_key in reversed(acquired):
            query("SELECT RELEASE_LOCK(%s)", (lock_key,), fetchone=True)


def _match_or_create_partner(partner_type, company_name, contact_person, phone, email, address,
                             source="package inquiry"):
    """Match an inquiry to an existing partner by email, then phone, so
    the same distributor/reseller inquiring more than once doesn't pile
    up duplicate partner rows. No match -> create a new partner from
    what they entered, same shape as admin.py's "Add a partner" form.

    On a match, the existing partner's name/type/contact/address are
    left ALONE: the first submission is what "wins" the partner record
    on the Partners page, permanently. Every inquiry's own submitted
    details (which may differ) still live forever, unedited, in
    partner_inquiries; see admin.partner_detail for that history. Only
    last_inquiry_at/inquiry_count on the matched partner move on every
    inquiry, since those are just a rollup of that same history (see
    schema.sql's partners table comment).

    Writes with commit=False: the caller runs this inside the same
    transaction as the inquiry insert (see _record_inquiry()), so a
    failed insert can never leave behind a partner whose inquiry_count
    was bumped for an inquiry that doesn't exist. The caller also holds
    _identity_locks() around it.

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
            (partner["partner_id"],), commit=False,
        )
        return partner["partner_id"]

    partner_id, _ = execute(
        """INSERT INTO partners
               (partner_type, partner_name, contact_person, phone, email, address, notes,
                last_inquiry_at, inquiry_count)
           VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, 1)""",
        (partner_type, company_name, contact_person, phone, email, address,
         f"Added automatically from a partner portal {source}."),
        commit=False,
    )
    return partner_id


def active_package_or_none(package_id):
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


CONTACT_METHODS = ("Call", "SMS", "Viber", "Email")


GENERAL_INQUIRY_LABEL = "General inquiry"


# The inquiry endpoints are the only writes (and the only endpoints that
# fan out to email + a DB row per hit) anywhere in the unauthenticated
# portal blueprint — there's no login to throttle abuse through the way
# auth.login() does ("10 per minute"), so they get their own limit
# instead. Two windows, same style as AI_CHAT_RATE_LIMIT in config.py:
# tight enough to blunt a scripted flood of fake inquiries, loose enough
# that a real distributor fumbling the form a few times in a row (or
# several people behind the same office/shared IP) never gets blocked.
# Shared between the package and general endpoints, so alternating
# between the two doesn't double what one visitor can send.
_inquiry_limit = limiter.shared_limit("5 per minute;30 per day", scope="portal-inquire")


def inquiry_reference(inquiry_id):
    """The short reference shown to the visitor after sending ("HA-00042"),
    so they can quote it when they follow up; it's just the inquiry_id."""
    return f"HA-{inquiry_id:05d}"


def _parse_inquiry(data):
    """Validate the inquiry form's JSON body (shared by the package and
    general inquiry endpoints). Returns a dict of clean fields, or raises
    ValidationError with a message to show the visitor.

    Every field is required except address, message and
    preferred_contact. A Distributor fills this in on behalf of a
    business, a Reseller on behalf of themselves (the form relabels
    "Business / company name" to "Your full name" for a Reseller;
    resellers don't necessarily have a registered company). Either way
    the underlying column is still company_name; only the label changes.
    """
    partner_type = str(data.get("partner_type") or "").strip()
    message = str(data.get("message") or "").strip() or None
    preferred_contact = str(data.get("preferred_contact") or "").strip() or None

    if partner_type not in PARTNER_TYPES:
        raise ValidationError("Select whether you're a distributor or a reseller.")

    name_field_label = "Business / company name" if partner_type == "Distributor" else "Your full name"
    fields = dict(
        partner_type=partner_type,
        company_name=parse_required_text(data.get("company_name"), name_field_label, max_length=150),
        contact_person=parse_required_text(data.get("contact_person"), "Contact person", max_length=100),
        phone=parse_phone(data.get("phone")),
        email=parse_email(data.get("email")),
        address=parse_optional_text(data.get("address"), "Address", max_length=255),
        message=message,
        preferred_contact=preferred_contact,
    )
    if len(message or "") > 500:
        raise ValidationError("Message is too long. Please keep it under 500 characters.")
    if preferred_contact and preferred_contact not in CONTACT_METHODS:
        raise ValidationError("Choose how you'd like us to reach you: call, SMS, Viber, or email.")
    return fields


DUPLICATE_WINDOW_SECONDS = 120


def _recent_duplicate(fields, package_id):
    """An inquiry from the same email, about the same package (or both
    general), with the same message, saved in the last couple of minutes:
    a double-tap, a retry after a slow response, or two tabs. Must run
    under _identity_locks() so a racing twin is either already visible
    here or still waiting for the lock."""
    return query(
        """SELECT inquiry_id FROM partner_inquiries
           WHERE email = %s AND package_id <=> %s AND message <=> %s
             AND created_at >= (CURRENT_TIMESTAMP - INTERVAL %s SECOND)
           ORDER BY inquiry_id DESC LIMIT 1""",
        (fields["email"], package_id, fields["message"], DUPLICATE_WINDOW_SECONDS),
        fetchone=True,
    )


def _inquiry_response(inquiry_id):
    """The visitor's 201 for a saved inquiry (or for the already-saved
    twin of a duplicate submission, so a retry looks like success)."""
    reply_time = current_app.config.get("PORTAL_REPLY_TIME", "").strip()
    follow_up = f"Our team will reach out {reply_time}." if reply_time else "Our team will reach out shortly."
    return jsonify(
        message=f"Thanks! Your inquiry has been sent. {follow_up}",
        reference=inquiry_reference(inquiry_id),
    ), 201


def _honeypot_tripped(data):
    """The form carries a hidden "website" field that people never see or
    fill. Bots that fill every input trip it; the server answers as if it
    worked (so the bot learns nothing) and saves nothing."""
    return bool(str(data.get("website") or "").strip())


def _fake_success():
    return jsonify(message="Thanks! Your inquiry has been sent.", reference=None), 201


def _record_inquiry(fields, *, package_id, package_name, package_snapshot, order_amount, items=()):
    """Steps (a)-(d) from the module docstring, shared by both inquiry
    endpoints: save the inquiry, link it to a partner, email HQ off the
    request thread, and ping every open admin tab. Returns the visitor's
    201 response, carrying the inquiry's reference."""
    try:
        with _identity_locks(fields["email"], fields["phone"], package_id):
            # End the snapshot this request's earlier SELECTs opened
            # (REPEATABLE READ): after taking the locks, the duplicate
            # and partner lookups below must see whatever a racing
            # request has just committed.
            get_db().commit()

            duplicate = _recent_duplicate(fields, package_id)
            if duplicate:
                return _inquiry_response(duplicate["inquiry_id"])

            # The partner link, the inquiry and its package-contents
            # snapshot (partner_inquiry_items, what Fulfill later takes
            # out of HQ stock) are saved together: an inquiry can never
            # exist without its items, and a failed insert can't leave a
            # partner behind with an inquiry_count it never earned.
            with transaction() as conn:
                partner_id = _match_or_create_partner(
                    fields["partner_type"], fields["company_name"], fields["contact_person"],
                    fields["phone"], fields["email"], fields["address"],
                    source="package inquiry" if package_id else "general inquiry",
                )
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO partner_inquiries
                           (package_id, partner_id, partner_type, company_name, contact_person,
                            phone, email, address, message, preferred_contact,
                            package_name_snapshot, order_amount)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (package_id, partner_id, fields["partner_type"], fields["company_name"],
                     fields["contact_person"], fields["phone"], fields["email"], fields["address"],
                     fields["message"], fields["preferred_contact"], package_snapshot, order_amount),
                )
                inquiry_id = cur.lastrowid
                if items:
                    cur.executemany(
                        """INSERT INTO partner_inquiry_items (inquiry_id, sku, item_name, unit, qty, unit_price)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        [(inquiry_id, i["sku"], i["item_name"], i["unit"], i["qty"], i["price"]) for i in items],
                    )
                cur.close()
    except _PartnerLockUnavailable:
        return jsonify(
            error="Your inquiry couldn't be matched safely right now. Please try again shortly."
        ), 503

    # Off the request thread — see _send_inquiry_notification_async()'s
    # docstring above. The inquiry row is already committed at this
    # point, so the visitor's own response below no longer waits on
    # mailer.py's smtplib call (up to a 10s timeout) at all.
    threading.Thread(
        target=_send_inquiry_notification_async,
        args=(
            current_app._get_current_object(),
            inquiry_id,
            dict(package_name=package_name, reference=inquiry_reference(inquiry_id),
                 order_amount=order_amount, **fields),
        ),
        daemon=True,
    ).start()

    # Realtime: every open HQ Admin tab's "Partner Inquiries" sidebar
    # badge (and the Partners page, since a new partner may have just
    # been created) updates immediately — see main.js's initRealtime(),
    # which already refetches that badge on the "partner_inquiries" scope.
    notify_admin(["partners", "partner_inquiries"])
    notify_bell(
        f"New {fields['partner_type'].lower()} inquiry from {fields['company_name']} — {package_name}",
        room="admin", level="success",
    )

    return _inquiry_response(inquiry_id)


@bp.route("/<slug>/api/packages/<int:package_id>/inquire", methods=["POST"])
@_inquiry_limit
def inquire(slug, package_id):
    """Takes a JSON body with the inquiry form's fields. Responds 201
    {"message", "reference"} on success, or 4xx {"error"} with a message
    to show the visitor."""
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
    if _honeypot_tripped(data):
        return _fake_success()
    try:
        fields = _parse_inquiry(data)
    except ValidationError as err:
        return jsonify(error=str(err)), 400

    # order_amount snapshots what this partner would actually pay for the
    # package right now — same reference-total-then-discount math as
    # api_package_detail() above, computed fresh here rather than
    # trusting anything the browser sends. Frozen permanently at insert
    # time, same "snapshot, don't recompute later" philosophy as
    # package_name_snapshot itself — see schema.sql's note on
    # partner_inquiries.order_amount for why this only counts once an
    # admin marks the inquiry Closed.
    # The item rows themselves are also snapshotted onto the inquiry
    # (partner_inquiry_items) — they're what Fulfill takes out of HQ stock.
    items = query(
        """SELECT pi.sku, pi.qty, p.item_name, p.unit, p.price
           FROM package_items pi JOIN products p ON p.sku = pi.sku
           WHERE pi.package_id = %s ORDER BY p.item_name""",
        (package_id,),
    )
    reference_total = sum((i["qty"] * i["price"] for i in items), decimal.Decimal("0"))
    _, order_amount = package_value(pkg["discount_percent"], reference_total)

    return _record_inquiry(
        fields,
        package_id=package_id,
        package_name=pkg["package_name"],
        package_snapshot=f"{pkg['package_name']} ({pkg['discount_percent']}% off)",
        order_amount=order_amount,
        items=items,
    )


@bp.route("/<slug>/api/inquire", methods=["POST"])
@_inquiry_limit
def inquire_general(slug):
    """A question or partnership request not tied to one package (the
    "Send an inquiry" buttons around the landing page and catalog). Same
    fields, validation and follow-up as inquire(); saved with no package
    and no order amount."""
    _verify_slug(slug)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Something went wrong sending your inquiry. Please try again."), 400
    if _honeypot_tripped(data):
        return _fake_success()
    try:
        fields = _parse_inquiry(data)
    except ValidationError as err:
        return jsonify(error=str(err)), 400

    return _record_inquiry(
        fields,
        package_id=None,
        package_name=GENERAL_INQUIRY_LABEL,
        package_snapshot=GENERAL_INQUIRY_LABEL,
        order_amount=None,
    )


# ------------------------------------------------- unsent-email retry sweep
