import os

from dotenv import load_dotenv


load_dotenv()

basedir = os.path.abspath(os.path.dirname(__file__))

# Only ever meant to work for local development (see app.py's startup
# check, which refuses to boot with this key outside of DEBUG mode —
# not just when APP_ENV=production, since a deployment can easily run
# with APP_ENV unset too).
INSECURE_DEFAULT_SECRET_KEY = "dev-secret-key-change-me"


class Config:
    """
    Default values match a stock XAMPP install (MySQL on localhost:3306,
    user 'root', empty password). Override any of these with a .env file
    or real environment variables in production.
    """
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
    SECRET_KEY = os.environ.get("SECRET_KEY", INSECURE_DEFAULT_SECRET_KEY)

    MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
    MYSQL_PORT = int(os.environ.get("MYSQL_PORT", 3306))
    MYSQL_USER = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
    MYSQL_DB = os.environ.get("MYSQL_DB", "heaven_and_angel_scents")

    WTF_CSRF_TIME_LIMIT = None
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False

    # AI Assistant (Gemini). Read-only, role/branch-scoped — see routes/ai.py.
    # If GEMINI_API_KEY is blank, the assistant just tells the user it isn't configured.
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # Per-user rate limit for the AI chat endpoint (Flask-Limiter syntax,
    # semicolon-separated for multiple windows), so a single account
    # can't accidentally spike Gemini API costs.
    AI_CHAT_RATE_LIMIT = os.environ.get(
        "AI_CHAT_RATE_LIMIT", "15 per minute;150 per day")


class ProductionConfig(Config):
    """Settings for deployment behind HTTPS and a production WSGI server."""

    DEBUG = False
    TESTING = False
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


CONFIG_BY_ENV = {
    "development": Config,
    "production": ProductionConfig,
}
