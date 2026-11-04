import os

from flask import current_app, send_file


from heaven_angel.routes.portal._common import _verify_slug, bp


def _serve_public_site():
    """Hand the browser the built React app (public-site/, built into
    static/public-site/ by `npm run build`). Served no-cache so a fresh
    build is picked up on the next page load; the hashed JS/CSS files it
    references are ordinary static files and cache normally."""
    index_path = os.path.join(current_app.static_folder, "public-site", "index.html")
    if not os.path.isfile(index_path):
        current_app.logger.error(
            "Partner portal front end is not built: %s is missing. Run `npm install` and "
            "`npm run build` inside public-site/ (or use `npm run dev` there while developing).",
            index_path,
        )
        return (
            "The partner portal front end hasn't been built yet. "
            "Run `npm install && npm run build` inside public-site/.",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )
    response = send_file(index_path, max_age=0)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/<slug>/packages")
def packages(slug):
    """Page URL for the package list — see _serve_public_site(). Still
    the URL admin.py's Partners page copies for HQ to share."""
    _verify_slug(slug)
    return _serve_public_site()


@bp.route("/<slug>/packages/<int:package_id>")
def package_detail(slug, package_id):
    """Page URL for one package. A missing/inactive package is handled
    in the browser (the API below 404s and the app sends the visitor
    back to the list with an explanation), so this always serves the
    app once the slug checks out."""
    _verify_slug(slug)
    return _serve_public_site()


@bp.route("/<slug>/products")
def products_page(slug):
    """Page URL for the full product catalog ("View all" under the
    collection strip) — see _serve_public_site()."""
    _verify_slug(slug)
    return _serve_public_site()
