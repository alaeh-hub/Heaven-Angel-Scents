"""Shared Flask extension instances.

Kept in their own module (rather than created inline in app.py) so
route blueprints can `from extensions import limiter` without a
circular import — app.py imports the blueprints, so the blueprints
can't import an object that only exists inside app.py.
"""
import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
)

# Defaults to '*' for local dev convenience. In production, set
# SOCKETIO_CORS_ALLOWED_ORIGINS to the real domain(s) (comma-separated)
# instead — same rationale as pinning CSP: an open wildcard is fine for a
# machine only you're hitting, not for a server other people can reach.
_socketio_origins = os.environ.get("SOCKETIO_CORS_ALLOWED_ORIGINS", "*")
socketio_cors_is_wildcard = _socketio_origins == "*"
if _socketio_origins != "*":
    _socketio_origins = [o.strip() for o in _socketio_origins.split(",") if o.strip()]

socketio = SocketIO(cors_allowed_origins=_socketio_origins)

# Same wildcard-by-default pattern as CORS above: fine for local dev,
# should be set explicitly (e.g. a redis:// URL) for any deployment
# that runs more than one worker process, since in-memory storage is
# per-process and rate limits silently stop being shared across
# workers otherwise. See app.py's startup check, which warns if this
# is still unset outside of DEBUG mode.
ratelimit_storage_is_memory = os.environ.get("RATELIMIT_STORAGE_URI", "memory://") == "memory://"
