"""Colors, status badges and cell formatting shared by the PDF and Excel renderers."""
import datetime

from reportlab.lib import colors



INK = colors.HexColor("#17140D")


INK_FAINT = colors.HexColor("#5B5445")


ACCENT = colors.HexColor("#C9A227")


ACCENT_INK = colors.HexColor("#8A6D1F")


ACCENT_SOFT = colors.HexColor("#FBF1D6")


BORDER = colors.HexColor("#E9E0C9")


ROW_ALT = colors.HexColor("#F5F0E1")


# Hex pairs (background, text) for each badge "style" — a direct port
# of the badge-* classes in style.css (light-theme values), so a
# Status/Type/Variant column in a generated report is colored exactly
# like the matching badge the person already sees on screen. Product
# variants use their own gold/ink scale rather than reusing status colors.
BADGE_STYLES = {
    "pending":   ("#FBF0D9", "#C9820B"),  # --warning-soft / --warning
    "transit":   ("#FBF1D6", "#8A6D1F"),  # --accent-soft / --accent-ink
    "fulfilled": ("#E1F5EC", "#17975E"),  # --success-soft / --success
    "rejected":  ("#FCE7EA", "#B31E30"),  # --red-soft / --red-ink
    "active":    ("#E1F5EC", "#17975E"),
    "inactive":  ("#FAF7EF", "#948C76"),  # --bg / --ink-faint
    "male":      ("#FBF1D6", "#8A6D1F"),  # --accent-soft / --accent-ink
    "female":    ("#F5F0E1", "#17140D"),  # --surface-2 / --ink
    "neutral":   ("#FAF7EF", "#5B5445"),  # --bg / --ink-soft
}


# Maps a column's *semantic kind* (not its literal value) to the
# badge-style key for each value it can hold. Mirrors the macro logic
# in _macros.html (status_badge / movement_badge / sale_type_badge /
# payment_method_badge / variant_badge) line for line, so report
# coloring can never drift out of sync with what the web UI shows for
# that same value.
BADGE_KIND_MAPS = {
    "status": {"Pending": "pending", "In Transit": "transit", "Partially Fulfilled": "transit", "Fulfilled": "fulfilled", "Rejected": "rejected"},
    "movement_type": {
        "PRODUCTION": "fulfilled", "DISPATCH": "transit", "RECEIPT": "fulfilled",
        "SALE": "neutral", "REFILL": "female", "FREEBIE": "pending",
        "ADJUSTMENT": "pending", "DAMAGE": "rejected",
        "PACKAGE_ORDER": "transit", "PACKAGE_RETURN": "inactive", "SALE_VOID": "rejected",
    },
    "sale_type": {"Sale": "fulfilled", "Refill": "transit", "Freebie": "pending"},
    "payment_method": {"Cash": "active", "Credit": "pending"},
    "variant": {"Male": "male", "Female": "female"},
    "active_status": {"Active": "active", "Discontinued": "inactive", "Deactivated": "inactive"},
    "role": {"Admin": "neutral", "Branch": "transit"},
    # Mirrors partners.html / partner_inquiries.html's inline badge:
    # badge-transit for Distributor, badge-neutral for Reseller.
    "partner_type": {"Distributor": "transit", "Reseller": "neutral"},
    # Mirrors packages.html's "For" column.
    "package_scope": {"Both": "pending", "Distributor": "transit", "Reseller": "neutral"},
    # Mirrors packages.html's Status column (Active/Retired badge).
    "package_status": {"Active": "active", "Retired": "inactive"},
    # Mirrors _macros.html's inquiry_status_badge line for line.
    "inquiry_status": {
        "New": "pending", "Contacted": "transit", "Follow-up": "neutral",
        "On Hold": "inactive", "Closed": "fulfilled", "Declined": "rejected",
    },
}


def badge_colors(kind, value):
    """Return (bg_hex, text_hex) for a badge-kind column's value, or
    None to fall back to plain text (unrecognized kind/value)."""
    style_key = BADGE_KIND_MAPS.get(kind, {}).get(value)
    return BADGE_STYLES.get(style_key) if style_key else None


def fmt_cell(value, ctype):
    if value is None:
        return "—"
    if ctype == "money":
        return f"₱{float(value):,.2f}"
    if ctype == "int":
        return f"{int(value):,}"
    if ctype == "num":
        # A fractional quantity (raw_materials.package_qty, cogs_logs.
        # qty_produced, unit_formula_items.qty_per_unit — all DECIMAL
        # columns, unlike the plain-INT qty columns "int" above covers)
        # — shown to 3 decimal places (matches their schema precision)
        # rather than forced through int(), which would silently
        # truncate e.g. 2.5 grams down to 2.
        return f"{float(value):,.3f}"
    if ctype == "datetime":
        return value.strftime("%b %d, %Y %I:%M %p") if isinstance(value, (datetime.date, datetime.datetime)) else str(value)
    return str(value)
