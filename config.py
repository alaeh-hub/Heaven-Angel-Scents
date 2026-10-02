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

    # "Business day" boundary used throughout the app (CURDATE()-based
    # reporting, _current_business_date() in routes/ai.py, etc.) — set as
    # a MySQL UTC-offset string on every pooled connection (see db.py),
    # independent of the MySQL server's own configured timezone. Default
    # is the Philippines (UTC+8), where this business operates; override
    # if ever deployed for a different region.
    BUSINESS_TIMEZONE_OFFSET = os.environ.get("BUSINESS_TIMEZONE_OFFSET", "+08:00")

    # Size of the shared connection pool each worker process keeps open
    # (see db.py) instead of opening a brand-new MySQL connection per
    # request. Must comfortably cover this worker's peak concurrent
    # request count — raise it if requests start blocking waiting for a
    # free pooled connection under real traffic.
    MYSQL_POOL_SIZE = int(os.environ.get("MYSQL_POOL_SIZE", 10))

    WTF_CSRF_TIME_LIMIT = None
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False

    # Hard cap on request body size (bytes), enforced by Werkzeug before
    # any route code runs — without this, Flask/Werkzeug impose no limit
    # at all, so a signed-in account could send an oversized POST body
    # (a giant AI chat message, a huge CSV import) repeatedly within its
    # rate limit and force the server to buffer/parse it every time. 8MB
    # comfortably covers the largest legitimate upload (a product photo)
    # plus the CSV import with headroom; override via env if a real
    # catalog import ever needs more.
    MAX_CONTENT_LENGTH = int(
        os.environ.get("MAX_CONTENT_LENGTH", 8 * 1024 * 1024))

    # Master on/off switch for Flask-Limiter, read by flask-limiter itself
    # (RATELIMIT_ENABLED is its own config key, not app-specific). Default
    # on; the test suite (see tests/conftest.py) sets this to "0" before
    # the app is ever imported so a run of many requests against
    # rate-limited routes (login, the AI chat endpoint, ...) doesn't trip
    # a real limit and fail unrelated tests.
    RATELIMIT_ENABLED = os.environ.get("RATELIMIT_ENABLED", "1") == "1"

    # AI Assistant (Gemini). Read-only, role/branch-scoped — see routes/ai.py.
    # If GEMINI_API_KEY is blank, the assistant just tells the user it isn't configured.
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # Per-user rate limit for the AI chat endpoint (Flask-Limiter syntax,
    # semicolon-separated for multiple windows), so a single account
    # can't accidentally spike Gemini API costs.
    AI_CHAT_RATE_LIMIT = os.environ.get(
        "AI_CHAT_RATE_LIMIT", "15 per minute;150 per day")

    # Outbound mail (SMTP), used only to notify HQ of a new inquiry from
    # the public partner portal — see mailer.py / routes/portal.py. If
    # MAIL_SERVER, MAIL_DEFAULT_SENDER, or PARTNER_INQUIRY_NOTIFY_EMAIL is
    # blank, mailer.py just logs a warning and skips sending; the inquiry
    # itself is still saved either way, so an unconfigured mailer never
    # loses a lead — see partner_inquiries.email_sent in schema.sql.
    #
    # To send these to a Gmail inbox specifically:
    #   MAIL_SERVER=smtp.gmail.com
    #   MAIL_PORT=587
    #   MAIL_USE_TLS=1
    #   MAIL_USERNAME=yourname@gmail.com
    #   MAIL_PASSWORD=<a 16-character Gmail "App Password", not your normal
    #       login password — Google Account -> Security -> 2-Step
    #       Verification -> App passwords. Gmail rejects SMTP login with a
    #       regular password once 2FA is on.>
    #   MAIL_DEFAULT_SENDER=yourname@gmail.com
    #   PARTNER_INQUIRY_NOTIFY_EMAIL=the-inbox-that-should-get-leads@gmail.com
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "1") == "1"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "")
    # Where new-inquiry notifications are sent — the HQ inbox that should
    # follow up with the distributor/reseller.
    PARTNER_INQUIRY_NOTIFY_EMAIL = os.environ.get(
        "PARTNER_INQUIRY_NOTIFY_EMAIL", "")

    # Partner Portal (public, unauthenticated — see routes/portal.py).
    # The portal is never linked from the login page or anywhere else in
    # the signed-in app — the only way a distributor or reseller reaches
    # it is a link HQ shares with them directly, shaped like:
    #     https://yourdomain.com/partner-portal/<slug>/packages
    # That link's only protection is the slug itself, so:
    #   - Set PARTNER_PORTAL_SLUG explicitly in production to a long,
    #     random value (e.g. `python -c "import secrets;
    #     print(secrets.token_urlsafe(16))"`) so the link survives
    #     restarts and is identical across every worker process.
    #   - If left unset, app.py generates a random one at startup purely
    #     for local development convenience — it's logged once and
    #     changes on every restart, which is exactly why it's unsuitable
    #     for a real deployment.
    # The full, current link (built from whichever value is active) is
    # always shown to admins on the Partners page so it's easy to copy
    # and send to a distributor or reseller.
    PARTNER_PORTAL_SLUG = os.environ.get("PARTNER_PORTAL_SLUG", "")

    # Direct contact details shown on the partner portal (the closing
    # "talk to us" band, the inquiry form's confirmation). Each is
    # optional: anything left blank is simply not shown, so the portal
    # never displays a placeholder number or address. PORTAL_REPLY_TIME
    # is the follow-up promise shown next to the inquiry form, phrased to
    # complete "We reply ..." (e.g. "within 1 business day").
    PORTAL_CONTACT_PHONE = os.environ.get("PORTAL_CONTACT_PHONE", "")
    PORTAL_CONTACT_EMAIL = os.environ.get("PORTAL_CONTACT_EMAIL", "")
    PORTAL_CONTACT_VIBER = os.environ.get("PORTAL_CONTACT_VIBER", "")
    PORTAL_CONTACT_MESSENGER_URL = os.environ.get("PORTAL_CONTACT_MESSENGER_URL", "")
    PORTAL_REPLY_TIME = os.environ.get("PORTAL_REPLY_TIME", "")

    # Development only: enables Admin > Clear Data (admin.clear_data),
    # which wipes every business record in the database. Off unless
    # explicitly opted into, and hard-disabled in ProductionConfig below
    # regardless of the environment variable.
    ALLOW_DATA_RESET = os.environ.get("ALLOW_DATA_RESET", "0") == "1"


class ProductionConfig(Config):
    """Settings for deployment behind
    HTTPS and a production WSGI server.
    """

    DEBUG = False
    TESTING = False
    ALLOW_DATA_RESET = False
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


CONFIG_BY_ENV = {
    "development": Config,
    "production": ProductionConfig,
}
