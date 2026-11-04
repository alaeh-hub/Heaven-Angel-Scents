"""Shared pieces for the partner portal's route modules: the blueprint, the
slug gate and the read-endpoint rate limit/cache helpers."""
import secrets

from flask import Blueprint, abort, current_app

from heaven_angel.extensions import limiter


bp = Blueprint("portal", __name__, url_prefix="/partner-portal")


# The read endpoints run several grouped queries per hit and need nothing
# but the slug, so anyone holding the link could hammer them or scrape the
# whole price list. Generous enough that a visitor clicking through filters
# and pages (or a whole office behind one IP) never notices it. One shared
# bucket per client for all four reads.
_read_limit = limiter.shared_limit("120 per minute", scope="portal-read")


# Package and catalog lists change only when HQ edits them, so the browser
# may reuse one for half a minute (back/forward, quick filter toggles).
# "private": never stored by a shared proxy. Not used for the responses
# that carry a CSRF token.
_LIST_CACHE_SECONDS = 30


def _cacheable(response):
    response.headers["Cache-Control"] = f"private, max-age={_LIST_CACHE_SECONDS}"
    return response


def _verify_slug(slug):
    """Gate every route in this blueprint behind the configured secret
    slug (see PARTNER_PORTAL_SLUG in config.py). A wrong or missing
    slug 404s exactly like a page that doesn't exist — it never reveals
    that a portal lives at this path at all.

    secrets.compare_digest avoids leaking the real slug's length/prefix
    through response-timing differences.
    """
    configured = current_app.config.get("PARTNER_PORTAL_SLUG", "")
    if not configured or not secrets.compare_digest(slug, configured):
        abort(404)
