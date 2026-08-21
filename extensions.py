"""Shared Flask extension instances.

Kept in their own module (rather than created inline in app.py) so
route blueprints can `from extensions import limiter` without a
circular import — app.py imports the blueprints, so the blueprints
can't import an object that only exists inside app.py.
"""
import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Default storage is in-process memory, which is fine for a single
# worker process. If you run multiple Waitress/gunicorn worker
# processes in production, point RATELIMIT_STORAGE_URI at a shared
# backend (e.g. redis://localhost:6379) or each process will enforce
# its own separate counters and the limits won't be accurate.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=[],  # no blanket limit; routes opt in explicitly
)
