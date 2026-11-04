"""Shared pieces for the admin blueprint's route modules.

Every module in this package registers its routes on the single `bp`
below, so URLs and `url_for("admin.<endpoint>")` names are unchanged by
the split into per-feature files.
"""
from flask import Blueprint

bp = Blueprint("admin", __name__, url_prefix="/admin")

# The head-office branch: HQ's own stock lives under this branch id.
HQ_BRANCH_ID = 1
