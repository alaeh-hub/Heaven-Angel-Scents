"""PDF rendering of a report."""
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from heaven_angel.services.brand_assets import NumberedCanvas, logo_drawing, register_fonts
from heaven_angel.services.reports.constants import MAX_ROWS
from heaven_angel.services.reports.formatting import ACCENT, ACCENT_INK, ACCENT_SOFT, BORDER, INK, INK_FAINT, ROW_ALT, badge_colors, fmt_cell


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
                    badge = badge_colors(kind, value)
                    text = fmt_cell(value, "str")
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
                        fmt_cell(value, ctype), s["td_num"] if ctype in num_types else s["td"]))
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
                        Paragraph(fmt_cell(totals[key], ctype), total_num_style))
                else:
                    total_row.append(Paragraph("", s["td"]))
            data.append(total_row)
            total_row_idx = len(data) - 1

        # Columns default to equal width (weight 1 each). A report can
        # give a column a different weight (see e.g. report_partners /
        # report_partner_inquiries) when an even split leaves some
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
