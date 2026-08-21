import os

from flask import Flask, render_template
from flask_wtf import CSRFProtect

import db
from config import CONFIG_BY_ENV


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

    from routes.auth import bp as auth_bp
    from routes.admin import bp as admin_bp
    from routes.branch import bp as branch_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(branch_bp)

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/error.html", code=403, message="You don't have access to that page."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/error.html", code=404, message="That page doesn't exist."), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/error.html", code=500, message="Something went wrong on our end."), 500

    @app.template_filter("peso")
    def peso(value):
        return f"₱{float(value):,.2f}"

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"], port=5000)
