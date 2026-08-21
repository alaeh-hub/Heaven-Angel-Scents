"""Small shared helpers used across route blueprints.

Centralizing form-value parsing here means every route gets the same
"bad input -> friendly flash message" behavior instead of a raw
ValueError bubbling up into an unhandled 500.
"""
import secrets
import string


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


def generate_temp_password(length=12):
    """Generate a random temporary password for admin-triggered resets."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
