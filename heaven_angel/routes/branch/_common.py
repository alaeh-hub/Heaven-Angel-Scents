"""Shared pieces for the branch blueprint's route modules.

Every module in this package registers its routes on the single `bp`
below, so URLs and `url_for("branch.<endpoint>")` names are unchanged.
"""
from flask import Blueprint, session

bp = Blueprint("branch", __name__, url_prefix="/branch")


def _branch_id():
    """The signed-in branch user's own branch (branch_required guarantees it's set)."""
    return session["branch_id"]
