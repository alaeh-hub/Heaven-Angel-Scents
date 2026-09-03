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

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from db import query
from utils import make_receipt_code

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


def _fetch_sale(sale_id, branch_id=None):
    """A single recorded sale/refill, with the product, branch, and
    (if it was a Salary Deduction) the employee name already joined in.
    """
    sql = """SELECT s.*, p.item_name, p.variant, p.unit, b.branch_name, b.location,
                    COALESCE(s.buyer_name, bu.username) AS buyer_username
             FROM sales s
             JOIN products p ON s.sku = p.sku
             JOIN branches b ON s.branch_id = b.branch_id
             LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
             WHERE s.sale_id = %s"""
    params = [sale_id]
    if branch_id is not None:
        sql += " AND s.branch_id = %s"
        params.append(branch_id)
    return query(sql, tuple(params), fetchone=True)


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


# ---------------------------------------------------------------------------
# Sales receipt — a single Sale or Refill, printable right after Record
# Sale saves it. Unlike the goods-received receipt above, a sale has no
# multi-step lifecycle to wait on (dispatch -> receive): it's complete
# the instant it's inserted, so this can be generated immediately, same
# request-response cycle as the redirect back to Record Sale.
#
# This is deliberately shaped like a small thermal-printer slip (80mm
# roll width) instead of a full letter-size page — one item per sale
# means there's rarely more than a dozen lines to show, and a receipt
# this size is what a customer actually expects to be handed. Height
# isn't fixed up front and can't just be trimmed after the fact either
# (shrinking a PDF's page size after drawing on it would clip content
# near the old top right off the new, shorter page instead of moving it
# down). Instead this renders in two passes: a "dry" pass runs the exact
# same layout with a no-op canvas purely to measure how much vertical
# space the content needs, then a real pass draws it for real onto a
# canvas already sized to fit — so a Refill note or a long customer
# address grows the receipt instead of leaving blank space or clipping.
#
# Every receipt carries a QR code encoding a short signed verification
# code (see utils.make_receipt_code()) — not the sale's data itself,
# just a code that /scan/verify (routes/scan.py) can turn back into
# this exact sale. That's what lets Scan Receipt confirm a printed
# receipt is genuinely on file and pull up what was recorded, without
# ever trusting raw QR content on its own.
#
# Access control is the caller's job, same convention as
# build_receipt_pdf(): pass branch_id from the branch side so a branch
# can only ever pull a receipt for a sale recorded at its own branch;
# admin callers omit it since HQ can see every branch (including its
# own HQ/Main Branch sales, and — if ever needed — any branch's).
# ---------------------------------------------------------------------------
RECEIPT_WIDTH = 80 * mm
RECEIPT_MARGIN = 4.5 * mm
RECEIPT_CONTENT_WIDTH = RECEIPT_WIDTH - (2 * RECEIPT_MARGIN)
RECEIPT_TOP_MARGIN = 7 * mm
RECEIPT_BOTTOM_MARGIN = 7 * mm


def _qr_drawing(data, size):
    """A reportlab Drawing containing a QR code encoding `data`, scaled
    to exactly `size` x `size` points so it can be placed like any other
    flowable/graphic regardless of how many modules the code itself has.
    """
    widget = QrCodeWidget(data)
    x0, y0, x1, y1 = widget.getBounds()
    native_w, native_h = (x1 - x0), (y1 - y0)
    drawing = Drawing(size, size, transform=[size / native_w, 0, 0, size / native_h, 0, 0])
    drawing.add(widget)
    return drawing


def _render_sale_receipt(c, sale, receipt_code, top_y, dry=False):
    """Draw the receipt body top-down starting at `top_y`, and return the
    total vertical space it used. When dry=True, `c` is ignored (pass
    None) and nothing is actually drawn — only the layout's height is
    computed, by running every line through the exact same advance()
    calls a real draw would make.
    """
    line_total = sale["qty_sold"] * sale["unit_price"]
    is_refill = sale["sale_type"] == "Refill"

    x_left = RECEIPT_MARGIN
    x_right = RECEIPT_WIDTH - RECEIPT_MARGIN
    x_center = RECEIPT_WIDTH / 2
    state = {"y": top_y}

    def advance(pt):
        state["y"] -= pt

    def center(text, font=FONT_BOLD, size=10.5, color=INK, gap=None):
        if not dry:
            c.setFont(font, size)
            c.setFillColor(color)
            c.drawCentredString(x_center, state["y"], text)
        advance(gap if gap is not None else size + 4.5)

    def rule(char="*", size=7.5, gap=None):
        if not dry:
            c.setFont(FONT_REGULAR, size)
            c.setFillColor(INK_FAINT)
            cell = size * 0.62
            n = max(3, int(RECEIPT_CONTENT_WIDTH / (cell * 2)))
            c.drawCentredString(x_center, state["y"], (char + " ") * n)
        advance(gap if gap is not None else size + 5)

    def kv(label, value, size=7.8, gap=None):
        if not dry:
            c.setFont(FONT_REGULAR, size)
            c.setFillColor(INK_FAINT)
            c.drawString(x_left, state["y"], label)
            c.setFont(FONT_BOLD, size)
            c.setFillColor(INK)
            c.drawRightString(x_right, state["y"], value)
        advance(gap if gap is not None else size + 6.5)

    def note(text, font=FONT_REGULAR, size=7.2, color=INK_FAINT, gap=None):
        if not dry:
            c.setFont(font, size)
            c.setFillColor(color)
            c.drawCentredString(x_center, state["y"], text)
        advance(gap if gap is not None else size + 4)

    def wrapped_left(label, text, size=7.2, gap=None):
        if not dry:
            c.setFont(FONT_BOLD, size)
            c.setFillColor(INK_FAINT)
            c.drawString(x_left, state["y"], label)
        advance(size + 3.5)
        for wrapped_line in simpleSplit(text, FONT_REGULAR, size, RECEIPT_CONTENT_WIDTH):
            if not dry:
                c.setFont(FONT_REGULAR, size)
                c.setFillColor(INK)
                c.drawString(x_left, state["y"], wrapped_line)
            advance(size + 3)
        advance(gap if gap is not None else 2)

    # ---- Letterhead ----
    center("HEAVEN & ANGEL SCENTS", font=FONT_BOLD, size=12, gap=15)
    center("Perfume Manufacturing & Retail", size=7.2, gap=9)
    if sale.get("location"):
        center(sale["location"], size=7.2, gap=9)
    advance(5)
    rule(gap=13)
    center("REFILL RECEIPT" if is_refill else "SALES RECEIPT", size=10, gap=13)
    rule(gap=13)

    # ---- Sale meta ----
    kv("Receipt No.", f"SR-{sale['sale_id']:06d}")
    kv("Date", _fmt_dt(sale["sold_at"]))
    kv("Branch", sale["branch_name"])
    kv("Sale type", sale["sale_type"])
    payment_value = sale["payment_method"]
    if sale["payment_method"] == "Salary Deduction" and sale["buyer_username"]:
        payment_value += f" ({sale['buyer_username']})"
    kv("Payment", payment_value)
    kv("Customer", sale["customer_name"] or "Walk-in", gap=10)
    rule(char="-", gap=12)

    # ---- Line item ----
    if not dry:
        c.setFont(FONT_BOLD, 8.4)
        c.setFillColor(INK)
        c.drawString(x_left, state["y"], sale["item_name"])
    advance(10.5)
    if not dry:
        c.setFont(FONT_REGULAR, 7)
        c.setFillColor(INK_FAINT)
        c.drawString(x_left, state["y"], f"{sale['sku']} \u00b7 {sale['variant']} \u00b7 {sale['unit']}")
    advance(11.5)
    if not dry:
        c.setFont(FONT_REGULAR, 8.2)
        c.setFillColor(INK)
        c.drawString(x_left, state["y"], f"{sale['qty_sold']} x \u20b1{sale['unit_price']:,.2f}")
        c.setFont(FONT_BOLD, 8.4)
        c.drawRightString(x_right, state["y"], f"\u20b1{line_total:,.2f}")
    advance(13)

    if sale["customer_address"]:
        wrapped_left("ADDRESS", sale["customer_address"], gap=4)

    rule(char="-", gap=12)
    if not dry:
        c.setFont(FONT_BOLD, 11)
        c.setFillColor(INK)
        c.drawString(x_left, state["y"], "TOTAL")
        c.drawRightString(x_right, state["y"], f"\u20b1{line_total:,.2f}")
    advance(16)
    rule(gap=14)

    if is_refill:
        note("Refill \u2014 customer's own bottle;", gap=9)
        note("product cost only, no stock unit deducted.", gap=13)

    # ---- QR verification ----
    qr_size = 30 * mm
    advance(4)
    if not dry:
        qr_drawing = _qr_drawing(receipt_code, qr_size)
        renderPDF.draw(qr_drawing, c, x_center - (qr_size / 2), state["y"] - qr_size)
    advance(qr_size + 8)
    note("Scan to verify this receipt", size=6.8, gap=10)
    if not dry:
        c.setFont(FONT_REGULAR, 6.8)
        c.setFillColor(INK_FAINT)
        c.drawCentredString(x_center, state["y"], receipt_code)
    advance(14)

    rule(gap=12)
    note("Thank you for your purchase!", font=FONT_BOLD, size=8.2, color=INK, gap=11)
    note("Generated by the Heaven & Angel Scents", size=6.5, gap=8.5)
    note("inventory system.", size=6.5, gap=10)

    return top_y - state["y"]


def build_sale_receipt_pdf(sale_id, branch_id=None):
    """Return (BytesIO, sale_row) for a single recorded sale/refill.

    Returns (None, None) if the sale doesn't exist, or — when branch_id
    is given — didn't happen at that branch.
    """
    sale = _fetch_sale(sale_id, branch_id)
    if not sale:
        return None, None

    receipt_code = make_receipt_code(sale_id)

    # Pass 1 (dry): run the identical layout with no canvas, purely to
    # find out how tall this particular receipt needs to be.
    content_height = _render_sale_receipt(None, sale, receipt_code, top_y=0, dry=True)
    page_height = content_height + RECEIPT_TOP_MARGIN + RECEIPT_BOTTOM_MARGIN

    # Pass 2 (real): draw for real onto a canvas already sized to fit —
    # nothing clipped, nothing left blank underneath.
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(RECEIPT_WIDTH, page_height))
    c.setTitle(f"Sales Receipt SR-{sale_id:06d}")
    _render_sale_receipt(c, sale, receipt_code, top_y=page_height - RECEIPT_TOP_MARGIN, dry=False)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf, sale
