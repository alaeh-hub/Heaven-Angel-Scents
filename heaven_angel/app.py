import os
import secrets
import uuid

from flask import Flask, g, request, send_from_directory
from flask_talisman import Talisman
from flask_wtf import CSRFProtect

from heaven_angel import db
from heaven_angel.config import CONFIG_BY_ENV, INSECURE_DEFAULT_SECRET_KEY
from heaven_angel.context import register_context_processors
from heaven_angel.extensions import limiter, socketio, socketio_cors_is_wildcard
from heaven_angel.logging_setup import REQUEST_ID_OK, configure_logging
from heaven_angel.template_helpers import register_error_handlers, register_template_helpers


# Content-Security-Policy for Talisman below. script-src has no
# 'unsafe-inline': every inline <script> carries the per-request nonce
# ({{ csp_nonce() }}), and templates use data- attributes handled in
# static/js/app/ instead of on*= handlers. style-src keeps 'unsafe-inline'
# because the templates use several hundred inline style="" attributes; that
# is the remaining gap. Clickjacking, MIME-sniffing and (in production) HSTS
# are covered regardless.
_CSP = {
    "default-src": "'self'",
    "script-src": ["'self'", "https://cdn.socket.io"],
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

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_config(app, environment):
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

    if not app.config.get("PARTNER_PORTAL_SLUG"):
        # See config.py's PARTNER_PORTAL_SLUG comment: this keeps local
        # dev usable out of the box, but a real deployment should set
        # PARTNER_PORTAL_SLUG explicitly so the shared link is stable.
        app.config["PARTNER_PORTAL_SLUG"] = secrets.token_urlsafe(12)
        if not app.config["DEBUG"]:
            app.logger.warning(
                "PARTNER_PORTAL_SLUG is unset, so a random slug was generated for this process "
                "only — it will change on every restart and differ across worker processes. Set "
                "PARTNER_PORTAL_SLUG in your environment so the partner portal link you hand to "
                "distributors/resellers stays stable. Generated link for this process: "
                f"/partner-portal/{app.config['PARTNER_PORTAL_SLUG']}/packages"
            )


def _trust_proxy_if_configured(app):
    if os.environ.get("TRUST_PROXY", "0") == "1":
        # Behind a reverse proxy (Caddy in docker-compose.yml) every request
        # arrives from the proxy's IP over plain HTTP. Without this, rate
        # limiting would treat all users as one client and Talisman would
        # redirect-loop trying to force HTTPS. Only enable it when the app is
        # reachable exclusively through that proxy, or X-Forwarded-* can be forged.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _install_request_id(app):
    @app.before_request
    def _assign_request_id():
        # Reuse the proxy's/client's ID when it looks sane, so a support
        # report can be matched to log lines; otherwise mint one.
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if REQUEST_ID_OK.match(incoming) else uuid.uuid4().hex[:16]

    @app.after_request
    def _echo_request_id(response):
        response.headers["X-Request-ID"] = g.get("request_id", "-")
        return response


def _init_extensions(app, environment):
    db.init_app(app)
    CSRFProtect(app)
    limiter.init_app(app)
    socketio.init_app(app)
    Talisman(
        app,
        content_security_policy=_CSP,
        content_security_policy_nonce_in=["script-src"],
        force_https=(environment == "production"),
        strict_transport_security=(environment == "production"),
        session_cookie_secure=app.config["SESSION_COOKIE_SECURE"],
        # Permissions-Policy: switch off every browser feature this app does
        # not use, so a compromised or embedded page cannot reach them. The
        # only one it needs is the camera, for the receipt QR scanner
        # (static/js/scan.js), and only from its own pages. Kept to the widely
        # implemented feature names so browsers do not log "Unrecognized
        # feature" for the header (the reason Talisman's default
        # `browsing-topics=()` was dropped before).
        permissions_policy={
            "camera": "(self)",
            "microphone": "()",
            "geolocation": "()",
            "payment": "()",
            "usb": "()",
            "accelerometer": "()",
            "gyroscope": "()",
            "magnetometer": "()",
            "display-capture": "()",
        },
    )

    if not app.config["DEBUG"] and socketio_cors_is_wildcard:
        app.logger.warning(
            "SOCKETIO_CORS_ALLOWED_ORIGINS is unset, so Socket.IO accepts connections from any "
            "origin. Signed-in-only data is still protected (see sockets.py's connect handler), "
            "but set this to your real domain(s) for defense in depth in production."
        )


def _register_blueprints(app):
    from heaven_angel.routes.auth import bp as auth_bp
    from heaven_angel.routes.admin import bp as admin_bp
    from heaven_angel.routes.branch import bp as branch_bp
    from heaven_angel.routes.ai import bp as ai_bp
    from heaven_angel.routes.portal import bp as portal_bp
    from heaven_angel.routes.scan import bp as scan_bp

    app.register_blueprint(scan_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(branch_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(portal_bp)


def _register_site_routes(app):
    """Unauthenticated plumbing routes: health check, PWA files, robots."""
    static_dir = os.path.join(app.root_path, "static")

    @app.route("/healthz")
    def healthz():
        # Liveness + DB reachability for the Docker healthcheck. Returns no
        # data and no details, so it is safe to leave unauthenticated.
        try:
            db.query("SELECT 1", fetchone=True)
        except Exception:
            app.logger.exception("Health check failed")
            return "unhealthy", 503, {"Content-Type": "text/plain; charset=utf-8"}
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.route("/manifest.webmanifest")
    def web_manifest():
        # Makes the site installable (pin to the taskbar, own window). Public
        # on purpose: the browser fetches it before anyone has signed in.
        resp = send_from_directory(
            static_dir, "manifest.webmanifest", mimetype="application/manifest+json")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/sw.js")
    def service_worker():
        # Served from the site root so its scope covers every page. It caches
        # only static files, never pages or data (see static/sw.js).
        resp = send_from_directory(static_dir, "sw.js", mimetype="application/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    @app.route("/robots.txt")
    def robots_txt():
        # Every page here is either login-gated (admin/branch/ai) or
        # slug-gated (the partner portal — see routes/portal.py) and never
        # meant to be publicly discoverable, so block crawling site-wide
        # rather than maintaining a per-path allow/deny list.
        return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


def _ensure_support_tables(app):
    from heaven_angel.services import audit, login_activity

    for module, label in ((audit, "admin_actions audit"), (login_activity, "login_activity")):
        with app.app_context():
            try:
                module.ensure_table()
            except Exception:
                app.logger.exception("Failed to ensure %s table exists", label)


def _should_run_background_workers(app, env_flag):
    """Background threads are skipped for tests, for the dev reloader's
    parent process, and when the given env flag is set to 0."""
    return (os.environ.get(env_flag, "1") != "0" and not app.testing
            and not (app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true"))


def _start_background_workers(app):
    # Scheduled backups (Settings -> Backup): one background thread per process.
    if _should_run_background_workers(app, "BACKUP_SCHEDULER"):
        from heaven_angel.services import backup_jobs
        backup_jobs.start_scheduler(app)

    # Resends HQ inquiry emails that never went out (see routes/portal.py).
    if _should_run_background_workers(app, "INQUIRY_EMAIL_RETRY"):
        from heaven_angel.routes.portal import start_inquiry_email_retry
        start_inquiry_email_retry(app)


def create_app():
    # root_path is the project root (not this package) so templates/, static/
    # and backups/ keep living beside the code package.
    app = Flask(__name__, root_path=PROJECT_ROOT)
    environment = os.environ.get("APP_ENV", "development").lower()
    # JSON lines in production (or LOG_FORMAT=json); readable colour text otherwise.
    configure_logging(
        app, json_logs=os.environ.get("LOG_FORMAT", "json" if environment == "production" else "text") == "json")
    _load_config(app, environment)
    _trust_proxy_if_configured(app)
    _install_request_id(app)
    _init_extensions(app, environment)
    _register_blueprints(app)
    _register_site_routes(app)
    _ensure_support_tables(app)
    register_error_handlers(app)
    register_template_helpers(app)
    register_context_processors(app)
    _start_background_workers(app)
    return app


app = create_app()
