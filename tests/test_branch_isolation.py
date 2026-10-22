"""Branch data isolation: one branch's staff must never read or change
another branch's sales, receipts or AI stock drafts, and an account that
isn't tied to a branch must never be treated as unrestricted (the scan
and void/settle helpers use branch_id=None to mean "HQ, no restriction").
"""
import pytest

from factories import (get_form_token, login, make_branch, make_inventory, make_product,
                       make_user)
from heaven_angel.utils import make_receipt_code

RECORD_SALE_URL = "/branch/record-sale"


@pytest.fixture()
def other_client(app):
    return app.test_client()


def _branch_login(client, sql, branch_id=None):
    branch_id = branch_id or make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    return branch_id


def _record_sale(client, sql, branch_id):
    """Branch B sells one bottle; returns its sale_id."""
    sku = make_product(sql, price="50.00")
    make_inventory(sql, branch_id, sku, stock_qty=10)
    client.post(RECORD_SALE_URL, data={
        "sku": sku, "sale_type": "Sale", "payment_method": "Cash", "qty_sold": "1",
        "unit_price": "50.00", "form_token": get_form_token(client, RECORD_SALE_URL),
    })
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT MAX(sale_id) AS id FROM sales WHERE branch_id = %s AND sku = %s", (branch_id, sku))
    sale_id = cur.fetchone()["id"]
    cur.close()
    assert sale_id, "setup: sale was not recorded"
    return sale_id


def _sale_row(sql, sale_id):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT * FROM sales WHERE sale_id = %s", (sale_id,))
    row = cur.fetchone()
    cur.close()
    return row


def _make_credit(sql, sale_id):
    cur = sql.cursor()
    cur.execute("UPDATE sales SET payment_method = 'Credit', buyer_name = 'Tester' WHERE sale_id = %s", (sale_id,))
    sql.commit()
    cur.close()


def _verify(client, sale_id, app):
    with app.test_request_context():
        code = make_receipt_code(sale_id)
    return client.post("/scan/verify", json={"code": code}).get_json()


def test_branch_cannot_download_another_branchs_sale_receipt(client, other_client, sql):
    owner_branch = _branch_login(client, sql)
    sale_id = _record_sale(client, sql, owner_branch)
    _branch_login(other_client, sql)

    assert client.get(f"/branch/record-sale/{sale_id}/receipt").status_code == 200
    assert other_client.get(f"/branch/record-sale/{sale_id}/receipt").status_code == 404


def test_scan_verify_is_scoped_to_the_callers_branch(app, client, other_client, sql):
    owner_branch = _branch_login(client, sql)
    sale_id = _record_sale(client, sql, owner_branch)
    _branch_login(other_client, sql)

    assert _verify(client, sale_id, app)["match"] is True
    assert _verify(other_client, sale_id, app)["match"] is False


def test_scan_void_and_settle_refuse_another_branchs_sale(client, other_client, sql):
    owner_branch = _branch_login(client, sql)
    sale_id = _record_sale(client, sql, owner_branch)
    _make_credit(sql, sale_id)
    _branch_login(other_client, sql)

    void = other_client.post(f"/scan/sales/{sale_id}/void", json={"reason": "not mine"})
    settle = other_client.post(f"/scan/sales/{sale_id}/settle-credit")

    assert void.status_code == 400
    assert settle.status_code == 400
    row = _sale_row(sql, sale_id)
    assert row is not None, "another branch's void must not delete the sale"
    assert row["credit_settled_at"] is None


def test_branch_cannot_approve_or_reject_another_branchs_ai_draft(client, other_client, sql):
    owner_branch = make_branch(sql)
    cur = sql.cursor()
    cur.execute("INSERT INTO ai_stock_drafts (branch_id, status) VALUES (%s, 'Pending Review')", (owner_branch,))
    sql.commit()
    draft_id = cur.lastrowid
    cur.close()
    _branch_login(other_client, sql)

    assert other_client.post(f"/ai/drafts/{draft_id}/approve").status_code == 403
    assert other_client.post(f"/ai/drafts/{draft_id}/reject").status_code == 403
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT status FROM ai_stock_drafts WHERE draft_id = %s", (draft_id,))
    assert cur.fetchone()["status"] == "Pending Review"
    cur.close()


def test_branch_account_without_a_branch_is_not_treated_as_unrestricted(app, client, other_client, sql):
    """branch_id can become NULL (users.branch_id is ON DELETE SET NULL).
    Such an account must be locked out, not given HQ-wide scan/void access."""
    owner_branch = _branch_login(client, sql)
    sale_id = _record_sale(client, sql, owner_branch)
    _make_credit(sql, sale_id)

    orphan = make_user(sql, role="Branch", branch_id=None)
    login(other_client, orphan["username"], orphan["password"], "Branch")

    verify = other_client.post("/scan/verify", json={"code": _code(app, sale_id)})
    void = other_client.post(f"/scan/sales/{sale_id}/void", json={"reason": "orphan"})
    settle = other_client.post(f"/scan/sales/{sale_id}/settle-credit")

    assert verify.status_code in (302, 403)
    assert void.status_code in (302, 403)
    assert settle.status_code in (302, 403)
    row = _sale_row(sql, sale_id)
    assert row is not None and row["credit_settled_at"] is None


def _code(app, sale_id):
    with app.test_request_context():
        return make_receipt_code(sale_id)
