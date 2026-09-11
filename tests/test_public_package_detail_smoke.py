"""Smoke test for the public partner-portal package detail page — this
page doesn't load main.js and has its own inline copy of the shared
loading-overlay JS (see its own comment), so this just confirms the
template still renders cleanly (no Jinja/JS embedding mistakes) after
that addition. Not meant to exercise the inquiry flow itself.
"""
import os

from factories import make_package


def test_public_package_detail_page_renders(client, sql, app):
    slug = app.config.get("PARTNER_PORTAL_SLUG") or os.environ.get("PARTNER_PORTAL_SLUG")
    package_id = make_package(sql)

    resp = client.get(f"/partner-portal/{slug}/packages/{package_id}")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "loading-overlay" in html
    assert "showLoadingOverlay" in html
