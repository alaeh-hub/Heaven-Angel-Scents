"""Create the first Admin account (seed.py refuses to run in production).

Run inside the container so it uses the production database settings:

    docker compose exec web python scripts/create_admin.py

Prompts for the password, so it never lands in shell history. Safe to re-run:
it refuses to overwrite an existing username.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from werkzeug.security import generate_password_hash

from app import create_app  # noqa: E402
from db import execute, query  # noqa: E402


def main():
    username = input("Admin username [admin]: ").strip() or "admin"
    password = getpass.getpass("New admin password (min 10 chars): ")
    if len(password) < 10:
        sys.exit("Password too short.")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")

    with create_app().app_context():
        if query("SELECT 1 FROM users WHERE username = %s", (username,), fetchone=True):
            sys.exit(f"User '{username}' already exists; nothing changed.")
        execute(
            "INSERT INTO users (username, password_hash, role, branch_id, must_change_password) "
            "VALUES (%s, %s, %s, %s, %s)",
            (username, generate_password_hash(password), "Admin", None, False),
        )
    print(f"Admin account '{username}' created.")


if __name__ == "__main__":
    main()
