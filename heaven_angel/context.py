"""Template context processors and the small JSON endpoint the top bar polls."""
from flask import session

from heaven_angel import db, utils
from heaven_angel.decorators import live_account_or_none
from heaven_angel.template_helpers import peso


def today_sales_summary(account=None):
    """Today's sales for the top bar chip — the signed-in branch's own,
    or every branch's for an admin. Same revenue formula as the branch
    dashboard (qty x unit price, voids already deleted), with the trend
    compared against yesterday up to this same time of day.

    Pass `account` (a live users row, see decorators.live_account_or_none)
    to scope by the database's current role/branch instead of the
    session's login-time copy."""
    if "user_id" not in session:
        return None
    role = account["role"] if account else session.get("role")
    branch_id = account["branch_id"] if account else session.get("branch_id")
    branch_name = account["branch_name"] if account else session.get("branch_name")
    if role == "Admin":
        scope_sql, params = "", ()
    elif role == "Branch" and branch_id:
        scope_sql, params = " AND branch_id = %s", (branch_id,)
    else:
        return None
    today = db.query(
        f"""SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS revenue, COUNT(*) AS sale_count
            FROM sales
            WHERE sold_at >= CURDATE() AND sold_at < CURDATE() + INTERVAL 1 DAY{scope_sql}""",
        params, fetchone=True,
    )
    yesterday = db.query(
        f"""SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
            FROM sales
            WHERE sold_at >= CURDATE() - INTERVAL 1 DAY AND sold_at < NOW() - INTERVAL 1 DAY{scope_sql}""",
        params, fetchone=True,
    )
    revenue = today["revenue"]
    return {
        "revenue": float(revenue),
        "revenue_display": peso(revenue),
        "revenue_compact": utils.compact_peso(revenue),
        "count": int(today["sale_count"]),
        "trend": utils.percent_change(revenue, yesterday["revenue"]),
        "scope": "all branches" if role == "Admin" else (branch_name or "your branch"),
    }


def _count(sql, params=()):
    row = db.query(sql, params, fetchone=True)
    return row["c"] if row else 0


def _admin_task_counts():
    return {
        "pending_requests_count": _count(
            "SELECT COUNT(*) c FROM stock_requests WHERE status IN ('Pending', 'Partially Fulfilled')"),
        "new_inquiries_count": _count(
            "SELECT COUNT(*) c FROM partner_inquiries WHERE status = 'New'"),
        "pending_ai_drafts_count": _count(
            "SELECT COUNT(*) c FROM ai_stock_drafts WHERE status = 'Pending Review'"),
    }


def _branch_task_counts(branch_id):
    return {
        "in_transit_count": _count(
            "SELECT COUNT(*) c FROM stock_requests WHERE branch_id = %s AND status = 'In Transit'",
            (branch_id,)),
        "pending_ai_drafts_count": _count(
            "SELECT COUNT(*) c FROM ai_stock_drafts WHERE branch_id = %s AND status = 'Pending Review'",
            (branch_id,)),
    }


def register_context_processors(app):
    @app.context_processor
    def inject_today():
        # ISO date string for capping date-picker inputs at "today" (e.g.
        # Add material/supplier, Record sale's backdating field) — a
        # picker shouldn't let someone log a record dated in the future.
        return {"today": utils.business_today().isoformat()}

    @app.context_processor
    def inject_login_splash():
        # Popped, not just read, so it renders true on exactly one page
        # load (the dashboard a fresh login redirects to) and never
        # again on a later refresh — see auth.py's login(), which is the
        # only place that sets it.
        return {"show_login_splash": session.pop("show_login_splash", False)}

    @app.context_processor
    def inject_today_sales():
        try:
            return {"today_sales_chip": today_sales_summary()}
        except Exception:
            app.logger.exception("Failed to compute today's sales for the top bar")
            return {"today_sales_chip": None}

    @app.context_processor
    def inject_sidebar_task_counts():
        if "user_id" not in session:
            return {}
        try:
            role = session.get("role")
            if role == "Admin":
                return _admin_task_counts()
            if role == "Branch":
                return _branch_task_counts(session.get("branch_id"))
        except Exception:
            app.logger.exception("Failed to compute sidebar task-count badges")
        return {}

    @app.route("/api/today-sales")
    def api_today_sales():
        # Re-check the account against the database like @login_required
        # does, so a deactivated or demoted user stops getting revenue
        # figures immediately instead of when their cookie expires.
        account = live_account_or_none()
        summary = today_sales_summary(account) if account else None
        if summary is None:
            return {"error": "not signed in"}, 401
        return summary
