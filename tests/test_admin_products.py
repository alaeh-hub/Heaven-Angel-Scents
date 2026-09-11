"""routes/admin.py's products() catalog page — specifically the
product_thumb() macro's no-photo fallback (see templates/_macros.html
and product_avatar() in utils.py). A product with no image_path should
render a generated initials avatar rather than a broken/empty-image
icon, and end-to-end through the real route + template + Jinja globals
wiring (app.py registers product_avatar as a jinja global) — not just
the pure function tested in test_utils.py.
"""
from factories import login, make_product, make_user


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def test_product_with_no_photo_renders_an_initials_avatar(client, sql):
    _signed_in_admin(client, sql)
    make_product(sql)  # make_product never sets image_path -> NULL

    resp = client.get("/admin/products")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "product-thumb-avatar" in html
    # The old broken/empty-image placeholder icon must be gone.
    assert "M21 15l-5-5L5 21" not in html


def test_unit_filter_defaults_to_85ml_but_keeps_all_units_option(client, sql):
    """The catalog's unit filter should open pre-set to 85ML (the most
    commonly sold size) rather than showing everything, but "All units"
    must still be there for anyone who wants to clear it."""
    _signed_in_admin(client, sql)
    make_product(sql, unit="85ML")

    resp = client.get("/admin/products")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<option value="">All units</option>' in html
    assert '<option value="85ML" selected>85ML</option>' in html
    # No other unit option should carry the default selection.
    assert '<option value="50ML" selected>' not in html
