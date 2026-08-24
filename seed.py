"""
Run once after importing schema.sql to create starter login accounts:

    python seed.py

Creates three accounts (Admin / HQ, plus two starter branch accounts
for Manila and Cebu), each with must_change_password=TRUE so whoever
signs in first is forced to set their own password immediately.

Two safety rules, on top of that:

1. This script refuses to run at all when APP_ENV=production. It
   exists to get a local/dev database into a usable state quickly —
   it should never be the thing that plants known accounts on a real
   deployment. If you genuinely need starter accounts in production,
   create them through the app's own Accounts page instead, where an
   admin sets the password by hand.

2. Passwords are no longer hardcoded. Each one is read from its own
   environment variable if you've set one (SEED_ADMIN_PASSWORD,
   SEED_MANILA_PASSWORD, SEED_CEBU_PASSWORD); otherwise a random
   password is generated and printed once. Either way, the password is
   only ever shown in this script's own output — never checked into
   source control, never reused across setups.
"""
import os
import sys

from werkzeug.security import generate_password_hash

from app import create_app
from db import execute, query
from utils import generate_temp_password

app = create_app()

with app.app_context():
    environment = os.environ.get("APP_ENV", "development").lower()
    if environment == "production":
        print("Refusing to run: APP_ENV=production.")
        print(
            "seed.py creates known starter accounts for local/dev setup only. "
            "Create accounts on a production database through the app's own "
            "Accounts page instead, so each password is set deliberately by an admin."
        )
        sys.exit(1)

    accounts = [
        ("admin", os.environ.get("SEED_ADMIN_PASSWORD") or generate_temp_password(), "Admin", None),
        ("manila", os.environ.get("SEED_MANILA_PASSWORD") or generate_temp_password(), "Branch", "Manila Branch"),
        ("cebu", os.environ.get("SEED_CEBU_PASSWORD") or generate_temp_password(), "Branch", "Cebu Branch"),
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

print("\nSeed complete. Copy any generated passwords above now — they are not stored anywhere.")
