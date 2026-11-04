"""Parsing the querystring filters and turning them into SQL time windows."""
import datetime
from heaven_angel.services.reports.constants import INQUIRY_STATUS_CHOICES, MAX_ROWS, MOVEMENT_TYPE_CHOICES, PACKAGE_SCOPE_CHOICES, PACKAGE_STATUS_CHOICES, PARTNER_TYPE_CHOICES, PAYMENT_METHOD_CHOICES, RECENT_CHOICES, ROLE_CHOICES, SALE_TYPE_CHOICES, STATUS_CHOICES, UNIT_CHOICES, VARIANT_CHOICES


from heaven_angel.utils import (
    business_today,
)


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

    def _month(name):
        raw = (args.get(name) or "").strip()
        try:
            # Normalizes to exactly "YYYY-MM" (e.g. a stray "2026-9" from
            # a hand-edited querystring becomes "2026-09") rather than
            # trusting whatever shape the browser's <input type="month">
            # actually sent.
            return datetime.datetime.strptime(raw, "%Y-%m").strftime("%Y-%m")
        except ValueError:
            return None

    mode = _choice("mode", ("recent", "range", "month", "all"), "recent")
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
        "month": _month("month"),
        "branch_id": branch_id,
        "status": _choice("status", STATUS_CHOICES, "all"),
        "movement_type": _choice("movement_type", MOVEMENT_TYPE_CHOICES, "all"),
        "variant": _choice("variant", VARIANT_CHOICES, "all"),
        "unit": _choice("unit", UNIT_CHOICES, "all"),
        "sale_type": _choice("sale_type", SALE_TYPE_CHOICES, "all"),
        "payment_method": _choice("payment_method", PAYMENT_METHOD_CHOICES, "all"),
        "role": _choice("role", ROLE_CHOICES, "all"),
        "account_status": _choice("account_status", ("active", "inactive"), "all"),
        "low_stock_only": args.get("low_stock_only") == "1",
        "partner_type": _choice("partner_type", PARTNER_TYPE_CHOICES, "all"),
        "package_scope": _choice("package_scope", PACKAGE_SCOPE_CHOICES, "all"),
        "package_status": _choice("package_status", PACKAGE_STATUS_CHOICES, "all"),
        "inquiry_status": _choice("inquiry_status", INQUIRY_STATUS_CHOICES, "all"),
        "search": (args.get("search") or "").strip()[:100],
    }


def _month_bounds(month_str):
    """(first_day, last_day) date objects for a "YYYY-MM" string, or for
    the current calendar month if month_str is missing/invalid — the
    same "fall back to a safe default rather than error" contract as
    every other filter parsed in this module."""
    try:
        year, mon = (int(p) for p in month_str.split("-"))
        first_day = datetime.date(year, mon, 1)
    except (AttributeError, TypeError, ValueError):
        today = business_today()
        first_day = today.replace(day=1)
        year, mon = first_day.year, first_day.month
    next_month_first = (
        datetime.date(year + 1, 1, 1) if mon == 12
        else datetime.date(year, mon + 1, 1)
    )
    return first_day, next_month_first - datetime.timedelta(days=1)


def time_window(date_col, filters, params):
    """Append a time-window WHERE fragment for date_col and return
    (where_fragment, order_by_sql, row_limit, is_capped_all_time).

    params is mutated in place (range/month modes append their bound(s)).
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
    if mode == "month":
        first_day, last_day = _month_bounds(filters["month"])
        frag = f" AND {date_col} >= %s AND {date_col} <= %s"
        params.append(f"{first_day.isoformat()} 00:00:00")
        params.append(f"{last_day.isoformat()} 23:59:59")
        # A single calendar month of activity is realistically never
        # anywhere near MAX_ROWS, but capped the same way range/all-time
        # are just in case — same reasoning as range mode above.
        return frag, f"ORDER BY {date_col} ASC", MAX_ROWS, True
    if mode == "all":
        return "", f"ORDER BY {date_col} DESC", MAX_ROWS, True
    return "", f"ORDER BY {date_col} DESC", filters["recent_n"], False


def describe_window(filters, truncated):
    mode = filters["mode"]
    if mode == "recent":
        note = f"Most recent {filters['recent_n']} entries"
    elif mode == "range":
        frm = filters["date_from"] or "the beginning"
        to = filters["date_to"] or "today"
        note = f"{frm} through {to}"
    elif mode == "month":
        first_day, _ = _month_bounds(filters["month"])
        note = first_day.strftime("%B %Y")
    else:
        note = "All time"
    if truncated:
        note += f" (capped at the first {MAX_ROWS:,} rows — narrow the filters for a complete report)"
    return note


def window_where(date_col, filters, params):
    """Range/Month bound for a summary report (one figure per window, not
    "latest N rows"), so Recent/All both read as all time — same as
    Branch Performance."""
    if filters["mode"] in ("range", "month"):
        frag, _, _, _ = time_window(date_col, filters, params)
        return frag
    return ""
