"""Admin (HQ) blueprint, split by feature.

Importing each module below registers its routes on the shared `bp`.
"""
from heaven_angel.routes.admin._common import bp

from heaven_angel.routes.admin import (  # noqa: F401  (imported for route registration)
    announcements,
    backup,
    branches,
    clear_data,
    dashboard,
    formulas,
    inquiries,
    logs,
    materials,
    packages,
    partners,
    production,
    products,
    reports,
    sales,
    stock_requests,
    users,
)

__all__ = ["bp"]
