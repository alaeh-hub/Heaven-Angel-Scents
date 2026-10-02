"""On-demand reports (PDF + Excel) for the admin and branch Reports pages.

Design intent:
- One reusable pattern instead of a bespoke page per report: pick a report
  TYPE, pick a time window (Recent N / Date range / All time), pick that
  type's own extra filters, then download as PDF or Excel. Admin.py and
  branch.py both call get_report() + render_report_pdf()/render_report_excel()
  — the branch side just always passes branch_scope=<their own branch_id>,
  which quietly removes the Branch column and ignores any branch_id filter
  the querystring might contain, so a branch user can never pull another
  branch's report by editing the URL.
- Every report is capped at MAX_ROWS rows even on "All time" — see the
  `truncated` flag in the returned dict, which both templates surface to
  the user rather than silently dropping rows.
- Numbers/dates are kept as native Python types (Decimal, date, datetime)
  in the row dicts for as long as possible, and only formatted to strings
  right before they're placed on the page — Excel gets real numbers/dates
  with number formats, PDF gets formatted strings.
"""
import datetime
import io

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from brand_assets import NumberedCanvas, logo_drawing, register_fonts
from db import query
from utils import BOTTLE_UNITS, PARTNER_TYPES, PAYMENT_METHODS, PRODUCT_UNITS, SALE_TYPES, bottle_size_ml


# Units behind a delivery discrepancy log row (DAMAGE or ADJUSTMENT,
# reference_type='STOCK_REQUEST'). receive_stock() logs these with
# change_qty = 0 (the units never entered branch stock, so nothing is
# deducted), which means the unit count has to come from the delivery
# line itself: damaged_qty for DAMAGE, and whatever was dispatched but
# neither received nor reported damaged for ADJUSTMENT. A request holds
# one line per SKU (request_stock() merges duplicates), so joining on
# (request_id, sku) matches exactly one line. Falls back to
# -change_qty for any row with no matching line.
DISCREPANCY_ITEM_JOIN = (
    "LEFT JOIN stock_request_items sri "
    "ON sri.request_id = sml.reference_id AND sri.sku = sml.sku"
)
# The one "low stock" rule — Low Stock page, dashboards, the Low Stock /
# Branch Stock reports and the AI assistant all use it. Bulk/Refill stock
# (mL) has no reorder level, so it's never "low".
LOW_STOCK_WHERE = "bi.stock_qty <= bi.reorder_level AND p.unit <> 'BULK'"

DISCREPANCY_UNITS_SQL = (
    "(CASE WHEN sri.item_id IS NULL THEN -sml.change_qty "
    "WHEN sml.movement_type = 'DAMAGE' THEN sri.damaged_qty "
    "ELSE GREATEST(COALESCE(sri.dispatched_qty, 0) - COALESCE(sri.received_qty, 0) "
    "- sri.damaged_qty, 0) END)"
)

# Built-in PDF fonts (Helvetica etc.) only cover Latin-1 and have no glyph
# for the ₱ (Philippine peso) sign — it silently renders as a black "tofu"
# box instead of erroring, which is easy to miss until someone opens the
# PDF. IBM Plex Sans does have that glyph, and matches the IBM Plex Mono
# already used for SKU/mono styling elsewhere in the app, so reports and
# the web UI share a type family. See brand_assets.register_fonts() for
# the shared vendored-font registration (also used by receipts.py and
# this module's own NumberedCanvas), so the font files/names only need
# to be right in one place.
register_fonts()

MAX_ROWS = 1000
RECENT_CHOICES = (20, 50, 100, 200)
STATUS_CHOICES = ("Pending", "In Transit", "Fulfilled", "Rejected")
MOVEMENT_TYPE_CHOICES = ("PRODUCTION", "DISPATCH", "RECEIPT",
                         "SALE", "REFILL", "FREEBIE", "ADJUSTMENT", "DAMAGE",
                         "PACKAGE_ORDER", "PACKAGE_RETURN", "SALE_VOID")
VARIANT_CHOICES = ("Male", "Female", "Unisex")
ROLE_CHOICES = ("Admin", "Branch")
UNIT_CHOICES = PRODUCT_UNITS
SALE_TYPE_CHOICES = SALE_TYPES
PAYMENT_METHOD_CHOICES = PAYMENT_METHODS
PARTNER_TYPE_CHOICES = PARTNER_TYPES
# Mirrors packages.partner_scope's ENUM ('Both' plus each PARTNER_TYPES
# value) — kept separate from PARTNER_TYPE_CHOICES since a package can
# also be scoped to "Both", which a partner itself never is.
PACKAGE_SCOPE_CHOICES = ("Both",) + PARTNER_TYPES
# packages.is_active as it reads on screen (Packages page's Status
# column) rather than the raw boolean.
PACKAGE_STATUS_CHOICES = ("Active", "Retired")
# Mirrors partner_inquiries.status's ENUM — same pipeline shown on the
# Partner Inquiries page and _macros.html's inquiry_status_badge.
INQUIRY_STATUS_CHOICES = ("New", "Contacted", "Follow-up",
                          "On Hold", "Closed", "Declined")

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
# like the matching badge the person already sees on screen. Male/
# Female/Unisex is an identity category, not a status, so it gets its
# own gold/ink/muted-ink scale rather than reusing red/blue.
BADGE_STYLES = {
    "pending":   ("#FBF0D9", "#C9820B"),  # --warning-soft / --warning
    "transit":   ("#FBF1D6", "#8A6D1F"),  # --accent-soft / --accent-ink
    "fulfilled": ("#E1F5EC", "#17975E"),  # --success-soft / --success
    "rejected":  ("#FCE7EA", "#B31E30"),  # --red-soft / --red-ink
    "active":    ("#E1F5EC", "#17975E"),
    "inactive":  ("#FAF7EF", "#948C76"),  # --bg / --ink-faint
    "male":      ("#FBF1D6", "#8A6D1F"),  # --accent-soft / --accent-ink
    "female":    ("#F5F0E1", "#17140D"),  # --surface-2 / --ink
    "unisex":    ("#FAF7EF", "#5B5445"),  # --bg / --ink-soft
}

# Maps a column's *semantic kind* (not its literal value) to the
# badge-style key for each value it can hold. Mirrors the macro logic
# in _macros.html (status_badge / movement_badge / sale_type_badge /
# payment_method_badge / variant_badge) line for line, so report
# coloring can never drift out of sync with what the web UI shows for
# that same value.
BADGE_KIND_MAPS = {
    "status": {"Pending": "pending", "In Transit": "transit", "Fulfilled": "fulfilled", "Rejected": "rejected"},
    "movement_type": {
        "PRODUCTION": "fulfilled", "DISPATCH": "transit", "RECEIPT": "fulfilled",
        "SALE": "unisex", "REFILL": "female", "FREEBIE": "pending",
        "ADJUSTMENT": "pending", "DAMAGE": "rejected",
        "PACKAGE_ORDER": "transit", "PACKAGE_RETURN": "inactive", "SALE_VOID": "rejected",
    },
    "sale_type": {"Sale": "fulfilled", "Refill": "transit", "Freebie": "pending"},
    "payment_method": {"Cash": "active", "Credit": "pending"},
    "variant": {"Male": "male", "Female": "female", "Unisex": "unisex"},
    "active_status": {"Active": "active", "Discontinued": "inactive", "Deactivated": "inactive"},
    "role": {"Admin": "unisex", "Branch": "transit"},
    # Mirrors partners.html / partner_inquiries.html's inline badge:
    # badge-transit for Distributor, badge-unisex for Reseller.
    "partner_type": {"Distributor": "transit", "Reseller": "unisex"},
    # Mirrors packages.html's "For" column.
    "package_scope": {"Both": "pending", "Distributor": "transit", "Reseller": "unisex"},
    # Mirrors packages.html's Status column (Active/Retired badge).
    "package_status": {"Active": "active", "Retired": "inactive"},
    # Mirrors _macros.html's inquiry_status_badge line for line.
    "inquiry_status": {
        "New": "pending", "Contacted": "transit", "Follow-up": "unisex",
        "On Hold": "inactive", "Closed": "fulfilled", "Declined": "rejected",
    },
}


def _badge_colors(kind, value):
    """Return (bg_hex, text_hex) for a badge-kind column's value, or
    None to fall back to plain text (unrecognized kind/value)."""
    style_key = BADGE_KIND_MAPS.get(kind, {}).get(value)
    return BADGE_STYLES.get(style_key) if style_key else None


XL_HEADER_FILL = PatternFill("solid", fgColor="2E5AF0")
XL_TITLE_FILL = PatternFill("solid", fgColor="E7ECFE")
XL_BORDER = Border(*(Side(style="thin", color="E5E8EF"),) * 4)
XL_MONEY_FMT = '"₱"#,##0.00'
XL_DATE_FMT = "mmm dd, yyyy hh:mm AM/PM"
# Up to 3 decimals, trailing zeros trimmed by Excel's own "#" placeholders
# (2.500 shows as "2.5", 10.000 shows as "10") — for fractional quantity
# columns (see the "num" column type), which range from whole numbers up
# to schema's DECIMAL(10,3)/DECIMAL(10,4) precision.
XL_NUM_FMT = "#,##0.###"

# ---------------------------------------------------------------- registry
# admin / branch: whether that role can generate this report at all.
# windowed: whether a time-window (Recent/Range/All) control applies.
REPORT_TYPES = {
    "products":        {"label": "Products",        "admin": True, "branch": False, "windowed": False},
    "production_log":  {"label": "Production Log",  "admin": True, "branch": False, "windowed": True},
    # Materials/Suppliers/Formulas/Cost of Goods Log — admin-only (raw
    # materials and cost of goods are HQ-side bookkeeping; branch
    # accounts never see the Materials/Formulas pages either). Materials
    # and Cost of Goods Log are dated logs (windowed); Suppliers and
    # Formulas are catalog-style snapshots, same reasoning as Products/
    # Accounts/Partners/Packages above.
    "materials":       {"label": "Materials",        "admin": True, "branch": False, "windowed": True},
    "suppliers":       {"label": "Suppliers",        "admin": True, "branch": False, "windowed": False},
    "formulas":        {"label": "Formulas (Cost of Goods)", "admin": True, "branch": False, "windowed": False},
    "cogs_logs":       {"label": "Cost of Goods Log", "admin": True, "branch": False, "windowed": True},
    # Bulk Batches — the step between raw materials and bottling (see
    # bulk_batches in schema.sql). Admin-only like the rest of the
    # materials/cost-of-goods bookkeeping above, and a dated log (when
    # each batch was mixed), so windowed.
    "bulk_batches":    {"label": "Bulk Batches",     "admin": True, "branch": False, "windowed": True},
    "branch_stock":    {"label": "Stock (HQ & Branches)", "admin": True, "branch": True,  "windowed": False,
                        "branch_label": "My Inventory"},
    # Low Stock — the Low Stock page as a report: every SKU at or below
    # its reorder level (LOW_STOCK_WHERE), HQ first, most critical first.
    "low_stock":       {"label": "Low Stock",        "admin": True, "branch": True,  "windowed": False},
    "stock_requests":  {"label": "Stock Requests",   "admin": True, "branch": True,  "windowed": True},
    "inventory_log":   {"label": "Inventory Log",    "admin": True, "branch": True,  "windowed": True},
    "sales_history":   {"label": "Sales History",    "admin": True, "branch": True,  "windowed": True},
    "credit_purchases": {"label": "Credit Purchases", "admin": True, "branch": True,
                         "windowed": True, "branch_label": "Credit Purchases"},
    # Voided Sales — the permanent record of sales removed by Void
    # (sale_voids): what it was, who voided it and why.
    "voided_sales":    {"label": "Voided Sales",     "admin": True, "branch": True,  "windowed": True},
    # Customers — the repeat-customer directory (built from
    # sales.customer_name, no table of its own). A per-customer rollup
    # like the Customers pages, so a snapshot rather than windowed.
    "customers":       {"label": "Customers",        "admin": True, "branch": True,  "windowed": False},
    # Discrepancies — damaged/short units on deliveries, the same
    # DAMAGE/ADJUSTMENT rows both Discrepancies pages list. Dated, so
    # windowed.
    "discrepancies":   {"label": "Discrepancies",    "admin": True, "branch": True,  "windowed": True},
    # Branch Performance — one summary row per retail branch (the Branch
    # Performance page). Windowed so revenue/units can be pulled for a
    # month or a date range; "Recent N" doesn't mean anything for a
    # per-branch total, so it reads as all time (see the builder).
    "branch_performance": {"label": "Branch Performance", "admin": True, "branch": False, "windowed": True},
    # Financial Summary — the Dashboard / Reports page money figures for
    # a window: register sales + Closed package orders, cost of goods,
    # gross profit, raw materials bought. Recent mode reads as all time.
    "financial_summary": {"label": "Financial Summary", "admin": True, "branch": False, "windowed": True},
    "accounts":        {"label": "Accounts",         "admin": True, "branch": False, "windowed": False},
    # Partners & Distribution — admin-only (branch accounts never see
    # this section of the app at all, same as Accounts above).
    # Partners/Packages are catalog-style snapshots (not windowed),
    # same reasoning as Products/Accounts. Partner Inquiries is a dated
    # pipeline of leads, so it gets the Recent/Range/All time window.
    "partners":        {"label": "Partners",         "admin": True, "branch": False, "windowed": False},
    "packages":        {"label": "Packages",         "admin": True, "branch": False, "windowed": False},
    "partner_inquiries": {"label": "Partner Inquiries", "admin": True, "branch": False, "windowed": True},
    # Admin Log / Login Activity — the two audit pages, as dated logs.
    "admin_log":       {"label": "Admin Log",        "admin": True, "branch": False, "windowed": True},
    "login_activity":  {"label": "Login Activity",   "admin": True, "branch": False, "windowed": True},
}

# Report types whose builder actually honors filters["branch_id"]. Only
# these get a branch name in the subtitle, so a Branch picked for one
# report type and left behind on the form never mislabels another.
BRANCH_FILTERED_TYPES = {
    "branch_stock", "low_stock", "stock_requests", "inventory_log", "sales_history",
    "credit_purchases", "customers", "discrepancies", "voided_sales",
}


# ---------------------------------------------------------------- filter parsing
def parse_report_filters(args):
    """Sanitize raw querystring args into a plain dict of known-safe values.

    Never trusts a raw value into SQL directly — every field is either
    checked against a fixed allow-list, cast to int, or parsed as a date.
    Anything invalid or missing quietly falls back to a safe default
    rather than erroring, since this only ever affects what's *filtered*,
    not whether the request is allowed.
    """
    def _choice(name, choices, default):
        v = args.get(name, default)
        return v if v in choices else default

    def _date(name):
        raw = (args.get(name) or "").strip()
        try:
            return datetime.datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None

    def _month(name):
        raw = (args.get(name) or "").strip()
        try:
            # Normalizes to exactly "YYYY-MM" (e.g. a stray "2026-9" from
            # a hand-edited querystring becomes "2026-09") rather than
            # trusting whatever shape the browser's <input type="month">
            # actually sent.
            return datetime.datetime.strptime(raw, "%Y-%m").strftime("%Y-%m")
        except ValueError:
            return None

    mode = _choice("mode", ("recent", "range", "month", "all"), "recent")
    try:
        recent_n = int(args.get("recent_n", 20))
    except (TypeError, ValueError):
        recent_n = 20
    if recent_n not in RECENT_CHOICES:
        recent_n = 20

    branch_id_raw = (args.get("branch_id") or "all").strip()
    branch_id = int(branch_id_raw) if branch_id_raw.isdigit() else "all"

    return {
        "mode": mode,
        "recent_n": recent_n,
        "date_from": _date("date_from"),
        "date_to": _date("date_to"),
        "month": _month("month"),
        "branch_id": branch_id,
        "status": _choice("status", STATUS_CHOICES, "all"),
        "movement_type": _choice("movement_type", MOVEMENT_TYPE_CHOICES, "all"),
        "variant": _choice("variant", VARIANT_CHOICES, "all"),
        "unit": _choice("unit", UNIT_CHOICES, "all"),
        "sale_type": _choice("sale_type", SALE_TYPE_CHOICES, "all"),
        "payment_method": _choice("payment_method", PAYMENT_METHOD_CHOICES, "all"),
        "role": _choice("role", ROLE_CHOICES, "all"),
        "account_status": _choice("account_status", ("active", "inactive"), "all"),
        "low_stock_only": args.get("low_stock_only") == "1",
        "partner_type": _choice("partner_type", PARTNER_TYPE_CHOICES, "all"),
        "package_scope": _choice("package_scope", PACKAGE_SCOPE_CHOICES, "all"),
        "package_status": _choice("package_status", PACKAGE_STATUS_CHOICES, "all"),
        "inquiry_status": _choice("inquiry_status", INQUIRY_STATUS_CHOICES, "all"),
        "search": (args.get("search") or "").strip()[:100],
    }


def _month_bounds(month_str):
    """(first_day, last_day) date objects for a "YYYY-MM" string, or for
    the current calendar month if month_str is missing/invalid — the
    same "fall back to a safe default rather than error" contract as
    every other filter parsed in this module."""
    try:
        year, mon = (int(p) for p in month_str.split("-"))
        first_day = datetime.date(year, mon, 1)
    except (AttributeError, TypeError, ValueError):
        today = datetime.date.today()
        first_day = today.replace(day=1)
        year, mon = first_day.year, first_day.month
    next_month_first = (
        datetime.date(year + 1, 1, 1) if mon == 12
        else datetime.date(year, mon + 1, 1)
    )
    return first_day, next_month_first - datetime.timedelta(days=1)


def _time_window(date_col, filters, params):
    """Append a time-window WHERE fragment for date_col and return
    (where_fragment, order_by_sql, row_limit, is_capped_all_time).

    params is mutated in place (range/month modes append their bound(s)).
    """
    mode = filters["mode"]
    if mode == "range":
        frag = ""
        if filters["date_from"]:
            frag += f" AND {date_col} >= %s"
            params.append(f"{filters['date_from']} 00:00:00")
        if filters["date_to"]:
            frag += f" AND {date_col} <= %s"
            params.append(f"{filters['date_to']} 23:59:59")
        # Range mode is capped at MAX_ROWS just like "all time" — it was
        # previously hardcoded to False here, so a date range matching
        # more than MAX_ROWS rows would silently drop the excess with no
        # "capped, narrow your filters" note anywhere in the report.
        return frag, f"ORDER BY {date_col} ASC", MAX_ROWS, True
    if mode == "month":
        first_day, last_day = _month_bounds(filters["month"])
        frag = f" AND {date_col} >= %s AND {date_col} <= %s"
        params.append(f"{first_day.isoformat()} 00:00:00")
        params.append(f"{last_day.isoformat()} 23:59:59")
        # A single calendar month of activity is realistically never
        # anywhere near MAX_ROWS, but capped the same way range/all-time
        # are just in case — same reasoning as range mode above.
        return frag, f"ORDER BY {date_col} ASC", MAX_ROWS, True
    if mode == "all":
        return "", f"ORDER BY {date_col} DESC", MAX_ROWS, True
    return "", f"ORDER BY {date_col} DESC", filters["recent_n"], False


def _window_note(filters, truncated):
    mode = filters["mode"]
    if mode == "recent":
        note = f"Most recent {filters['recent_n']} entries"
    elif mode == "range":
        frm = filters["date_from"] or "the beginning"
        to = filters["date_to"] or "today"
        note = f"{frm} through {to}"
    elif mode == "month":
        first_day, _ = _month_bounds(filters["month"])
        note = first_day.strftime("%B %Y")
    else:
        note = "All time"
    if truncated:
        note += f" (capped at the first {MAX_ROWS:,} rows — narrow the filters for a complete report)"
    return note


# Column keys that are money/int/num but shouldn't be summed into a
# report's totals row — a per-unit price summed across rows produces a
# meaningless number (e.g. three ₱85 sales don't total to "₱255 of
# unit price"), unlike qty/line-total columns where a sum is a genuine
# total. Kept as a small denylist rather than an opt-in flag on every
# column definition, since every other money/int/num column across
# every report *is* meant to total.
#
# cost_per_unit/cogs_per_unit are the same "per-unit price" shape as
# unit_price (raw_materials/cogs_logs' own per-unit cost, not a line
# total). price/hq_price (products.price, shown as-is on the Products
# and Branch Stock reports) are that exact same per-unit reference
# price too — summing it across unrelated products is just as
# meaningless as summing unit_price across unrelated sales. qty_per_unit
# (unit_formula_items) and package_qty (raw_materials) are both a
# quantity in whatever unit that particular material happens to use
# (grams, mL, pieces, ...) — unlike a plain piece-count like
# qty_sold/qty_produced on a bottled product (a bulk product's rows
# hold mL instead; _split_bulk_quantities() moves those into their own
# column so they're totalled apart), summing these across rows for different materials on the
# same report would add incompatible units together (e.g. grams +
# milliliters) into a number that means nothing. cost_per_ml (a bulk
# batch's cost per mL) is the same per-unit shape as cost_per_unit, and
# turnover (Branch Performance) is a ratio per branch, not an amount.
NO_TOTAL_COLUMNS = {"unit_price", "cost_per_unit", "cogs_per_unit",
                    "qty_per_unit", "package_qty", "price", "hq_price",
                    "cost_per_ml", "turnover",
                    # A reorder threshold / current material stock is a
                    # per-row level, and inventory_log's change is signed
                    # (in and out net to nothing meaningful).
                    "reorder_level", "stock_on_hand", "change_qty", "change_qty_bulk_ml",
                    # Partner Inquiries' quoted amount covers every lead;
                    # only the Closed-only sale_amount column is a total.
                    "package_value",
                    # Financial Summary is one row per different figure.
                    "figure_amount"}


# Per-row quantity columns that hold mL rather than a bottle count on a
# bulk product's row (products.unit = 'BULK'): qty_sold, stock and
# production quantities are all recorded in mL for bulk.
BULK_QTY_COLUMNS = {"qty_sold", "stock_qty", "total_stock", "qty_produced", "change_qty"}


def _split_bulk_quantities(columns, rows):
    """Keep bulk mL out of bottle-count columns.

    For a report whose rows carry the product's `unit`, each column in
    BULK_QTY_COLUMNS is split in two when the rows mix bottled and bulk
    products: "<label> (bottles)" and "<label> (bulk mL)", each row's
    value landing in exactly one of them. _compute_totals() then sums
    each on its own instead of adding mL to bottles. When every row is
    bulk the column is just relabelled; all-bottled reports are unchanged.
    """
    if not rows or "unit" not in rows[0]:
        return columns
    bulk_flags = [r.get("unit") == "BULK" for r in rows]
    if not any(bulk_flags):
        return columns
    all_bulk = all(bulk_flags)
    out = []
    for col in columns:
        key, label = col[0], col[1]
        if key not in BULK_QTY_COLUMNS:
            out.append(col)
            continue
        if all_bulk:
            out.append((key, f"{label} (mL)") + tuple(col[2:]))
            continue
        ml_key = f"{key}_bulk_ml"
        for r, is_bulk in zip(rows, bulk_flags):
            r[ml_key] = r[key] if is_bulk else None
            if is_bulk:
                r[key] = None
        out.append((key, f"{label} (bottles)") + tuple(col[2:]))
        out.append((ml_key, f"{label} (bulk mL)") + tuple(col[2:]))
    return out


def _compute_totals(columns, rows):
    """Sum every money/int column across all rows of a report.

    Returns a {column_key: summed_value} dict, or None when there's
    nothing summable (e.g. a report made entirely of text/date/badge
    columns, like Accounts or Partners) — a totals row would just be
    a row of dashes there, so it's skipped rather than shown.

    Columns listed in NO_TOTAL_COLUMNS (e.g. a per-unit price) are
    skipped even though they're typed money/int — summing a per-unit
    figure across rows doesn't produce a meaningful total the way
    summing a quantity or a line total does.
    """
    if not rows:
        return None
    totals = {}
    for col in columns:
        key, _, ctype = col[0], col[1], col[2]
        if ctype not in ("money", "int", "num") or key in NO_TOTAL_COLUMNS:
            continue
        total = 0
        has_value = False
        for row in rows:
            value = row.get(key)
            if value is None:
                continue
            total += value
            has_value = True
        if has_value:
            totals[key] = total
    return totals or None


def _branch_name(branch_id):
    if branch_id in (None, "all"):
        return None
    row = query("SELECT branch_name FROM branches WHERE branch_id = %s",
                (branch_id,), fetchone=True)
    return row["branch_name"] if row else None


# ---------------------------------------------------------------- per-type builders
def _report_products(filters, branch_scope):
    # NOTE: products.is_active was dropped from the schema (products are
    # now edited in place from the admin Products page instead of being
    # discontinued/reactivated — see schema.sql's migration block), so
    # there is no active/discontinued status left to filter or show here.
    where, params = "", []
    if filters["variant"] != "all":
        where += " AND p.variant = %s"
        params.append(filters["variant"])
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]

    rows = query(
        f"""SELECT p.sku, p.item_name, p.variant, p.unit, p.price,
                   COALESCE(SUM(bi.stock_qty), 0) AS total_stock
            FROM products p LEFT JOIN branch_inventory bi ON p.sku = bi.sku
            WHERE 1=1 {where}
            GROUP BY p.sku ORDER BY p.item_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["price"] = float(r["price"])

    columns = [
        ("sku", "SKU", "str"), ("item_name", "Item",
                                "str"), ("variant", "Variant", "badge:variant"),
        ("unit", "Unit", "str"), ("price", "HQ Price", "money"),
        ("total_stock", "Total Stock (all branches)", "int"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def _report_production_log(filters, branch_scope):
    where, params = "", []
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR pl.batch_code LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = _time_window(
        "pl.produced_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT pl.produced_at, p.sku, p.item_name, p.unit, pl.batch_code, pl.qty_produced,
                   c.cogs_per_unit, c.total_cogs
            FROM production_logs pl JOIN products p ON pl.sku = p.sku
            LEFT JOIN (
                SELECT production_log_id, MAX(cogs_per_unit) AS cogs_per_unit, SUM(total_cogs) AS total_cogs
                FROM cogs_logs WHERE production_log_id IS NOT NULL GROUP BY production_log_id
            ) c ON c.production_log_id = pl.log_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["batch_code"] = r["batch_code"] or "—"
        # Same cost figures the Production Log page shows per run (from
        # cogs_logs); a bulk run's cost is per mL.
        r["cogs_per_unit"] = float(r["cogs_per_unit"]) if r["cogs_per_unit"] is not None else None
        r["total_cogs"] = float(r["total_cogs"]) if r["total_cogs"] is not None else None

    columns = [
        ("produced_at", "Produced", "datetime"), ("sku",
                                                  "SKU", "str"), ("item_name", "Item", "str"),
        ("unit", "Unit", "str"), ("batch_code", "Batch",
                                  "str"), ("qty_produced", "Qty Produced", "int"),
        ("cogs_per_unit", "Cost / Unit (or / mL)", "money"), ("total_cogs", "Cost of Goods", "money"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_materials(filters, branch_scope):
    """One row per raw material, same as the Materials page: when it was
    first bought, the running package quantity/cost (a Restock adds to
    that same row rather than logging a new dated purchase — see
    admin.restock_material()), cost per unit, and current stock on hand.
    Windowed by that first-purchase date. branch_scope is unused
    (materials aren't branch-scoped) but every builder is called with it.
    """
    where, params = "", []
    if filters["search"]:
        where += " AND (rm.material_name LIKE %s OR rm.receipt_number LIKE %s OR s.supplier_name LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    # raw_materials.created_at actually holds the purchase date entered
    # on the Materials page (see routes/admin.py's materials(), which
    # inserts parse_past_date(...) into this column) — not necessarily
    # when the row was saved, so "Purchased" below is accurate either way.
    time_where, order, limit_n, truncated = _time_window(
        "rm.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT rm.created_at AS purchased_at, rm.material_name, s.supplier_name, rm.unit,
                   rm.purchase_mode, rm.package_qty, rm.package_cost, rm.cost_per_unit, rm.receipt_number,
                   rm.stock_qty AS stock_on_hand
            FROM raw_materials rm
            LEFT JOIN suppliers s ON rm.supplier_id = s.supplier_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["supplier_name"] = r["supplier_name"] or "— none on file —"
        r["package_qty"] = float(r["package_qty"])
        r["package_cost"] = float(r["package_cost"])
        r["cost_per_unit"] = float(r["cost_per_unit"])
        r["stock_on_hand"] = float(r["stock_on_hand"] or 0)
        r["receipt_number"] = r["receipt_number"] or "—"

    columns = [
        ("purchased_at", "First Purchased", "datetime"),
        ("material_name", "Material", "str"),
        ("supplier_name", "Supplier", "str"),
        ("unit", "Unit", "str"),
        ("purchase_mode", "Purchase Mode", "str"),
        ("package_qty", "Quantity", "num"),
        ("package_cost", "Cost", "money"),
        ("cost_per_unit", "Cost / Unit", "money"),
        ("stock_on_hand", "Stock on Hand", "num"),
        ("receipt_number", "Receipt #", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_suppliers(filters, branch_scope):
    """One row per supplier, rolled up the same way the Materials page's
    merged Suppliers directory is (see routes/admin.py's materials()) —
    a catalog-style snapshot, not windowed, same reasoning as Products/
    Accounts/Partners above.
    """
    where, params = "", []
    if filters["search"]:
        where += " AND (s.supplier_name LIKE %s OR s.contact_person LIKE %s OR s.phone LIKE %s OR s.email LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like, like]

    rows = query(
        f"""SELECT s.supplier_name, s.contact_person, s.phone, s.email, s.created_at,
                   COUNT(rm.material_id) AS material_count,
                   COALESCE(SUM(rm.package_cost), 0) AS total_spent,
                   MAX(rm.created_at) AS last_purchase_at
            FROM suppliers s
            LEFT JOIN raw_materials rm ON rm.supplier_id = s.supplier_id
            WHERE 1=1 {where}
            GROUP BY s.supplier_id, s.supplier_name, s.contact_person, s.phone, s.email, s.created_at
            ORDER BY total_spent DESC, s.supplier_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["total_spent"] = float(r["total_spent"])
        r["contact"] = " · ".join(
            p for p in (r.pop("contact_person"), r.pop("phone"), r.pop("email")) if p
        ) or "—"

    columns = [
        ("supplier_name", "Supplier", "str"),
        ("contact", "Contact", "str"),
        ("material_count", "Materials Supplied", "int"),
        ("total_spent", "Total Spent", "money"),
        ("last_purchase_at", "Latest New Material", "datetime"),
        ("created_at", "On File Since", "datetime"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def _report_formulas(filters, branch_scope):
    """The cost of goods on file right now: the flat base cost per
    Bottled packaging size (unit_cogs_settings) plus the single per-mL
    rate for Bulk/Refill (bulk_rate_settings) — exactly what the
    Formulas page edits and what production() logs as cogs_per_unit.

    This used to list unit_formula_items (a per-size materials recipe),
    but production() no longer reads that table — it was superseded by
    unit_cogs_settings (see schema.sql's migration 38) — so the report
    was showing a recipe that no longer drives any cost. A snapshot, not
    windowed, same as before. The unit filter still applies: a bottle
    size narrows to that size's row, BULK narrows to the bulk rate.
    """
    rows = []
    if filters["unit"] != "BULK":
        where, params = "", []
        if filters["unit"] != "all":
            where = " WHERE unit = %s"
            params.append(filters["unit"])
        for r in query(
            f"""SELECT unit, base_cost_per_unit, updated_at
                FROM unit_cogs_settings{where}
                ORDER BY FIELD(unit, '85ML', '50ML', '10ML', '3ML')""",
            tuple(params),
        ):
            rows.append({
                "unit": r["unit"], "basis": "Per bottle",
                "cost_per_unit": float(r["base_cost_per_unit"]),
                "updated_at": r["updated_at"],
            })
    if filters["unit"] in ("all", "BULK"):
        bulk = query(
            "SELECT rate_per_ml, updated_at FROM bulk_rate_settings WHERE id = 1", fetchone=True)
        if bulk:
            rows.append({
                "unit": "BULK", "basis": "Per mL (Bulk/Refill)",
                "cost_per_unit": float(bulk["rate_per_ml"]),
                "updated_at": bulk["updated_at"],
            })

    columns = [
        ("unit", "Packaging Size", "str"),
        ("basis", "Charged", "str"),
        ("cost_per_unit", "Cost of Goods", "money"),
        ("updated_at", "Last Changed", "datetime"),
    ]
    return columns, rows, False, "Snapshot as of now — the cost of goods each production run is charged"


def _report_cogs_logs(filters, branch_scope):
    """One row per cost-of-goods batch (cogs_logs) — every time a
    production run was costed off a formula (see routes/admin.py's
    production()). This is the line-item detail behind the dashboard's
    "Total Capital" tile and the Reports page's Revenue vs. Capital
    chart, both of which are just SUM(total_cogs) over this same table
    — so "All time" on this report reconciles exactly to that figure.
    """
    where, params = "", []
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["search"]:
        where += (" AND (p.item_name LIKE %s OR p.sku LIKE %s OR pl.batch_code LIKE %s"
                  " OR bb.batch_code LIKE %s OR bb.scent_name LIKE %s)")
        like = f"%{filters['search']}%"
        params += [like, like, like, like, like]
    time_where, order, limit_n, truncated = _time_window(
        "c.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT c.created_at, p.sku, p.item_name, p.unit, pl.batch_code,
                   COALESCE(bb.batch_code, bb.scent_name) AS bulk_batch,
                   c.qty_produced, c.cogs_per_unit, c.total_cogs, u.username
            FROM cogs_logs c
            JOIN products p ON c.sku = p.sku
            LEFT JOIN production_logs pl ON c.production_log_id = pl.log_id
            LEFT JOIN bulk_batches bb ON c.bulk_batch_id = bb.batch_id
            LEFT JOIN users u ON c.created_by_user_id = u.user_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["batch_code"] = r["batch_code"] or "—"
        r["bulk_batch"] = r["bulk_batch"] or "—"
        r["qty_produced"] = float(r["qty_produced"])
        r["cogs_per_unit"] = float(r["cogs_per_unit"])
        r["total_cogs"] = float(r["total_cogs"])
        r["username"] = r["username"] or "—"

    columns = [
        ("created_at", "Logged", "datetime"),
        ("sku", "SKU", "str"), ("item_name", "Item", "str"),
        ("unit", "Unit", "str"), ("batch_code", "Batch", "str"),
        # Which bulk batch a Bottled run was filled from (cogs_logs.
        # bulk_batch_id) — "—" for Bulk/Refill runs and older rows.
        ("bulk_batch", "Filled From", "str"),
        ("qty_produced", "Qty Produced", "num"),
        ("cogs_per_unit", "Cost / Unit", "money"),
        ("total_cogs", "Total Cost of Goods", "money"),
        ("username", "Logged By", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_branch_stock(filters, branch_scope):
    where, params = "", []
    if branch_scope is not None:
        where += " AND b.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND b.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["variant"] != "all":
        where += " AND p.variant = %s"
        params.append(filters["variant"])
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]
    if filters["low_stock_only"]:
        where += f" AND {LOW_STOCK_WHERE}"
    if branch_scope is not None:
        # Same as My Inventory: a branch only ever stocks bottled sizes.
        where += " AND p.unit <> 'BULK'"

    # No more per-branch price override — every branch sells at
    # products.price, so this is just stock levels per branch now.
    # (products.is_active no longer exists — see schema.sql's migration
    # block — so there's nothing to filter out here anymore.)
    rows = query(
        f"""SELECT b.branch_name, p.sku, p.item_name, p.variant, p.unit, p.price AS hq_price,
                   bi.stock_qty, bi.reorder_level
            FROM branch_inventory bi
            JOIN branches b ON bi.branch_id = b.branch_id
            JOIN products p ON bi.sku = p.sku
            WHERE 1=1 {where}
            ORDER BY b.is_hq DESC, b.branch_name, p.item_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["hq_price"] = float(r["hq_price"])

    columns = [("branch_name", "Branch", "str")
               ] if branch_scope is None else []
    columns += [
        ("sku", "SKU", "str"), ("item_name", "Item",
                                "str"), ("variant", "Variant", "badge:variant"),
        ("unit", "Unit", "str"), ("hq_price", "HQ Price", "money"),
        ("stock_qty", "Stock Qty", "int"), ("reorder_level", "Reorder Level", "int"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def _report_stock_requests(filters, branch_scope):
    """One row per product on a delivery.

    A stock request is now a delivery *header* (stock_requests) that can
    carry several products, each its own line in stock_request_items —
    sku/requested_qty/dispatched_qty/received_qty/damaged_qty all live on
    the item row now, not on the request itself (see schema.sql's
    migration block and receipts.py). So this joins through
    stock_request_items rather than reading those columns off sr
    directly, and surfaces delivery_number (the human-facing identifier
    used everywhere else in the app) alongside them.
    """
    where, params = "", []
    if branch_scope is not None:
        where += " AND sr.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND sr.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["status"] != "all":
        where += " AND sr.status = %s"
        params.append(filters["status"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR sr.delivery_number LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = _time_window(
        "sr.requested_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT sr.requested_at, sr.delivery_number, b.branch_name, p.item_name, p.sku,
                   sri.requested_qty, sri.dispatched_qty, sri.received_qty, sri.damaged_qty, sr.status,
                   sri.unit_price, (sri.requested_qty * sri.unit_price) AS line_value
            FROM stock_request_items sri
            JOIN stock_requests sr ON sri.request_id = sr.request_id
            JOIN branches b ON sr.branch_id = b.branch_id
            JOIN products p ON sri.sku = p.sku
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        for k in ("dispatched_qty", "received_qty", "damaged_qty"):
            r[k] = r[k] or 0
        r["unit_price"] = float(r["unit_price"])
        r["line_value"] = float(r["line_value"])

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [
        ("requested_at", "Requested", "datetime"),
        ("delivery_number", "Delivery #", "str"),
    ] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU",
                                       "str"), ("requested_qty", "Requested Qty", "int"),
        ("dispatched_qty", "Dispatched Qty",
         "int"), ("received_qty", "Received Qty", "int"),
        ("damaged_qty", "Damaged Qty", "int"), ("unit_price", "Unit Price", "money"),
        ("line_value", "Value", "money"), ("status", "Status", "badge:status"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_inventory_log(filters, branch_scope):
    where, params = "", []
    if branch_scope is not None:
        where += " AND sml.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND sml.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["movement_type"] != "all":
        where += " AND sml.movement_type = %s"
        params.append(filters["movement_type"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR sml.notes LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = _time_window(
        "sml.created_at", filters, params)
    where += time_where

    rows = query(
        # Delivery DAMAGE/ADJUSTMENT rows are logged with change_qty = 0
        # (stock was never added), so their real units lost come from the
        # same DISCREPANCY_UNITS_SQL the Discrepancies pages use, shown
        # as a negative change like any other stock loss.
        f"""SELECT sml.created_at, b.branch_name, p.item_name, p.sku, p.unit,
                   sml.movement_type,
                   CASE WHEN sml.reference_type = 'STOCK_REQUEST'
                             AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
                        THEN -{DISCREPANCY_UNITS_SQL} ELSE sml.change_qty END AS change_qty,
                   sml.notes
            FROM stock_movement_logs sml
            JOIN branches b ON sml.branch_id = b.branch_id
            JOIN products p ON sml.sku = p.sku
            {DISCREPANCY_ITEM_JOIN}
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["notes"] = r["notes"] or "—"
        r["change_qty"] = int(r["change_qty"])

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [("created_at", "When", "datetime")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU",
                                       "str"), ("movement_type", "Type", "badge:movement_type"),
        ("change_qty", "Change", "int"), ("notes", "Notes", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_sales_history(filters, branch_scope):
    where, params = "", []
    if branch_scope is not None:
        where += " AND s.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND s.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["variant"] != "all":
        where += " AND p.variant = %s"
        params.append(filters["variant"])
    if filters["unit"] != "all":
        where += " AND p.unit = %s"
        params.append(filters["unit"])
    if filters["sale_type"] != "all":
        where += " AND s.sale_type = %s"
        params.append(filters["sale_type"])
    if filters["payment_method"] != "all":
        where += " AND s.payment_method = %s"
        params.append(filters["payment_method"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR s.customer_name LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = _time_window(
        "s.sold_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT s.sold_at, b.branch_name, p.item_name, p.sku, p.variant, p.unit,
                   s.qty_sold, s.unit_price, (s.qty_sold * s.unit_price) AS line_total,
                   s.sale_type, s.payment_method, COALESCE(s.buyer_name, bu.username) AS buyer_username,
                   s.customer_name
            FROM sales s
            JOIN branches b ON s.branch_id = b.branch_id
            JOIN products p ON s.sku = p.sku
            LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["unit_price"] = float(r["unit_price"])
        r["line_total"] = float(r["line_total"])
        r["buyer_username"] = r["buyer_username"] or "—"
        r["customer_name"] = r["customer_name"] or "Walk-in"

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [("sold_at", "Sold", "datetime")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU",
                                       "str"), ("variant", "Variant", "badge:variant"),
        ("unit", "Unit", "str"), ("sale_type",
                                  "Type", "badge:sale_type"), ("qty_sold", "Qty", "int"),
        ("unit_price", "Unit Price", "money"), ("line_total", "Total", "money"),
        ("payment_method", "Payment", "badge:payment_method"), ("buyer_username",
                                                                "Buyer (if Credit)", "str"),
        ("customer_name", "Customer", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_credit_purchases(filters, branch_scope):
    """Sales taken on credit rather than cash — an employee against their
    own pay, or a customer buying on store credit ("utang"). Same shape
    as sales_history but always scoped to payment_method = 'Credit', so
    HQ/branch staff can pull exactly what needs to be collected from or
    deducted for each buyer for a given period.
    """
    where, params = "", ["Credit"]
    if branch_scope is not None:
        where += " AND s.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND s.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["sale_type"] != "all":
        where += " AND s.sale_type = %s"
        params.append(filters["sale_type"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR COALESCE(s.buyer_name, bu.username) LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = _time_window(
        "s.sold_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT s.sold_at, b.branch_name, p.item_name, p.sku, p.unit, s.sale_type,
                   s.qty_sold, s.unit_price, (s.qty_sold * s.unit_price) AS line_total,
                   COALESCE(s.buyer_name, bu.username, '(unspecified)') AS buyer_username
            FROM sales s
            JOIN branches b ON s.branch_id = b.branch_id
            JOIN products p ON s.sku = p.sku
            LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
            WHERE s.payment_method = %s {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["unit_price"] = float(r["unit_price"])
        r["line_total"] = float(r["line_total"])

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [("sold_at", "Date", "datetime"), ("buyer_username", "Buyer", "str")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU",
                                       "str"), ("sale_type", "Type", "badge:sale_type"),
        ("qty_sold", "Qty", "int"), ("unit_price", "Unit Price", "money"),
        ("line_total", "Amount Owed", "money"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_accounts(filters, branch_scope):
    where, params = "", []
    if filters["role"] != "all":
        where += " AND u.role = %s"
        params.append(filters["role"])
    if filters["account_status"] != "all":
        where += " AND u.is_active = %s"
        params.append(filters["account_status"] == "active")
    if filters["search"]:
        where += " AND u.username LIKE %s"
        params.append(f"%{filters['search']}%")

    rows = query(
        f"""SELECT u.username, u.role, b.branch_name, u.is_active, u.created_at
            FROM users u LEFT JOIN branches b ON u.branch_id = b.branch_id
            WHERE 1=1 {where} ORDER BY u.role, b.branch_name, u.username LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["branch_name"] = r["branch_name"] or "— HQ —"
        r["is_active"] = "Active" if r["is_active"] else "Deactivated"

    columns = [
        ("username", "Username", "str"), ("role", "Role",
                                          "badge:role"), ("branch_name", "Branch", "str"),
        ("is_active", "Status", "badge:active_status"), ("created_at",
                                                         "Created", "datetime"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def _report_partners(filters, branch_scope):
    """One row per partner. total_sales/closed_order_count are computed
    the same way the Partners page computes "package sales" — only
    inquiries an admin has marked Closed count, same rule the Partners
    and Dashboard pages both follow (see partners.html's footnote).

    Contact Person/Phone/Email collapse into one Contact column, and
    Total/Closed Inquiries collapse into one Inquiries column — same
    shape the Partners page itself already shows (see the "Contact" and
    "Package sales" cells in partners.html), so the report isn't more
    spread out than the screen it's summarizing. Address is dropped
    entirely: it's an optional field on the partner-portal form and is
    almost always blank in practice, so keeping its own column mostly
    just added width for a column that read "—" on nearly every row.
    """
    where, params = "", []
    if filters["partner_type"] != "all":
        where += " AND p.partner_type = %s"
        params.append(filters["partner_type"])
    if filters["search"]:
        where += " AND (p.partner_name LIKE %s OR p.contact_person LIKE %s OR p.phone LIKE %s OR p.email LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like, like]

    rows = query(
        f"""SELECT p.partner_name, p.partner_type, p.contact_person, p.phone, p.email,
                   p.inquiry_count, p.last_inquiry_at, p.created_at,
                   COALESCE(SUM(CASE WHEN pi.status = 'Closed' THEN pi.order_amount ELSE 0 END), 0) AS total_sales,
                   COUNT(CASE WHEN pi.status = 'Closed' THEN 1 END) AS closed_order_count
            FROM partners p
            LEFT JOIN partner_inquiries pi ON pi.partner_id = p.partner_id
            WHERE 1=1 {where}
            GROUP BY p.partner_id
            ORDER BY p.partner_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["total_sales"] = float(r["total_sales"])
        r["contact"] = " · ".join(
            p for p in (r.pop("contact_person"), r.pop("phone"), r.pop("email")) if p
        ) or "—"
        r["inquiries_summary"] = f"{r['inquiry_count']} total · {r['closed_order_count']} closed"

    # 4th element = relative PDF column width (see render_report_pdf's
    # width-weight comment). Contact now carries three merged fields so
    # it gets the biggest share; Type/Inquiries are short so they shrink.
    columns = [
        ("partner_name", "Partner", "str", 1.2),
        ("partner_type", "Type", "badge:partner_type", 0.7),
        ("contact", "Contact", "str", 1.8),
        ("inquiries_summary", "Inquiries", "str", 0.95),
        ("total_sales", "Package Sales", "money", 1.0),
        ("last_inquiry_at", "Last Inquiry", "datetime", 1.0),
        ("created_at", "On File Since", "datetime", 1.0),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def _report_packages(filters, branch_scope):
    """One row per package. Reference/order totals mirror package_detail's
    footer math (sum of qty * products.price, then the package's own
    discount_percent applied) — see packages.html / package_detail.html.
    """
    where, params = "", []
    if filters["package_scope"] != "all":
        where += " AND pk.partner_scope = %s"
        params.append(filters["package_scope"])
    if filters["package_status"] != "all":
        where += " AND pk.is_active = %s"
        params.append(filters["package_status"] == "Active")
    if filters["search"]:
        where += " AND (pk.package_name LIKE %s OR pk.description LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]

    rows = query(
        f"""SELECT pk.package_name, pk.description, pk.partner_scope, pk.discount_percent,
                   pk.is_active, pk.created_at,
                   COUNT(pki.package_item_id) AS item_count,
                   COALESCE(SUM(pki.qty * pr.price), 0) AS reference_total
            FROM packages pk
            LEFT JOIN package_items pki ON pki.package_id = pk.package_id
            LEFT JOIN products pr ON pki.sku = pr.sku
            WHERE 1=1 {where}
            GROUP BY pk.package_id
            ORDER BY pk.package_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["description"] = r["description"] or "—"
        discount = float(r["discount_percent"])
        r["discount_percent"] = f"{discount:.2f}%"
        reference_total = float(r["reference_total"])
        r["reference_total"] = reference_total
        r["discounted_total"] = round(
            reference_total * (1 - discount / 100), 2)
        r["is_active"] = "Active" if r["is_active"] else "Retired"

    columns = [
        ("package_name", "Package", "str"), ("description", "Description", "str"),
        ("partner_scope", "Available To",
         "badge:package_scope"), ("item_count", "Items", "int"),
        ("reference_total", "Reference Value",
         "money"), ("discount_percent", "Discount", "str"),
        ("discounted_total", "Order Price",
         "money"), ("is_active", "Status", "badge:package_status"),
        ("created_at", "Created", "datetime"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def _report_partner_inquiries(filters, branch_scope):
    """One row per inquiry — the same permanent, never-edited-except-
    status/remarks history shown on the Partner Inquiries page. Only
    Closed inquiries represent actual revenue (order_amount); everything
    else is still a lead — see schema.sql's partner_inquiries comment
    and partners.html/dashboard.html's matching footnotes.
    """
    where, params = "", []
    if filters["partner_type"] != "all":
        where += " AND pi.partner_type = %s"
        params.append(filters["partner_type"])
    if filters["inquiry_status"] != "all":
        where += " AND pi.status = %s"
        params.append(filters["inquiry_status"])
    if filters["search"]:
        where += (" AND (pi.company_name LIKE %s OR pi.contact_person LIKE %s "
                  "OR pi.package_name_snapshot LIKE %s OR pi.remarks LIKE %s)")
        like = f"%{filters['search']}%"
        params += [like, like, like, like]
    time_where, order, limit_n, truncated = _time_window(
        "pi.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT pi.created_at, pi.company_name, pi.partner_type, pi.contact_person, pi.phone,
                   pi.email, pi.preferred_contact, pi.package_name_snapshot, pi.order_amount,
                   pi.status, pi.fulfilled_at, pi.remarks
            FROM partner_inquiries pi
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        contact_person = r.pop("contact_person")
        r["company_name"] = (
            f"{r['company_name']} — {contact_person}" if contact_person else r["company_name"]
        )
        preferred = r.pop("preferred_contact")
        r["contact"] = " · ".join(p for p in (
            r.pop("phone"), r.pop("email"),
            f"prefers {preferred}" if preferred else None) if p) or "—"
        # Same short package name the Partner Inquiries page shows.
        r["package_name_snapshot"] = (r["package_name_snapshot"] or "—").split(" (")[0]
        # Nullable — see schema.sql's comment: NULL means "unknown"
        # (older row predating this column), never coerced to 0. The
        # quoted package value is shown for every lead, but only a
        # Closed inquiry is a sale (same rule as the Dashboard/Partners),
        # so only sale_amount is totalled.
        amount = r.pop("order_amount")
        r["package_value"] = float(amount) if amount is not None else None
        r["sale_amount"] = float(amount) if amount is not None and r["status"] == "Closed" else None
        r["remarks"] = r["remarks"] or "—"

    # See _report_partners' comment above — same reasoning: Contact
    # Person now rides along inside Company, and Phone/Email collapse
    # into one Contact column, so both the column count and the short
    # badge/status columns shrink; Remarks/Package/Contact (the ones
    # that actually need room) grow. Address and HQ Notified (whether
    # the notification email happened to send) are dropped outright —
    # Address is almost always blank, and HQ Notified is an internal
    # mailer-ops flag, not information about the partner or the sale.
    columns = [
        ("created_at", "When", "datetime", 0.9),
        ("company_name", "Company", "str", 1.3),
        ("partner_type", "Type", "badge:partner_type", 0.7),
        ("contact", "Contact", "str", 1.5),
        ("package_name_snapshot", "Package", "str", 1.05),
        ("package_value", "Package Value", "money", 0.9),
        ("sale_amount", "Sale (Closed only)", "money", 0.9),
        ("status", "Status", "badge:inquiry_status", 0.8),
        # When a Closed order was shipped out of HQ stock (Fulfill);
        # "—" = not fulfilled yet.
        ("fulfilled_at", "Fulfilled", "datetime", 0.85),
        ("remarks", "Remarks (internal)", "str", 1.5),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_bulk_batches(filters, branch_scope):
    """One row per bulk batch (bulk_batches) — what was mixed, how much,
    what it really cost, and how much is left to bottle from. Materials
    are folded into one "Materials Used" cell per batch (from
    bulk_batch_materials) rather than one row per ingredient, so the
    batch totals stay one-row-per-batch and total up correctly.
    """
    where, params = "", []
    if filters["search"]:
        where += " AND (bb.scent_name LIKE %s OR bb.batch_code LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]
    time_where, order, limit_n, truncated = _time_window(
        "bb.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT bb.created_at, bb.batch_code, bb.scent_name, bb.input_qty, bb.input_unit, bb.notes,
                   bb.total_volume_ml, bb.remaining_ml, bb.total_cost, bb.cost_per_ml,
                   (SELECT GROUP_CONCAT(
                        CONCAT(rm.material_name, ' ', TRIM(TRAILING '.' FROM TRIM(TRAILING '0' FROM bbm.qty_used)),
                               ' ', COALESCE(bbm.qty_used_unit, rm.unit))
                        ORDER BY rm.material_name SEPARATOR ', ')
                    FROM bulk_batch_materials bbm
                    JOIN raw_materials rm ON rm.material_id = bbm.material_id
                    WHERE bbm.batch_id = bb.batch_id) AS materials_used,
                   u.username
            FROM bulk_batches bb
            LEFT JOIN users u ON bb.created_by_user_id = u.user_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["batch_code"] = r["batch_code"] or "—"
        r["total_volume_ml"] = float(r["total_volume_ml"])
        r["remaining_ml"] = float(r["remaining_ml"])
        r["used_ml"] = r["total_volume_ml"] - r["remaining_ml"]
        r["total_cost"] = float(r["total_cost"])
        r["cost_per_ml"] = float(r["cost_per_ml"])
        r["materials_used"] = r["materials_used"] or "—"
        r["username"] = r["username"] or "—"
        r["size_entered"] = f"{float(r.pop('input_qty')):g} {r.pop('input_unit')}"
        # Same "bottles left" figure as the Bulk Batches page: full
        # bottles of each size the remaining mL could still fill.
        r["bottles_left"] = " · ".join(
            f"{int(r['remaining_ml'] // float(bottle_size_ml(u)))}×{u}" for u in BOTTLE_UNITS
        )
        r["notes"] = r["notes"] or "—"

    columns = [
        ("created_at", "Mixed", "datetime"),
        ("batch_code", "Batch", "str"), ("scent_name", "Scent", "str"),
        ("size_entered", "Size", "str"),
        ("materials_used", "Materials Used", "str"),
        ("total_volume_ml", "Volume (mL)", "num"),
        ("used_ml", "Bottled (mL)", "num"),
        ("remaining_ml", "Remaining (mL)", "num"),
        ("bottles_left", "Bottles Left", "str"),
        ("total_cost", "Batch Cost", "money"),
        ("cost_per_ml", "Cost / mL", "money"),
        ("username", "Made By", "str"),
        ("notes", "Notes", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_customers(filters, branch_scope):
    """The repeat-customer directory — same data as the admin/branch
    Customers pages, built from sales.customer_name (walk-ins with no
    name are left out, since there's nothing to group them by).

    Address is the one on that customer's earliest sale, the same
    "first sale wins" convention as both Customers pages — looked up
    with a correlated subquery (earliest by sold_at, then sale_id) so a
    customer with two sales at the same timestamp still gets exactly one
    row. A branch account (branch_scope) only ever sees its own sales;
    admin can narrow to one branch or see the fleet-wide rollup, where
    a customer who shopped at several branches is still one row.
    """
    scope_sql, scope_params = "", []
    if branch_scope is not None:
        scope_sql, scope_params = " AND {a}.branch_id = %s", [branch_scope]
    elif filters["branch_id"] != "all":
        scope_sql, scope_params = " AND {a}.branch_id = %s", [filters["branch_id"]]

    search_sql, search_params = "", []
    if filters["search"]:
        search_sql = " AND sa.customer_name LIKE %s"
        search_params = [f"%{filters['search']}%"]

    rows = query(
        f"""SELECT agg.customer_name AS name,
                   (SELECT s2.customer_address FROM sales s2
                    WHERE s2.customer_name = agg.customer_name{scope_sql.format(a="s2")}
                    ORDER BY s2.sold_at, s2.sale_id LIMIT 1) AS address,
                   (SELECT lb.branch_name FROM sales l JOIN branches lb ON lb.branch_id = l.branch_id
                    WHERE l.customer_name = agg.customer_name{scope_sql.format(a="l")}
                    ORDER BY l.sold_at DESC, l.sale_id DESC LIMIT 1) AS last_branch,
                   agg.branches, agg.purchase_count, agg.total_units, agg.total_bulk_ml,
                   agg.total_spent, agg.last_purchase_at
            FROM (
                SELECT sa.customer_name,
                       GROUP_CONCAT(DISTINCT b.branch_name ORDER BY b.branch_name SEPARATOR ', ') AS branches,
                       COUNT(*) AS purchase_count,
                       -- Bulk qty_sold is mL: kept apart from bottles.
                       COALESCE(SUM(CASE WHEN p.unit <> 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_units,
                       COALESCE(SUM(CASE WHEN p.unit = 'BULK' THEN sa.qty_sold ELSE 0 END), 0) AS total_bulk_ml,
                       COALESCE(SUM(sa.qty_sold * sa.unit_price), 0) AS total_spent,
                       MAX(sa.sold_at) AS last_purchase_at
                FROM sales sa
                JOIN branches b ON sa.branch_id = b.branch_id
                JOIN products p ON p.sku = sa.sku
                WHERE sa.customer_name IS NOT NULL AND sa.customer_name <> ''
                      {scope_sql.format(a="sa")}{search_sql}
                GROUP BY sa.customer_name
            ) agg
            ORDER BY agg.total_spent DESC LIMIT {MAX_ROWS}""",
        tuple(scope_params + scope_params + scope_params + search_params),
    )
    for r in rows:
        r["address"] = r["address"] or "—"
        r["total_units"] = int(r["total_units"])
        r["total_bulk_ml"] = int(r["total_bulk_ml"])
        r["total_spent"] = float(r["total_spent"])

    columns = [("name", "Customer", "str"), ("address", "Address", "str")]
    if branch_scope is None:
        columns.append(("branches", "Branches Shopped", "str"))
    columns += [
        ("purchase_count", "Purchases", "int"),
        ("total_units", "Bottles Bought", "int"),
        ("total_bulk_ml", "Bulk Bought (mL)", "int"),
        ("total_spent", "Total Spent", "money"),
        ("last_purchase_at", "Last Purchase", "datetime"),
    ]
    if branch_scope is None:
        columns.append(("last_branch", "Last At", "str"))
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now — named customers only (walk-ins excluded)"


def _report_discrepancies(filters, branch_scope):
    """Delivery discrepancies — DAMAGE (reported damaged at receipt) and
    ADJUSTMENT (dispatched but never received) rows tied to a delivery
    (reference_type='STOCK_REQUEST'), the same rows both Discrepancies
    pages list. Those rows are logged with change_qty = 0 (the stock was
    never added), so units lost come from DISCREPANCY_UNITS_SQL — the
    delivery item's damaged / dispatched-minus-received figures — same as
    the admin page's summary.
    """
    where, params = "", []
    if branch_scope is not None:
        where += " AND sml.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND sml.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR sr.delivery_number LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    if filters["movement_type"] in ("DAMAGE", "ADJUSTMENT"):
        where += " AND sml.movement_type = %s"
        params.append(filters["movement_type"])
    time_where, order, limit_n, truncated = _time_window(
        "sml.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT sml.created_at, b.branch_name, sr.delivery_number, p.item_name, p.sku,
                   sml.movement_type, {DISCREPANCY_UNITS_SQL} AS units_lost, sml.notes
            FROM stock_movement_logs sml
            JOIN branches b ON sml.branch_id = b.branch_id
            JOIN products p ON sml.sku = p.sku
            LEFT JOIN stock_requests sr
              ON sml.reference_type = 'STOCK_REQUEST' AND sml.reference_id = sr.request_id
            {DISCREPANCY_ITEM_JOIN}
            WHERE sml.reference_type = 'STOCK_REQUEST'
              AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
              {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["delivery_number"] = r["delivery_number"] or "—"
        r["units_lost"] = int(r["units_lost"])
        r["notes"] = r["notes"] or "—"

    columns = [] if branch_scope is not None else [
        ("branch_name", "Branch", "str")]
    columns = [("created_at", "When", "datetime")] + columns + [
        ("delivery_number", "Delivery", "str"),
        ("item_name", "Item", "str"), ("sku", "SKU", "str"),
        ("movement_type", "Type", "badge:movement_type"),
        ("units_lost", "Units Lost", "int"), ("notes", "Notes", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_branch_performance(filters, branch_scope):
    """One row per retail branch — the Branch Performance page as a
    report: sales count, units, revenue and delivery discrepancies over
    the chosen window, plus current stock on hand and the same rough
    turnover figure (units sold / current stock) the page shows.

    This is a per-branch total, so there's no "most recent N rows" to
    take — Recent mode reads as all time. Range/Month bound the sales
    and discrepancies (stock on hand is always as of now; there's no
    stock history to take it at a past date from).
    """
    sales_params, disc_params = [], []
    if filters["mode"] in ("range", "month"):
        sales_where, _, _, _ = _time_window("s.sold_at", filters, sales_params)
        disc_where, _, _, _ = _time_window("sml.created_at", filters, disc_params)
        note = _window_note(filters, False)
    else:
        sales_where = disc_where = ""
        note = "All time"

    rows = query(
        f"""SELECT b.branch_name,
                   COALESCE(sales_agg.sales_count, 0) AS sales_count,
                   COALESCE(sales_agg.units_sold, 0) AS units_sold,
                   COALESCE(sales_agg.bulk_ml_sold, 0) AS bulk_ml_sold,
                   COALESCE(sales_agg.revenue, 0) AS revenue,
                   COALESCE(stock_agg.total_stock, 0) AS total_stock,
                   COALESCE(stock_agg.bulk_ml_stock, 0) AS bulk_ml_stock,
                   COALESCE(disc_agg.discrepancy_count, 0) AS discrepancy_count
            FROM branches b
            LEFT JOIN (
                -- Bottles and bulk mL kept apart (bulk qty/stock is mL).
                SELECT s.branch_id, COUNT(*) AS sales_count,
                       SUM(CASE WHEN p.unit <> 'BULK' THEN s.qty_sold ELSE 0 END) AS units_sold,
                       SUM(CASE WHEN p.unit = 'BULK' THEN s.qty_sold ELSE 0 END) AS bulk_ml_sold,
                       SUM(s.qty_sold * s.unit_price) AS revenue
                FROM sales s JOIN products p ON p.sku = s.sku
                WHERE 1=1 {sales_where} GROUP BY s.branch_id
            ) sales_agg ON sales_agg.branch_id = b.branch_id
            LEFT JOIN (
                SELECT bi.branch_id,
                       SUM(CASE WHEN p.unit <> 'BULK' THEN bi.stock_qty ELSE 0 END) AS total_stock,
                       SUM(CASE WHEN p.unit = 'BULK' THEN bi.stock_qty ELSE 0 END) AS bulk_ml_stock
                FROM branch_inventory bi JOIN products p ON p.sku = bi.sku GROUP BY bi.branch_id
            ) stock_agg ON stock_agg.branch_id = b.branch_id
            LEFT JOIN (
                SELECT sml.branch_id, SUM({DISCREPANCY_UNITS_SQL}) AS discrepancy_count
                FROM stock_movement_logs sml
                {DISCREPANCY_ITEM_JOIN}
                WHERE sml.reference_type = 'STOCK_REQUEST'
                  AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
                      {disc_where}
                GROUP BY sml.branch_id
            ) disc_agg ON disc_agg.branch_id = b.branch_id
            WHERE b.is_hq = FALSE
            ORDER BY revenue DESC, b.branch_name""",
        tuple(sales_params + disc_params),
    )
    for r in rows:
        r["units_sold"] = int(r["units_sold"])
        r["bulk_ml_sold"] = int(r["bulk_ml_sold"])
        r["revenue"] = float(r["revenue"])
        r["total_stock"] = int(r["total_stock"])
        r["bulk_ml_stock"] = int(r["bulk_ml_stock"])
        # None (shown as "—") when there's no stock on hand to divide by,
        # same as the page — not a misleading 0 or an unbounded number.
        r["turnover"] = round(r["units_sold"] / r["total_stock"], 2) if r["total_stock"] else None

    columns = [
        ("branch_name", "Branch", "str"),
        ("sales_count", "Sales", "int"),
        ("units_sold", "Bottles Sold", "int"),
        ("bulk_ml_sold", "Bulk Sold (mL)", "int"),
        ("revenue", "Revenue", "money"),
        ("total_stock", "Bottles on Hand (now)", "int"),
        ("bulk_ml_stock", "Bulk on Hand (mL)", "int"),
        ("discrepancy_count", "Discrepancies", "int"),
        ("turnover", "Turnover", "num"),
    ]
    return columns, rows, False, note


def _report_low_stock(filters, branch_scope):
    """The Low Stock page as a report: every SKU at or below its own
    reorder level (LOW_STOCK_WHERE — bulk never counts), HQ's warehouse
    first, then most critical (furthest below reorder level) first."""
    where, params = "", []
    if branch_scope is not None:
        where += " AND bi.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND bi.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]
    rows = query(
        f"""SELECT b.branch_name, b.is_hq, p.sku, p.item_name, p.variant, p.unit,
                   bi.stock_qty, bi.reorder_level, (bi.reorder_level - bi.stock_qty) AS shortfall
            FROM branch_inventory bi
            JOIN branches b ON bi.branch_id = b.branch_id
            JOIN products p ON bi.sku = p.sku
            WHERE {LOW_STOCK_WHERE} {where}
            ORDER BY b.is_hq DESC, (bi.stock_qty - bi.reorder_level) ASC, p.item_name
            LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        if r.pop("is_hq"):
            r["branch_name"] = f"{r['branch_name']} (HQ)"
    columns = [] if branch_scope is not None else [("branch_name", "Location", "str")]
    columns += [
        ("sku", "SKU", "str"), ("item_name", "Item", "str"),
        ("variant", "Variant", "badge:variant"), ("unit", "Unit", "str"),
        ("stock_qty", "Stock Qty", "int"), ("reorder_level", "Reorder Level", "int"),
        ("shortfall", "Below Reorder By", "int"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now — at or below reorder level"


def _window_where(date_col, filters, params):
    """Range/Month bound for a summary report (one figure per window, not
    "latest N rows"), so Recent/All both read as all time — same as
    Branch Performance."""
    if filters["mode"] in ("range", "month"):
        frag, _, _, _ = _time_window(date_col, filters, params)
        return frag
    return ""


def _report_financial_summary(filters, branch_scope):
    """Dashboard / Reports money figures for the chosen window, one row
    per figure: register sales (HQ + branches) + Closed partner package
    orders = total revenue; cost of goods produced (cogs_logs); gross
    profit = revenue - cost of goods; raw materials bought (for
    comparison — they're costed into goods when produced, not here)."""
    def total(sql, date_col):
        params = []
        frag = _window_where(date_col, filters, params)
        return query(sql + frag, tuple(params), fetchone=True)["v"]

    sales = float(total("SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS v FROM sales WHERE 1=1", "sold_at"))
    packages = float(total(
        "SELECT COALESCE(SUM(order_amount), 0) AS v FROM partner_inquiries WHERE status = 'Closed'", "created_at"))
    package_orders = int(total(
        "SELECT COUNT(*) AS v FROM partner_inquiries WHERE status = 'Closed'", "created_at"))
    cogs = float(total("SELECT COALESCE(SUM(total_cogs), 0) AS v FROM cogs_logs WHERE 1=1", "created_at"))
    materials = float(total(
        "SELECT COALESCE(SUM(package_cost), 0) AS v FROM raw_materials WHERE 1=1", "created_at"))
    revenue = sales + packages
    rows = [
        {"figure": "Register sales (HQ + branches)", "figure_amount": sales},
        {"figure": f"Partner package orders (Closed, {package_orders})", "figure_amount": packages},
        {"figure": "Total revenue", "figure_amount": revenue},
        {"figure": "Cost of goods produced", "figure_amount": cogs},
        {"figure": "Gross profit (revenue − cost of goods)", "figure_amount": revenue - cogs},
        {"figure": "Raw materials purchased (comparison only)", "figure_amount": materials},
    ]
    columns = [("figure", "Figure", "str", 2.5), ("figure_amount", "Amount", "money", 1.0)]
    note = _window_note(filters, False) if filters["mode"] in ("range", "month") else "All time"
    return columns, rows, False, note + " — gross profit excludes rent, payroll and other untracked costs"


def _report_admin_log(filters, branch_scope):
    """The Admin Log page: non-inventory admin actions (accounts,
    products, branches, settings)."""
    where, params = "", []
    if filters["search"]:
        where += " AND (aa.actor_username LIKE %s OR aa.action LIKE %s OR aa.target LIKE %s OR aa.details LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like, like]
    time_where, order, limit_n, truncated = _time_window("aa.created_at", filters, params)
    where += time_where
    rows = query(
        f"""SELECT aa.created_at, aa.actor_username, aa.action, aa.target, aa.details
            FROM admin_actions aa WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["actor_username"] = r["actor_username"] or "—"
        r["action"] = r["action"].replace("_", " ").capitalize()
        r["target"] = r["target"] or "—"
        r["details"] = r["details"] or "—"
    columns = [
        ("created_at", "When", "datetime", 0.9), ("actor_username", "Admin", "str", 0.8),
        ("action", "Action", "str", 1.1), ("target", "Target", "str", 1.1),
        ("details", "Details", "str", 2.0),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_login_activity(filters, branch_scope):
    """The Login Activity page: every sign-in attempt, successful or not."""
    where, params = "", []
    if filters["search"]:
        where += " AND (la.username_attempted LIKE %s OR la.ip_address LIKE %s OR la.failure_reason LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = _time_window("la.created_at", filters, params)
    where += time_where
    rows = query(
        f"""SELECT la.created_at, la.username_attempted, u.username AS current_username,
                   la.role_attempted, la.success, la.failure_reason, la.ip_address
            FROM login_activity la LEFT JOIN users u ON la.user_id = u.user_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        current = r.pop("current_username")
        if current and current != r["username_attempted"]:
            r["username_attempted"] = f"{r['username_attempted']} (now {current})"
        r["result"] = "Success" if r.pop("success") else "Failed"
        r["failure_reason"] = (r["failure_reason"] or "—").replace("_", " ")
        r["ip_address"] = r["ip_address"] or "—"
    columns = [
        ("created_at", "When", "datetime"), ("username_attempted", "Username", "str"),
        ("role_attempted", "Role Tab", "badge:role"), ("result", "Result", "str"),
        ("failure_reason", "Reason", "str"), ("ip_address", "IP Address", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_voided_sales(filters, branch_scope):
    """Sales removed by Void (sale_voids) — never counted in any sales
    figure — with who voided each one and why."""
    where, params = "", []
    if branch_scope is not None:
        where += " AND v.branch_id = %s"
        params.append(branch_scope)
    elif filters["branch_id"] != "all":
        where += " AND v.branch_id = %s"
        params.append(filters["branch_id"])
    if filters["search"]:
        where += " AND (v.item_name LIKE %s OR v.sku LIKE %s OR v.reason LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = _time_window("v.voided_at", filters, params)
    where += time_where
    rows = query(
        f"""SELECT v.voided_at, b.branch_name, v.item_name, v.sku, v.unit, v.sale_type,
                   v.qty_sold, (v.qty_sold * v.unit_price) AS line_total, v.sold_at,
                   COALESCE(v.voided_by_username, '—') AS voided_by, v.reason
            FROM sale_voids v JOIN branches b ON b.branch_id = v.branch_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["line_total"] = float(r["line_total"])
    columns = [] if branch_scope is not None else [("branch_name", "Location", "str")]
    columns = [("voided_at", "Voided", "datetime")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU", "str"), ("unit", "Unit", "str"),
        ("sale_type", "Type", "badge:sale_type"), ("qty_sold", "Qty", "int"),
        ("line_total", "Amount", "money"), ("sold_at", "Originally Sold", "datetime"),
        ("voided_by", "Voided By", "str"), ("reason", "Reason", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


_BUILDERS = {
    "products": _report_products,
    "production_log": _report_production_log,
    "materials": _report_materials,
    "suppliers": _report_suppliers,
    "formulas": _report_formulas,
    "cogs_logs": _report_cogs_logs,
    "bulk_batches": _report_bulk_batches,
    "branch_stock": _report_branch_stock,
    "low_stock": _report_low_stock,
    "stock_requests": _report_stock_requests,
    "inventory_log": _report_inventory_log,
    "sales_history": _report_sales_history,
    "credit_purchases": _report_credit_purchases,
    "voided_sales": _report_voided_sales,
    "customers": _report_customers,
    "discrepancies": _report_discrepancies,
    "branch_performance": _report_branch_performance,
    "accounts": _report_accounts,
    "partners": _report_partners,
    "packages": _report_packages,
    "partner_inquiries": _report_partner_inquiries,
    "financial_summary": _report_financial_summary,
    "admin_log": _report_admin_log,
    "login_activity": _report_login_activity,
}


def get_report(report_type, filters, branch_scope=None, actor_label=""):
    """Build a report dict: title, subtitle, window_note, columns, rows, truncated.

    branch_scope: pass the signed-in branch's branch_id to force every
    query above to that branch and drop the Branch column entirely (used
    by routes/branch.py). Leave None for the admin side, where the
    branch_id filter (or "all branches") from the querystring applies.
    """
    if report_type not in _BUILDERS:
        raise ValueError(f"Unknown report type: {report_type}")

    meta = REPORT_TYPES[report_type]
    label = meta["branch_label"] if (
        branch_scope is not None and "branch_label" in meta) else meta["label"]

    columns, rows, truncated, window_note = _BUILDERS[report_type](
        filters, branch_scope)
    columns = _split_bulk_quantities(columns, rows)

    scoped_branch_name = _branch_name(branch_scope) if branch_scope is not None else (
        _branch_name(filters.get("branch_id"))
        if report_type in BRANCH_FILTERED_TYPES and filters.get("branch_id") not in (None, "all")
        else None
    )
    subtitle_bits = [scoped_branch_name] if scoped_branch_name else []
    if meta["windowed"]:
        subtitle_bits.append(window_note)
    subtitle = " · ".join(subtitle_bits) if subtitle_bits else window_note

    return {
        "report_type": report_type,
        "title": label,
        "subtitle": subtitle,
        "actor_label": actor_label,
        "generated_at": datetime.datetime.now(),
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        # {column_key: summed_value} for every money/int column, or None
        # — see _compute_totals(). Both render_report_pdf() and
        # render_report_excel() add a bold Total row from this when present.
        "totals": _compute_totals(columns, rows),
    }


# ---------------------------------------------------------------- PDF rendering
def _pdf_styles():
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle("brand", parent=base["Normal"], fontName="IBMPlexSans-Bold",
                                fontSize=16, textColor=INK, leading=19),
        "brand_sub": ParagraphStyle("brand_sub", parent=base["Normal"], fontName="IBMPlexSans",
                                    fontSize=8, textColor=INK_FAINT, leading=11),
        "doc_title": ParagraphStyle("doc_title", parent=base["Normal"], fontName="IBMPlexSans-Bold",
                                    fontSize=13, textColor=ACCENT_INK, alignment=TA_RIGHT, leading=16),
        "doc_meta": ParagraphStyle("doc_meta", parent=base["Normal"], fontName="IBMPlexSans",
                                   fontSize=8.5, textColor=INK_FAINT, alignment=TA_RIGHT, leading=12),
        "th": ParagraphStyle("th", parent=base["Normal"], fontName="IBMPlexSans-Bold",
                             fontSize=7.6, textColor=colors.white, leading=10),
        "td": ParagraphStyle("td", parent=base["Normal"], fontName="IBMPlexSans",
                             fontSize=7.6, textColor=INK, leading=10),
        "td_num": ParagraphStyle("td_num", parent=base["Normal"], fontName="IBMPlexSans",
                                 fontSize=7.6, textColor=INK, leading=10, alignment=TA_RIGHT),
        "footer": ParagraphStyle("footer", parent=base["Normal"], fontName="IBMPlexSans",
                                 fontSize=7.3, textColor=INK_FAINT, leading=10),
    }


def _fmt_cell(value, ctype):
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


def render_report_pdf(report):
    """Return a BytesIO PDF for a report dict built by get_report()."""
    s = _pdf_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        topMargin=16 * mm, bottomMargin=14 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
        title=f"{report['title']} Report",
    )
    story = []

    # The three header columns add up to exactly the frame width
    # (doc.width), the same width the data table and rules below use.
    # They used to be fixed at 265mm on a 247mm frame, so the centered
    # header hung past both margins, pushing the logo toward the page edge.
    logo_w = 12 * mm
    brand_w = 100 * mm
    title_w = doc.width - logo_w - brand_w

    header = Table(
        [[
            logo_drawing(30),
            Table([[Paragraph(
                "<font color='#8A6D1F'>Heaven</font> <font color='#5B5445'>&amp;</font> "
                "<font color='#17140D'>Angel</font> Scents", s["brand"])],
                [Paragraph("Perfume Manufacturing &amp; Retail &middot; Inventory System", s["brand_sub"])]],
                colWidths=[brand_w]),
            Table([[Paragraph(report["title"] + " report", s["doc_title"])],
                   [Paragraph(report["subtitle"], s["doc_meta"])],
                   [Paragraph(
                       f"Generated {report['generated_at'].strftime('%b %d, %Y %I:%M %p')}"
                       + (f" by {report['actor_label']}" if report["actor_label"] else ""),
                       s["doc_meta"])]],
                  colWidths=[title_w]),
        ]],
        colWidths=[logo_w, brand_w, title_w],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.2,
                 color=ACCENT, spaceAfter=10))

    # Columns are (key, label, ctype) from most report builders, or
    # (key, label, ctype, weight) from ones that specify custom relative
    # widths (see the width-weight comment further down) — normalize to
    # the 4-tuple shape once here so every loop below can unpack it the
    # same way regardless of which builder produced it.
    columns = [c if len(c) == 4 else (*c, 1) for c in report["columns"]]
    rows = report["rows"]

    if not rows:
        story.append(Spacer(1, 30))
        story.append(
            Paragraph("No data matches the selected filters.", s["footer"]))
    else:
        num_types = ("money", "int", "num")
        header_row = [Paragraph(label, s["th"]) for _, label, _, _ in columns]
        data = [header_row]
        # Collect (row, col, bg_hex) for every badge-kind cell that
        # matched a known value, so the table style below can paint
        # just that cell — same colored-pill look as the web UI,
        # instead of every column rendering as flat black text.
        badge_cells = []
        for r_idx, row in enumerate(rows, start=1):
            cells = []
            for c_idx, (key, _, ctype, _) in enumerate(columns):
                value = row.get(key)
                if ctype.startswith("badge:"):
                    kind = ctype.split(":", 1)[1]
                    badge = _badge_colors(kind, value)
                    text = _fmt_cell(value, "str")
                    if badge:
                        bg_hex, text_hex = badge
                        badge_cells.append((r_idx, c_idx, bg_hex))
                        badge_style = ParagraphStyle(
                            f"badge_{r_idx}_{c_idx}", parent=s["td"],
                            textColor=colors.HexColor(text_hex), fontName="IBMPlexSans-Bold",
                        )
                        cells.append(Paragraph(text, badge_style))
                    else:
                        cells.append(Paragraph(text, s["td"]))
                else:
                    cells.append(Paragraph(
                        _fmt_cell(value, ctype), s["td_num"] if ctype in num_types else s["td"]))
            data.append(cells)

        # ---- totals row ----
        # Bold, right-aligned sums under every money/int column, with a
        # "Total" label in the first column. Tracked separately (rather
        # than reusing s["td"]/s["td_num"]) so it can be bolded without
        # touching the styles ordinary cells use.
        total_row_idx = None
        if report.get("totals"):
            totals = report["totals"]
            total_label_style = ParagraphStyle(
                "total_label", parent=s["td"], fontName="IBMPlexSans-Bold", textColor=ACCENT_INK)
            total_num_style = ParagraphStyle(
                "total_num", parent=s["td_num"], fontName="IBMPlexSans-Bold", textColor=ACCENT_INK)
            total_row = []
            for c_idx, (key, _, ctype, _) in enumerate(columns):
                if c_idx == 0:
                    total_row.append(Paragraph("Total", total_label_style))
                elif key in totals:
                    total_row.append(
                        Paragraph(_fmt_cell(totals[key], ctype), total_num_style))
                else:
                    total_row.append(Paragraph("", s["td"]))
            data.append(total_row)
            total_row_idx = len(data) - 1

        # Columns default to equal width (weight 1 each). A report can
        # give a column a different weight (see e.g. _report_partners /
        # _report_partner_inquiries) when an even split leaves some
        # columns too narrow to hold their content without ugly
        # mid-word wraps (a badge word breaking across two lines, a
        # phone number splitting mid-digit) while others sit mostly
        # empty — narrow columns shrink, columns that need the room
        # (Email, Address, Remarks, ...) grow to take up the slack.
        available_width = doc.width
        n = len(columns)
        weights = [w for _, _, _, w in columns]
        total_weight = sum(weights) or n
        col_widths = [available_width * (w / total_weight) for w in weights]
        table = Table(data, colWidths=col_widths, repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ]
        # Badge backgrounds are appended after ROWBACKGROUNDS so they
        # win for their specific cell — later commands override earlier
        # ones on the same cell in ReportLab's TableStyle.
        for r_idx, c_idx, bg_hex in badge_cells:
            style.append(
                ("BACKGROUND", (c_idx, r_idx), (c_idx, r_idx), colors.HexColor(bg_hex)))
        if total_row_idx is not None:
            style.append(("BACKGROUND", (0, total_row_idx),
                         (-1, total_row_idx), ACCENT_SOFT))
            style.append(("LINEABOVE", (0, total_row_idx),
                         (-1, total_row_idx), 1, ACCENT))
        table.setStyle(TableStyle(style))
        story.append(table)

        story.append(Spacer(1, 8))
        count_note = f"{report['row_count']:,} row{'s' if report['row_count'] != 1 else ''} shown."
        if report["truncated"]:
            count_note += f" Results were capped at {MAX_ROWS:,} rows — narrow the filters for a complete report."
        story.append(Paragraph(count_note, s["footer"]))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5,
                 color=BORDER, spaceAfter=6))
    story.append(Paragraph(
        "Generated from the Heaven &amp; Angel Scents inventory system. Figures reflect the underlying "
        "data at the moment this report was generated.",
        s["footer"],
    ))

    doc.build(story, canvasmaker=NumberedCanvas)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- Excel rendering
def render_report_excel(report):
    """Return a BytesIO .xlsx for a report dict built by get_report()."""
    wb = Workbook()
    ws = wb.active
    ws.title = report["title"][:31] or "Report"

    # Some reports' columns carry a 4th "PDF width weight" element (see
    # render_report_pdf) — irrelevant here since Excel auto-sizes each
    # column from its own content below, so only the first three fields
    # are kept.
    columns = [c[:3] for c in report["columns"]]
    rows = report["rows"]
    n_cols = len(columns)

    # ---- title block ----
    ws.merge_cells(start_row=1, start_column=1,
                   end_row=1, end_column=max(n_cols, 1))
    title_cell = ws.cell(row=1, column=1)
    # Rich text so "Heaven" / "Angel" render in the same two brand
    # colors as the sidebar, login page, and PDF report header,
    # instead of the whole title printing in one flat dark color.
    title_cell.value = CellRichText(
        TextBlock(InlineFont(rFont="Calibri", sz=14,
                  b=True, color="2E5AF0"), "Heaven "),
        TextBlock(InlineFont(rFont="Calibri", sz=14,
                  b=True, color="5B6272"), "& "),
        TextBlock(InlineFont(rFont="Calibri", sz=14,
                  b=True, color="E23A48"), "Angel "),
        TextBlock(InlineFont(rFont="Calibri", sz=14, b=True, color="12141A"),
                  f"Scents — {report['title']} Report"),
    )
    title_cell.fill = XL_TITLE_FILL
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24

    ws.merge_cells(start_row=2, start_column=1,
                   end_row=2, end_column=max(n_cols, 1))
    meta = report["subtitle"]
    meta += f"  ·  Generated {report['generated_at'].strftime('%Y-%m-%d %H:%M')}"
    if report["actor_label"]:
        meta += f" by {report['actor_label']}"
    meta_cell = ws.cell(row=2, column=1, value=meta)
    meta_cell.font = Font(name="Calibri", size=9.5,
                          italic=True, color="5B6272")

    header_row_idx = 4
    if not rows:
        ws.cell(row=header_row_idx, column=1,
                value="No data matches the selected filters.").font = Font(italic=True)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    # ---- header ----
    for c, (_, label, _) in enumerate(columns, start=1):
        cell = ws.cell(row=header_row_idx, column=c, value=label)
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        cell.fill = XL_HEADER_FILL
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True)
        cell.border = XL_BORDER
    ws.row_dimensions[header_row_idx].height = 20

    # ---- data ----
    col_widths = [len(label) for _, label, _ in columns]
    for r_off, row in enumerate(rows):
        r = header_row_idx + 1 + r_off
        for c, (key, _, ctype) in enumerate(columns, start=1):
            value = row.get(key)
            cell = ws.cell(row=r, column=c)
            cell.border = XL_BORDER
            if value is None:
                cell.value = "—"
            elif ctype == "money":
                cell.value = float(value)
                cell.number_format = XL_MONEY_FMT
                cell.alignment = Alignment(horizontal="right")
            elif ctype == "int":
                cell.value = int(value)
                cell.alignment = Alignment(horizontal="right")
            elif ctype == "num":
                # A fractional quantity (see _fmt_cell's own "num"
                # branch) — real number with a number format, not
                # int()'d, so e.g. 2.5 grams isn't silently truncated.
                cell.value = float(value)
                cell.number_format = XL_NUM_FMT
                cell.alignment = Alignment(horizontal="right")
            elif ctype == "datetime" and isinstance(value, (datetime.date, datetime.datetime)):
                cell.value = value
                cell.number_format = XL_DATE_FMT
            elif ctype.startswith("badge:"):
                cell.value = str(value)
                # Same fill/text color as that value's badge on screen
                # (see BADGE_STYLES/BADGE_KIND_MAPS above) — falls back
                # to plain text if the value doesn't map to a badge.
                badge = _badge_colors(ctype.split(":", 1)[1], value)
                if badge:
                    bg_hex, text_hex = badge
                    cell.fill = PatternFill(
                        "solid", fgColor=bg_hex.lstrip("#"))
                    cell.font = Font(
                        name="Calibri", size=10, bold=True, color=text_hex.lstrip("#"))
                    cell.alignment = Alignment(
                        horizontal="center", vertical="center")
            else:
                cell.value = str(value)
            width = len(str(cell.value)) if cell.value is not None else 0
            if width > col_widths[c - 1]:
                col_widths[c - 1] = width

    last_col_letter = get_column_letter(n_cols)
    ws.auto_filter.ref = f"A{header_row_idx}:{last_col_letter}{header_row_idx + len(rows)}"
    ws.freeze_panes = f"A{header_row_idx + 1}"

    # ---- totals row ----
    # Sits right below the data (outside the auto-filter range, since
    # it isn't a data row) — bold, right-aligned sums under every
    # money/int column, with a "TOTAL" label in the first column.
    totals = report.get("totals")
    total_row_idx = None
    if totals:
        total_row_idx = header_row_idx + 1 + len(rows)
        for c, (key, _, ctype) in enumerate(columns, start=1):
            cell = ws.cell(row=total_row_idx, column=c)
            cell.border = XL_BORDER
            cell.font = Font(name="Calibri", size=10,
                             bold=True, color="1D3BC4")
            cell.fill = XL_TITLE_FILL
            if c == 1:
                cell.value = "Total"
                cell.alignment = Alignment(horizontal="left")
            elif key in totals:
                if ctype == "money":
                    cell.value = float(totals[key])
                    cell.number_format = XL_MONEY_FMT
                elif ctype == "num":
                    cell.value = float(totals[key])
                    cell.number_format = XL_NUM_FMT
                else:
                    cell.value = int(totals[key])
                cell.alignment = Alignment(horizontal="right")
            else:
                cell.value = ""
        ws.row_dimensions[total_row_idx].height = 20

    for c, width in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(
            c)].width = min(max(width + 3, 10), 42)

    footer_row = (total_row_idx or header_row_idx + len(rows)) + 2
    note = f"{report['row_count']:,} row(s) shown."
    if report["truncated"]:
        note += f" Results were capped at {MAX_ROWS:,} rows — narrow the filters for a complete report."
    ws.cell(row=footer_row, column=1, value=note).font = Font(
        size=9, italic=True, color="5B6272")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
