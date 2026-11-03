"""Create (or reset) a login account. Works the same on a dev machine and on
the production server, because it uses whatever database the app itself is
configured for (.env / environment variables).

    python scripts/create_admin.py                       # first Admin, prompts
    python scripts/create_admin.py --username owner
    python scripts/create_admin.py --role Branch --branch "Balayan Branch"
    python scripts/create_admin.py --username admin --reset-password

Docker deployment:

    docker compose exec web python scripts/create_admin.py

Passwords are always typed at a hidden prompt, never given as an argument, so
they never land in shell history. Without --reset-password an existing
username is never touched.

By default an account is NOT forced to change its password at first login
(you just typed it yourself). Pass --must-change to hand an account to
someone else and make them choose their own password.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from werkzeug.security import generate_password_hash  # noqa: E402

from heaven_angel.app import create_app  # noqa: E402
from heaven_angel.db import execute, query  # noqa: E402

MIN_PASSWORD = 10


def ask_password():
    password = getpass.getpass(f"Password (min {MIN_PASSWORD} chars): ")
    if len(password) < MIN_PASSWORD:
        sys.exit("Password too short.")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return password


def main():
    parser = argparse.ArgumentParser(description="Create or reset a login account.")
    parser.add_argument("--username", help="login name (asked if omitted)")
    parser.add_argument("--role", choices=["Admin", "Branch"], default="Admin")
    parser.add_argument("--branch", help='branch name for a Branch account, e.g. "Balayan Branch"')
    parser.add_argument("--reset-password", action="store_true",
                        help="change the password of an existing account instead of refusing")
    parser.add_argument("--must-change", action="store_true",
                        help="force a password change at first login")
    args = parser.parse_args()

    default_name = "admin" if args.role == "Admin" else ""
    username = args.username or input(
        f"Username [{default_name}]: " if default_name else "Username: "
    ).strip() or default_name
    if not username:
        sys.exit("A username is required.")

    with create_app().app_context():
        existing = query("SELECT user_id FROM users WHERE username = %s", (username,), fetchone=True)

        if existing and not args.reset_password:
            sys.exit(f"User '{username}' already exists; nothing changed "
                     "(use --reset-password to change its password).")
        if not existing and args.reset_password:
            sys.exit(f"User '{username}' does not exist; nothing to reset.")

        if existing:
            execute(
                "UPDATE users SET password_hash = %s, must_change_password = %s WHERE user_id = %s",
                (generate_password_hash(ask_password()), args.must_change, existing["user_id"]),
            )
            print(f"Password for '{username}' updated.")
            return

        branch_id = None
        if args.role == "Branch":
            if not args.branch:
                names = ", ".join(r["branch_name"] for r in query(
                    "SELECT branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name"))
                sys.exit(f'A Branch account needs --branch "NAME". Existing branches: {names or "none yet"}.')
            row = query("SELECT branch_id FROM branches WHERE branch_name = %s", (args.branch,), fetchone=True)
            if not row:
                sys.exit(f"No branch named '{args.branch}'. Create it first in Admin > Branches.")
            branch_id = row["branch_id"]

        execute(
            "INSERT INTO users (username, password_hash, role, branch_id, must_change_password) "
            "VALUES (%s, %s, %s, %s, %s)",
            (username, generate_password_hash(ask_password()), args.role, branch_id, args.must_change),
        )
    print(f"{args.role} account '{username}' created.")


if __name__ == "__main__":
    main()
