"""Time-bucket options for the Reports page's trend charts (shared by admin and branch)."""


# Bucket-size options for the "Ledger movement" trend chart on the
# Reports page (see the admin and branch reports_data() endpoints). Every `trunc` expression is
# wrapped so it evaluates to an actual DATE, not a formatted string —
# DATE_FORMAT() on its own returns text, and wrapping it back in DATE()
# keeps all four options returning the same Python type (datetime.date)
# so reports_data() can call .isoformat() on the result the same way
# regardless of which option was picked, with no extra branching.
TREND_GRANULARITIES = {
    "daily": {
        "trunc": "DATE(sml.created_at)",
        "window": "INTERVAL 14 DAY",
        "window2": "INTERVAL 28 DAY",
    },
    "weekly": {
        # WEEKDAY() is 0 for Monday, so this rounds each timestamp back
        # to the Monday that starts its week.
        "trunc": "DATE(DATE_SUB(sml.created_at, INTERVAL WEEKDAY(sml.created_at) DAY))",
        "window": "INTERVAL 12 WEEK",
        "window2": "INTERVAL 24 WEEK",
    },
    "monthly": {
        "trunc": "DATE(DATE_FORMAT(sml.created_at, '%Y-%m-01'))",
        "window": "INTERVAL 12 MONTH",
        "window2": "INTERVAL 24 MONTH",
    },
    "yearly": {
        "trunc": "DATE(DATE_FORMAT(sml.created_at, '%Y-01-01'))",
        "window": "INTERVAL 5 YEAR",
        "window2": "INTERVAL 10 YEAR",
    },
}


def trend_bucket_dates(granularity, today):
    """The bucket start dates a trend window covers, oldest first, so
    KPI sparklines can zero-fill buckets that had no activity."""
    from datetime import timedelta
    if granularity == "weekly":
        monday = today - timedelta(days=today.weekday())
        return [monday - timedelta(weeks=i) for i in range(11, -1, -1)]
    if granularity == "monthly":
        out = []
        y, m = today.year, today.month
        for _ in range(12):
            out.append(today.replace(year=y, month=m, day=1))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        return out[::-1]
    if granularity == "yearly":
        return [today.replace(year=today.year - i, month=1, day=1) for i in range(4, -1, -1)]
    return [today - timedelta(days=i) for i in range(13, -1, -1)]
