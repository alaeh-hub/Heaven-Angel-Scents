"""On-demand reports (PDF + Excel) for the admin and branch Reports pages.

Design intent:
- One reusable pattern instead of a bespoke page per report: pick a report
  TYPE, pick a time window (Recent N / Date range / All time), pick that
  type's own extra filters, then download as PDF or Excel. The admin and
  branch routes both call get_report() + render_report_pdf()/render_report_excel()
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

Layout: definitions (the catalog) -> filters/totals -> builders_* (one query
per report) -> registry.get_report(); pdf.py and excel.py render the result.
"""
from heaven_angel.services.reports.constants import (  # noqa: F401
    DISCREPANCY_ITEM_JOIN, DISCREPANCY_UNITS_SQL, LOW_STOCK_WHERE,
)
from heaven_angel.services.reports.definitions import REPORT_TYPES  # noqa: F401
from heaven_angel.services.reports.excel import (  # noqa: F401
    XL_BORDER, XL_HEADER_FILL, XL_MONEY_FMT, render_report_excel,
)
from heaven_angel.services.reports.filters import parse_report_filters  # noqa: F401
from heaven_angel.services.reports.pdf import render_report_pdf  # noqa: F401
from heaven_angel.services.reports.registry import get_report  # noqa: F401
