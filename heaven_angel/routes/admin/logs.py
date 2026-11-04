
from flask import (
    render_template,
    request,
)

from heaven_angel.db import query
from heaven_angel.decorators import admin_required
from heaven_angel.services import login_activity
from heaven_angel.services.reports import (DISCREPANCY_ITEM_JOIN, DISCREPANCY_UNITS_SQL)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- ledger
@bp.route("/movement-logs")
@admin_required
def movement_logs():
    """Production/Sale/Refill activity only.

    Stock-request-driven movements (DISPATCH, RECEIPT, DAMAGE,
    ADJUSTMENT — every row tagged reference_type='STOCK_REQUEST' by
    dispatch_request() / receive_stock()) are deliberately excluded
    here. A delivery can carry any number of SKUs at once now, so one
    row here — one branch, one item, one qty — would misrepresent it
    and just duplicate what the delivery's own review page and its
    receipt PDF already show in full, correctly grouped under one
    delivery number. See requests_list()/review_request() and
    receipts.py for that history instead.
    """
    branch_filter = request.args.get("branch_id", "all")
    sql = """SELECT sml.*, b.branch_name, p.item_name
              FROM stock_movement_logs sml
              JOIN branches b ON sml.branch_id = b.branch_id
              JOIN products p ON sml.sku = p.sku
              WHERE (sml.reference_type IS NULL OR sml.reference_type != 'STOCK_REQUEST')"""
    params = []
    if branch_filter != "all":
        sql += " AND sml.branch_id = %s"
        params.append(branch_filter)
    sql += " ORDER BY sml.created_at DESC LIMIT 200"
    logs = query(sql, tuple(params))
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches ORDER BY branch_name")
    return render_template("admin/movement_logs.html", logs=logs, branch_list=branch_list, branch_filter=branch_filter)


# ---------------------------------------------------------------- discrepancies (fleet-wide)
@bp.route("/discrepancies")
@admin_required
def discrepancies():
    """Fleet-wide view of delivery discrepancies — DAMAGE (reported at
    receipt) and ADJUSTMENT (dispatched but never received/accounted
    for — see branch.py's receive_stock() shortfall handling) rows tied
    to a delivery (reference_type='STOCK_REQUEST'), across every branch.

    Each delivery's own receipt already shows this per-item, and each
    branch has its own Discrepancies page scoped to itself (see
    branch.py's discrepancies()) — this is the admin-side rollup that
    lets HQ spot a bad courier or a branch that keeps reporting damage/
    shortfall, instead of only ever seeing one delivery at a time on
    review_request().
    """
    branch_filter = request.args.get("branch_id", "all")
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")

    # Per-branch rollup, always fleet-wide regardless of the tab below.
    # Units come from each delivery line (see reports.py's
    # DISCREPANCY_UNITS_SQL): the log rows themselves carry
    # change_qty = 0, so summing that always showed 0 damaged/shortfall
    # units. The total is damaged + shortfall units, so the three
    # columns always add up.
    summary = query(
        f"""SELECT b.branch_id, b.branch_name,
                  COUNT(DISTINCT sml.reference_id) AS deliveries_affected,
                  COALESCE(SUM(CASE WHEN sml.movement_type = 'DAMAGE'
                                    THEN {DISCREPANCY_UNITS_SQL} ELSE 0 END), 0) AS damaged_units,
                  COALESCE(SUM(CASE WHEN sml.movement_type = 'ADJUSTMENT'
                                    THEN {DISCREPANCY_UNITS_SQL} ELSE 0 END), 0) AS shortfall_units
           FROM branches b
           LEFT JOIN stock_movement_logs sml
             ON sml.branch_id = b.branch_id
            AND sml.reference_type = 'STOCK_REQUEST'
            AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')
           {DISCREPANCY_ITEM_JOIN}
           WHERE b.is_hq = FALSE
           GROUP BY b.branch_id, b.branch_name"""
    )
    for s in summary:
        s["damaged_units"] = int(s["damaged_units"])
        s["shortfall_units"] = int(s["shortfall_units"])
        s["total_units"] = s["damaged_units"] + s["shortfall_units"]
    summary.sort(key=lambda s: (-s["total_units"], -s["damaged_units"], s["branch_name"]))

    # Detail log — same shape as branch.py's discrepancies(), just
    # joined across every branch and filterable by one, same
    # all/per-branch tabs convention as low_stock().
    sql = f"""SELECT sml.*, p.item_name, b.branch_name, sr.delivery_number,
                     {DISCREPANCY_UNITS_SQL} AS units_lost
              FROM stock_movement_logs sml
              JOIN products p ON sml.sku = p.sku
              JOIN branches b ON sml.branch_id = b.branch_id
              LEFT JOIN stock_requests sr
                ON sml.reference_type = 'STOCK_REQUEST' AND sml.reference_id = sr.request_id
              {DISCREPANCY_ITEM_JOIN}
              WHERE sml.reference_type = 'STOCK_REQUEST'
                AND sml.movement_type IN ('DAMAGE', 'ADJUSTMENT')"""
    params = []
    if branch_filter != "all":
        sql += " AND sml.branch_id = %s"
        params.append(branch_filter)
    sql += " ORDER BY sml.created_at DESC LIMIT 300"
    logs = query(sql, tuple(params))

    totals = {
        "total_units": sum(s["total_units"] for s in summary),
        "damaged_units": sum(s["damaged_units"] for s in summary),
        "shortfall_units": sum(s["shortfall_units"] for s in summary),
    }

    return render_template(
        "admin/discrepancies.html",
        summary=summary, logs=logs, totals=totals,
        branch_list=branch_list, branch_filter=branch_filter,
    )


# ---------------------------------------------------------------- audit log
@bp.route("/audit-log")
@admin_required
def audit_log():
    """Non-inventory admin activity — account/product/branch changes.

    Separate from the Movement Ledger above on purpose: that ledger is
    strictly stock quantity events (production/dispatch/receipt/sale/
    damage), while this covers everything else an admin can do that
    isn't a stock movement. See audit.py for how entries get written.
    """
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    page_size = 300
    offset = (page - 1) * page_size
    # Fetch one extra row to know whether another page exists, without a
    # separate COUNT(*) query — see login_activity_log() below for the
    # same pattern.
    rows = query(
        """SELECT action_id, actor_username, action, target, details, created_at
           FROM admin_actions
           ORDER BY created_at DESC LIMIT %s OFFSET %s""",
        (page_size + 1, offset),
    )
    has_next = len(rows) > page_size
    logs = rows[:page_size]
    return render_template(
        "admin/audit_log.html", logs=logs, page=page, has_next=has_next)


# ---------------------------------------------------------------- login activity
@bp.route("/login-activity")
@admin_required
def login_activity_log():
    """Every sign-in attempt, successful or not — separate from Admin
    Log above, which only ever covers changes an already-authenticated
    admin makes, never the act of signing in itself. See login_activity.py
    for how entries get written (from routes/auth.py's login()).

    username_attempted / role_attempted are what was actually typed at
    the time, kept even if that username never matched a real account
    (a typo, or someone probing for valid logins) or if the matching
    account's username has since changed — current_username shows what
    that account is called today, for cross-reference, when the two
    differ.
    """
    outcome_filter = request.args.get("outcome", "all")
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    page_size = 300
    offset = (page - 1) * page_size
    sql = """SELECT la.*, u.username AS current_username
             FROM login_activity la
             LEFT JOIN users u ON la.user_id = u.user_id"""
    if outcome_filter == "success":
        sql += " WHERE la.success = TRUE"
    elif outcome_filter == "failed":
        sql += " WHERE la.success = FALSE"
    # Fetch one extra row to know whether another page exists, without a
    # separate COUNT(*) query.
    sql += " ORDER BY la.created_at DESC LIMIT %s OFFSET %s"
    rows = query(sql, (page_size + 1, offset))
    has_next = len(rows) > page_size
    logs = rows[:page_size]

    totals = query(
        """SELECT COUNT(*) AS total_attempts,
                  COALESCE(SUM(CASE WHEN success THEN 0 ELSE 1 END), 0) AS failed_attempts,
                  COUNT(DISTINCT CASE WHEN NOT success THEN ip_address END) AS failed_ip_count
           FROM login_activity""",
        fetchone=True,
    )

    # Usernames with several failed attempts in the last 24 hours —
    # worth a second look (repeated typos from a real user, or someone
    # actively guessing at an account's password).
    recent_failures = query(
        """SELECT username_attempted, COUNT(*) AS attempt_count, MAX(created_at) AS last_attempt_at
           FROM login_activity
           WHERE success = FALSE AND created_at >= NOW() - INTERVAL 24 HOUR
           GROUP BY username_attempted
           HAVING attempt_count >= 3
           ORDER BY attempt_count DESC LIMIT 10"""
    )

    return render_template(
        "admin/login_activity.html", logs=logs, totals=totals,
        outcome_filter=outcome_filter, recent_failures=recent_failures,
        failure_reasons=login_activity.LOGIN_FAILURE_REASONS,
        page=page, has_next=has_next,
    )
