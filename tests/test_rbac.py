"""decorators.py's login_required / admin_required / branch_required —
the single chokepoint every protected route in the app funnels through.

Covers: anonymous access is redirected to login, a signed-in user of the
wrong role gets 403 rather than access, and the two properties the
project's README calls out by name as the point of re-checking the
database on every request rather than trusting the session cookie —
an account deactivated mid-session loses access on its very next
request, and an account flagged must_change_password is forced to
/change-password (except for the endpoints that must stay reachable so
it isn't a lockout).
"""
from factories import login, make_branch, make_user, set_account_active

ADMIN_ROUTE = "/admin/"
BRANCH_ROUTE = "/branch/inventory"


def test_anonymous_user_redirected_to_login_from_admin_route(client):
    resp = client.get(ADMIN_ROUTE)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_anonymous_user_redirected_to_login_from_branch_route(client):
    resp = client.get(BRANCH_ROUTE)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_branch_user_gets_403_on_admin_route(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    resp = client.get(ADMIN_ROUTE)

    assert resp.status_code == 403


def test_admin_user_gets_403_on_branch_route(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")

    resp = client.get(BRANCH_ROUTE)

    assert resp.status_code == 403


def test_admin_user_can_reach_admin_dashboard(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")

    resp = client.get(ADMIN_ROUTE)

    assert resp.status_code == 200


def test_branch_user_can_reach_branch_inventory(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    resp = client.get(BRANCH_ROUTE)

    assert resp.status_code == 200


def test_deactivated_account_loses_access_on_its_next_request(client, sql):
    """decorators._current_account_or_none() re-reads is_active from the
    database on every request — this is what makes an admin-side
    deactivation take effect immediately instead of waiting for the
    session to expire. If a future change made login_required trust the
    session's cached role/is_active instead, this is the test that would
    catch it.
    """
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    assert client.get(BRANCH_ROUTE).status_code == 200

    set_account_active(sql, user["user_id"], False)

    resp = client.get(BRANCH_ROUTE)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    # The session was cleared, not just denied for this one request —
    # confirm a follow-up request doesn't quietly regain access.
    resp2 = client.get(BRANCH_ROUTE)
    assert resp2.status_code == 302
    assert "/login" in resp2.headers["Location"]


def test_must_change_password_redirects_everywhere_except_the_exempt_endpoints(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id,
                      must_change_password=True)
    login(client, user["username"], user["password"], "Branch")

    # An ordinary protected page is blocked...
    resp = client.get(BRANCH_ROUTE)
    assert resp.status_code == 302
    assert "change-password" in resp.headers["Location"]

    # ...but the change-password page itself must stay reachable, or the
    # account can never clear the flag and is locked out for good.
    resp = client.get("/change-password")
    assert resp.status_code == 200
