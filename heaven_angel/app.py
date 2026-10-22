import decimal
import json
import logging
import os
import re
import secrets
import uuid

from flask import Flask, g, has_request_context, render_template, request, session
from flask_talisman import Talisman
from flask_wtf import CSRFProtect

from heaven_angel import db
from heaven_angel import utils
from heaven_angel.config import CONFIG_BY_ENV, INSECURE_DEFAULT_SECRET_KEY
from heaven_angel.decorators import live_account_or_none
from heaven_angel.extensions import limiter, socketio, socketio_cors_is_wildcard


# Content-Security-Policy for Talisman below. script-src has no
# 'unsafe-inline': every inline <script> carries the per-request nonce
# ({{ csp_nonce() }}), and templates use data- attributes handled in main.js
# instead of on*= handlers. style-src keeps 'unsafe-inline' because the
# templates use several hundred inline style="" attributes; that is the
# remaining gap. Clickjacking, MIME-sniffing and (in production) HSTS are
# covered regardless.
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


class _ColorFormatter(logging.Formatter):
    _COLORS = {
        logging.DEBUG: "\x1b[36m",
        logging.INFO: "\x1b[32m",
        logging.WARNING: "\x1b[33m",
        logging.ERROR: "\x1b[31m",
        logging.CRITICAL: "\x1b[1;31m",
    }
    _RESET = "\x1b[0m"

    def __init__(self, stream):
        super().__init__(
            "[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
        self._use_color = stream.isatty()

    def format(self, record):
        message = super().format(record)
        if not self._use_color:
            return message
        color = self._COLORS.get(record.levelno, self._RESET)
        return f"{color}{message}{self._RESET}"


class _RequestIdFilter(logging.Filter):
    """Stamps every record with the current request's ID ("-" outside a request)."""

    def filter(self, record):
        record.request_id = g.get("request_id", "-") if has_request_context() else "-"
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, so a log search can filter by level or request_id."""

    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "module": record.module,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging(app, json_logs):
    for logger_name in (app.logger.name, "werkzeug"):
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.addFilter(_RequestIdFilter())
                handler.setFormatter(
                    _JsonFormatter() if json_logs else _ColorFormatter(handler.stream))


_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app():
    # root_path is the project root (not this package) so templates/, static/
    # and backups/ keep living beside the code package.
    app = Flask(__name__, root_path=PROJECT_ROOT)
    environment = os.environ.get("APP_ENV", "development").lower()
    # JSON lines in production (or LOG_FORMAT=json); readable colour text otherwise.
    _configure_logging(
        app, json_logs=os.environ.get("LOG_FORMAT", "json" if environment == "production" else "text") == "json")
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

    if os.environ.get("TRUST_PROXY", "0") == "1":
        # Behind a reverse proxy (Caddy in docker-compose.yml) every request
        # arrives from the proxy's IP over plain HTTP. Without this, rate
        # limiting would treat all users as one client and Talisman would
        # redirect-loop trying to force HTTPS. Only enable it when the app is
        # reachable exclusively through that proxy, or X-Forwarded-* can be forged.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.before_request
    def _assign_request_id():
        # Reuse the proxy's/client's ID when it looks sane, so a support
        # report can be matched to log lines; otherwise mint one.
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _REQUEST_ID_OK.match(incoming) else uuid.uuid4().hex[:16]

    @app.after_request
    def _echo_request_id(response):
        response.headers["X-Request-ID"] = g.get("request_id", "-")
        return response

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
        # Talisman defaults to sending Permissions-Policy: browsing-topics=(),
        # opting out of Chrome's Topics API. This app has no ads/tracking to
        # opt out of, and Edge/Brave don't recognize that feature name — they
        # just log "Unrecognized feature: 'browsing-topics'" for it. Disabling
        # it here removes that console noise on browsers that don't implement it.
        permissions_policy={},
    )

    if not app.config["DEBUG"]:
        if socketio_cors_is_wildcard:
            app.logger.warning(
                "SOCKETIO_CORS_ALLOWED_ORIGINS is unset, so Socket.IO accepts connections from any "
                "origin. Signed-in-only data is still protected (see sockets.py's connect handler), "
                "but set this to your real domain(s) for defense in depth in production."
            )

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

    @app.route("/robots.txt")
    def robots_txt():
        # Every page here is either login-gated (admin/branch/ai) or
        # slug-gated (the partner portal — see routes/portal.py) and never
        # meant to be publicly discoverable, so block crawling site-wide
        # rather than maintaining a per-path allow/deny list.
        return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


    from heaven_angel.services import audit
    with app.app_context():
        try:
            audit.ensure_table()
        except Exception:
            app.logger.exception(
                "Failed to ensure admin_actions audit table exists")

    from heaven_angel.services import login_activity
    with app.app_context():
        try:
            login_activity.ensure_table()
        except Exception:
            app.logger.exception(
                "Failed to ensure login_activity table exists")

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("errors/error.html", code=400, message="That request couldn't be understood."), 400

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

    @app.template_filter("compact_peso")
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

    @app.template_filter("peso")
    def peso(value):
        try:
            return f"₱{decimal.Decimal(value):,.2f}"
        except (decimal.InvalidOperation, TypeError, ValueError):
            return f"₱{float(value):,.2f}"

    # Used by the product_thumb() macro (templates/_macros.html) to render
    # a per-product initials avatar in place of a photo when none is set.
    app.jinja_env.globals["product_avatar"] = utils.product_avatar

    @app.context_processor
    def inject_today():
        # ISO date string for capping date-picker inputs at "today" (e.g.
        # Add material/supplier, Record sale's backdating field) — a
        # picker shouldn't let someone log a record dated in the future.
        return {"today": utils.business_today().isoformat()}

    @app.context_processor
    def inject_login_splash():
        # Popped, not just read, so it renders true on exactly one page
        # load (the dashboard a fresh login redirects to) and never
        # again on a later refresh — see auth.py's login(), which is the
        # only place that sets it.
        return {"show_login_splash": session.pop("show_login_splash", False)}

    def today_sales_summary(account=None):
        """Today's sales for the top bar chip — the signed-in branch's own,
        or every branch's for an admin. Same revenue formula as the branch
        dashboard (qty x unit price, voids already deleted), with the trend
        compared against yesterday up to this same time of day.

        Pass `account` (a live users row, see decorators.live_account_or_none)
        to scope by the database's current role/branch instead of the
        session's login-time copy."""
        if "user_id" not in session:
            return None
        role = account["role"] if account else session.get("role")
        branch_id = account["branch_id"] if account else session.get("branch_id")
        branch_name = account["branch_name"] if account else session.get("branch_name")
        if role == "Admin":
            scope_sql, params = "", ()
        elif role == "Branch" and branch_id:
            scope_sql, params = " AND branch_id = %s", (branch_id,)
        else:
            return None
        today = db.query(
            f"""SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS revenue, COUNT(*) AS sale_count
                FROM sales
                WHERE sold_at >= CURDATE() AND sold_at < CURDATE() + INTERVAL 1 DAY{scope_sql}""",
            params, fetchone=True,
        )
        yesterday = db.query(
            f"""SELECT COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
                FROM sales
                WHERE sold_at >= CURDATE() - INTERVAL 1 DAY AND sold_at < NOW() - INTERVAL 1 DAY{scope_sql}""",
            params, fetchone=True,
        )
        revenue = today["revenue"]
        return {
            "revenue": float(revenue),
            "revenue_display": peso(revenue),
            "revenue_compact": utils.compact_peso(revenue),
            "count": int(today["sale_count"]),
            "trend": utils.percent_change(revenue, yesterday["revenue"]),
            "scope": "all branches" if role == "Admin" else (branch_name or "your branch"),
        }

    @app.context_processor
    def inject_today_sales():
        try:
            return {"today_sales_chip": today_sales_summary()}
        except Exception:
            app.logger.exception("Failed to compute today's sales for the top bar")
            return {"today_sales_chip": None}

    @app.route("/api/today-sales")
    def api_today_sales():
        # Re-check the account against the database like @login_required
        # does, so a deactivated or demoted user stops getting revenue
        # figures immediately instead of when their cookie expires.
        account = live_account_or_none()
        summary = today_sales_summary(account) if account else None
        if summary is None:
            return {"error": "not signed in"}, 401
        return summary

    @app.context_processor
    def inject_sidebar_task_counts():

        if "user_id" not in session:
            return {}
        try:
            role = session.get("role")
            if role == "Admin":
                row = db.query(
                    "SELECT COUNT(*) c FROM stock_requests WHERE status IN ('Pending', 'Partially Fulfilled')",
                    fetchone=True,
                )
                inquiries_row = db.query(
                    "SELECT COUNT(*) c FROM partner_inquiries WHERE status = 'New'", fetchone=True
                )
                drafts_row = db.query(
                    "SELECT COUNT(*) c FROM ai_stock_drafts WHERE status = 'Pending Review'", fetchone=True
                )
                return {
                    "pending_requests_count": row["c"] if row else 0,
                    "new_inquiries_count": inquiries_row["c"] if inquiries_row else 0,
                    "pending_ai_drafts_count": drafts_row["c"] if drafts_row else 0,
                }
            if role == "Branch":
                row = db.query(
                    "SELECT COUNT(*) c FROM stock_requests WHERE branch_id = %s AND status = 'In Transit'",
                    (session.get("branch_id"),), fetchone=True,
                )
                drafts_row = db.query(
                    "SELECT COUNT(*) c FROM ai_stock_drafts WHERE branch_id = %s AND status = 'Pending Review'",
                    (session.get("branch_id"),), fetchone=True,
                )
                return {
                    "in_transit_count": row["c"] if row else 0,
                    "pending_ai_drafts_count": drafts_row["c"] if drafts_row else 0,
                }
        except Exception:
            app.logger.exception("Failed to compute sidebar task-count badges")
        return {}

    # Scheduled backups (Settings -> Backup): one background thread per
    # process. Skipped for tests, for the dev reloader's parent process,
    # and when BACKUP_SCHEDULER=0.
    if (os.environ.get("BACKUP_SCHEDULER", "1") != "0" and not app.testing
            and not (app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true")):
        from heaven_angel.services import backup_jobs
        backup_jobs.start_scheduler(app)

    return app


app = create_app()
