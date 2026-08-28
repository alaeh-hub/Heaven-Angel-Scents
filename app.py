import decimal
import os

from flask import Flask, render_template, session
from flask_talisman import Talisman
from flask_wtf import CSRFProtect

import db
from config import CONFIG_BY_ENV, INSECURE_DEFAULT_SECRET_KEY
from extensions import limiter, ratelimit_storage_is_memory, socketio, socketio_cors_is_wildcard

# Content-Security-Policy for Talisman below. 'unsafe-inline' is kept for
# script-src/style-src because several templates (login.html, chat.html,
# production.html, reports.html) use inline <script>/<style> blocks —
# removing it cleanly would mean adding a nonce to every one of those,
# which is a bigger follow-up than this pass. Everything else here is
# still real hardening: clickjacking, MIME-sniffing, and (in production)
# HSTS are all covered regardless.
_CSP = {
    "default-src": "'self'",
    "script-src": ["'self'", "'unsafe-inline'", "https://cdn.socket.io"],
    # motion.js used to load from cdn.jsdelivr.net — it's now vendored at
    # static/js/motion.js (see base.html / login.html), so jsdelivr no
    # longer needs to be here. Being a third-party origin was also why
    # Edge's Tracking Prevention / Brave Shields blocked it from storage
    # access in the console — self-hosting removes that too, since the
    # browser now sees it as same-origin.
    "style-src": ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
    "font-src": ["'self'", "https://fonts.gstatic.com"],
    "connect-src": ["'self'", "wss:", "ws:", "https://cdn.socket.io"],
    "img-src": ["'self'", "data:"],
}


def create_app():
    app = Flask(__name__)
    environment = os.environ.get("APP_ENV", "development").lower()
    config_class = CONFIG_BY_ENV.get(environment)
    if config_class is None:
        raise RuntimeError(f"Unsupported APP_ENV: {environment}")
    app.config.from_object(config_class)

    if not app.config["DEBUG"] and app.config["SECRET_KEY"] in (None, "", INSECURE_DEFAULT_SECRET_KEY):
        raise RuntimeError(
            "SECRET_KEY must be set to a real, private value for any non-debug run. "
            "Set the SECRET_KEY environment variable (e.g. via `python -c "
            "\"import secrets; print(secrets.token_hex(32))\"`), or set FLASK_DEBUG=1 "
            "if this really is local development."
        )

    db.init_app(app)
    CSRFProtect(app)
    limiter.init_app(app)
    socketio.init_app(app)
    Talisman(
        app,
        content_security_policy=_CSP,
        force_https=(environment == "production"),
        strict_transport_security=(environment == "production"),
        session_cookie_secure=app.config["SESSION_COOKIE_SECURE"],
        # Talisman defaults to sending Permissions-Policy: browsing-topics=(),
        # opting out of Chrome's Topics API. This app has no ads/tracking to
        # opt out of, and Edge/Brave don't recognize that feature name — they
        # just log "Unrecognized feature: 'browsing-topics'" for it. Disabling
        # it here removes that console noise on browsers that don't implement it.
        permissions_policy={},
    )

    if not app.config["DEBUG"]:
        if ratelimit_storage_is_memory:
            app.logger.warning(
                "RATELIMIT_STORAGE_URI is unset, so rate limiting is using in-memory storage. "
                "This only works correctly with a single worker process — if you run more than "
                "one gunicorn/uwsgi worker, each gets its own separate counters and the configured "
                "limits (login attempts, AI chat calls, etc.) are effectively multiplied by the "
                "worker count. Set RATELIMIT_STORAGE_URI to a shared store (e.g. a redis:// URL) "
                "for any multi-worker deployment."
            )
        if socketio_cors_is_wildcard:
            app.logger.warning(
                "SOCKETIO_CORS_ALLOWED_ORIGINS is unset, so Socket.IO accepts connections from any "
                "origin. Signed-in-only data is still protected (see sockets.py's connect handler), "
                "but set this to your real domain(s) for defense in depth in production."
            )

    from routes.auth import bp as auth_bp
    from routes.admin import bp as admin_bp
    from routes.branch import bp as branch_bp
    from routes.ai import bp as ai_bp
    from routes.portal import bp as portal_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(branch_bp)
    app.register_blueprint(ai_bp)
    # Public, unauthenticated — see routes/portal.py's module docstring.
    app.register_blueprint(portal_bp)

    import sockets

    import audit
    with app.app_context():
        try:
            audit.ensure_table()
        except Exception:
            app.logger.exception(
                "Failed to ensure admin_actions audit table exists")

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/error.html", code=403, message="You don't have access to that page."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/error.html", code=404, message="That page doesn't exist."), 404

    @app.errorhandler(429)
    def too_many_requests(e):
        return render_template(
            "errors/error.html", code=429,
            message="Too many requests — please slow down and try again shortly.",
        ), 429

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/error.html", code=500, message="Something went wrong on our end."), 500

    @app.template_filter("peso")
    def peso(value):
        try:
            return f"₱{decimal.Decimal(value):,.2f}"
        except (decimal.InvalidOperation, TypeError, ValueError):
            return f"₱{float(value):,.2f}"

    @app.context_processor
    def inject_sidebar_task_counts():

        if "user_id" not in session:
            return {}
        try:
            role = session.get("role")
            if role == "Admin":
                row = db.query(
                    "SELECT COUNT(*) c FROM stock_requests WHERE status = 'Pending'", fetchone=True
                )
                inquiries_row = db.query(
                    "SELECT COUNT(*) c FROM partner_inquiries WHERE status = 'New'", fetchone=True
                )
                return {
                    "pending_requests_count": row["c"] if row else 0,
                    "new_inquiries_count": inquiries_row["c"] if inquiries_row else 0,
                }
            if role == "Branch":
                row = db.query(
                    "SELECT COUNT(*) c FROM stock_requests WHERE branch_id = %s AND status = 'In Transit'",
                    (session.get("branch_id"),), fetchone=True,
                )
                return {"in_transit_count": row["c"] if row else 0}
        except Exception:
            app.logger.exception("Failed to compute sidebar task-count badges")
        return {}

    return app


app = create_app()

if __name__ == "__main__":
    socketio.run(app, debug=app.config["DEBUG"], port=5000)
