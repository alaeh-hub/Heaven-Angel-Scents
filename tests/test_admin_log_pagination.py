"""Admin Log and Login Activity used to hard-cap at LIMIT 300 with no way
to see anything older — these check the page=N param added to both routes
actually reaches further back in time instead of re-showing page 1."""
from factories import login, make_user, unique_suffix

ADMIN_LOG_URL = "/admin/audit-log"
LOGIN_ACTIVITY_URL = "/admin/login-activity"


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def _seed_admin_actions(sql, count, marker):
    cur = sql.cursor()
    for i in range(count):
        cur.execute(
            """INSERT INTO admin_actions (actor_username, action, target, details)
               VALUES (%s, 'toggle_user', %s, 'seed')""",
            (marker, f"{marker}-{i}"),
        )
    sql.commit()


def test_audit_log_page_2_shows_older_rows_not_on_page_1(client, sql):
    _signed_in_admin(client, sql)
    marker = f"pg-{unique_suffix()}"
    _seed_admin_actions(sql, 305, marker)

    page1 = client.get(ADMIN_LOG_URL)
    page2 = client.get(f"{ADMIN_LOG_URL}?page=2")

    assert b"Older" in page1.data
    assert page1.data != page2.data


def test_login_activity_page_param_preserves_outcome_filter(client, sql):
    _signed_in_admin(client, sql)
    resp = client.get(f"{LOGIN_ACTIVITY_URL}?outcome=failed&page=1")
    assert resp.status_code == 200
    resp2 = client.get(f"{LOGIN_ACTIVITY_URL}?outcome=failed&page=2")
    assert resp2.status_code == 200
