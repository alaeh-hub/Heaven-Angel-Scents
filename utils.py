"""Small shared helpers used across route blueprints.

Centralizing form-value parsing here means every route gets the same
"bad input -> friendly flash message" behavior instead of a raw
ValueError bubbling up into an unhandled 500.
"""
import re
import secrets
import string

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

# Cash is a normal register transaction. Salary Deduction is an employee
# taking product for themselves where the cost comes out of their pay
# instead of the register — see buyer_user_id on the sales table.
PAYMENT_METHODS = ("Cash", "Salary Deduction")

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
    """Parse a form value as a non-negative price/decimal."""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{field_label} must be a valid number.")
    if value < 0:
        raise ValidationError(f"{field_label} can't be negative.")
    return value


def parse_positive_decimal(raw, field_label="Value"):
    """Parse a form value as a strictly positive (> 0) decimal."""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
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
        raise ValidationError(f"{field_label} must be under {max_length} characters.")
    return value


def parse_email(raw, field_label="Email"):
    value = str(raw or "").strip()
    if not value:
        raise ValidationError(f"{field_label} is required.")
    if len(value) > 120 or not _EMAIL_RE.match(value):
        raise ValidationError(f"{field_label} doesn't look like a valid email address.")
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


def generate_temp_password(length=12):
    """Generate a random temporary password for admin-triggered resets."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
