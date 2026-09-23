"""Public partner portal: the page URLs that serve the React app
(public-site/, built into static/public-site/) and the JSON API it talks
to — see routes/portal.py."""
import decimal
import os

import pytest

from factories import make_package, make_product, unique_suffix


@pytest.fixture()
def slug(app):
    return app.config.get("PARTNER_PORTAL_SLUG") or os.environ.get("PARTNER_PORTAL_SLUG")


def add_package_item(sql, package_id, sku, qty):
    cur = sql.cursor()
    cur.execute(
        "INSERT INTO package_items (package_id, sku, qty) VALUES (%s, %s, %s)",
        (package_id, sku, qty),
    )
    sql.commit()
    cur.close()


def make_filled_package(sql, **kwargs):
    """A package with two products: 2 x ₱100 + 1 x ₱250 = ₱450 reference."""
    package_id = make_package(sql, **kwargs)
    add_package_item(sql, package_id, make_product(sql, price="100.00"), 2)
    add_package_item(sql, package_id, make_product(sql, price="250.00"), 1)
    return package_id


def inquiry_payload(**overrides):
    payload = {
        "partner_type": "Distributor",
        "company_name": f"Portal Test Co {unique_suffix()}",
        "contact_person": "Test Person",
        "phone": "0917 123 4567",
        "email": f"portal-{unique_suffix()}@example.com",
        "address": "",
        "message": "Hello",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------- pages

@pytest.fixture()
def built_site(app, tmp_path):
    """Point the app at a fake static folder holding a stand-in build."""
    (tmp_path / "public-site").mkdir()
    (tmp_path / "public-site" / "index.html").write_text(
        "<!DOCTYPE html><div id=\"root\"></div><!-- test build -->", encoding="utf-8")
    original = app.static_folder
    app.static_folder = str(tmp_path)
    yield
    app.static_folder = original


@pytest.mark.parametrize("path", ["/packages", "/packages/123"])
def test_page_urls_serve_the_built_app(client, slug, built_site, path):
    resp = client.get(f"/partner-portal/{slug}{path}")

    assert resp.status_code == 200
    assert "test build" in resp.get_data(as_text=True)
    assert resp.headers["Cache-Control"] == "no-cache"


def test_page_url_explains_a_missing_build(client, slug, app, tmp_path):
    original = app.static_folder
    app.static_folder = str(tmp_path)
    try:
        resp = client.get(f"/partner-portal/{slug}/packages")
    finally:
        app.static_folder = original

    assert resp.status_code == 503
    assert "npm run build" in resp.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/packages", "/packages/1", "/products", "/api/packages", "/api/packages/1", "/api/products"])
def test_wrong_slug_is_a_plain_404(client, path):
    assert client.get(f"/partner-portal/not-the-slug{path}").status_code == 404


# ------------------------------------------------------------ list API

def test_package_list_includes_discounted_totals(client, sql, slug):
    package_id = make_filled_package(sql, discount_percent="10.00")

    resp = client.get(f"/partner-portal/{slug}/api/packages")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["scope"] == "all"
    assert body["partner_types"] == ["Distributor", "Reseller"]
    pkg = next(p for p in body["packages"] if p["package_id"] == package_id)
    assert pkg["item_count"] == 2
    assert pkg["reference_total"] == 450.0
    assert pkg["discounted_total"] == 405.0
    assert pkg["discount_percent"] == 10.0


def test_package_list_scope_filter_keeps_both_scoped_packages(client, sql, slug):
    distributor = make_filled_package(sql, partner_scope="Distributor")
    reseller = make_filled_package(sql, partner_scope="Reseller")
    both = make_filled_package(sql, partner_scope="Both")

    body = client.get(f"/partner-portal/{slug}/api/packages?scope=Reseller").get_json()
    ids = {p["package_id"] for p in body["packages"]}

    assert body["scope"] == "Reseller"
    assert reseller in ids and both in ids
    assert distributor not in ids


def test_package_list_ignores_an_unknown_scope(client, sql, slug):
    body = client.get(f"/partner-portal/{slug}/api/packages?scope=Nonsense").get_json()
    assert body["scope"] == "all"


# ---------------------------------------------------------- detail API

def test_package_detail_lists_items_and_a_csrf_token(client, sql, slug):
    package_id = make_filled_package(sql, discount_percent="20.00")

    resp = client.get(f"/partner-portal/{slug}/api/packages/{package_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["package"]["package_id"] == package_id
    assert body["package"]["item_count"] == 2
    assert body["package"]["reference_total"] == 450.0
    assert body["package"]["discounted_total"] == 360.0
    assert {i["qty"] for i in body["items"]} == {1, 2}
    item = body["items"][0]
    assert item["image_url"] is None  # no photo -> the app shows initials
    assert item["initials"] == "TP"   # "Test Product ..."
    assert body["csrf_token"]


def test_package_detail_of_an_inactive_package_is_a_json_404(client, sql, slug):
    package_id = make_filled_package(sql)
    cur = sql.cursor()
    cur.execute("UPDATE packages SET is_active = FALSE WHERE package_id = %s", (package_id,))
    cur.close()

    resp = client.get(f"/partner-portal/{slug}/api/packages/{package_id}")

    assert resp.status_code == 404
    assert "no longer available" in resp.get_json()["error"]


# --------------------------------------------------------- inquiry API

def test_inquiry_is_saved_with_a_server_computed_order_amount(client, sql, slug):
    package_id = make_filled_package(sql, discount_percent="10.00")
    payload = inquiry_payload()

    resp = client.post(f"/partner-portal/{slug}/api/packages/{package_id}/inquire", json=payload)

    assert resp.status_code == 201
    assert "Thanks" in resp.get_json()["message"]
    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT * FROM partner_inquiries WHERE company_name = %s", (payload["company_name"],))
    row = cur.fetchone()
    cur.close()
    assert row["package_id"] == package_id
    assert row["partner_type"] == "Distributor"
    assert row["partner_id"] is not None
    assert row["order_amount"] == decimal.Decimal("405.00")


def test_inquiry_validation_error_comes_back_as_json(client, sql, slug):
    package_id = make_filled_package(sql)

    resp = client.post(
        f"/partner-portal/{slug}/api/packages/{package_id}/inquire",
        json=inquiry_payload(partner_type="Reseller", company_name="  "),
    )

    assert resp.status_code == 400
    assert "Your full name" in resp.get_json()["error"]


def test_inquiry_requires_a_known_partner_type(client, sql, slug):
    package_id = make_filled_package(sql)

    resp = client.post(
        f"/partner-portal/{slug}/api/packages/{package_id}/inquire",
        json=inquiry_payload(partner_type="Wholesaler"),
    )

    assert resp.status_code == 400
    assert "distributor or a reseller" in resp.get_json()["error"]


def test_inquiry_rejects_a_non_json_body(client, sql, slug):
    package_id = make_filled_package(sql)

    resp = client.post(
        f"/partner-portal/{slug}/api/packages/{package_id}/inquire",
        data={"partner_type": "Distributor"},
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_inquiry_for_an_unavailable_package_is_a_404(client, slug):
    resp = client.post(f"/partner-portal/{slug}/api/packages/999999/inquire", json=inquiry_payload())
    assert resp.status_code == 404


# --------------------------------------------------------- catalog API

def make_named_product(sql, name, variant, unit, image_path=None):
    sku = f"CAT-{unique_suffix().upper()}"
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO products (sku, item_name, variant, unit, price, image_path)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (sku, name, variant, unit, "100.00", image_path),
    )
    sql.commit()
    cur.close()
    return sku


def test_catalog_groups_sizes_under_one_product(client, sql, slug):
    name = f"Catalog Scent {unique_suffix()}"
    make_named_product(sql, name, "Female", "85ML")
    make_named_product(sql, name, "Female", "10ML", image_path="uploads/products/x.jpg")

    body = client.get(f"/partner-portal/{slug}/api/products?gender=Female&page=1").get_json()
    pages = [body]
    while body["page"] < body["pages"]:
        body = client.get(f"/partner-portal/{slug}/api/products?gender=Female&page={body['page'] + 1}").get_json()
        pages.append(body)
    entry = next(p for page in pages for p in page["products"] if p["item_name"] == name)

    assert entry["variant"] == "Female"
    assert entry["sizes"] == ["10ML", "85ML"]  # smallest first
    assert entry["image_url"].endswith("/static/uploads/products/x.jpg")


def test_catalog_gender_filter_and_counts(client, sql, slug):
    make_named_product(sql, f"Catalog Male {unique_suffix()}", "Male", "50ML")

    everything = client.get(f"/partner-portal/{slug}/api/products").get_json()
    men = client.get(f"/partner-portal/{slug}/api/products?gender=Male").get_json()

    assert everything["gender"] == "all"
    assert everything["counts"]["all"] == sum(everything["counts"][g] for g in ("Male", "Female", "Unisex"))
    assert men["gender"] == "Male"
    assert men["total"] == everything["counts"]["Male"]
    assert all(p["variant"] == "Male" for p in men["products"])


def test_catalog_paginates_and_clamps_the_page(client, sql, slug):
    for _ in range(13):
        make_named_product(sql, f"Catalog Page {unique_suffix()}", "Unisex", "50ML")

    first = client.get(f"/partner-portal/{slug}/api/products?gender=Unisex").get_json()
    beyond = client.get(f"/partner-portal/{slug}/api/products?gender=Unisex&page=999").get_json()

    assert first["per_page"] == 12
    assert len(first["products"]) == 12
    assert first["pages"] >= 2
    assert beyond["page"] == beyond["pages"]
    assert beyond["products"]


def test_catalog_page_url_serves_the_app(client, slug, built_site):
    resp = client.get(f"/partner-portal/{slug}/products")
    assert resp.status_code == 200
    assert "test build" in resp.get_data(as_text=True)
