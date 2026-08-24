import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
)

_socketio_origins = os.environ.get("SOCKETIO_CORS_ALLOWED_ORIGINS", "*")
socketio_cors_is_wildcard = _socketio_origins == "*"
if _socketio_origins != "*":
    _socketio_origins = [o.strip()
                         for o in _socketio_origins.split(",") if o.strip()]

socketio = SocketIO(cors_allowed_origins=_socketio_origins)

ratelimit_storage_is_memory = os.environ.get(
    "RATELIMIT_STORAGE_URI", "memory://") == "memory://"
