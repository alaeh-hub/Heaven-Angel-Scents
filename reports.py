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
import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from db import query

# Built-in PDF fonts (Helvetica etc.) only cover Latin-1 and have no glyph
# for the ₱ (Philippine peso) sign — it silently renders as a black "tofu"
# box instead of erroring, which is easy to miss until someone opens the
# PDF. DejaVu Sans does have that glyph, so every style below uses it
# instead. Bundled under fonts/ so this works the same on any machine
# this app runs on, regardless of what fonts happen to be installed
# system-wide.
_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(_FONTS_DIR, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf")))

MAX_ROWS = 1000
RECENT_CHOICES = (20, 50, 100, 200)
STATUS_CHOICES = ("Pending", "In Transit", "Fulfilled", "Rejected")
MOVEMENT_TYPE_CHOICES = ("PRODUCTION", "DISPATCH", "RECEIPT", "SALE", "ADJUSTMENT", "DAMAGE")
VARIANT_CHOICES = ("Male", "Female", "Unisex")
ROLE_CHOICES = ("Admin", "Branch")

INK = colors.HexColor("#1C1B19")
INK_FAINT = colors.HexColor("#6E6A62")
ACCENT = colors.HexColor("#A17A3A")
ACCENT_SOFT = colors.HexColor("#F3EBDB")
BORDER = colors.HexColor("#E7E3D9")
ROW_ALT = colors.HexColor("#FBF9F4")

XL_HEADER_FILL = PatternFill("solid", fgColor="A17A3A")
XL_TITLE_FILL = PatternFill("solid", fgColor="F3EBDB")
XL_BORDER = Border(*(Side(style="thin", color="E7E3D9"),) * 4)
XL_MONEY_FMT = '"₱"#,##0.00'
XL_DATE_FMT = "yyyy-mm-dd hh:mm"

# ---------------------------------------------------------------- registry
# admin / branch: whether that role can generate this report at all.
# windowed: whether a time-window (Recent/Range/All) control applies.
REPORT_TYPES = {
    "product_catalog": {"label": "Product Catalog", "admin": True, "branch": False, "windowed": False},
    "hq_production":   {"label": "HQ Production",   "admin": True, "branch": False, "windowed": True},
    "branch_stock":    {"label": "Branch Stock",     "admin": True, "branch": True,  "windowed": False,
                         "branch_label": "My Inventory"},
    "stock_requests":  {"label": "Stock Requests",   "admin": True, "branch": True,  "windowed": True},
    "movement_logs":   {"label": "Movement Ledger",  "admin": True, "branch": True,  "windowed": True},
    "sales_history":   {"label": "Sales History",    "admin": True, "branch": True,  "windowed": True},
    "accounts":        {"label": "User Accounts",    "admin": True, "branch": False, "windowed": False},
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

    mode = _choice("mode", ("recent", "range", "all"), "recent")
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
        "branch_id": branch_id,
        "status": _choice("status", STATUS_CHOICES, "all"),
        "movement_type": _choice("movement_type", MOVEMENT_TYPE_CHOICES, "all"),
        "variant": _choice("variant", VARIANT_CHOICES, "all"),
        "active_status": _choice("active_status", ("active", "discontinued"), "all"),
        "role": _choice("role", ROLE_CHOICES, "all"),
        "account_status": _choice("account_status", ("active", "inactive"), "all"),
        "low_stock_only": args.get("low_stock_only") == "1",
        "search": (args.get("search") or "").strip()[:100],
    }


def _time_window(date_col, filters, params):
    """Append a time-window WHERE fragment for date_col and return
    (where_fragment, order_by_sql, row_limit, is_capped_all_time).

    params is mutated in place (range mode appends its bound(s)).
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
    else:
        note = "All time"
    if truncated:
        note += f" (capped at the first {MAX_ROWS:,} rows — narrow the filters for a complete report)"
    return note


def _branch_name(branch_id):
    if branch_id in (None, "all"):
        return None
    row = query("SELECT branch_name FROM branches WHERE branch_id = %s", (branch_id,), fetchone=True)
    return row["branch_name"] if row else None


# ---------------------------------------------------------------- per-type builders
def _report_product_catalog(filters, branch_scope):
    where, params = "", []
    if filters["active_status"] != "all":
        where += " AND p.is_active = %s"
        params.append(filters["active_status"] == "active")
    if filters["variant"] != "all":
        where += " AND p.variant = %s"
        params.append(filters["variant"])
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]

    rows = query(
        f"""SELECT p.sku, p.item_name, p.variant, p.price, p.is_active,
                   COALESCE(SUM(bi.stock_qty), 0) AS total_stock
            FROM products p LEFT JOIN branch_inventory bi ON p.sku = bi.sku
            WHERE 1=1 {where}
            GROUP BY p.sku ORDER BY p.item_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["price"] = float(r["price"])
        r["is_active"] = "Active" if r["is_active"] else "Discontinued"

    columns = [
        ("sku", "SKU", "str"), ("item_name", "Item", "str"), ("variant", "Variant", "str"),
        ("price", "HQ Price", "money"), ("total_stock", "Total Stock (all branches)", "int"),
        ("is_active", "Status", "str"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def _report_hq_production(filters, branch_scope):
    where, params = "", []
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s OR pl.batch_code LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = _time_window("pl.produced_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT pl.produced_at, p.sku, p.item_name, pl.batch_code, pl.qty_produced
            FROM production_logs pl JOIN products p ON pl.sku = p.sku
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["batch_code"] = r["batch_code"] or "—"

    columns = [
        ("produced_at", "Produced", "datetime"), ("sku", "SKU", "str"), ("item_name", "Item", "str"),
        ("batch_code", "Batch", "str"), ("qty_produced", "Qty Produced", "int"),
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
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]
    if filters["low_stock_only"]:
        where += " AND bi.stock_qty <= bi.reorder_level"

    rows = query(
        f"""SELECT b.branch_name, p.sku, p.item_name, p.variant, p.price AS hq_price,
                   bi.branch_price, COALESCE(bi.branch_price, p.price) AS effective_price,
                   bi.stock_qty, bi.reorder_level
            FROM branch_inventory bi
            JOIN branches b ON bi.branch_id = b.branch_id
            JOIN products p ON bi.sku = p.sku
            WHERE b.is_hq = FALSE AND p.is_active = TRUE {where}
            ORDER BY b.branch_name, p.item_name LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["hq_price"] = float(r["hq_price"])
        r["effective_price"] = float(r["effective_price"])
        r["branch_price"] = float(r["branch_price"]) if r["branch_price"] is not None else None

    columns = [("branch_name", "Branch", "str")] if branch_scope is None else []
    columns += [
        ("sku", "SKU", "str"), ("item_name", "Item", "str"), ("variant", "Variant", "str"),
        ("hq_price", "HQ Price", "money"), ("effective_price", "Selling Price", "money"),
        ("stock_qty", "Stock Qty", "int"), ("reorder_level", "Reorder Level", "int"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def _report_stock_requests(filters, branch_scope):
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
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]
    time_where, order, limit_n, truncated = _time_window("sr.requested_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT sr.requested_at, b.branch_name, p.item_name, p.sku,
                   sr.requested_qty, sr.dispatched_qty, sr.received_qty, sr.damaged_qty, sr.status
            FROM stock_requests sr
            JOIN branches b ON sr.branch_id = b.branch_id
            JOIN products p ON sr.sku = p.sku
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        for k in ("dispatched_qty", "received_qty", "damaged_qty"):
            r[k] = r[k] or 0

    columns = [] if branch_scope is not None else [("branch_name", "Branch", "str")]
    columns = [("requested_at", "Requested", "datetime")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU", "str"), ("requested_qty", "Requested Qty", "int"),
        ("dispatched_qty", "Dispatched Qty", "int"), ("received_qty", "Received Qty", "int"),
        ("damaged_qty", "Damaged Qty", "int"), ("status", "Status", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, _window_note(filters, truncated)


def _report_movement_logs(filters, branch_scope):
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
    time_where, order, limit_n, truncated = _time_window("sml.created_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT sml.created_at, b.branch_name, p.item_name, p.sku,
                   sml.movement_type, sml.change_qty, sml.notes
            FROM stock_movement_logs sml
            JOIN branches b ON sml.branch_id = b.branch_id
            JOIN products p ON sml.sku = p.sku
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["notes"] = r["notes"] or "—"

    columns = [] if branch_scope is not None else [("branch_name", "Branch", "str")]
    columns = [("created_at", "When", "datetime")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU", "str"), ("movement_type", "Type", "str"),
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
    if filters["search"]:
        where += " AND (p.item_name LIKE %s OR p.sku LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like]
    time_where, order, limit_n, truncated = _time_window("s.sold_at", filters, params)
    where += time_where

    rows = query(
        f"""SELECT s.sold_at, b.branch_name, p.item_name, p.sku, p.variant,
                   s.qty_sold, s.unit_price, (s.qty_sold * s.unit_price) AS line_total
            FROM sales s
            JOIN branches b ON s.branch_id = b.branch_id
            JOIN products p ON s.sku = p.sku
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["unit_price"] = float(r["unit_price"])
        r["line_total"] = float(r["line_total"])

    columns = [] if branch_scope is not None else [("branch_name", "Branch", "str")]
    columns = [("sold_at", "Sold", "datetime")] + columns + [
        ("item_name", "Item", "str"), ("sku", "SKU", "str"), ("variant", "Variant", "str"),
        ("qty_sold", "Qty", "int"), ("unit_price", "Unit Price", "money"), ("line_total", "Total", "money"),
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
        ("username", "Username", "str"), ("role", "Role", "str"), ("branch_name", "Branch", "str"),
        ("is_active", "Status", "str"), ("created_at", "Created", "datetime"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


_BUILDERS = {
    "product_catalog": _report_product_catalog,
    "hq_production": _report_hq_production,
    "branch_stock": _report_branch_stock,
    "stock_requests": _report_stock_requests,
    "movement_logs": _report_movement_logs,
    "sales_history": _report_sales_history,
    "accounts": _report_accounts,
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
    label = meta["branch_label"] if (branch_scope is not None and "branch_label" in meta) else meta["label"]

    columns, rows, truncated, window_note = _BUILDERS[report_type](filters, branch_scope)

    scoped_branch_name = _branch_name(branch_scope) if branch_scope is not None else (
        _branch_name(filters.get("branch_id")) if filters.get("branch_id") not in (None, "all") else None
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
    }


# ---------------------------------------------------------------- PDF rendering
def _pdf_styles():
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle("brand", parent=base["Normal"], fontName="DejaVuSans-Bold",
                                 fontSize=16, textColor=INK, leading=19),
        "brand_sub": ParagraphStyle("brand_sub", parent=base["Normal"], fontName="DejaVuSans",
                                     fontSize=8, textColor=INK_FAINT, leading=11),
        "doc_title": ParagraphStyle("doc_title", parent=base["Normal"], fontName="DejaVuSans-Bold",
                                     fontSize=13, textColor=ACCENT, alignment=TA_RIGHT, leading=16),
        "doc_meta": ParagraphStyle("doc_meta", parent=base["Normal"], fontName="DejaVuSans",
                                    fontSize=8.5, textColor=INK_FAINT, alignment=TA_RIGHT, leading=12),
        "th": ParagraphStyle("th", parent=base["Normal"], fontName="DejaVuSans-Bold",
                              fontSize=7.6, textColor=colors.white, leading=10),
        "td": ParagraphStyle("td", parent=base["Normal"], fontName="DejaVuSans",
                              fontSize=7.6, textColor=INK, leading=10),
        "td_num": ParagraphStyle("td_num", parent=base["Normal"], fontName="DejaVuSans",
                                  fontSize=7.6, textColor=INK, leading=10, alignment=TA_RIGHT),
        "footer": ParagraphStyle("footer", parent=base["Normal"], fontName="DejaVuSans",
                                  fontSize=7.3, textColor=INK_FAINT, leading=10),
    }


def _fmt_cell(value, ctype):
    if value is None:
        return "—"
    if ctype == "money":
        return f"₱{float(value):,.2f}"
    if ctype == "int":
        return f"{int(value):,}"
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

    header = Table(
        [[
            Table([[Paragraph("Heaven <font color='#A17A3A'>&amp;</font> Angel Scents", s["brand"])],
                   [Paragraph("Perfume Manufacturing &amp; Retail &middot; Inventory System", s["brand_sub"])]],
                  colWidths=[130 * mm]),
            Table([[Paragraph(report["title"].upper() + " REPORT", s["doc_title"])],
                   [Paragraph(report["subtitle"], s["doc_meta"])],
                   [Paragraph(
                       f"Generated {report['generated_at'].strftime('%b %d, %Y %I:%M %p')}"
                       + (f" by {report['actor_label']}" if report["actor_label"] else ""),
                       s["doc_meta"])]],
                  colWidths=[135 * mm]),
        ]],
        colWidths=[130 * mm, 135 * mm],
    )
    story.append(header)
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceAfter=10))

    columns = report["columns"]
    rows = report["rows"]

    if not rows:
        story.append(Spacer(1, 30))
        story.append(Paragraph("No data matches the selected filters.", s["footer"]))
    else:
        num_types = ("money", "int")
        header_row = [Paragraph(label, s["th"]) for _, label, _ in columns]
        data = [header_row]
        for row in rows:
            data.append([
                Paragraph(_fmt_cell(row.get(key), ctype), s["td_num"] if ctype in num_types else s["td"])
                for key, _, ctype in columns
            ])

        available_width = doc.width
        n = len(columns)
        base_w = available_width / n
        table = Table(data, colWidths=[base_w] * n, repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#A17A3A")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ]
        table.setStyle(TableStyle(style))
        story.append(table)

        story.append(Spacer(1, 8))
        count_note = f"{report['row_count']:,} row{'s' if report['row_count'] != 1 else ''} shown."
        if report["truncated"]:
            count_note += f" Results were capped at {MAX_ROWS:,} rows — narrow the filters for a complete report."
        story.append(Paragraph(count_note, s["footer"]))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=6))
    story.append(Paragraph(
        "Generated from the Heaven &amp; Angel Scents inventory system. Figures reflect the underlying "
        "data at the moment this report was generated.",
        s["footer"],
    ))

    doc.build(story)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- Excel rendering
def render_report_excel(report):
    """Return a BytesIO .xlsx for a report dict built by get_report()."""
    wb = Workbook()
    ws = wb.active
    ws.title = report["title"][:31] or "Report"

    columns = report["columns"]
    rows = report["rows"]
    n_cols = len(columns)

    # ---- title block ----
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(n_cols, 1))
    title_cell = ws.cell(row=1, column=1, value=f"{report['title']} Report — Heaven & Angel Scents")
    title_cell.font = Font(name="Calibri", size=14, bold=True, color="1C1B19")
    title_cell.fill = XL_TITLE_FILL
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(n_cols, 1))
    meta = report["subtitle"]
    meta += f"  ·  Generated {report['generated_at'].strftime('%Y-%m-%d %H:%M')}"
    if report["actor_label"]:
        meta += f" by {report['actor_label']}"
    meta_cell = ws.cell(row=2, column=1, value=meta)
    meta_cell.font = Font(name="Calibri", size=9.5, italic=True, color="6E6A62")

    header_row_idx = 4
    if not rows:
        ws.cell(row=header_row_idx, column=1, value="No data matches the selected filters.").font = Font(italic=True)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    # ---- header ----
    for c, (_, label, _) in enumerate(columns, start=1):
        cell = ws.cell(row=header_row_idx, column=c, value=label)
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        cell.fill = XL_HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
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
            elif ctype == "datetime" and isinstance(value, (datetime.date, datetime.datetime)):
                cell.value = value
                cell.number_format = XL_DATE_FMT
            else:
                cell.value = str(value)
            width = len(str(cell.value)) if cell.value is not None else 0
            if width > col_widths[c - 1]:
                col_widths[c - 1] = width

    last_col_letter = get_column_letter(n_cols)
    ws.auto_filter.ref = f"A{header_row_idx}:{last_col_letter}{header_row_idx + len(rows)}"
    ws.freeze_panes = f"A{header_row_idx + 1}"

    for c, width in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = min(max(width + 3, 10), 42)

    footer_row = header_row_idx + len(rows) + 2
    note = f"{report['row_count']:,} row(s) shown."
    if report["truncated"]:
        note += f" Results were capped at {MAX_ROWS:,} rows — narrow the filters for a complete report."
    ws.cell(row=footer_row, column=1, value=note).font = Font(size=9, italic=True, color="6E6A62")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
