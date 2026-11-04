import decimal

from flask import current_app, jsonify, request, url_for
from flask_wtf.csrf import generate_csrf

from heaven_angel.db import query
from heaven_angel.services.finance import package_value
from heaven_angel.utils import (
    PARTNER_TYPES, product_avatar,
)

from heaven_angel.routes.portal._common import _cacheable, _read_limit, _verify_slug, bp
from heaven_angel.routes.portal.inquiries import CONTACT_METHODS, active_package_or_none


def _money(value):
    """Decimal -> float rounded to centavos, for JSON. Display-only: the
    stored order_amount on an inquiry is still computed server-side in
    inquire() below, never taken from anything the browser sends back."""
    return float(decimal.Decimal(value).quantize(decimal.Decimal("0.01")))


def _package_summary(row, item_count=None, previews=None):
    """One package as the React app sees it — the same fields the old
    Jinja templates read, with totals already discounted via
    package_value(). `unit_count` is the total pieces (sum of qty), for
    the per-piece price on the cards; `previews` is a few of its
    products (see _package_previews()) for the card's bottle stack."""
    reference_total, discounted_total = package_value(
        row["discount_percent"], row["reference_total"])
    return {
        "package_id": row["package_id"],
        "package_name": row["package_name"],
        "description": row["description"],
        "partner_scope": row["partner_scope"],
        "discount_percent": float(row["discount_percent"]),
        "item_count": int(row["item_count"] if item_count is None else item_count),
        "reference_total": _money(reference_total),
        "discounted_total": _money(discounted_total),
        "unit_count": int(row.get("unit_count") or 0),
        "previews": previews or [],
    }


PACKAGE_PREVIEWS = 3


def _package_previews(package_ids):
    """Up to PACKAGE_PREVIEWS products per package (largest qty first),
    as {item_name, variant, image_url}, keyed by package_id. The app
    falls back to the gender's placeholder bottle when image_url is None
    (see ProductVisual), the same as everywhere else products show."""
    if not package_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(package_ids))
    rows = query(
        f"""SELECT pi.package_id, p.item_name, p.variant, p.image_path
            FROM package_items pi JOIN products p ON p.sku = pi.sku
            WHERE pi.package_id IN ({placeholders})
            ORDER BY pi.package_id, pi.qty DESC, p.item_name""",
        tuple(package_ids),
    )
    previews = {}
    for r in rows:
        bucket = previews.setdefault(r["package_id"], [])
        if len(bucket) < PACKAGE_PREVIEWS:
            bucket.append({
                "item_name": r["item_name"],
                "variant": r["variant"],
                "image_url": url_for("static", filename=r["image_path"]) if r["image_path"] else None,
            })
    return previews


PRODUCTS_PER_PAGE = 12


PRODUCT_GENDERS = ("Male", "Female")


# Smallest bottle first, bulk last, whatever order the SKUs were added in.
_UNIT_ORDER = "FIELD(unit, '3ML', '10ML', '50ML', '85ML', 'BULK')"


@bp.route("/<slug>/api/products")
@_read_limit
def api_products(slug):
    """Every scent in the catalog, one entry per product name + gender
    (the SKU table has a row per size, so sizes are folded into a list),
    optionally filtered by gender and paginated.

    ?gender=Male|Female (anything else = all), ?page=N (1-based,
    clamped to the last page). Also returns per-gender counts for the
    filter tabs.
    """
    _verify_slug(slug)

    gender = request.args.get("gender", "all")
    if gender not in PRODUCT_GENDERS:
        gender = "all"
    where = "WHERE variant = %s" if gender != "all" else "WHERE variant IN ('Male', 'Female')"
    params = (gender,) if gender != "all" else ()

    count_rows = query(
        """SELECT variant, COUNT(*) AS c
           FROM (SELECT item_name, variant FROM products GROUP BY item_name, variant) grouped
           GROUP BY variant"""
    )
    counts = {g: 0 for g in PRODUCT_GENDERS}
    for row in count_rows:
        if row["variant"] in counts:
            counts[row["variant"]] = int(row["c"])
    counts["all"] = sum(counts.values())

    total = counts[gender]
    pages = max(1, -(-total // PRODUCTS_PER_PAGE))
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)

    rows = query(
        f"""SELECT item_name, variant,
                   GROUP_CONCAT(DISTINCT unit ORDER BY {_UNIT_ORDER} SEPARATOR ',') AS units,
                   MAX(image_path) AS image_path
            FROM products {where}
            GROUP BY item_name, variant
            ORDER BY item_name, variant
            LIMIT %s OFFSET %s""",
        params + (PRODUCTS_PER_PAGE, (page - 1) * PRODUCTS_PER_PAGE),
    )

    return _cacheable(jsonify(
        products=[
            {
                "item_name": r["item_name"],
                "variant": r["variant"],
                "sizes": [u for u in (r["units"] or "").split(",") if u],
                "image_url": url_for("static", filename=r["image_path"]) if r["image_path"] else None,
            }
            for r in rows
        ],
        gender=gender,
        counts=counts,
        page=page,
        pages=pages,
        per_page=PRODUCTS_PER_PAGE,
        total=total,
    ))


@bp.route("/<slug>/api/packages")
@_read_limit
def api_packages(slug):
    """Active packages, optionally filtered to just Distributor- or
    Reseller-scoped ones (packages scoped 'Both' always show either
    way)."""
    _verify_slug(slug)

    scope_filter = request.args.get("scope", "all")
    where_extra = ""
    params = ()
    if scope_filter in PARTNER_TYPES:
        where_extra = "AND (pkg.partner_scope = 'Both' OR pkg.partner_scope = %s)"
        params = (scope_filter,)
    else:
        scope_filter = "all"

    package_rows = query(
        f"""SELECT pkg.*, COUNT(pi.package_item_id) AS item_count,
                   COALESCE(SUM(pi.qty), 0) AS unit_count,
                   COALESCE(SUM(pi.qty * p.price), 0) AS reference_total
            FROM packages pkg
            LEFT JOIN package_items pi ON pi.package_id = pkg.package_id
            LEFT JOIN products p ON p.sku = pi.sku
            WHERE pkg.is_active = TRUE {where_extra}
            GROUP BY pkg.package_id
            ORDER BY pkg.created_at DESC""",
        params,
    )

    previews = _package_previews([row["package_id"] for row in package_rows])
    return _cacheable(jsonify(
        packages=[_package_summary(row, previews=previews.get(row["package_id"]))
                  for row in package_rows],
        scope=scope_filter,
        partner_types=list(PARTNER_TYPES),
    ))


@bp.route("/<slug>/api/packages/<int:package_id>")
@_read_limit
def api_package_detail(slug, package_id):
    """One package's full detail: every product in it (photo, variant,
    unit, qty per set), pricing, a few other packages, and the CSRF
    token the inquiry form sends back.

    A missing/inactive package_id (deactivated by an admin, or
    auto-deleted after its last product was removed) is a 404 with a
    visitor-facing message — the app shows it and returns to the
    package list rather than a dead-end error page, since a
    distributor/reseller following an old bookmark or a shared link has
    no way to know a package disappeared.
    """
    _verify_slug(slug)
    pkg = active_package_or_none(package_id)
    if not pkg:
        return jsonify(
            error="That package is no longer available. Here's what's on offer right now."), 404

    items = query(
        """SELECT p.item_name, p.variant, p.unit, p.image_path, p.price, pi.qty
           FROM package_items pi JOIN products p ON pi.sku = p.sku
           WHERE pi.package_id = %s ORDER BY p.item_name""",
        (package_id,),
    )
    pkg["reference_total"] = sum(
        (decimal.Decimal(i["qty"]) * decimal.Decimal(i["price"])
         for i in items),
        decimal.Decimal("0"),
    )
    pkg["unit_count"] = sum(int(i["qty"]) for i in items)

    # A handful of other active packages (same visibility rule as the
    # main list — scoped to this package's own partner_scope, or 'Both'
    # packages, so a Distributor-only visitor never sees a Reseller-only
    # suggestion and vice versa) so a visitor who lands directly on this
    # page via a shared link isn't stuck with nowhere else to look.
    other_rows = query(
        """SELECT pkg2.*, COUNT(pi.package_item_id) AS item_count,
                  COALESCE(SUM(pi.qty * p.price), 0) AS reference_total
           FROM packages pkg2
           LEFT JOIN package_items pi ON pi.package_id = pkg2.package_id
           LEFT JOIN products p ON p.sku = pi.sku
           WHERE pkg2.is_active = TRUE AND pkg2.package_id != %s
             AND (pkg2.partner_scope = 'Both' OR pkg2.partner_scope = %s OR %s = 'Both')
           GROUP BY pkg2.package_id
           ORDER BY pkg2.created_at DESC
           LIMIT 3""",
        (package_id, pkg["partner_scope"], pkg["partner_scope"]),
    )

    return jsonify(
        package=_package_summary(pkg, item_count=len(items)),
        items=[
            {
                "item_name": i["item_name"],
                "variant": i["variant"],
                "unit": i["unit"],
                "qty": int(i["qty"]),
                "image_url": url_for("static", filename=i["image_path"]) if i["image_path"] else None,
                "initials": product_avatar(i["item_name"])["initials"],
            }
            for i in items
        ],
        other_packages=[_package_summary(row) for row in other_rows],
        partner_types=list(PARTNER_TYPES),
        # Sent back as the X-CSRFToken header on the inquiry POST — see
        # CSRFProtect in app.py. Tied to the session cookie this same
        # response sets.
        csrf_token=generate_csrf(),
    )


@bp.route("/<slug>/api/site")
@_read_limit
def api_site(slug):
    """Portal-wide facts for the landing page: the direct contact details
    configured in config.py (blank ones are left out, never shown as
    placeholders), the reply-time promise, real catalog and package
    counts for the stats band, and a CSRF token for the general inquiry
    form (package inquiries get theirs from the package detail endpoint
    instead)."""
    _verify_slug(slug)
    cfg = current_app.config
    contact = {}
    for key, name in (
        ("phone", "PORTAL_CONTACT_PHONE"),
        ("email", "PORTAL_CONTACT_EMAIL"),
        ("viber", "PORTAL_CONTACT_VIBER"),
        ("messenger_url", "PORTAL_CONTACT_MESSENGER_URL"),
    ):
        value = (cfg.get(name) or "").strip()
        if value:
            contact[key] = value

    counts = query(
        """SELECT
               (SELECT COUNT(*) FROM (SELECT 1 FROM products GROUP BY item_name, variant) g) AS scents,
               (SELECT COUNT(*) FROM packages WHERE is_active = TRUE) AS packages""",
        fetchone=True,
    )
    return jsonify(
        contact=contact,
        reply_time=(cfg.get("PORTAL_REPLY_TIME") or "").strip() or None,
        stats={"scents": int(counts["scents"]), "packages": int(counts["packages"])},
        partner_types=list(PARTNER_TYPES),
        contact_methods=list(CONTACT_METHODS),
        csrf_token=generate_csrf(),
    )
