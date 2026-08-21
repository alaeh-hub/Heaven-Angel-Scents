from functools import wraps

from flask import abort, flash, redirect, request, session, url_for

from db import query

# Endpoints a signed-in user must always be able to reach even while
# must_change_password is set — otherwise they'd be locked out of the
# one page that lets them clear it.
_PASSWORD_CHANGE_EXEMPT_ENDPOINTS = {"auth.change_password", "auth.logout"}


def _current_account_or_none():
    """Re-fetch the signed-in user's live status from the database.

    Session data (role, is_active, ...) is only ever set at login time.
    Without this, an admin deactivating someone — or a forced password
    reset — wouldn't take effect until that user's cookie happened to
    expire or they logged out on their own. This makes both take
    effect on the very next request instead.
    """
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return query(
        "SELECT user_id, role, is_active, must_change_password FROM users WHERE user_id = %s",
        (user_id,), fetchone=True,
    )


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
