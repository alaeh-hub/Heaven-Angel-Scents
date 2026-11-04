"""Report builders for accounts and audit trails."""
from heaven_angel.services.reports.constants import MAX_ROWS
from heaven_angel.services.reports.filters import describe_window, time_window


from heaven_angel.db import query


def report_accounts(filters, branch_scope):
    where, params = "", []
    if filters["role"] != "all":
        where += " AND u.role = %s"
        params.append(filters["role"])
    if filters["account_status"] != "all":
        where += " AND u.is_active = %s"
        params.append(filters["account_status"] == "active")
    if filters["search"]:
        where += " AND u.username LIKE %s"
        params.append(f"%{filters['search']}%")

    rows = query(
        f"""SELECT u.username, u.role, b.branch_name, u.is_active, u.created_at
            FROM users u LEFT JOIN branches b ON u.branch_id = b.branch_id
            WHERE 1=1 {where} ORDER BY u.role, b.branch_name, u.username LIMIT {MAX_ROWS}""",
        tuple(params),
    )
    for r in rows:
        r["branch_name"] = r["branch_name"] or "— HQ —"
        r["is_active"] = "Active" if r["is_active"] else "Deactivated"

    columns = [
        ("username", "Username", "str"), ("role", "Role",
                                          "badge:role"), ("branch_name", "Branch", "str"),
        ("is_active", "Status", "badge:active_status"), ("created_at",
                                                         "Created", "datetime"),
    ]
    return columns, rows, len(rows) == MAX_ROWS, "Snapshot as of now"


def report_admin_log(filters, branch_scope):
    """The Admin Log page: non-inventory admin actions (accounts,
    products, branches, settings)."""
    where, params = "", []
    if filters["search"]:
        where += " AND (aa.actor_username LIKE %s OR aa.action LIKE %s OR aa.target LIKE %s OR aa.details LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like, like]
    time_where, order, limit_n, truncated = time_window("aa.created_at", filters, params)
    where += time_where
    rows = query(
        f"""SELECT aa.created_at, aa.actor_username, aa.action, aa.target, aa.details
            FROM admin_actions aa WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        r["actor_username"] = r["actor_username"] or "—"
        r["action"] = r["action"].replace("_", " ").capitalize()
        r["target"] = r["target"] or "—"
        r["details"] = r["details"] or "—"
    columns = [
        ("created_at", "When", "datetime", 0.9), ("actor_username", "Admin", "str", 0.8),
        ("action", "Action", "str", 1.1), ("target", "Target", "str", 1.1),
        ("details", "Details", "str", 2.0),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)


def report_login_activity(filters, branch_scope):
    """The Login Activity page: every sign-in attempt, successful or not."""
    where, params = "", []
    if filters["search"]:
        where += " AND (la.username_attempted LIKE %s OR la.ip_address LIKE %s OR la.failure_reason LIKE %s)"
        like = f"%{filters['search']}%"
        params += [like, like, like]
    time_where, order, limit_n, truncated = time_window("la.created_at", filters, params)
    where += time_where
    rows = query(
        f"""SELECT la.created_at, la.username_attempted, u.username AS current_username,
                   la.role_attempted, la.success, la.failure_reason, la.ip_address
            FROM login_activity la LEFT JOIN users u ON la.user_id = u.user_id
            WHERE 1=1 {where} {order} LIMIT {limit_n}""",
        tuple(params),
    )
    for r in rows:
        current = r.pop("current_username")
        if current and current != r["username_attempted"]:
            r["username_attempted"] = f"{r['username_attempted']} (now {current})"
        r["result"] = "Success" if r.pop("success") else "Failed"
        r["failure_reason"] = (r["failure_reason"] or "—").replace("_", " ")
        r["ip_address"] = r["ip_address"] or "—"
    columns = [
        ("created_at", "When", "datetime"), ("username_attempted", "Username", "str"),
        ("role_attempted", "Role Tab", "badge:role"), ("result", "Result", "str"),
        ("failure_reason", "Reason", "str"), ("ip_address", "IP Address", "str"),
    ]
    truncated = truncated and len(rows) == MAX_ROWS
    return columns, rows, truncated, describe_window(filters, truncated)
