import os

from flask import (
    abort, current_app, flash, redirect, render_template,
    request, url_for,
)

from heaven_angel.db import get_db, query
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_all

from heaven_angel.routes.admin.products import PRODUCT_IMAGE_SUBDIR
from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- clear data (dev only)
# Kept through a reset so everyone can still sign in and the app still
# has its HQ/branches and pricing settings to run against. Every other
# table in the database is emptied, including ones added after this was
# written (the list comes from information_schema, not a hard-coded set).
CLEAR_DATA_KEEP_TABLES = frozenset({
    "users", "branches", "bulk_rate_settings", "unit_cogs_settings",
})


CLEAR_DATA_CONFIRM_PHRASE = "CLEAR ALL DATA"


def _clearable_tables():
    """Every base table in the current database except the keep-list,
    with its row count (exact, via COUNT(*) — information_schema's
    table_rows is only an InnoDB estimate)."""
    names = [
        row["t"] for row in query(
            """SELECT table_name AS t FROM information_schema.tables
               WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
               ORDER BY table_name"""
        )
        if row["t"] not in CLEAR_DATA_KEEP_TABLES
    ]
    tables = []
    for name in names:
        # Names come from information_schema, never from the request;
        # still refuse anything that isn't a plain identifier before it
        # goes into SQL unparameterized (table names can't be bound).
        if not name.replace("_", "").isalnum():
            continue
        count = query(f"SELECT COUNT(*) AS c FROM `{name}`", fetchone=True)["c"]
        tables.append({"name": name, "rows": count})
    return tables


@bp.route("/clear-data", methods=["GET", "POST"])
@admin_required
def clear_data():
    """Development-only reset: empties every business table (products,
    inventory, sales, requests, materials, partners, logs...) while
    keeping user accounts, branches and pricing settings. Uploaded
    product photos are deleted too, since nothing references them after.

    Only exists when ALLOW_DATA_RESET is on (see config.py — forced off
    in production); otherwise this is a plain 404, so the page's very
    existence isn't advertised. Requires typing CLEAR_DATA_CONFIRM_PHRASE.
    """
    if not current_app.config.get("ALLOW_DATA_RESET"):
        abort(404)

    if request.method == "POST":
        if request.form.get("confirm", "").strip() != CLEAR_DATA_CONFIRM_PHRASE:
            flash(f'Type "{CLEAR_DATA_CONFIRM_PHRASE}" exactly to confirm. Nothing was deleted.', "error")
            return redirect(url_for("admin.clear_data"))

        tables = _clearable_tables()
        conn = get_db()
        cur = conn.cursor()
        try:
            # TRUNCATE (not DELETE) so auto-increment ids restart too —
            # the next delivery is DR-000001 again. TRUNCATE can't run
            # against FK-referenced tables with checks on, and it commits
            # implicitly, so this is not one atomic transaction: a failure
            # partway leaves the earlier tables already emptied. Fine for
            # a dev tool, where the answer is simply to run it again.
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in tables:
                cur.execute(f"TRUNCATE TABLE `{table['name']}`")
        except Exception:
            current_app.logger.exception("clear_data failed partway")
            flash("Clearing failed partway — some tables may already be empty. Check the server log "
                  "and run it again.", "error")
            return redirect(url_for("admin.clear_data"))
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            cur.close()

        images_dir = os.path.join(current_app.static_folder, *PRODUCT_IMAGE_SUBDIR.split("/"))
        removed_images = 0
        if os.path.isdir(images_dir):
            for entry in os.scandir(images_dir):
                if entry.is_file():
                    try:
                        os.remove(entry.path)
                        removed_images += 1
                    except OSError:
                        current_app.logger.warning("clear_data: couldn't delete %s", entry.path)

        total_rows = sum(t["rows"] for t in tables)
        # admin_actions was just emptied, so this is the log's first row.
        log_action("clear_all_data", target="Entire database",
                   details=f"{total_rows} rows across {len(tables)} tables, {removed_images} product photos")
        notify_all([
            "products", "inventory", "sales", "requests", "movement_logs", "production", "materials",
            "partners", "partner_inquiries", "users", "branches", "ai_drafts",
        ])
        flash(f"Cleared {total_rows} rows from {len(tables)} tables and {removed_images} product "
              "photos. User accounts, branches and pricing settings were kept.", "success")
        # The Settings dialog posts from whatever page it was opened on;
        # only same-site paths are honoured.
        back = request.form.get("next", "")
        if back.startswith("/") and not back.startswith(("//", "/\\")):
            return redirect(back)
        return redirect(url_for("admin.clear_data"))

    tables = _clearable_tables()
    return render_template(
        "admin/clear_data.html",
        tables=tables,
        total_rows=sum(t["rows"] for t in tables),
        kept_tables=sorted(CLEAR_DATA_KEEP_TABLES),
        confirm_phrase=CLEAR_DATA_CONFIRM_PHRASE,
    )
