import os

from flask import Flask, render_template, session
from flask_wtf import CSRFProtect

import db
from config import CONFIG_BY_ENV
from extensions import limiter


def create_app():
    app = Flask(__name__)
    environment = os.environ.get("APP_ENV", "development").lower()
    config_class = CONFIG_BY_ENV.get(environment)
    if config_class is None:
        raise RuntimeError(f"Unsupported APP_ENV: {environment}")
    app.config.from_object(config_class)

    if environment == "production" and not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")

    db.init_app(app)
    CSRFProtect(app)
    limiter.init_app(app)

    from routes.auth import bp as auth_bp
    from routes.admin import bp as admin_bp
    from routes.branch import bp as branch_bp
    from routes.ai import bp as ai_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(branch_bp)
    app.register_blueprint(ai_bp)

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
    app.run(debug=app.config["DEBUG"], port=5000)
