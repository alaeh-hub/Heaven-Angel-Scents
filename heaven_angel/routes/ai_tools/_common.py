"""Helpers every AI tool module shares: the row cap and branch-name scoping."""
from heaven_angel.db import query


MAX_ROWS = 30  # cap on any single tool's result rows, to keep responses cheap


def _resolve_branch_ids(ctx, branch_name, include_hq=False):
    """Turn an (optional, model-supplied) branch_name into a list of
    branch_ids the caller is actually allowed to see.

    Branch-role users ALWAYS get their own branch_id, regardless of what
    the model passed — this is the actual access boundary, not the
    model's argument. Admins may name a branch (fuzzy match) or leave it
    blank to mean "every branch".

    include_hq lets the name match HQ's own warehouse too (e.g. "HQ"),
    for tools where HQ is one of the locations being reported on.
    """
    if ctx["role"] != "Admin":
        return [ctx["branch_id"]], None

    if not branch_name:
        return None, None  # None = no filter = every branch, admin only

    sql = "SELECT branch_id, branch_name FROM branches WHERE branch_name LIKE %s"
    if not include_hq:
        sql += " AND is_hq = FALSE"
    rows = query(sql, (f"%{branch_name}%",))
    if not rows:
        return [], f"No branch matching '{branch_name}'."
    if len(rows) > 1:
        names = ", ".join(r["branch_name"] for r in rows)
        return [], f"'{branch_name}' matches more than one branch ({names}) — be more specific."
    return [rows[0]["branch_id"]], None


def _branch_filter(ctx, branch_name, column, include_hq=True):
    """(sql_fragment, params, error) restricting `column` to the branches
    the caller may see. An empty fragment means every branch (Admin with
    no branch named). HQ is nameable by default, since HQ records sales
    of its own."""
    branch_ids, err = _resolve_branch_ids(ctx, branch_name, include_hq=include_hq)
    if err:
        return None, None, err
    if branch_ids is None:
        return "", [], None
    if not branch_ids:
        return None, None, "No matching branch."
    placeholders = ",".join(["%s"] * len(branch_ids))
    return f" AND {column} IN ({placeholders})", list(branch_ids), None
