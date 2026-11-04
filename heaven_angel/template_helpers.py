"""Jinja filters/globals and the shared error pages."""
import decimal

from flask import render_template
from mysql.connector.errors import PoolError

from heaven_angel import utils


def peso(value):
    try:
        return f"₱{decimal.Decimal(value):,.2f}"
    except (decimal.InvalidOperation, TypeError, ValueError):
        return f"₱{float(value):,.2f}"


def compact_peso(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "₱0"
    sign, a = ("-" if n < 0 else ""), abs(n)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= limit:
            text = f"{a / limit:.2f}".rstrip("0").rstrip(".")
            return f"{sign}₱{text}{suffix}"
    return f"{sign}₱{a:,.0f}" if a % 1 == 0 else f"{sign}₱{a:,.2f}"


def register_template_helpers(app):
    app.template_filter("compact_peso")(compact_peso)
    app.template_filter("peso")(peso)
    # Used by the product_thumb() macro (templates/_macros.html) to render
    # a per-product initials avatar in place of a photo when none is set.
    app.jinja_env.globals["product_avatar"] = utils.product_avatar


def register_error_handlers(app):
    def _error_page(code, message, headers=None):
        response = render_template("errors/error.html", code=code, message=message), code
        return response + (headers,) if headers else response

    @app.errorhandler(400)
    def bad_request(e):
        return _error_page(400, "That request couldn't be understood.")

    @app.errorhandler(403)
    def forbidden(e):
        return _error_page(403, "You don't have access to that page.")

    @app.errorhandler(404)
    def not_found(e):
        return _error_page(404, "That page doesn't exist.")

    @app.errorhandler(429)
    def too_many_requests(e):
        return _error_page(429, "Too many requests — please slow down and try again shortly.")

    @app.errorhandler(PoolError)
    def pool_exhausted(e):
        app.logger.error("Database connection pool exhausted: %s", e)
        return _error_page(503, "The system is busy right now. Please try again in a moment.",
                           {"Retry-After": "5"})

    @app.errorhandler(500)
    def server_error(e):
        return _error_page(500, "Something went wrong on our end.")
