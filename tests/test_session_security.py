"""Password changes and resets must end every other signed-in session for
the account (decorators.password_fingerprint), without logging out the
session that made the change.
"""
import pytest
from werkzeug.security import generate_password_hash

from factories import login, make_branch, make_user

BRANCH_ROUTE = "/branch/inventory"


@pytest.fixture()
def second_client(app):
    return app.test_client()


def _branch_user(sql):
    return make_user(sql, role="Branch", branch_id=make_branch(sql))


def test_changing_password_signs_out_other_sessions_but_not_this_one(client, second_client, sql):
    user = _branch_user(sql)
    login(client, user["username"], user["password"], "Branch")
    login(second_client, user["username"], user["password"], "Branch")
    assert second_client.get(BRANCH_ROUTE).status_code == 200

    resp = client.post("/change-password", data={
        "current_password": user["password"],
        "new_password": "A-brand-new-Passw0rd",
        "confirm_password": "A-brand-new-Passw0rd",
    })
    assert resp.status_code == 302

    assert client.get(BRANCH_ROUTE).status_code == 200, "the session that changed it stays signed in"
    other = second_client.get(BRANCH_ROUTE)
    assert other.status_code == 302 and "/login" in other.headers["Location"]


def test_admin_password_reset_signs_out_the_target_users_session(client, sql):
    user = _branch_user(sql)
    login(client, user["username"], user["password"], "Branch")
    assert client.get(BRANCH_ROUTE).status_code == 200

    cur = sql.cursor()
    cur.execute("UPDATE users SET password_hash = %s WHERE user_id = %s",
                (generate_password_hash("Reset-by-HQ-123"), user["user_id"]))
    sql.commit()
    cur.close()

    resp = client.get(BRANCH_ROUTE)
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_session_without_a_password_fingerprint_is_rejected(client, sql):
    user = _branch_user(sql)
    login(client, user["username"], user["password"], "Branch")
    with client.session_transaction() as sess:
        sess.pop("pwv", None)

    resp = client.get(BRANCH_ROUTE)
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
