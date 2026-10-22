import hashlib
from functools import wraps

from flask import abort, flash, redirect, request, session, url_for


from heaven_angel.db import query

# Endpoints a signed-in user must always be able to reach even while
# must_change_password is set — otherwise they'd be locked out of the
# one page that lets them clear it.
_PASSWORD_CHANGE_EXEMPT_ENDPOINTS = {"auth.change_password", "auth.logout"}


def password_fingerprint(password_hash):
    """Short, non-reversible tag for the current password hash. Stored in the
    session at login; once the password changes (self-service or HQ reset)
    the tag no longer matches, so every other signed-in session for that
    account is rejected on its next request."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def _current_account_or_none():
    """Re-fetch the signed-in user's live status from the database.

    Session data (role, is_active, ...) is only ever set at login time.
    Without this, an admin deactivating someone — or a forced password
    reset — wouldn't take effect until that user's cookie happened to
    expire or they logged out on their own. This makes both take
    effect on the very next request instead. branch_id/branch_name are
    included here (and re-synced onto the session below) for the same
    reason: if a Branch account's branch_id is ever reassigned, an
    already-signed-in session should immediately start reading/writing
    the new branch's data instead of the stale one from login time.
    """
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return query(
        """SELECT u.user_id, u.role, u.is_active, u.must_change_password,
                  u.password_hash, u.branch_id, b.branch_name
           FROM users u LEFT JOIN branches b ON u.branch_id = b.branch_id
           WHERE u.user_id = %s""",
        (user_id,), fetchone=True,
    )


def live_account_or_none():
    """The signed-in account if its session is still valid right now, else
    None — same active / password-fingerprint checks as _require_session(),
    for JSON endpoints that must answer 401 instead of redirecting."""
    account = _current_account_or_none()
    if account is None or not account["is_active"]:
        return None
    if session.get("pwv") != password_fingerprint(account["password_hash"]):
        return None
    return account


def _require_session(required_role=None):
    """Shared gate used by all three decorators below.

    Returns a Flask redirect/abort response if access should be
    denied, or None if the request should proceed.
    """
    if "user_id" not in session:
        flash("Please sign in to continue.", "warning")
        return redirect(url_for("auth.login"))

    account = _current_account_or_none()
    if account is None or not account["is_active"]:
        session.clear()
        flash("This account is no longer active. Please contact HQ.", "error")
        return redirect(url_for("auth.login"))

    if session.get("pwv") != password_fingerprint(account["password_hash"]):
        session.clear()
        flash("Your password changed, so please sign in again.", "warning")
        return redirect(url_for("auth.login"))

    # A Branch account must belong to a branch. users.branch_id is
    # ON DELETE SET NULL, and several helpers (scan, void_sale, settle_credit)
    # read branch_id=None as "HQ, unrestricted" — so a branchless Branch
    # account is refused outright instead of failing open.
    if account["role"] == "Branch" and account["branch_id"] is None:
        session.clear()
        flash("This account isn't assigned to a branch. Please contact HQ.", "error")
        return redirect(url_for("auth.login"))

    # Keep the session's branch in sync with the database on every
    # request, same reasoning as the role/is_active re-check above —
    # see _current_account_or_none()'s docstring.
    session["branch_id"] = account["branch_id"]
    session["branch_name"] = account["branch_name"]

    if required_role is not None and account["role"] != required_role:
        abort(403)

    if account["must_change_password"] and request.endpoint not in _PASSWORD_CHANGE_EXEMPT_ENDPOINTS:
        flash("Please set a new password before continuing.", "warning")
        return redirect(url_for("auth.change_password"))

    return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        deny = _require_session()
        if deny is not None:
            return deny
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        deny = _require_session(required_role="Admin")
        if deny is not None:
            return deny
        return view(*args, **kwargs)
    return wrapped


def branch_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        deny = _require_session(required_role="Branch")
        if deny is not None:
            return deny
        return view(*args, **kwargs)
    return wrapped
