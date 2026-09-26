"""Live bell notifications (sockets.notify_bell) for the stock request
lifecycle: each step tells the *other* side — HQ admins when a branch
requests or receives, the branch when HQ dispatches or rejects. The
browser shows these in the bell and as a toast (main.js's
initRealtime()). notify_bell is swapped for a recorder so these check
who gets told what, without a live socket.
"""
import pytest

import routes.admin
import routes.branch
from factories import (get_form_token, get_request_item, login, make_branch,
                       make_inventory, make_product, make_stock_request,
                       make_user)

HQ_BRANCH_ID = 1


@pytest.fixture
def bells(monkeypatch):
    sent = []

    def record(message, room=None, level="info"):
        sent.append({"message": message, "room": room, "level": level})

    monkeypatch.setattr(routes.branch, "notify_bell", record)
    monkeypatch.setattr(routes.admin, "notify_bell", record)
    return sent


def _signed_in_branch(client, sql):
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    return branch_id


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")


def test_branch_request_notifies_admins(client, sql, bells):
    _signed_in_branch(client, sql)
    sku = make_product(sql)
    token = get_form_token(client, "/branch/request-stock")

    client.post("/branch/request-stock",
                data={"sku[]": [sku], "requested_qty[]": ["3"], "form_token": token})

    assert len(bells) == 1
    assert bells[0]["room"] == "admin"
    assert "requested delivery DR-" in bells[0]["message"]
    assert "(1 item)" in bells[0]["message"]


def test_dispatch_notifies_the_branch(client, sql, bells):
    branch_id = make_branch(sql)
    _signed_in_admin(client, sql)
    sku = make_product(sql)
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=50)
    request_id = make_stock_request(sql, branch_id, [{"sku": sku, "requested_qty": 5}])

    client.post(f"/admin/requests/{request_id}/dispatch",
                data={"item_id[]": [str(get_request_item(sql, request_id, sku)["item_id"])],
                      "dispatched_qty[]": ["5"]})

    assert len(bells) == 1
    assert bells[0]["room"] == f"branch:{branch_id}"
    assert bells[0]["level"] == "success"
    assert "is on its way from HQ" in bells[0]["message"]


def test_failed_dispatch_sends_nothing(client, sql, bells):
    branch_id = make_branch(sql)
    _signed_in_admin(client, sql)
    sku = make_product(sql)
    make_inventory(sql, HQ_BRANCH_ID, sku, stock_qty=1)
    request_id = make_stock_request(sql, branch_id, [{"sku": sku, "requested_qty": 5}])

    client.post(f"/admin/requests/{request_id}/dispatch",
                data={"item_id[]": [str(get_request_item(sql, request_id, sku)["item_id"])],
                      "dispatched_qty[]": ["5"]})

    assert bells == []


def test_reject_notifies_the_branch_but_not_on_a_no_op(client, sql, bells):
    branch_id = make_branch(sql)
    _signed_in_admin(client, sql)
    sku = make_product(sql)
    pending = make_stock_request(sql, branch_id, [{"sku": sku, "requested_qty": 5}])
    in_transit = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 5, "dispatched_qty": 5}],
        status="In Transit")

    client.post(f"/admin/requests/{pending}/reject")
    client.post(f"/admin/requests/{in_transit}/reject")

    assert len(bells) == 1
    assert bells[0]["room"] == f"branch:{branch_id}"
    assert bells[0]["level"] == "warning"
    assert "rejected" in bells[0]["message"]


@pytest.mark.parametrize("received, level, text", [
    ("10", "success", "received delivery"),
    ("6", "warning", "4 unit(s) unaccounted for"),
])
def test_receipt_notifies_admins(client, sql, bells, received, level, text):
    branch_id = _signed_in_branch(client, sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=0)
    request_id = make_stock_request(
        sql, branch_id, [{"sku": sku, "requested_qty": 10, "dispatched_qty": 10}],
        status="In Transit")
    item = get_request_item(sql, request_id, sku)

    client.post("/branch/receive-stock", data={
        "request_id": str(request_id), "item_id[]": [str(item["item_id"])],
        "received_qty[]": [received], "damaged_qty[]": ["0"],
    })

    assert len(bells) == 1
    assert bells[0]["room"] == "admin"
    assert bells[0]["level"] == level
    assert text in bells[0]["message"]
