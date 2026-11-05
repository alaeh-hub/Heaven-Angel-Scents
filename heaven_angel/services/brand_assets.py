"""Shared letterhead mark for generated PDFs (reports.py, receipts.py):
the brand icon (static/img/apple-touch-icon.png, cut from the new
icon.png) so every PDF carries the same icon as the web UI's tab favicon
and the sidebar's collapsed-rail mark, instead of a text-only wordmark.

Kept out of utils.py deliberately: utils.py is imported by every route
blueprint for its form-parsing helpers, and there's no reason to pull
reportlab's graphics stack into that import path just for the two
modules (reports.py, receipts.py) that render PDFs.
"""
import os

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import Image

FOOTER_INK = colors.HexColor("#5B5445")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ICON_PATH = os.path.join(_ROOT, "static", "img", "apple-touch-icon.png")
_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "fonts")


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
    """The brand icon as a size x size pt flowable. Drop into any Table
    cell or draw directly onto a canvas like any other flowable. The
    180px source is ~400 dpi at the 26-40pt sizes the PDFs use."""
    return Image(_ICON_PATH, width=size, height=size)


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
        # reportlab's Canvas only ever exposes the page size as the
        # private `_pagesize` (see reportlab.pdfgen.canvas.Canvas.__init__)
        # — there is no public `pagesize` attribute, in this pinned
        # version (5.0.1) or any other. Reading `self.pagesize` here
        # raised AttributeError on every PDF that reached a second page
        # (single-page PDFs never call this, since save() only stamps
        # page numbers when total_pages > 1), which meant any report or
        # receipt long enough to spill past one page failed to generate
        # at all instead of downloading.
        page_w, _ = self._pagesize
        font, _ = register_fonts()
        self.setFont(font, 7.3)
        self.setFillColor(FOOTER_INK)
        self.drawRightString(
            page_w - 16 * mm, 10 * mm,
            f"Page {self._pageNumber} of {total_pages}",
        )
