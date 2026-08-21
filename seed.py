"""
Run once after importing schema.sql to create starter login accounts:

    python seed.py

Creates:
  - admin / admin123      (Admin, HQ)
  - manila / branch123    (Branch, Manila Branch)
  - cebu / branch123      (Branch, Cebu Branch)

Each starter account is created with must_change_password=TRUE, so
whoever signs in with these credentials first is required to set a
new password immediately — the known starter password can't quietly
stay in place in a real deployment.
"""
from werkzeug.security import generate_password_hash

from app import create_app
from db import execute, query

app = create_app()

with app.app_context():
    accounts = [
        ("admin", "admin123", "Admin", None),
        ("manila", "branch123", "Branch", "Manila Branch"),
        ("cebu", "branch123", "Branch", "Cebu Branch"),
    ]

    for username, password, role, branch_name in accounts:
        exists = query("SELECT user_id FROM users WHERE username = %s", (username,), fetchone=True)
        if exists:
            print(f"  skip  {username} (already exists)")
            continue

        branch_id = None
        if branch_name:
            row = query("SELECT branch_id FROM branches WHERE branch_name = %s", (branch_name,), fetchone=True)
            branch_id = row["branch_id"] if row else None

        execute(
            """INSERT INTO users (username, password_hash, role, branch_id, must_change_password)
               VALUES (%s, %s, %s, %s, TRUE)""",
            (username, generate_password_hash(password), role, branch_id),
        )
        print(f"  added {username} / {password}  ({role}) — must change password on first login")

print("\nSeed complete.")
