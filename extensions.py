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

# Socket.IO rooms (see sockets.py's join_room calls) and the emits that
# target them (notify_admin/notify_branch/notify_admin_and_branch/
# notify_all/notify_bell) only reach the worker process that happens to
# hold the relevant connection unless every worker shares the same
# message queue — a plain in-process SocketIO() instance has no way to
# know about a room a *different* worker's connection joined. Setting
# SOCKETIO_MESSAGE_QUEUE to a redis:// URL (the same Redis instance used
# for RATELIMIT_STORAGE_URI works fine — Flask-SocketIO namespaces its
# own pub/sub channel there) fixes that by giving every worker a shared
# channel to publish/subscribe on. Left unset, Socket.IO falls back to
# single-process behavior, which is only correct when running with
# `gunicorn -w 1` (see wsgi.py's docstring) — exactly the same
# single-worker requirement RATELIMIT_STORAGE_URI has today.
_socketio_message_queue = os.environ.get("SOCKETIO_MESSAGE_QUEUE") or None
socketio_message_queue_is_unset = _socketio_message_queue is None

socketio = SocketIO(
    cors_allowed_origins=_socketio_origins,
    message_queue=_socketio_message_queue,
)

ratelimit_storage_is_memory = os.environ.get(
    "RATELIMIT_STORAGE_URI", "memory://") == "memory://"
