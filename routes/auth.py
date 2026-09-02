from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import execute, query
from decorators import login_required
from extensions import limiter

bp = Blueprint("auth", __name__)


@bp.route("/", methods=["GET"])
def index():
    if "user_id" in session:
        return redirect(url_for("admin.dashboard"
                                if session["role"] == "Admin"
                                else "branch.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if "user_id" in session:
        return redirect(url_for("admin.dashboard"
                                if session["role"] == "Admin"
                                else "branch.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        login_type = request.form.get("login_type", "Branch")
        if login_type not in ("Admin", "Branch"):
            login_type = "Branch"

        if not username or not password:
            flash("Please enter both your username and password.", "error")
            return render_template("login.html", login_type=login_type), 400

        user = query(
            """SELECT u.user_id, u.username, u.password_hash, u.role, u.branch_id,
            u.is_active, u.must_change_password, b.branch_name
            FROM users u LEFT JOIN  branches b ON u.branch_id = b.branch_id
            WHERE u.username = %s""",
            (username,), fetchone=True,
        )

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Incorrect username or password.", "error")
            return render_template("login.html", login_type=login_type), 401

        if user["role"] != login_type:
            flash(
                f"That account is a {user['role']} account. Switch tabs above and try again.", "error")
            return render_template("login.html", login_type=login_type), 403

        session.clear()
        session["user_id"] = user["user_id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        session["branch_id"] = user["branch_id"]
        session["branch_name"] = user["branch_name"]
        session["must_change_password"] = bool(user["must_change_password"])

        if session["must_change_password"]:
            flash(
                f"Welcome back, {user['username']}. Please set a new password to continue.", "warning")
            return redirect(url_for("auth.change_password"))

        flash(f"Welcome back, {user['username']}.", "success")
        return redirect(url_for("admin.dashboard"
                                if user["role"] == "Admin"
                                else "branch.dashboard"))
    return render_template("login.html")


# POST-only, not GET. A GET route with a side effect (clearing the
# session) can be triggered by any other site the signed-in user's
# browser visits — e.g. <img src="https://this-app/logout"> — since the
# browser will happily follow that request with the user's session
# cookie attached, no interaction required. CSRFProtect(app) in app.py
# already exempts GET/HEAD/OPTIONS by design (that's normal — GET isn't
# supposed to have side effects), so GET was the actual hole here, not
# a missing CSRF token; switching to POST is what puts this behind
# CSRFProtect's real protection, and the hidden csrf_token field on the
# sign-out form in base.html is what satisfies it.
@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You've been signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        user = query("SELECT password_hash FROM users WHERE user_id = %s",
                     (session["user_id"],), fetchone=True)

        if not user or not check_password_hash(user["password_hash"], current_password):
            flash("Current password is incorrect.", "error")
        elif len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new_password != confirm_password:
            flash("New password and confirmation don't match.", "error")
        elif check_password_hash(user["password_hash"], new_password):
            flash("Choose a password different from your current one.", "error")
        else:
            execute(
                "UPDATE users SET password_hash = %s, must_change_password = FALSE WHERE user_id = %s",
                (generate_password_hash(new_password), session["user_id"]),
            )
            session["must_change_password"] = False
            flash("Password updated.", "success")
            return redirect(url_for("admin.dashboard"
                                    if session["role"] == "Admin"
                                    else "branch.dashboard"))

    return render_template("change_password.html")
