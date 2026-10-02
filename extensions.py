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
    default_limits=[],
)

_socketio_origins = os.environ.get("SOCKETIO_CORS_ALLOWED_ORIGINS", "*")
socketio_cors_is_wildcard = _socketio_origins == "*"
if _socketio_origins != "*":
    _socketio_origins = [o.strip()
                         for o in _socketio_origins.split(",") if o.strip()]

socketio = SocketIO(cors_allowed_origins=_socketio_origins)
