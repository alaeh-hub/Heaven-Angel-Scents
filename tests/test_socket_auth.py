"""Socket.IO connections are checked against live account state, like HTTP routes."""
from heaven_angel.extensions import socketio
from factories import login, make_branch, make_user


def _connect(app, client):
    return socketio.test_client(app, flask_test_client=client)


def test_active_branch_user_connects(app, client, sql):
    user = make_user(sql, role="Branch", branch_id=make_branch(sql))
    login(client, user["username"], user["password"], "Branch")
    assert _connect(app, client).is_connected()


def test_anonymous_visitor_is_refused(app, client):
    assert not _connect(app, client).is_connected()


def test_deactivated_account_is_refused_even_with_a_valid_cookie(app, client, sql):
    user = make_user(sql, role="Branch", branch_id=make_branch(sql))
    login(client, user["username"], user["password"], "Branch")
    cur = sql.cursor()
    cur.execute("UPDATE users SET is_active = FALSE WHERE user_id = %s", (user["user_id"],))
    sql.commit()
    cur.close()
    assert not _connect(app, client).is_connected()


def test_session_from_before_a_password_change_is_refused(app, client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    cur = sql.cursor()
    cur.execute("UPDATE users SET password_hash = 'x-changed' WHERE user_id = %s", (user["user_id"],))
    sql.commit()
    cur.close()
    assert not _connect(app, client).is_connected()
