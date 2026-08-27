"""Goods-received receipt (PDF) for a fulfilled stock request (delivery).

Why this exists / where it's sourced from:
- A stock request is now a *delivery* — one branch-submitted cart that can
  carry any number of different products (see stock_requests as the
  header row, and stock_request_items for the per-product lines: sku,
  requested_qty, unit_price snapshotted at request time, dispatched_qty,
  received_qty, damaged_qty). This module reads both.
- The only point in the request lifecycle where the full picture exists
  for every line (requested vs. dispatched vs. received vs. damaged vs.
  unaccounted) is once a branch confirms receipt in
  routes/branch.py:receive_stock() and the request flips to 'Fulfilled'.
  That route already writes every one of those numbers onto each
  stock_request_items row, and onto stock_movement_logs.
- This module never recomputes or re-derives any of those numbers — it
  only reads them back out of stock_request_items and
  stock_movement_logs (movement_type DISPATCH / RECEIPT / DAMAGE /
  ADJUSTMENT, all tagged reference_type='STOCK_REQUEST',
  reference_id=<request_id>, one row per SKU on the delivery). That
  keeps the receipt physically incapable of drifting from the ledger,
  since it IS the ledger, just formatted.
- Generated on demand as a PDF, in memory, every time it's requested —
  nothing is pre-rendered or stored as a file on disk.

Access control is the caller's job: pass branch_id when called from the
branch side so a branch can only ever pull a receipt for its own
request; admin callers omit it since HQ can see every branch.
"""
import io
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from db import query

INK = colors.HexColor("#12141A")
INK_FAINT = colors.HexColor("#5B6272")
ACCENT = colors.HexColor("#2E5AF0")
ACCENT_INK = colors.HexColor("#1D3BC4")
ACCENT_SOFT = colors.HexColor("#E7ECFE")
BORDER = colors.HexColor("#E5E8EF")
DANGER = colors.HexColor("#E23A48")
DANGER_SOFT = colors.HexColor("#FCE7EA")
GOOD = colors.HexColor("#17975E")

# Vendored copy lives in a root-level fonts/ folder, alongside this file
# (i.e. project_root/fonts/DejaVuSans.ttf). Checked FIRST and is what a
# production deploy should actually be relying on: it travels with the
# app instead of depending on the host OS happening to have the
# fonts-dejavu-core package installed, which is exactly why ₱ was
# rendering as a black box on a server that doesn't have it.
_VENDORED_DEJAVU = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "DejaVuSans.ttf"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "DejaVuSans-Bold.ttf"),
)

# Fallback for machines that don't have the vendored copy yet (e.g. an
# older deploy) but do have the OS package installed.
_DEJAVU_CANDIDATES = [
    _VENDORED_DEJAVU,
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
]


def _register_receipt_fonts():
    """This receipt prints the peso sign (₱) inline in the items table.
    Reportlab's built-in Helvetica only supports WinAnsiEncoding, which
    doesn't include ₱ — it renders as a solid black box instead.

    DejaVu Sans has full Unicode currency-symbol coverage. It's vendored
    directly into static/fonts/ so this doesn't depend on the deploy
    target happening to have fonts-dejavu-core (or any font package) on
    the OS — that's what caused the ₱ symbol to silently degrade to a
    box on a server where the app worked in every other respect. The
    OS-path candidates are kept as a secondary fallback only. If truly
    nothing is found, this quietly falls back to Helvetica — every ₱
    would then render as a box, but the PDF still generates instead of
    raising.
    """
    if "DejaVuSans" in pdfmetrics.getRegisteredFontNames():
        return "DejaVuSans", "DejaVuSans-Bold"
    for regular_path, bold_path in _DEJAVU_CANDIDATES:
        if os.path.exists(regular_path) and os.path.exists(bold_path):
            pdfmetrics.registerFont(TTFont("DejaVuSans", regular_path))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold_path))
            return "DejaVuSans", "DejaVuSans-Bold"
    return "Helvetica", "Helvetica-Bold"


FONT_REGULAR, FONT_BOLD = _register_receipt_fonts()


def _fetch_request(request_id, branch_id=None):
    """The delivery header — branch, delivery number, overall status."""
    sql = """SELECT sr.*, b.branch_name, b.location
              FROM stock_requests sr
              JOIN branches b ON sr.branch_id = b.branch_id
              WHERE sr.request_id = %s AND sr.status = 'Fulfilled'"""
    params = [request_id]
    if branch_id is not None:
        sql += " AND sr.branch_id = %s"
        params.append(branch_id)
    return query(sql, tuple(params), fetchone=True)


def _fetch_items(request_id):
    """Every product on this delivery, in the same shape review_request()
    and receive_stock() already work with."""
    return query(
        """SELECT sri.*, p.item_name, p.variant, p.unit
           FROM stock_request_items sri
           JOIN products p ON sri.sku = p.sku
           WHERE sri.request_id = %s ORDER BY p.item_name""",
        (request_id,),
    )


def _fetch_movements(request_id):
    """Every ledger entry tied to this delivery — one DISPATCH/RECEIPT/
    DAMAGE/ADJUSTMENT row per SKU on it, since each line item is dispatched
    and received as its own stock_movement_logs entry."""
    return query(
        """SELECT sml.*, u.username
           FROM stock_movement_logs sml
           LEFT JOIN users u ON sml.created_by_user_id = u.user_id
           WHERE sml.reference_type = 'STOCK_REQUEST' AND sml.reference_id = %s
           ORDER BY sml.created_at""",
        (request_id,),
    )


def _fmt_dt(dt):
    return dt.strftime("%b %d, %Y  %I:%M %p") if dt else "—"


def _fmt_qty(value):
    return str(value) if value is not None else "—"


def _styles():
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle("brand", parent=base["Normal"], fontName=FONT_BOLD,
                                fontSize=17, textColor=INK, leading=20),
        "brand_sub": ParagraphStyle("brand_sub", parent=base["Normal"], fontName=FONT_REGULAR,
                                    fontSize=8.5, textColor=INK_FAINT, leading=12),
        "doc_title": ParagraphStyle("doc_title", parent=base["Normal"], fontName=FONT_BOLD,
                                    fontSize=12.5, textColor=ACCENT_INK, alignment=TA_RIGHT, leading=15),
        "doc_meta": ParagraphStyle("doc_meta", parent=base["Normal"], fontName=FONT_REGULAR,
                                   fontSize=8.5, textColor=INK_FAINT, alignment=TA_RIGHT, leading=12),
        "label": ParagraphStyle("label", parent=base["Normal"], fontName=FONT_BOLD,
                                fontSize=7.3, textColor=INK_FAINT, leading=10, spaceAfter=1),
        "value": ParagraphStyle("value", parent=base["Normal"], fontName=FONT_BOLD,
                                fontSize=10.8, textColor=INK, leading=13),
        "value_soft": ParagraphStyle("value_soft", parent=base["Normal"], fontName=FONT_REGULAR,
                                     fontSize=9.5, textColor=INK, leading=12),
        "section": ParagraphStyle("section", parent=base["Normal"], fontName=FONT_BOLD,
                                  fontSize=9.5, textColor=INK, leading=12, spaceBefore=14, spaceAfter=6),
        "qty_label": ParagraphStyle("qty_label", parent=base["Normal"], fontName=FONT_REGULAR,
                                    fontSize=7.5, textColor=INK_FAINT, alignment=TA_CENTER, leading=10),
        "qty_value": ParagraphStyle("qty_value", parent=base["Normal"], fontName=FONT_BOLD,
                                    fontSize=15, textColor=INK, alignment=TA_CENTER, leading=18),
        "timeline_label": ParagraphStyle("timeline_label", parent=base["Normal"], fontName=FONT_BOLD,
                                         fontSize=9, textColor=INK, leading=12),
        "timeline_meta": ParagraphStyle("timeline_meta", parent=base["Normal"], fontName=FONT_REGULAR,
                                        fontSize=8.5, textColor=INK_FAINT, leading=11),
        "note": ParagraphStyle("note", parent=base["Normal"], fontName=FONT_REGULAR,
                               fontSize=8.8, textColor=DANGER, leading=12),
        "footer": ParagraphStyle("footer", parent=base["Normal"], fontName=FONT_REGULAR,
                                 fontSize=7.5, textColor=INK_FAINT, leading=10),
        "sig_label": ParagraphStyle("sig_label", parent=base["Normal"], fontName=FONT_REGULAR,
                                    fontSize=8, textColor=INK_FAINT, leading=10, spaceBefore=4),
        "th_cell": ParagraphStyle("th_cell", parent=base["Normal"], fontName=FONT_BOLD,
                                  fontSize=7, textColor=INK_FAINT, leading=9),
        "th_cell_num": ParagraphStyle("th_cell_num", parent=base["Normal"], fontName=FONT_BOLD,
                                      fontSize=7, textColor=INK_FAINT, leading=9, alignment=TA_RIGHT),
        "item_cell": ParagraphStyle("item_cell", parent=base["Normal"], fontName=FONT_BOLD,
                                    fontSize=8.3, textColor=INK, leading=10.5),
        "row_cell": ParagraphStyle("row_cell", parent=base["Normal"], fontName=FONT_REGULAR,
                                   fontSize=8.3, textColor=INK, leading=10.5),
        "row_cell_num": ParagraphStyle("row_cell_num", parent=base["Normal"], fontName=FONT_REGULAR,
                                       fontSize=8.3, textColor=INK, leading=10.5, alignment=TA_RIGHT),
        "row_cell_num_bold": ParagraphStyle("row_cell_num_bold", parent=base["Normal"], fontName=FONT_BOLD,
                                            fontSize=8.3, textColor=INK, leading=10.5, alignment=TA_RIGHT),
    }


def build_receipt_pdf(request_id, branch_id=None):
    """Return (BytesIO, request_row) for a Fulfilled delivery.

    Returns (None, None) if the request doesn't exist, isn't Fulfilled
    yet, or — when branch_id is given — doesn't belong to that branch.
    """
    req = _fetch_request(request_id, branch_id)
    if not req:
        return None, None

    items = _fetch_items(request_id)
    movements = _fetch_movements(request_id)

    # One DISPATCH/RECEIPT row per SKU on the delivery — since they're all
    # written inside the same admin/branch transaction, the first of each
    # type is representative for the shared timeline below. Per-line
    # detail (which item, how much) lives in the items table instead.
    dispatch_mvs = [m for m in movements if m["movement_type"] == "DISPATCH"]
    receipt_mvs = [m for m in movements if m["movement_type"] == "RECEIPT"]
    damage_mvs = [m for m in movements if m["movement_type"] == "DAMAGE"]
    adjustment_mvs = [
        m for m in movements if m["movement_type"] == "ADJUSTMENT"]
    dispatch_mv = dispatch_mvs[0] if dispatch_mvs else None
    receipt_mv = receipt_mvs[0] if receipt_mvs else None

    sku_to_name = {item["sku"]: item["item_name"] for item in items}

    total_requested = sum(item["requested_qty"] for item in items)
    total_dispatched = sum(item["dispatched_qty"] or 0 for item in items)
    total_received = sum(item["received_qty"] or 0 for item in items)
    total_damaged = sum(item["damaged_qty"] or 0 for item in items)
    total_value = sum(item["requested_qty"] *
                      item["unit_price"] for item in items)
    shortfall = total_dispatched - total_received - total_damaged

    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=20 * mm, bottomMargin=16 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
        title=f"Goods Received Receipt GR-{request_id:06d}",
    )
    story = []

    # ---- Letterhead ----
    header = Table(
        [[
            Table(
                [[Paragraph("Heaven <font color='#2E5AF0'>&amp;</font> Angel Scents", s["brand"])],
                 [Paragraph("Perfume Manufacturing &amp; Retail &middot; Inventory System", s["brand_sub"])]],
                colWidths=[95 * mm],
            ),
            Table(
                [[Paragraph("GOODS RECEIVED RECEIPT", s["doc_title"])],
                 [Paragraph(
                     f"Receipt No. GR-{request_id:06d}", s["doc_meta"])],
                 [Paragraph(f"Delivery {req['delivery_number']}", s["doc_meta"])]],
                colWidths=[75 * mm],
            ),
        ]],
        colWidths=[95 * mm, 75 * mm],
    )
    story.append(header)
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.4,
                 color=ACCENT, spaceAfter=14))

    # ---- Branch / delivery info grid ----
    def info_cell(label, value):
        return [Paragraph(label.upper(), s["label"]), Paragraph(value, s["value"])]

    info_grid = Table(
        [[
            info_cell("Received by branch", req["branch_name"]),
            info_cell("Location", req["location"] or "—"),
        ], [
            info_cell("Delivery #", req["delivery_number"]),
            info_cell("Items on this delivery", str(len(items))),
        ]],
        colWidths=[85 * mm, 85 * mm],
        rowHeights=[15 * mm, 15 * mm],
    )
    info_grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.75, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(info_grid)

    # ---- Line items ----
    story.append(Paragraph("Items", s["section"]))

    def th(text, num=False):
        return Paragraph(text.upper(), s["th_cell_num"] if num else s["th_cell"])

    items_head = [
        th("Item"), th("Variant"), th("Unit"),
        th("Req", num=True), th("Disp", num=True), th("Recv", num=True), th("Dmg", num=True),
        th("Unit price", num=True), th("Line total", num=True),
    ]
    items_rows = [items_head]
    for item in items:
        line_total = item["requested_qty"] * item["unit_price"]
        item_cell = Paragraph(
            f"{item['item_name']}<br/><font size=6.8 color='#5B6272'>{item['sku']}</font>",
            s["item_cell"],
        )
        items_rows.append([
            item_cell,
            Paragraph(item["variant"], s["row_cell"]),
            Paragraph(item["unit"], s["row_cell"]),
            Paragraph(str(item["requested_qty"]), s["row_cell_num"]),
            Paragraph(_fmt_qty(item["dispatched_qty"]), s["row_cell_num"]),
            Paragraph(_fmt_qty(item["received_qty"]), s["row_cell_num"]),
            Paragraph(str(item["damaged_qty"]), s["row_cell_num"]),
            Paragraph(f"₱{item['unit_price']:,.2f}", s["row_cell_num"]),
            Paragraph(f"₱{line_total:,.2f}", s["row_cell_num_bold"]),
        ])
    items_rows.append([
        Paragraph("Total", s["row_cell_num_bold"]), "", "", "", "", "", "", "",
        Paragraph(f"₱{total_value:,.2f}", s["row_cell_num_bold"]),
    ])

    items_table = Table(
        items_rows,
        colWidths=[41 * mm, 17 * mm, 14 * mm, 13 * mm,
                  13 * mm, 14 * mm, 12 * mm, 22 * mm, 25 * mm],
    )
    items_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F5F6F9")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F5F6F9")),
        ("SPAN", (0, -1), (7, -1)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        # Header row only: a touch less side padding gives the narrow
        # qty columns (REQ/DISP/RECV/DMG) just enough extra room that
        # their all-caps labels never have to break mid-word.
        ("LEFTPADDING", (0, 0), (-1, 0), 4),
        ("RIGHTPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
    ]))
    story.append(items_table)

    # ---- Quantities (aggregate across every item on the delivery) ----
    story.append(Paragraph("Quantities", s["section"]))

    def qty_cell(label, value, color=INK):
        style = ParagraphStyle(
            "qv_%s" % label, parent=s["qty_value"], textColor=color)
        return [Paragraph(str(value), style), Paragraph(label.upper(), s["qty_label"])]

    qty_row = [
        qty_cell("Requested", total_requested),
        qty_cell("Dispatched", total_dispatched),
        qty_cell("Received", total_received, color=GOOD),
        qty_cell("Damaged", total_damaged,
                 color=DANGER if total_damaged else INK),
        qty_cell("Unaccounted", shortfall,
                 color=DANGER if shortfall > 0 else GOOD),
    ]
    qty_table = Table([qty_row], colWidths=[34 * mm] * 5)
    qty_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.75, BORDER),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(qty_table)

    if shortfall > 0:
        story.append(Spacer(1, 8))
        note_box = Table(
            [[Paragraph(
                f"<b>{shortfall} unit(s)</b> across this delivery were dispatched but neither received nor "
                "reported damaged. This has been flagged in the movement ledger for HQ follow-up.",
                s["note"],
            )]],
            colWidths=[170 * mm],
        )
        note_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), DANGER_SOFT),
            ("BOX", (0, 0), (-1, -1), 0.75, DANGER),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(note_box)

    # ---- Timeline ----
    story.append(Paragraph("Timeline", s["section"]))
    timeline_rows = [
        ("Requested", req["requested_at"], None),
        ("Dispatched by HQ", dispatch_mv["created_at"] if dispatch_mv else None,
         dispatch_mv["username"] if dispatch_mv else None),
        ("Received by branch", receipt_mv["created_at"] if receipt_mv else None,
         receipt_mv["username"] if receipt_mv else None),
    ]
    tl_data = []
    for label, dt, who in timeline_rows:
        meta = _fmt_dt(dt) + (f" &middot; confirmed by {who}" if who else "")
        tl_data.append([Paragraph(label, s["timeline_label"]),
                       Paragraph(meta, s["timeline_meta"])])
    tl_table = Table(tl_data, colWidths=[45 * mm, 125 * mm])
    tl_table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tl_table)

    # Damage/adjustment notes are per line item (one row per SKU in the
    # ledger), so each is prefixed with which product it's about.
    for mv in damage_mvs:
        if mv.get("notes"):
            label = sku_to_name.get(mv["sku"], mv["sku"])
            story.append(Spacer(1, 4))
            story.append(
                Paragraph(f"Damage note ({label}): {mv['notes']}", s["footer"]))
    for mv in adjustment_mvs:
        if mv.get("notes"):
            label = sku_to_name.get(mv["sku"], mv["sku"])
            story.append(Spacer(1, 2))
            story.append(
                Paragraph(f"Ledger note ({label}): {mv['notes']}", s["footer"]))

    # ---- Signatures ----
    story.append(Spacer(1, 26))
    sig_table = Table(
        [[
            Table([[HRFlowable(width=70 * mm, thickness=0.75, color=INK_FAINT)],
                   [Paragraph("Received by (Branch Staff)", s["sig_label"])]]),
            Table([[HRFlowable(width=70 * mm, thickness=0.75, color=INK_FAINT)],
                   [Paragraph("Verified by (HQ Admin)", s["sig_label"])]]),
        ]],
        colWidths=[85 * mm, 85 * mm],
    )
    story.append(sig_table)

    # ---- Footer ----
    story.append(Spacer(1, 22))
    story.append(HRFlowable(width="100%", thickness=0.5,
                 color=BORDER, spaceAfter=6))
    story.append(Paragraph(
        "Generated from the Heaven &amp; Angel Scents inventory system. Figures reflect the stock "
        "movement ledger recorded at the time this shipment was confirmed received and are not "
        "editable after the fact.",
        s["footer"],
    ))

    doc.build(story)
    buf.seek(0)
    return buf, req
