"""Small shared helpers used across route blueprints.

Centralizing form-value parsing here means every route gets the same
"bad input -> friendly flash message" behavior instead of a raw
ValueError bubbling up into an unhandled 500.
"""
import decimal
import hashlib
import hmac
import re
import secrets
import string
from datetime import date, datetime

from flask import current_app, request, session

# Packaging sizes a product SKU can be. Kept here (rather than duplicated
# in admin.py/branch.py/reports.py) so the one allow-list is what every
# form validates against, what every <select> is built from, and what
# reports.py filters against.
PRODUCT_UNITS = ("85ML", "50ML", "1L", "100ML", "10ML", "3ML Tester")

# Short, filename/SKU-safe suffix for each unit (no spaces), used only to
# build the stored SKU from an admin-entered base product code — see
# build_sku() below. Keys must exactly match PRODUCT_UNITS.
_PRODUCT_UNIT_SUFFIXES = {
    "85ML": "85ML",
    "50ML": "50ML",
    "1L": "1L",
    "100ML": "100ML",
    "10ML": "10ML",
    "3ML Tester": "3MLT",
}

# A base product code: letters, numbers, and hyphens only, must start
# with a letter or number, capped well under the products.sku VARCHAR(50)
# column (the longest unit suffix adds 5 chars incl. the separator).
_BASE_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-]{0,29}$")

# A sale is a normal transaction (customer takes a bottle); a refill is a
# customer bringing back their own bottle and only paying for product.
# Both consume stock; both carry their own manually-entered price.
SALE_TYPES = ("Sale", "Refill")

# Cash is a normal register transaction. Credit is anyone — an employee
# taking product against their own pay, or a customer buying on store
# credit ("utang") — taking product now without paying cash at the
# register; buyer_name on the sales table records who it's owed by.
PAYMENT_METHODS = ("Cash", "Credit")

MATERIAL_UNITS = ("Gram", "Milliliter", "Liter", "Gallon", "Piece")

# A Distributor buys in bulk to resell further down a chain of their own;
# a Reseller buys in bulk to sell directly to end customers. Both are
# bulk buyers outside the retail branch network — see the `partners`
# table in schema.sql.
PARTNER_TYPES = ("Distributor", "Reseller")

# Deliberately simple/permissive — this only guards against obvious
# typos and junk input on the public partner-portal inquiry form (see
# routes/portal.py), not full RFC 5322 / ITU E.164 correctness. Being
# too strict here would reject real addresses/numbers a distributor or
# reseller actually uses; the real verification happens when HQ calls
# or emails them back.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_PHONE_RE = re.compile(r"^[0-9+\-().\s]{7,30}$")


class ValidationError(ValueError):
    """Raised by the parse_* helpers on bad user input.

    Callers should catch this specifically, flash str(err), and
    redirect/re-render — messages are always hand-written and safe to
    show directly to the user.
    """


def parse_positive_int(raw, field_label="Value"):
    """Parse a form value as a strictly positive integer (>= 1)."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{field_label} must be a whole number.")
    if value <= 0:
        raise ValidationError(f"{field_label} must be greater than zero.")
    return value


def parse_non_negative_int(raw, field_label="Value"):
    """Parse a form value as an integer >= 0."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{field_label} must be a whole number.")
    if value < 0:
        raise ValidationError(f"{field_label} can't be negative.")
    return value


def parse_non_negative_decimal(raw, field_label="Value"):
    """Parse a form value as a non-negative price/decimal.

    Returns a decimal.Decimal, not a float — money shouldn't round-trip
    through binary floating point on its way from a form field into a
    DECIMAL database column (a plain `float(str(raw))` can turn e.g.
    "19.99" into something that no longer prints back as exactly
    "19.99" once summed with other values).

    Explicitly rejects NaN/Infinity via is_finite() rather than relying
    on the < 0 check below to catch them: neither NaN nor Infinity is
    ever "< 0", so a value of "nan" or "inf" would otherwise sail
    straight through as a seemingly valid, non-negative price — and
    unlike float, comparing a Decimal NaN with < raises
    decimal.InvalidOperation, which would surface as a raw 500 instead
    of the friendly ValidationError every other bad input gets here.
    """
    try:
        value = decimal.Decimal(str(raw).strip())
    except (TypeError, ValueError, decimal.InvalidOperation):
        raise ValidationError(f"{field_label} must be a valid number.")
    if not value.is_finite():
        raise ValidationError(f"{field_label} must be a valid number.")
    if value < 0:
        raise ValidationError(f"{field_label} can't be negative.")
    return value


def parse_positive_decimal(raw, field_label="Value"):
    """Parse a form value as a strictly positive (> 0) decimal.Decimal.

    See parse_non_negative_decimal() above for why this returns Decimal
    and explicitly rejects NaN/Infinity rather than just checking <= 0.
    """
    try:
        value = decimal.Decimal(str(raw).strip())
    except (TypeError, ValueError, decimal.InvalidOperation):
        raise ValidationError(f"{field_label} must be a valid number.")
    if not value.is_finite():
        raise ValidationError(f"{field_label} must be a valid number.")
    if value <= 0:
        raise ValidationError(f"{field_label} must be greater than zero.")
    return value


def parse_base_code(raw, field_label="Product code"):
    """Validate the admin-entered base product code (e.g. 'A1').

    This is NOT the final SKU — see build_sku() below, which combines
    this with a unit to make the real primary-key SKU stored in
    `products`. Kept separate so the same base code can be reused for
    multiple sizes of the same product without the admin having to
    invent a unique code by hand for each one.
    """
    code = str(raw or "").strip().upper()
    if not code:
        raise ValidationError(f"{field_label} is required.")
    if not _BASE_CODE_RE.match(code):
        raise ValidationError(
            f"{field_label} can only contain letters, numbers, and hyphens (max 30 characters)."
        )
    return code


def build_sku(base_code, unit):
    """Combine a validated base product code with a unit into the actual
    SKU stored in `products.sku`. E.g. build_sku('A1', '85ML') -> 'A1-85ML'.

    Two sizes of the same product share the same base_code but produce
    different SKUs (different suffixes), so they land as two separate
    rows — each with its own price and stock — without the admin ever
    having to type 'A1-85ML' / 'A1-15ML' by hand.
    """
    suffix = _PRODUCT_UNIT_SUFFIXES.get(unit)
    if suffix is None:
        raise ValidationError("Select a valid unit.")
    return f"{base_code}-{suffix}"


def parse_optional_id(raw, field_label="Value"):
    """Parse an optional foreign-key id from a form <select> (e.g. a
    'No supplier' blank option). Returns None for an empty value, or a
    positive int. Unlike parse_positive_int, blank is valid here — the
    field itself is optional; callers are still responsible for
    confirming the id actually exists before using it.
    """
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_label} is invalid.")
    if value <= 0:
        raise ValidationError(f"{field_label} is invalid.")
    return value


def parse_required_text(raw, field_label="Value", max_length=None):
    """Parse a plain required text field — strips whitespace, rejects
    blank, optionally caps length. Used for the partner-portal inquiry
    form fields (contact person, phone, email, company/name) now that
    they're all required rather than optional — see routes/portal.py.
    """
    value = str(raw or "").strip()
    if not value:
        raise ValidationError(f"{field_label} is required.")
    if max_length and len(value) > max_length:
        raise ValidationError(
            f"{field_label} must be under {max_length} characters.")
    return value


def parse_optional_text(raw, field_label="Value", max_length=None):
    """Like parse_required_text, but blank is valid — returns None
    instead of raising. Used for optional free-text fields such as
    Record Sale's customer name/address, where most cash sales don't
    bother naming a customer at all.
    """
    value = str(raw or "").strip()
    if not value:
        return None
    if max_length and len(value) > max_length:
        raise ValidationError(
            f"{field_label} must be under {max_length} characters.")
    return value


def parse_email(raw, field_label="Email"):
    value = str(raw or "").strip()
    if not value:
        raise ValidationError(f"{field_label} is required.")
    if len(value) > 120 or not _EMAIL_RE.match(value):
        raise ValidationError(
            f"{field_label} doesn't look like a valid email address.")
    return value


def parse_phone(raw, field_label="Phone number"):
    value = str(raw or "").strip()
    if not value:
        raise ValidationError(f"{field_label} is required.")
    digit_count = sum(ch.isdigit() for ch in value)
    if len(value) > 30 or digit_count < 7 or not _PHONE_RE.match(value):
        raise ValidationError(
            f"{field_label} doesn't look like a valid phone number (digits, spaces, +, -, and () only)."
        )
    return value


def parse_past_date(raw, field_label="Date"):
    """Parse an optional 'YYYY-MM-DD' date from a <input type="date">
    field — used to backdate a record (Record Sale, Add Material, Add
    Supplier) to when it actually happened instead of when it was typed
    in. Blank defaults to today, matching the old behavior of just
    stamping "now". Combined with the current time-of-day rather than
    midnight, so multiple records backdated to the same past date still
    sort by entry order. Future dates are rejected — this is only ever
    for logging something that already happened.
    """
    raw = str(raw or "").strip()
    today = date.today()
    if not raw:
        picked = today
    else:
        try:
            picked = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(f"{field_label} isn't a valid date.")
        if picked > today:
            raise ValidationError(f"{field_label} can't be in the future.")
    return datetime.combine(picked, datetime.now().time())


# ---------------------------------------------------------------------------
# Product avatar fallback.
#
# Every product thumbnail (catalog, inventory, low-stock report, the public
# package gallery) falls back to a generated initials avatar when a product
# has no uploaded photo, instead of a broken/empty-image icon. The color is
# deterministic per product name — hashed the same way here and in the
# admin "edit product" modal's live JS preview (see templates/admin/
# products.html) — so a given product wears the same color identity
# everywhere its thumbnail shows up, without ever needing to store one.
# ---------------------------------------------------------------------------

# Curated, muted tones — deliberately outside the gold/amber hue range so a
# generated avatar is never mistaken for --brand-gold/--accent, which mean
# "interactive" elsewhere in the UI — paired with one shared light
# foreground that reads clearly against all of them.
PRODUCT_AVATAR_PALETTE = (
    "#B5654A",  # terracotta
    "#9C4F5E",  # rosewood
    "#6E4C7D",  # plum
    "#4C5B8A",  # indigo
    "#3F6E8E",  # slate blue
    "#3D8078",  # teal
    "#4C7A54",  # forest
    "#6B7A3E",  # olive
    "#7A5A3E",  # umber
    "#8A5A73",  # mauve
    "#45607A",  # denim
    "#82405A",  # berry
)
PRODUCT_AVATAR_FG = "#FBF8EF"


def _stable_hash(text):
    """Deterministic string hash, mirrored exactly by the JS helper in
    templates/admin/products.html. A plain djb2-style rolling hash,
    folded with a modulus at every step (rather than left to grow and
    modded once at the end) so it never needs bignum/int64 handling to
    match between Python (arbitrary-precision ints) and JS (float64,
    safe only up to 2**53) for names of any length.
    """
    h = 0
    for ch in text:
        h = (h * 31 + ord(ch)) % 1_000_000_007
    return h


def product_avatar(name):
    """Build the fallback avatar for a product with no photo: initials
    plus a color picked deterministically from its name, so the same
    product always gets the same color wherever its thumbnail appears
    (catalog, inventory, low-stock, package gallery, ...) — its own
    color identity, not a random one re-rolled per page.

    Returns {"initials", "bg", "fg"}, ready to drop into the
    product_thumb() Jinja macro's inline style.
    """
    clean = re.sub(r"\s+", " ", str(name or "").strip())
    words = clean.split(" ") if clean else []
    if len(words) >= 2:
        initials = (words[0][0] + words[1][0]).upper()
    elif words:
        initials = words[0][:2].upper()
    else:
        initials = "?"
    color = PRODUCT_AVATAR_PALETTE[_stable_hash(
        clean.lower()) % len(PRODUCT_AVATAR_PALETTE)]
    return {"initials": initials, "bg": color, "fg": PRODUCT_AVATAR_FG}


def generate_temp_password(length=12):
    """Generate a random temporary password for admin-triggered resets."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------------------------------------------------------------------------
# Single-use form submission tokens.
#
# Guards a "create a record" form (Record Sale, Request Stock, ...) against
# a double-submit — a double-click before the page redirects, a browser
# back-button resubmit, or a client retrying a request whose response was
# lost — turning into two real writes (two sale rows quietly double-
# charging a customer and double-decrementing stock, two delivery
# requests, etc). Row-locking (see db.transaction()) already stops two
# *concurrent* requests from corrupting shared state, but does nothing
# about one browser legitimately sending the same "record this sale" POST
# twice in a row — each one is a perfectly valid write on its own.
#
# Usage: call issue_form_token(key) every time the form is rendered (GET)
# and put the result in a hidden field named "form_token"; call
# consume_form_token(key) as the very first thing the POST handler does,
# before any other validation, and stop (flash + redirect back to the GET
# route, which issues a fresh token) if it returns False.
# ---------------------------------------------------------------------------
def issue_form_token(key):
    """Generate a fresh single-use token for the form named `key`, stash
    it in the session, and return it to embed as a hidden field.
    """
    token = secrets.token_urlsafe(16)
    session[f"_form_token:{key}"] = token
    return token


def consume_form_token(key):
    """Check the submitted form's token against the one issue_form_token()
    stashed in the session, and remove it from the session either way —
    so a given token can only ever be accepted once, whether it matched
    or not. Every route calling this must re-render the form (and
    therefore call issue_form_token() again) on every exit path, success
    or failure, so the next attempt always carries a fresh token.
    """
    expected = session.pop(f"_form_token:{key}", None)
    submitted = request.form.get("form_token", "")
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)


# ---------------------------------------------------------------------------
# Sale receipt verification codes.
#
# The QR code printed on every sale receipt (see receipts.py) encodes one
# of these — never the sale's actual data. It's a short, human-typeable
# string of the shape HAS-<sale_id>-<signature>, where the signature is
# an HMAC-SHA256 of the sale_id keyed on the app's SECRET_KEY, truncated
# to 10 hex characters. That makes the code:
#   - Stateless to verify — no separate "receipt codes" table or column
#     to keep in sync; parse_receipt_code() just recomputes the expected
#     signature and compares. If it doesn't match, the code is rejected,
#     whether that's from a garbled scan, a QR from something else
#     entirely, or someone guessing at sale_ids in sequence.
#   - Safe to trust once verified — since only someone with SECRET_KEY
#     (this app) could have produced a signature that checks out, a
#     valid code couldn't have been forged by a customer or a partner
#     tampering with a printed slip.
#   - Still readable/typeable by hand as a fallback if a camera or photo
#     upload isn't available (see routes/scan.py's manual-entry path).
#
# Deliberately NOT itsdangerous's URLSafeSerializer here even though
# that's already a dependency (via Flask-WTF) and would do something
# similar: its tokens are base64, longer, and carry a dot-separated
# payload+signature shape that's harder to read off a slip or type by
# hand than HAS-000128-CEFCE5776C. A plain HMAC gives full control over
# that shape for the same security property (can't be forged without
# SECRET_KEY) at the cost of one extra small function.
# ---------------------------------------------------------------------------
_RECEIPT_CODE_PREFIX = "HAS"
_RECEIPT_CODE_SALT = b"sale-receipt-v1"  # bump if the code format ever changes


def make_receipt_code(sale_id):
    """Build the signed verification code for a given sale_id. Always
    returns the same code for the same sale_id (and the same
    SECRET_KEY) — this never touches the database or generates anything
    random, so it's safe to call as many times as needed (once when the
    receipt PDF is built, again later whenever that receipt is
    verified) without anything to keep in sync.
    """
    secret = current_app.config["SECRET_KEY"].encode("utf-8")
    signature = hmac.new(
        secret, _RECEIPT_CODE_SALT +
        str(sale_id).encode("utf-8"), hashlib.sha256
    ).hexdigest()[:10].upper()
    return f"{_RECEIPT_CODE_PREFIX}-{sale_id:06d}-{signature}"


def parse_receipt_code(raw):
    """Return the sale_id a receipt code points to if it's validly
    signed, or None otherwise. Never raises — garbled input, a QR from
    an unrelated app, or a hand-typed guess all just fail to verify
    rather than blowing up the caller.
    """
    if not raw:
        return None
    raw = raw.strip().upper()
    parts = raw.split("-")
    if len(parts) != 3 or parts[0] != _RECEIPT_CODE_PREFIX:
        return None
    try:
        sale_id = int(parts[1])
    except ValueError:
        return None
    if sale_id <= 0:
        return None
    expected = make_receipt_code(sale_id)
    if not hmac.compare_digest(expected, raw):
        return None
    return sale_id
