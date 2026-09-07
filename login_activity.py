"""Sign-in attempt logging — separate from admin_actions (audit.py),
which only ever logs account/product/branch *changes* an
already-authenticated admin makes, not the act of signing in itself.

Every submission of the login form (routes/auth.py's login()) writes
one row here, success or failure, via log_attempt(). Same "never raise"
contract as audit.log_action(): a broken or missing table should never
be the reason a login request fails — this only logs to the app logger
on failure rather than raising, so an unconfigured/pre-migration
database degrades gracefully instead of 500ing on every login attempt.
"""
from flask import current_app, request

from db import execute, query

# Short, fixed labels for why an attempt failed — kept as an allow-list
# (matching the same handful of branches login() can actually take)
# rather than free text, so this stays filterable/groupable instead of
# every row carrying a slightly different hand-written message.
FAILURE_MISSING_FIELDS = "missing_fields"
FAILURE_BAD_CREDENTIALS = "bad_credentials"
FAILURE_WRONG_ROLE_TAB = "wrong_role_tab"
FAILURE_INACTIVE_ACCOUNT = "inactive_account"
FAILURE_LOCKED_OUT = "locked_out"

LOGIN_FAILURE_REASONS = {
    FAILURE_MISSING_FIELDS: "Username or password left blank",
    FAILURE_BAD_CREDENTIALS: "Incorrect username or password",
    FAILURE_WRONG_ROLE_TAB: "Signed in on the wrong role tab",
    FAILURE_INACTIVE_ACCOUNT: "Account deactivated",
    FAILURE_LOCKED_OUT: "Temporarily locked out (too many failed attempts)",
}

# Per-username lockout, on top of auth.login's IP-based rate limit
# (10/min via flask-limiter). The IP limit alone doesn't stop a
# distributed brute force against one specific account from many IPs;
# this stops that regardless of how many source IPs are involved.
#
# A username is locked out once it has this many failed attempts within
# LOCKOUT_WINDOW_MINUTES *since its last successful login* (a success
# resets the count immediately, it doesn't just age out of the window).
LOCKOUT_FAILURE_THRESHOLD = 5
LOCKOUT_WINDOW_MINUTES = 15


def ensure_table():
    """No-op once login_activity exists (the normal case). Creates it,
    with a warning, only as a fallback for a database that hasn't
    picked up the schema.sql change yet — same pattern as
    audit.ensure_table() for admin_actions.
    """
    exists = query(
        """SELECT 1 FROM information_schema.tables
           WHERE table_schema = DATABASE() AND table_name = 'login_activity'""",
        fetchone=True,
    )
    if exists:
        return

    current_app.logger.warning(
        "login_activity table not found — creating it now as a fallback. "
        "This database predates login_activity being added to schema.sql; "
        "re-run schema.sql against it (or apply just the new table) to pick "
        "it up the normal way and avoid relying on this fallback."
    )
    execute(
        """CREATE TABLE IF NOT EXISTS login_activity (
               activity_id BIGINT AUTO_INCREMENT PRIMARY KEY,
               user_id INT NULL,
               username_attempted VARCHAR(80) NOT NULL,
               role_attempted VARCHAR(20) NOT NULL,
               success BOOLEAN NOT NULL,
               failure_reason VARCHAR(30) NULL,
               ip_address VARCHAR(45) NULL,
               user_agent VARCHAR(255) NULL,
               created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
               INDEX idx_login_activity_created_at (created_at),
               INDEX idx_login_activity_username (username_attempted),
               INDEX idx_login_activity_success (success)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
    )


def is_locked_out(username):
    """True if `username` currently has >= LOCKOUT_FAILURE_THRESHOLD
    failed login attempts within LOCKOUT_WINDOW_MINUTES, since its last
    successful login.

    Only counts FAILURE_BAD_CREDENTIALS rows — i.e. an actual wrong
    username/password guess. Every other failure reason is excluded:
      - FAILURE_LOCKED_OUT is logged when this function already said
        "locked out" and the login route bailed out before ever
        checking the password, so it isn't a credential guess at all.
        Counting it anyway would let an attacker keep the lockout alive
        indefinitely just by retrying against the locked account every
        few minutes forever (turning this into a way to lock a real
        user out long-term rather than a brief brute-force cooldown).
      - FAILURE_WRONG_ROLE_TAB and FAILURE_INACTIVE_ACCOUNT are both
        logged *after* routes/auth.py's login() has already confirmed
        the password was correct (see login()'s ordering) — the account
        just isn't reachable from that tab, or is deactivated. Counting
        those would lock out an admin who genuinely knows their
        password just for forgetting to switch tabs, or an account
        HQ deliberately deactivated getting flagged as if it were under
        attack.
      - FAILURE_MISSING_FIELDS never even reaches a password check, so
        it carries no information about whether anyone knows the real
        password — and counting it would mean 5 blank-password
        submissions of anyone's username alone lock that account out.
    Excluding all of these means the lockout only extends in response
    to actual wrong-password guesses, and otherwise expires
    LOCKOUT_WINDOW_MINUTES after the last real one.

    Same "never raise" contract as log_attempt(): if login_activity
    isn't there yet (pre-migration database) this fails open (not
    locked out) rather than breaking every login attempt.
    """
    try:
        row = query(
            """SELECT COUNT(*) AS c
               FROM login_activity
               WHERE username_attempted = %s
                 AND success = FALSE
                 AND failure_reason = %s
                 AND created_at >= NOW() - INTERVAL %s MINUTE
                 AND created_at > COALESCE(
                     (SELECT MAX(created_at) FROM login_activity
                      WHERE username_attempted = %s AND success = TRUE),
                     '1970-01-01'
                 )""",
            (username, FAILURE_BAD_CREDENTIALS, LOCKOUT_WINDOW_MINUTES, username),
            fetchone=True,
        )
    except Exception:
        current_app.logger.exception(
            "Failed to check login lockout status for username=%s", username)
        return False
    return bool(row and row["c"] >= LOCKOUT_FAILURE_THRESHOLD)


def log_attempt(*, username_attempted, role_attempted, success, user_id=None, failure_reason=None):
    """Record one login attempt. Best-effort — see this module's
    docstring for why a failed write here never raises.

    ip_address is request.remote_addr as reported by the WSGI layer.
    This app doesn't sit behind a trusted reverse proxy that sets
    X-Forwarded-For today, so that header is deliberately not trusted
    here — see schema.sql's comment on login_activity.ip_address.
    """
    try:
        user_agent = (request.headers.get("User-Agent") or "")[:255]
        execute(
            """INSERT INTO login_activity
                   (user_id, username_attempted, role_attempted, success,
                    failure_reason, ip_address, user_agent)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (user_id, username_attempted, role_attempted, success,
             failure_reason, request.remote_addr, user_agent),
        )
    except Exception:
        current_app.logger.exception(
            "Failed to write login_activity entry: username=%s success=%s",
            username_attempted, success,
        )
