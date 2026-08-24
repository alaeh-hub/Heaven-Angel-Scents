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
    "style-src": ["'self'", "'unsafe-inline'"],
    "connect-src": ["'self'", "wss:", "ws:"],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'"],
}


def create_app():
    app = Flask(__name__)
    environment = os.environ.get("APP_ENV", "development").lower()
    config_class = CONFIG_BY_ENV.get(environment)
    if config_class is None:
        raise RuntimeError(f"Unsupported APP_ENV: {environment}")
    app.config.from_object(config_class)

    # Refuse to boot with the known placeholder key (or no key at all)
    # for any run that isn't explicitly DEBUG. This intentionally does
    # NOT key off `environment == "production"` — that only protects a
    # deployment that remembered to set APP_ENV=production. A deployment
    # that just forgot to set APP_ENV falls back to the development
    # config, which used to let this pass silently. Checking DEBUG
    # instead means any non-debug run (any real server, any WSGI/ASGI
    # launcher) gets this check, regardless of which env var was or
    # wasn't set.
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
    )

    # Best-effort hardening reminders for anything that's easy to leave
    # on its permissive local-dev default and forget about. None of
    # these block startup (unlike the SECRET_KEY check above) since
    # they're not exploitable secrets, just things that quietly behave
    # worse than intended in a real deployment.
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(branch_bp)
    app.register_blueprint(ai_bp)

    import sockets  # noqa: F401 -- registers Socket.IO event handlers, see sockets.py

    import audit
    with app.app_context():
        try:
            audit.ensure_table()
        except Exception:
            app.logger.exception("Failed to ensure admin_actions audit table exists")

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
        # value is usually a Decimal straight from MySQL — format it
        # directly rather than round-tripping through float(), which can
        # introduce tiny binary-rounding artifacts on currency amounts.
        try:
            return f"₱{decimal.Decimal(value):,.2f}"
        except (decimal.InvalidOperation, TypeError, ValueError):
            return f"₱{float(value):,.2f}"

    @app.context_processor
    def inject_sidebar_task_counts():
        """"Needs action" badge counts for the sidebar nav.

        base.html renders on every signed-in page, so this runs once per
        request — kept to a single indexed COUNT(*) for whichever one
        queue this role can actually act on:
          - Admin  -> Pending stock requests (awaiting dispatch/reject)
          - Branch -> That branch's In Transit shipments (awaiting receipt)

        Returns {} outside a signed-in session (e.g. the login page) or
        if the count query fails for any reason — a missing badge should
        never be the reason a page fails to render.
        """
        if "user_id" not in session:
            return {}
        try:
            role = session.get("role")
            if role == "Admin":
                row = db.query(
                    "SELECT COUNT(*) c FROM stock_requests WHERE status = 'Pending'", fetchone=True
                )
                return {"pending_requests_count": row["c"] if row else 0}
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
