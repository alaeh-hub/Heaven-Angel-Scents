"""routes/admin.py's Formulas page — the Bulk/Refill rate per mL
(save_bulk_rate(), unchanged) plus a flat base cost of goods per Bottled
packaging size (save_unit_cogs(), see unit_cogs_settings in schema.sql).

This used to be a materials-cart formula per size (unit_formula_items,
save_formula()) — removed because it was double bookkeeping: a bulk
batch already records exactly which materials and quantities went into
it (see bulk_batches/bulk_batch_materials and tests/test_bulk_batches.py),
so a Bottled production run's cost of goods is now just this one
hand-typed number per size, not a re-derived recipe. unit_formula_items
itself is left in the schema as an unused, harmless leftover rather than
dropped — nothing here exercises it anymore.
"""
from decimal import Decimal

from factories import get_form_token, login, make_product, make_user

SAVE_UNIT_COGS_URL = "/admin/formulas/save-unit-cogs"
SAVE_BULK_RATE_URL = "/admin/formulas/save-bulk-rate"
PRODUCTION_URL = "/admin/production"
MATERIALS_URL = "/admin/materials"
FORMULAS_URL = "/admin/formulas"


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def _unit_cogs(sql, unit):
    cur = sql.cursor(dictionary=True)
    cur.execute(
        "SELECT base_cost_per_unit FROM unit_cogs_settings WHERE unit = %s", (unit,))
    row = cur.fetchone()
    cur.close()
    return row["base_cost_per_unit"] if row else None


def test_formulas_page_renders_and_lists_every_bottled_size(client, sql):
    _signed_in_admin(client, sql)
    resp = client.get(FORMULAS_URL)
    assert resp.status_code == 200
    # All 4 Bottled packaging sizes are always listed.
    assert b"85ML" in resp.data and b"3ML" in resp.data


def test_materials_page_no_longer_carries_cost_of_goods(client, sql):
    """Cost of goods (and its own history) lives on the Formulas /
    Production Log / Bulk Batches pages now — Materials is the raw
    materials purchase list plus real stock on hand (see
    admin.formulas()/admin.production()/admin.bulk_batches() and
    templates/admin/materials.html)."""
    _signed_in_admin(client, sql)
    resp = client.get(MATERIALS_URL)
    assert resp.status_code == 200
    assert b"Log material usage" not in resp.data
    assert b"Cost of goods logged" not in resp.data


def test_production_page_renders_with_a_bottled_product(client, sql):
    _signed_in_admin(client, sql)
    resp = client.get(PRODUCTION_URL)
    assert resp.status_code == 200

    sku = make_product(sql, unit="50ML")
    resp = client.get(PRODUCTION_URL)
    assert resp.status_code == 200
    assert sku.encode() in resp.data


def test_save_unit_cogs_sets_and_replaces_the_base_cost(client, sql):
    _signed_in_admin(client, sql)

    resp = client.post(SAVE_UNIT_COGS_URL, data={
        "unit": "85ML",
        "base_cost_per_unit": "95.00",
    })
    assert resp.status_code == 302
    assert _unit_cogs(sql, "85ML") == Decimal("95.0000")

    # Saving again replaces the figure, not adds to it.
    client.post(SAVE_UNIT_COGS_URL, data={
        "unit": "85ML",
        "base_cost_per_unit": "110.50",
    })
    assert _unit_cogs(sql, "85ML") == Decimal("110.5000")

    # Every other size is untouched by that.
    assert _unit_cogs(sql, "50ML") != Decimal("110.5000")


def test_save_unit_cogs_rejects_an_invalid_unit(client, sql):
    _signed_in_admin(client, sql)
    resp = client.post(SAVE_UNIT_COGS_URL, data={
        "unit": "BULK",  # Bulk/Refill has its own rate, not a base cost
        "base_cost_per_unit": "10.00",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"select a valid packaging size" in resp.data.lower()


def test_save_bulk_rate_updates_the_shared_rate(client, sql):
    _signed_in_admin(client, sql)
    resp = client.post(SAVE_BULK_RATE_URL, data={"rate_per_ml": "2.5000"})
    assert resp.status_code == 302

    cur = sql.cursor(dictionary=True)
    cur.execute("SELECT rate_per_ml FROM bulk_rate_settings WHERE id = 1")
    row = cur.fetchone()
    cur.close()
    assert row["rate_per_ml"] == Decimal("2.5000")


def test_dashboard_and_reports_pages_render(client, sql):
    _signed_in_admin(client, sql)
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/reports").status_code == 200
    assert client.get("/admin/api/reports-data").status_code == 200
