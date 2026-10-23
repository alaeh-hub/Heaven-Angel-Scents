import mysql.connector.errors
import pytest

from heaven_angel import db


class _FakePool:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def get_connection(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise mysql.connector.errors.PoolError("Failed getting connection; pool exhausted")
        return "conn"


def _app_ctx(app, wait):
    app.config["MYSQL_POOL_WAIT_SECONDS"] = wait
    return app.app_context()


def test_checkout_waits_for_a_free_connection(app, monkeypatch):
    pool = _FakePool(failures=3)
    monkeypatch.setattr(db, "_get_pool", lambda: pool)
    with _app_ctx(app, 5):
        assert db._checkout_connection() == "conn"
    assert pool.calls == 4


def test_checkout_raises_after_deadline(app, monkeypatch):
    pool = _FakePool(failures=10**9)
    monkeypatch.setattr(db, "_get_pool", lambda: pool)
    with _app_ctx(app, 0.2), pytest.raises(mysql.connector.errors.PoolError):
        db._checkout_connection()
