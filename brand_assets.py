"""Shared vector letterhead mark for generated PDFs (reports.py,
receipts.py) — a reportlab redraw of static/img/icon.svg so every PDF
the app produces carries the same icon as the web UI's tab favicon and
the sidebar's collapsed-rail mark, instead of a text-only wordmark.

Kept out of utils.py deliberately: utils.py is imported by every route
blueprint for its form-parsing helpers, and there's no reason to pull
reportlab's graphics stack into that import path just for the two
modules (reports.py, receipts.py) that render PDFs.

Why redrawn instead of rendered from the SVG file: reportlab has no
built-in SVG loader, and pulling in a whole extra dependency (svglib)
just to render one small static icon isn't worth it. Unlike the old
halo/drop mark this replaced (which needed several curve-approximating
shapes to reproduce), icon.svg is simple enough to redraw exactly:
  - the rounded-square background -> Rect(rx=...), gold stroke instead
    of the SVG's gold-gradient border (reportlab shapes have no easy
    gradient stroke; a flat gold reads the same at letterhead/receipt
    sizes) — same simplification the old mark already made for its own
    solid-white drop against the SVG's subtle background gradient.
  - the "&" -> a single String in Times-BoldItalic, a standard PDF
    base-14 font (always available, no embedding needed) that's a
    close match for the SVG's own bold italic Georgia/Times glyph —
    far simpler than approximating the ampersand's curves as shapes.

Coordinates are chosen directly in reportlab's own bottom-up 96x96
space (no SVG y-flip needed, unlike the old mark — this wasn't
translated from the SVG's coordinates, just designed to match its
look), scaled uniformly at draw time via transform=[size/96, ...].
"""
import os

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

BRAND_BLACK = "#1C170D"
GOLD = "#D4AF37"
FOOTER_INK = colors.HexColor("#5B5445")

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

    # Rounded-square background — 1.5pt inset, rx=21, stroke-width=2.25:
    # the same proportions (scaled from a 64-unit viewBox to this one's
    # 96) as icon.svg's own background rect.
    d.add(Rect(1.5, 1.5, 93, 93, rx=21, ry=21,
          fillColor=BRAND_BLACK, strokeColor=GOLD, strokeWidth=2.25))

    # The "&" itself. y=21 (String's y is its baseline, not a center)
    # was picked by eye against renders at the actual sizes this is
    # used at (26-40pt in reports.py/receipts.py) — reportlab has no
    # built-in way to ask a font for a glyph's true visual bounding
    # box, so there's no formula to derive it from instead.
    d.add(String(48, 21, "&", fontName="Times-BoldItalic", fontSize=63,
          fillColor=GOLD, textAnchor="middle"))

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
        page_w, _ = self.pagesize
        font, _ = register_fonts()
        self.setFont(font, 7.3)
        self.setFillColor(FOOTER_INK)
        self.drawRightString(
            page_w - 16 * mm, 10 * mm,
            f"Page {self._pageNumber} of {total_pages}",
        )
