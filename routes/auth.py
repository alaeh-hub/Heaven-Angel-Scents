from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from db import query

bp = Blueprint("auth", __name__)


@bp.route("/", methods=["GET"])
def index():
    if "user_id" in session:
        return redirect(url_for("admin.dashboard" if session["role"] == "Admin" else "branch.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("admin.dashboard" if session["role"] == "Admin" else "branch.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        login_type = request.form.get("login_type", "Branch")
        if login_type not in ("Admin", "Branch"):
            login_type = "Branch"

        if not username or not password:
            flash("Please enter both your username and password.", "error")
            return render_template("login.html", login_type=login_type), 400

        if len(username) > 80 or len(password) > 200:
            flash("Incorrect username or password.", "error")
            return render_template("login.html", login_type=login_type), 400

        user = query(
            """SELECT u.user_id, u.username, u.password_hash, u.role, u.branch_id,
                      u.is_active, b.branch_name
               FROM users u LEFT JOIN branches b ON u.branch_id = b.branch_id
               WHERE u.username = %s""",
            (username,), fetchone=True,
        )

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Incorrect username or password.", "error")
            return render_template("login.html", login_type=login_type), 401

        if user["role"] != login_type:
            flash(
                f"That account is a {user['role']} account. Switch tabs above and try again.", "error")
            return render_template("login.html", login_type=login_type), 401

        if not user["is_active"]:
            flash("This account has been deactivated. Contact HQ.", "error")
            return render_template("login.html", login_type=login_type), 403

        session.clear()
        session["user_id"] = user["user_id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        session["branch_id"] = user["branch_id"]
        session["branch_name"] = user["branch_name"]

        flash(f"Welcome back, {user['username']}.", "success")
        return redirect(url_for("admin.dashboard" if user["role"] == "Admin" else "branch.dashboard"))

    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    flash("You've been signed out.", "success")
    return redirect(url_for("auth.login"))
