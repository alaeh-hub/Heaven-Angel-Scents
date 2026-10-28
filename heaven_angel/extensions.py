import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO

# In-memory rate-limit counters and a single-process Socket.IO instance —
# both correct as long as this app runs as exactly one worker process
# (`gunicorn -w 1`, see wsgi.py). Multi-worker deployments would need a
# shared store (e.g. Redis) for both of these instead; not set up here
# since this app is deployed single-worker.
limiter = Limiter(
    key_func=get_remote_address,
    # Explicit, not just relying on Flask-Limiter's own default: passing
    # this silences its "no storage was explicitly specified" warning,
    # which otherwise prints unconditionally even though in-memory
    # storage is the deliberate, correct choice for this single-worker
    # deployment (see the comment above).
    # Counters live in memory (reset on restart, per process). To keep them
    # across restarts or share them between workers, set RATELIMIT_STORAGE_URI
    # (e.g. redis://redis:6379/0; needs `pip install redis`).
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
)

_socketio_origins = os.environ.get("SOCKETIO_CORS_ALLOWED_ORIGINS", "*")
socketio_cors_is_wildcard = _socketio_origins == "*"
if _socketio_origins == "same-origin":
    # python-engineio's default: accept only when the Origin header matches the
    # Host the request came in on. Works for the domain *and* LAN/IP access
    # through the proxy, while still refusing other websites.
    _socketio_origins = None
elif _socketio_origins != "*":
    _socketio_origins = [o.strip()
                         for o in _socketio_origins.split(",") if o.strip()]

socketio = SocketIO(cors_allowed_origins=_socketio_origins)
