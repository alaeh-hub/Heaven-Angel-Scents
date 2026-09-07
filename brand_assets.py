"""Shared vector letterhead mark for generated PDFs (reports.py,
receipts.py) — a reportlab redraw of static/img/logo-c-halo-drop.svg so
every PDF the app produces carries the same icon as the web UI's
sidebar/login logo and favicon, instead of a text-only wordmark.

Kept out of utils.py deliberately: utils.py is imported by every route
blueprint for its form-parsing helpers, and there's no reason to pull
reportlab's graphics stack into that import path just for the two
modules (reports.py, receipts.py) that render PDFs.

Why redrawn instead of rendered from the SVG file: reportlab has no
built-in SVG loader, and pulling in a whole extra dependency (svglib)
just to render one small static icon isn't worth it. The SVG's three
shapes translate directly to reportlab primitives:
  - the rounded-square background -> Rect(rx=...)
  - the halo ring -> Ellipse (stroke only, no fill)
  - the drop -> reportlab has no elliptical-arc primitive, so the
    original path's short bezier taper (from the tip down to each
    "shoulder") is approximated with a straight-sided Polygon, capped
    by a true Circle for the rounded bottom half. Both are solid white
    and overlap exactly at the circle's equator, so the seam between
    them is invisible — the combined silhouette reads as one teardrop,
    indistinguishable from the bezier original at letterhead/receipt
    icon sizes (24-40pt).

SVG coordinates run top-down (y grows downward) inside a 96x96 viewBox;
reportlab's canvas runs bottom-up. Every y below is pre-flipped
(96 - svg_y) so the shapes read correctly without a coordinate
transform at draw time — only uniform scaling (size / 96) is needed.
"""
import os

from reportlab.graphics.shapes import Circle, Drawing, Ellipse, Polygon, Rect
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

NAVY = "#1B2A63"
WHITE = "#FFFFFF"
FOOTER_INK = colors.HexColor("#5B6272")

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def register_fonts():
    """Register the vendored IBM Plex Sans (regular + bold) under the
    names "IBMPlexSans" / "IBMPlexSans-Bold", and return that pair.

    The single implementation for all three PDF-generating modules
    (reports.py, receipts.py, and this one's own NumberedCanvas) —
    previously each had its own copy of this registration logic, which
    meant the font files/names/directory could only be changed by
    editing three places in sync. Idempotent and safe to call from
    every module's import time: pdfmetrics is a process-wide registry,
    so whichever module runs first does the real work and the rest just
    get the already-registered names back immediately.

    Built-in PDF fonts (Helvetica etc.) only cover Latin-1 and have no
    glyph for the peso sign (₱) — it silently renders as a black "tofu"
    box instead of erroring. IBM Plex Sans does have that glyph, and
    matches the IBM Plex Mono already used for SKU/mono styling
    elsewhere in the app. Vendored into fonts/ so this doesn't depend on
    the deploy target happening to have it installed at the OS level.
    If the vendored files are ever missing, this quietly falls back to
    Helvetica — every peso sign would then render as a box, but the PDF
    still generates instead of raising.
    """
    if "IBMPlexSans" in pdfmetrics.getRegisteredFontNames():
        return "IBMPlexSans", "IBMPlexSans-Bold"
    regular = os.path.join(_FONTS_DIR, "IBMPlexSans-Regular.ttf")
    bold = os.path.join(_FONTS_DIR, "IBMPlexSans-Bold.ttf")
    if os.path.exists(regular) and os.path.exists(bold):
        pdfmetrics.registerFont(TTFont("IBMPlexSans", regular))
        pdfmetrics.registerFont(TTFont("IBMPlexSans-Bold", bold))
        return "IBMPlexSans", "IBMPlexSans-Bold"
    return "Helvetica", "Helvetica-Bold"


def logo_drawing(size):
    """A reportlab Drawing of the brand mark, scaled to size x size pt.
    Drop into any Table cell or draw directly onto a canvas like any
    other flowable/graphic."""
    d = Drawing(size, size, transform=[size / 96, 0, 0, size / 96, 0, 0])

    d.add(Rect(0, 0, 96, 96, rx=22, ry=22, fillColor=NAVY, strokeColor=None))

    # Halo ring — svg ellipse cx=48 cy=33 rx=13.5 ry=5.6, y flipped: 96-33=63
    d.add(Ellipse(48, 63, 13.5, 5.6, fillColor=None, strokeColor=WHITE, strokeWidth=3.2))

    # Drop, rounded bottom — svg circle-ish center (48, 63.1) r=12.2, flipped: 96-63.1=32.9
    d.add(Circle(48, 32.9, 12.2, fillColor=WHITE, strokeColor=None))

    # Drop, pointed top — svg tip (48, 41.5) and shoulders (60.2/35.8, 63.1),
    # flipped: tip (48, 54.5), shoulders at y=32.9 (the circle's equator).
    d.add(Polygon([48, 54.5, 60.2, 32.9, 35.8, 32.9], fillColor=WHITE, strokeColor=None))

    return d


class NumberedCanvas(pdfcanvas.Canvas):
    """A canvas that stamps "Page X of Y" bottom-right on every page.

    SimpleDocTemplate flowables only ever know the *current* page while
    drawing it — the total page count doesn't exist yet, since later
    pages haven't been laid out. The standard reportlab workaround (used
    here) is to defer every page's actual render until save(): showPage()
    just snapshots the canvas's state and starts a fresh page instead of
    finalizing anything, and save() replays each snapshot once, by which
    point len(snapshots) is the true total — so it can stamp "Page N of
    total" on every one of them before finally calling the real showPage.

    Pass as SimpleDocTemplate(..., canvasmaker=NumberedCanvas). Used by
    both reports.py (landscape) and receipts.py's goods-received receipt
    (portrait) — margins differ, but the footer position is computed from
    each page's own size/margin at draw time, so one class serves both.
    """

    def __init__(self, *args, **kwargs):
        pdfcanvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []
        register_fonts()

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            if total_pages > 1:
                self._draw_page_number(total_pages)
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)

    def _draw_page_number(self, total_pages):
        page_w, _ = self._pagesize
        font, _ = register_fonts()
        self.setFont(font, 7.3)
        self.setFillColor(FOOTER_INK)
        self.drawRightString(
            page_w - 16 * mm, 10 * mm,
            f"Page {self._pageNumber} of {total_pages}",
        )
