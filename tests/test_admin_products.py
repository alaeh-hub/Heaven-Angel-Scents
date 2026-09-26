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


# ---------------------------------------------------------------- CSV import/export
def _import_csv(client, text):
    import io
    return client.post(
        "/admin/products/import",
        data={"csv_file": (io.BytesIO(text.encode("utf-8-sig")), "products.csv")},
        content_type="multipart/form-data",
    )


def _product(sql, sku):
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT sku, item_name, category, unit, price FROM products WHERE sku = %s", (sku,))
    row = cur.fetchone()
    cur.close()
    return row


def test_import_accepts_a_bulk_row_as_bulk_refill(client, sql):
    import uuid
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    code = "T" + uuid.uuid4().hex[:6].upper()

    _import_csv(client, "base_code,item_name,variant,unit,price\n"
                        f"{code},Seraph,Unisex,50ML,350.00\n"
                        f"{code},Seraph,Unisex,BULK,12.50\n")

    bottle = _product(sql, f"{code}-50ML")
    bulk = _product(sql, f"{code}-BULK")
    assert bottle["category"] == "Bottled"
    assert bulk["category"] == "Bulk/Refill"
    assert bulk["unit"] == "BULK"
    assert str(bulk["price"]) == "12.50"


def test_exported_bulk_rows_import_back_as_updates(client, sql):
    import uuid
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    code = "T" + uuid.uuid4().hex[:6].upper()
    _import_csv(client, "base_code,item_name,variant,unit,price\n"
                        f"{code},Seraph,Unisex,BULK,12.50\n")

    exported = client.get("/admin/products/export").get_data().decode("utf-8-sig")
    assert f"{code},Seraph,Unisex,BULK,12.50" in exported

    # Re-import the exported file with the BULK price changed.
    edited = exported.replace(f"{code},Seraph,Unisex,BULK,12.50", f"{code},Seraph,Unisex,BULK,15.00")
    resp = _import_csv(client, edited)

    assert resp.status_code == 302
    assert str(_product(sql, f"{code}-BULK")["price"]) == "15.00"


def test_products_page_counts_skus_by_base_code(client, sql):
    """A1-85ML / A1-50ML / A1-BULK are one SKU ("A1") in the count."""
    import re
    import uuid
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")

    def catalog_count():
        html = client.get("/admin/products").get_data(as_text=True)
        return int(re.search(r'id="catalogCount">(\d+) SKU', html).group(1))

    before = catalog_count()
    code = "T" + uuid.uuid4().hex[:6].upper()
    _import_csv(client, "base_code,item_name,variant,unit,price\n"
                        f"{code},Seraph,Unisex,85ML,500.00\n"
                        f"{code},Seraph,Unisex,50ML,350.00\n"
                        f"{code},Seraph,Unisex,BULK,12.50\n")

    assert catalog_count() == before + 1
