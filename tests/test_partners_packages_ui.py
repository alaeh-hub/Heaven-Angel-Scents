"""Same collapsible mobile-card treatment as Record Sale/Customers (see
test_record_sale_ui.py) applied to All partners, All packages, Package
inquiries, and Branches — each row collapses to just its primary
identity (Partner / Package / Company / Branch), with the rest behind
a "Details" toggle. Only checks the rendered markup — the collapse/
expand interaction itself is CSS + client-side JS, out of reach for a
server-side test.
"""
from factories import (login, make_branch, make_package, make_partner,
                       make_partner_inquiry, make_user)


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def test_partners_table_renders_collapsible_mobile_markup(client, sql):
    _signed_in_admin(client, sql)
    make_partner(sql)

    resp = client.get("/admin/partners")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # Contact, Package sales, Last inquiry collapse away.
    assert html.count("mobile-detail") == 3


def test_packages_table_renders_collapsible_mobile_markup(client, sql):
    _signed_in_admin(client, sql)
    make_package(sql)

    resp = client.get("/admin/packages")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # Items, Reference value, Discount, Order price collapse away.
    assert html.count("mobile-detail") == 4


def test_partner_inquiries_table_renders_collapsible_mobile_markup(client, sql):
    _signed_in_admin(client, sql)
    make_partner_inquiry(sql)

    resp = client.get("/admin/partners/inquiries")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # Contact, Package, Status, Remarks collapse away; When is
    # mobile-fold (duplicated in Company's own badge row) instead.
    # Matches the closing quote of a class attribute specifically —
    # this page's own <style> block also mentions "mobile-detail" a
    # few times in CSS selectors/comments, which a bare substring count
    # would double-count.
    assert html.count('mobile-detail"') == 4
    # The max-width column-width fix: these classes must land on the
    # actual data cell, not just the <th> (see the CSS comment above
    # .pi-col-company in partner_inquiries.html) — a <th>-only max-width
    # never actually constrains an auto-layout column's real width.
    assert 'class="pi-col-company"' in html
    assert 'class="pi-col-contact mobile-detail"' in html
    assert 'class="pi-col-package mobile-detail"' in html
    assert 'class="pi-col-remarks mobile-detail"' in html


def test_branches_table_renders_collapsible_mobile_markup(client, sql):
    _signed_in_admin(client, sql)
    make_branch(sql)

    resp = client.get("/admin/branches")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "table-collapsible" in html
    assert 'class="row-toggle-btn" aria-expanded="false"' in html
    assert 'class="mobile-toggle-row"' in html
    # Location, Accounts, Added collapse away — 3 per row. schema.sql
    # also seeds a non-HQ branch that's already in the list alongside
    # the one just created here, so count rows rather than assume 1.
    cur = sql.cursor()
    cur.execute("SELECT COUNT(*) FROM branches WHERE is_hq = FALSE")
    branch_count = cur.fetchone()[0]
    cur.close()
    assert html.count("mobile-detail") == branch_count * 3
