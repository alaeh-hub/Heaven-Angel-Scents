"""Excel (.xlsx) rendering of a report."""
import datetime
import io
import os

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from heaven_angel.services.reports.constants import MAX_ROWS
from heaven_angel.services.reports.formatting import badge_colors



# Brand gold, same hexes as the PDF palette above (ACCENT / ACCENT_SOFT /
# BORDER). Also used by the sales-import template (sales_import.py).
XL_HEADER_FILL = PatternFill("solid", fgColor="C9A227")


XL_TITLE_FILL = PatternFill("solid", fgColor="FBF1D6")


XL_BORDER = Border(bottom=Side(style="thin", color="E9E0C9"))


XL_MONEY_FMT = '"₱"#,##0.00'


XL_DATE_FMT = "mmm dd, yyyy hh:mm AM/PM"


# Up to 3 decimals, trailing zeros trimmed by Excel's own "#" placeholders
# (2.500 shows as "2.5", 10.000 shows as "10") — for fractional quantity
# columns (see the "num" column type), which range from whole numbers up
# to schema's DECIMAL(10,3)/DECIMAL(10,4) precision.
XL_NUM_FMT = "#,##0.###"


# ---------------------------------------------------------------- Excel rendering
# The workbook mirrors the PDF: black letterhead band carrying the
# wordmark logo with the report title / subtitle / "generated" lines on
# the right, a gold rule, a gold header row, zebra-striped rows, a
# soft-gold Total row, and the same closing footer text.
XL_BAND_FILL = PatternFill("solid", fgColor="0C0B0A")   # matches the logo's own dark


XL_RULE_FILL = PatternFill("solid", fgColor="C9A227")


XL_ALT_FILL = PatternFill("solid", fgColor="F5F0E1")


XL_LOGO_W, XL_LOGO_H = 190, 107          # px, the wordmark's 16:9 shape
_logo_png = None


def _logo_png_bytes():
    """The wordmark (static/img/new-wordmark.png is 1672px wide —
    far too heavy to embed as-is) shrunk once to a crisp 2x size."""
    global _logo_png
    if _logo_png is None:
        from PIL import Image as PILImage
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                            "static", "img", "new-wordmark.png")
        with PILImage.open(path) as im:
            im = im.convert("RGBA")
            im.thumbnail((XL_LOGO_W * 2, XL_LOGO_H * 2), PILImage.LANCZOS)
            out = io.BytesIO()
            im.save(out, "PNG", optimize=True)
            _logo_png = out.getvalue()
    return _logo_png


def render_report_excel(report):
    """Return a BytesIO .xlsx for a report dict built by get_report()."""
    wb = Workbook()
    ws = wb.active
    ws.title = report["title"][:31] or "Report"
    ws.sheet_view.showGridLines = False

    # Some reports' columns carry a 4th "PDF width weight" element (see
    # render_report_pdf) — irrelevant here since Excel auto-sizes each
    # column from its own content below, so only the first three fields
    # are kept.
    columns = [c[:3] for c in report["columns"]]
    rows = report["rows"]
    n_cols = max(len(columns), 1)

    # ---- letterhead band (row 1) + gold rule (row 2) ----
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n_cols)
    for c in range(1, n_cols + 1):
        ws.cell(row=1, column=c).fill = XL_BAND_FILL
        ws.cell(row=2, column=c).fill = XL_RULE_FILL
    ws.row_dimensions[1].height = 84
    ws.row_dimensions[2].height = 3

    generated = f"Generated {report['generated_at'].strftime('%b %d, %Y %I:%M %p')}"
    if report["actor_label"]:
        generated += f" by {report['actor_label']}"
    head = ws.cell(row=1, column=1)
    head.value = CellRichText(
        TextBlock(InlineFont(rFont="Calibri", sz=15, b=True, color="E8C766"), f"{report['title']} report\n"),
        TextBlock(InlineFont(rFont="Calibri", sz=10, color="CFC6AE"), f"{report['subtitle']}\n"),
        TextBlock(InlineFont(rFont="Calibri", sz=10, color="9C937B"), generated),
    )
    head.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True, indent=1)

    logo = XLImage(io.BytesIO(_logo_png_bytes()))
    logo.width, logo.height = XL_LOGO_W, XL_LOGO_H
    ws.add_image(logo, "A1")

    header_row_idx = 4
    if not rows:
        ws.cell(row=header_row_idx, column=1,
                value="No data matches the selected filters.").font = Font(italic=True, color="5B5445")
        # Wide enough that the title text never runs under the logo.
        for c in range(1, n_cols + 1):
            ws.column_dimensions[get_column_letter(c)].width = 28
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    ink = "17140D"

    # ---- header ----
    for c, (_, label, _) in enumerate(columns, start=1):
        cell = ws.cell(row=header_row_idx, column=c, value=label)
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        cell.fill = XL_HEADER_FILL
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[header_row_idx].height = 22

    # ---- data ----
    col_widths = [len(label) for _, label, _ in columns]
    for r_off, row in enumerate(rows):
        r = header_row_idx + 1 + r_off
        zebra = XL_ALT_FILL if r_off % 2 else None
        for c, (key, _, ctype) in enumerate(columns, start=1):
            value = row.get(key)
            cell = ws.cell(row=r, column=c)
            cell.border = XL_BORDER
            cell.font = Font(name="Calibri", size=10, color=ink)
            if zebra:
                cell.fill = zebra
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
                # A fractional quantity (see fmt_cell's own "num"
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
                # and in the PDF — plain text if the value has no badge.
                badge = badge_colors(ctype.split(":", 1)[1], value)
                if badge:
                    bg_hex, text_hex = badge
                    cell.fill = PatternFill("solid", fgColor=bg_hex.lstrip("#"))
                    cell.font = Font(name="Calibri", size=10, bold=True, color=text_hex.lstrip("#"))
                    cell.alignment = Alignment(horizontal="center", vertical="center")
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
    # it isn't a data row) — bold sums under every money/int column,
    # with a "Total" label in the first column.
    totals = report.get("totals")
    total_row_idx = None
    if totals:
        total_row_idx = header_row_idx + 1 + len(rows)
        gold_line = Border(top=Side(style="medium", color="C9A227"), bottom=Side(style="thin", color="E9E0C9"))
        for c, (key, _, ctype) in enumerate(columns, start=1):
            cell = ws.cell(row=total_row_idx, column=c)
            cell.border = gold_line
            cell.font = Font(name="Calibri", size=10, bold=True, color="8A6D1F")
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

    widths = [min(max(w + 3, 10), 42) for w in col_widths]
    # The title text is right-aligned in the band; keep the band wide
    # enough that it can never run underneath the logo on the left.
    shortfall = 70 - sum(widths)
    if shortfall > 0:
        widths[-1] += shortfall
    for c, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = width

    footer_row = (total_row_idx or header_row_idx + len(rows)) + 2
    note = f"{report['row_count']:,} row{'s' if report['row_count'] != 1 else ''} shown."
    if report["truncated"]:
        note += f" Results were capped at {MAX_ROWS:,} rows — narrow the filters for a complete report."
    ws.cell(row=footer_row, column=1, value=note).font = Font(
        name="Calibri", size=9, italic=True, color="5B5445")
    ws.cell(row=footer_row + 1, column=1, value=(
        "Generated from the Heaven & Angel Scents inventory system. Figures reflect the underlying "
        "data at the moment this report was generated.")).font = Font(
        name="Calibri", size=9, color="5B5445")

    # Same landscape letter page the PDF uses, one page wide.
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
