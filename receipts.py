"""Goods-received receipt (PDF) for a fulfilled stock request.

Why this exists / where it's sourced from:
- The only point in the request lifecycle where the full picture exists
  (requested vs. dispatched vs. received vs. damaged vs. unaccounted) is
  once a branch confirms receipt in routes/branch.py:receive_stock() and
  the request flips to 'Fulfilled'. That route already writes every one
  of those numbers to stock_requests and to stock_movement_logs.
- This module never recomputes or re-derives any of those numbers — it
  only reads them back out of stock_requests and stock_movement_logs
  (movement_type DISPATCH / RECEIPT / DAMAGE / ADJUSTMENT, all tagged
  reference_type='STOCK_REQUEST', reference_id=<request_id>). That keeps
  the receipt physically incapable of drifting from the ledger, since
  it IS the ledger, just formatted.
- Generated on demand as a PDF, in memory, every time it's requested —
  nothing is pre-rendered or stored as a file on disk.

Access control is the caller's job: pass branch_id when called from the
branch side so a branch can only ever pull a receipt for its own
request; admin callers omit it since HQ can see every branch.
"""
import io


from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
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


def _fetch_request(request_id, branch_id=None):
    sql = """SELECT sr.*, b.branch_name, b.location, p.item_name, p.sku, p.variant
              FROM stock_requests sr
              JOIN branches b ON sr.branch_id = b.branch_id
              JOIN products p ON sr.sku = p.sku
              WHERE sr.request_id = %s AND sr.status = 'Fulfilled'"""
    params = [request_id]
    if branch_id is not None:
        sql += " AND sr.branch_id = %s"
        params.append(branch_id)
    return query(sql, tuple(params), fetchone=True)


def _fetch_movements(request_id):
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


def _styles():
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle("brand", parent=base["Normal"], fontName="Helvetica-Bold",
                                fontSize=17, textColor=INK, leading=20),
        "brand_sub": ParagraphStyle("brand_sub", parent=base["Normal"], fontName="Helvetica",
                                    fontSize=8.5, textColor=INK_FAINT, leading=12),
        "doc_title": ParagraphStyle("doc_title", parent=base["Normal"], fontName="Helvetica-Bold",
                                    fontSize=12.5, textColor=ACCENT_INK, alignment=TA_RIGHT, leading=15),
        "doc_meta": ParagraphStyle("doc_meta", parent=base["Normal"], fontName="Helvetica",
                                   fontSize=8.5, textColor=INK_FAINT, alignment=TA_RIGHT, leading=12),
        "label": ParagraphStyle("label", parent=base["Normal"], fontName="Helvetica-Bold",
                                fontSize=7.3, textColor=INK_FAINT, leading=10, spaceAfter=1),
        "value": ParagraphStyle("value", parent=base["Normal"], fontName="Helvetica-Bold",
                                fontSize=10.8, textColor=INK, leading=13),
        "value_soft": ParagraphStyle("value_soft", parent=base["Normal"], fontName="Helvetica",
                                     fontSize=9.5, textColor=INK, leading=12),
        "section": ParagraphStyle("section", parent=base["Normal"], fontName="Helvetica-Bold",
                                  fontSize=9.5, textColor=INK, leading=12, spaceBefore=14, spaceAfter=6),
        "qty_label": ParagraphStyle("qty_label", parent=base["Normal"], fontName="Helvetica",
                                    fontSize=7.5, textColor=INK_FAINT, alignment=TA_CENTER, leading=10),
        "qty_value": ParagraphStyle("qty_value", parent=base["Normal"], fontName="Helvetica-Bold",
                                    fontSize=15, textColor=INK, alignment=TA_CENTER, leading=18),
        "timeline_label": ParagraphStyle("timeline_label", parent=base["Normal"], fontName="Helvetica-Bold",
                                         fontSize=9, textColor=INK, leading=12),
        "timeline_meta": ParagraphStyle("timeline_meta", parent=base["Normal"], fontName="Helvetica",
                                        fontSize=8.5, textColor=INK_FAINT, leading=11),
        "note": ParagraphStyle("note", parent=base["Normal"], fontName="Helvetica",
                               fontSize=8.8, textColor=DANGER, leading=12),
        "footer": ParagraphStyle("footer", parent=base["Normal"], fontName="Helvetica",
                                 fontSize=7.5, textColor=INK_FAINT, leading=10),
        "sig_label": ParagraphStyle("sig_label", parent=base["Normal"], fontName="Helvetica",
                                    fontSize=8, textColor=INK_FAINT, leading=10, spaceBefore=4),
    }


def build_receipt_pdf(request_id, branch_id=None):
    """Return (BytesIO, request_row) for a Fulfilled request.

    Returns (None, None) if the request doesn't exist, isn't Fulfilled
    yet, or — when branch_id is given — doesn't belong to that branch.
    """
    req = _fetch_request(request_id, branch_id)
    if not req:
        return None, None

    movements = _fetch_movements(request_id)
    dispatch_mv = next(
        (m for m in movements if m["movement_type"] == "DISPATCH"), None)
    receipt_mv = next(
        (m for m in movements if m["movement_type"] == "RECEIPT"), None)
    damage_mv = next(
        (m for m in movements if m["movement_type"] == "DAMAGE"), None)
    adjustment_mv = next(
        (m for m in movements if m["movement_type"] == "ADJUSTMENT"), None)

    dispatched_qty = req["dispatched_qty"] or 0
    received_qty = req["received_qty"] or 0
    damaged_qty = req["damaged_qty"] or 0
    shortfall = dispatched_qty - received_qty - damaged_qty

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
                 [Paragraph(f"Request #{request_id}", s["doc_meta"])]],
                colWidths=[75 * mm],
            ),
        ]],
        colWidths=[95 * mm, 75 * mm],
    )
    story.append(header)
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.4,
                 color=ACCENT, spaceAfter=14))

    # ---- Branch / item info grid ----
    def info_cell(label, value):
        return [Paragraph(label.upper(), s["label"]), Paragraph(value, s["value"])]

    info_grid = Table(
        [[
            info_cell("Received by branch", req["branch_name"]),
            info_cell("Location", req["location"] or "—"),
        ], [
            info_cell("Item", f"{req['item_name']} ({req['variant']})"),
            info_cell("SKU", req["sku"]),
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

    # ---- Quantities ----
    story.append(Paragraph("Quantities", s["section"]))

    def qty_cell(label, value, color=INK):
        style = ParagraphStyle(
            "qv_%s" % label, parent=s["qty_value"], textColor=color)
        return [Paragraph(str(value), style), Paragraph(label.upper(), s["qty_label"])]

    qty_row = [
        qty_cell("Requested", req["requested_qty"]),
        qty_cell("Dispatched", dispatched_qty),
        qty_cell("Received", received_qty, color=GOOD),
        qty_cell("Damaged", damaged_qty, color=DANGER if damaged_qty else INK),
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
                f"<b>{shortfall} unit(s)</b> were dispatched but neither received nor reported damaged. "
                "This has been flagged in the movement ledger for HQ follow-up.",
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

    if damage_mv and damage_mv.get("notes"):
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(f"Damage note: {damage_mv['notes']}", s["footer"]))
    if adjustment_mv and adjustment_mv.get("notes"):
        story.append(Spacer(1, 2))
        story.append(
            Paragraph(f"Ledger note: {adjustment_mv['notes']}", s["footer"]))

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
