"""Branch blueprint, split by feature.

Importing each module below registers its routes on the shared `bp`.
"""
from heaven_angel.routes.branch._common import bp

from heaven_angel.routes.branch import (  # noqa: F401  (imported for route registration)
    dashboard,
    inventory,
    reports,
    sales,
    stock_requests,
)

__all__ = ["bp"]
