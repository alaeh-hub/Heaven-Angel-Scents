"""Package orders: order -> produce -> fulfill.

A portal inquiry snapshots its package's items; Closed is the confirmed
deal (stock untouched); Fulfill takes the items out of HQ stock — blocked
with the shortfall until enough is produced; moving a fulfilled order off
Closed puts the stock back. See admin.fulfill_inquiry().
"""
import os

from factories import login, make_package, make_product, make_user, unique_suffix

HQ = 1


def _slug(app):
    return app.config.get("PARTNER_PORTAL_SLUG") or os.environ.get("PARTNER_PORTAL_SLUG")


def _add_package_item(sql, package_id, sku, qty):
    cur = sql.cursor()
    cur.execute("INSERT INTO package_items (package_id, sku, qty) VALUES (%s, %s, %s)", (package_id, sku, qty))
    sql.commit()
    cur.close()


def _one(sql, q, params):
    cur = sql.cursor(dictionary=True)
    cur.execute(q, params)
    row = cur.fetchone()
    cur.close()
    return row


def _hq_stock(sql, sku):
    row = _one(sql, "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s", (HQ, sku))
    return row["stock_qty"] if row else 0


def _set_hq_stock(sql, sku, qty):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, %s)
           ON DUPLICATE KEY UPDATE stock_qty = VALUES(stock_qty)""",
        (HQ, sku, qty),
    )
    sql.commit()
    cur.close()


def _submit_inquiry(client, app, sql):
    """A real portal inquiry for a package of 3 x A + 1 x B."""
    package_id = make_package(sql)
    a, b = make_product(sql), make_product(sql)
    _add_package_item(sql, package_id, a, 3)
    _add_package_item(sql, package_id, b, 1)
    company = f"Order Co {unique_suffix()}"
    resp = client.post(
        f"/partner-portal/{_slug(app)}/api/packages/{package_id}/inquire",
        json={"partner_type": "Reseller", "company_name": company, "contact_person": "P",
              "phone": "0917 000 0000", "email": f"o-{unique_suffix()}@example.com",
              "address": "", "message": ""},
    )
    assert resp.status_code == 201
    inquiry_id = _one(sql, "SELECT inquiry_id FROM partner_inquiries WHERE company_name = %s",
                      (company,))["inquiry_id"]
    return inquiry_id, package_id, a, b


def _admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")


def _set_status(client, inquiry_id, status):
    return client.post(f"/admin/partners/inquiries/{inquiry_id}/status", data={"status": status},
                       follow_redirects=True)


def test_inquiry_snapshots_package_items(client, app, sql):
    inquiry_id, package_id, a, b = _submit_inquiry(client, app, sql)
    # Editing the package afterwards doesn't change what was quoted.
    _add_package_item(sql, package_id, make_product(sql), 5)
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT sku, qty FROM partner_inquiry_items WHERE inquiry_id = %s", (inquiry_id,))
    items = {r["sku"]: r["qty"] for r in cur.fetchall()}
    cur.close()
    assert items == {a: 3, b: 1}


def test_closing_does_not_touch_stock_and_fulfill_is_blocked_until_produced(client, app, sql):
    inquiry_id, _, a, b = _submit_inquiry(client, app, sql)
    _set_hq_stock(sql, a, 1)
    _set_hq_stock(sql, b, 5)
    _admin(client, sql)

    _set_status(client, inquiry_id, "Closed")
    assert (_hq_stock(sql, a), _hq_stock(sql, b)) == (1, 5)

    html = client.post(f"/admin/partners/inquiries/{inquiry_id}/fulfill",
                       follow_redirects=True).get_data(as_text=True)
    assert "Not enough HQ stock" in html
    assert "need 3" in html
    assert (_hq_stock(sql, a), _hq_stock(sql, b)) == (1, 5)
    assert _one(sql, "SELECT fulfilled_at FROM partner_inquiries WHERE inquiry_id = %s",
                (inquiry_id,))["fulfilled_at"] is None


def test_fulfill_deducts_hq_stock_and_reopening_returns_it(client, app, sql):
    inquiry_id, _, a, b = _submit_inquiry(client, app, sql)
    _set_hq_stock(sql, a, 10)
    _set_hq_stock(sql, b, 10)
    _admin(client, sql)
    _set_status(client, inquiry_id, "Closed")

    client.post(f"/admin/partners/inquiries/{inquiry_id}/fulfill", follow_redirects=True)
    assert (_hq_stock(sql, a), _hq_stock(sql, b)) == (7, 9)
    assert _one(sql, "SELECT fulfilled_at FROM partner_inquiries WHERE inquiry_id = %s",
                (inquiry_id,))["fulfilled_at"] is not None
    log = _one(sql, """SELECT change_qty, before_qty, after_qty FROM stock_movement_logs
                       WHERE reference_type = 'PARTNER_INQUIRY' AND reference_id = %s AND sku = %s
                         AND movement_type = 'PACKAGE_ORDER'""", (inquiry_id, a))
    assert (log["change_qty"], log["before_qty"], log["after_qty"]) == (-3, 10, 7)

    # Fulfilling twice is refused.
    html = client.post(f"/admin/partners/inquiries/{inquiry_id}/fulfill",
                       follow_redirects=True).get_data(as_text=True)
    assert "already been fulfilled" in html
    assert _hq_stock(sql, a) == 7

    # The deal falls through: stock goes back, logged as PACKAGE_RETURN.
    _set_status(client, inquiry_id, "Declined")
    assert (_hq_stock(sql, a), _hq_stock(sql, b)) == (10, 10)
    assert _one(sql, "SELECT fulfilled_at FROM partner_inquiries WHERE inquiry_id = %s",
                (inquiry_id,))["fulfilled_at"] is None
    assert _one(sql, """SELECT COUNT(*) AS n FROM stock_movement_logs
                        WHERE reference_type = 'PARTNER_INQUIRY' AND reference_id = %s
                          AND movement_type = 'PACKAGE_RETURN'""", (inquiry_id,))["n"] == 2


def test_only_closed_inquiries_can_be_fulfilled(client, app, sql):
    inquiry_id, _, a, _ = _submit_inquiry(client, app, sql)
    _set_hq_stock(sql, a, 10)
    _admin(client, sql)
    html = client.post(f"/admin/partners/inquiries/{inquiry_id}/fulfill",
                       follow_redirects=True).get_data(as_text=True)
    assert "Only a Closed inquiry" in html
    assert _hq_stock(sql, a) == 10


def test_inquiries_page_shows_fulfill_with_shortfall(client, app, sql):
    inquiry_id, _, a, b = _submit_inquiry(client, app, sql)
    _set_hq_stock(sql, a, 0)
    _set_hq_stock(sql, b, 5)
    _admin(client, sql)
    _set_status(client, inquiry_id, "Closed")
    html = client.get("/admin/partners/inquiries").get_data(as_text=True)
    assert f'data-template="fulfill-{inquiry_id}"' in html
    assert "1 item to produce" in html
