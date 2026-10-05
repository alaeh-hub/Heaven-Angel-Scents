"""QR-code receipt verification — scan a sale receipt's QR (webcam), or
decode one from an uploaded photo/screenshot, and confirm the sale it
points to is really on file, with the details pulled straight from the
database rather than trusted off the receipt itself.

The QR code printed on every sale receipt (see receipts.py) encodes a
short signed code — not the sale's data — built by
utils.make_receipt_code(). Decoding a QR only ever gets you that string
back; this module is what turns it into an actual sale, and refuses
anything that isn't validly signed (a garbled scan, a QR from some
unrelated app, a hand-typed guess) rather than trusting it blindly. See
utils.py's comment above make_receipt_code() for why a plain HMAC code
was used instead of, say, itsdangerous's serializer.

Both the webcam and "upload a photo" paths decode the QR client-side
(see static/js/scan.js and the vendored static/js/vendor/jsqr.js) — this
module only ever receives the already-decoded text, over POST
/scan/verify. Nothing here parses an uploaded file directly; there's
nothing here to route by content-type or content-length for a file at
all, since the browser never uploads one — decoding happens entirely
in the tab before anything is sent to the server. A manual code entry
field on the same page hits the exact same endpoint, for when neither a
camera nor a legible photo is available.

Access is scoped the same way as everywhere else with role/branch data:
Branch staff can only verify a sale recorded at their own branch (same
convention as receipts.py's build_sale_receipt_pdf and every other
branch-scoped read in this app); Admin can verify any sale, at any
branch.

A verified sale also gets two quick actions right from this page — void
it, or (for a Credit sale) mark it as paid — instead of having to look it
up again in Sales History / Credit Purchases. Both reuse sale_stock.py's
void_sale()/settle_credit() so stock, the void snapshot, and the Admin Log
all behave exactly the same as doing it from those pages; the only
difference is these two routes answer with JSON (not a redirect + flash)
since the scan page is a single-page, no-reload flow. Branch staff are
held to the same restrictions as elsewhere: void only a sale recorded
today, settle-credit only their own branch's sales; Admin has neither
restriction.
"""
from flask import Blueprint, current_app, jsonify, render_template, request, session

from audit import log_action
from db import TransactionAborted, query, transaction
from decorators import login_required
from sale_stock import settle_credit, void_sale
from sockets import notify_admin_and_branch
from utils import parse_receipt_code

bp = Blueprint("scan", __name__, url_prefix="/scan")


def _fetch_sale_for_verify(sale_id, branch_id=None):
    sql = """SELECT s.sale_id, s.sku, s.qty_sold, s.unit_price, s.sale_type, s.payment_method,
                    s.customer_name, s.customer_address, s.sold_at,
                    s.credit_settled_at, DATE(s.recorded_at) = CURDATE() AS recorded_today,
                    p.item_name, p.variant, p.unit, b.branch_name,
                    COALESCE(s.buyer_name, bu.username) AS buyer_username
             FROM sales s
             JOIN products p ON s.sku = p.sku
             JOIN branches b ON s.branch_id = b.branch_id
             LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
             WHERE s.sale_id = %s"""
    params = [sale_id]
    if branch_id is not None:
        sql += " AND s.branch_id = %s"
        params.append(branch_id)
    return query(sql, tuple(params), fetchone=True)


def _scope():
    """(is_admin, branch_id-to-restrict-to-or-None) for the logged-in user."""
    is_admin = session.get("role") == "Admin"
    return is_admin, (None if is_admin else session.get("branch_id"))


@bp.route("/")
@login_required
def scan_page():
    return render_template("scan/index.html")


@bp.route("/verify", methods=["POST"])
@login_required
def verify():
    """Take a decoded QR string (or a hand-typed code) and say whether
    it's a genuine, on-file Heaven & Angel Scents receipt — and if so,
    what was actually recorded for it. Branch staff only ever see this
    for their own branch's sales; a code from another branch comes back
    as not-found for them exactly the same as one that doesn't exist at
    all, so this can't be used to probe whether a sale_id exists
    elsewhere.
    """
    payload = request.get_json(silent=True) or {}
    raw_code = str(payload.get("code", "")).strip()
    if not raw_code:
        return jsonify(match=False, message="No code provided."), 400

    sale_id = parse_receipt_code(raw_code)
    if sale_id is None:
        return jsonify(
            match=False,
            message="That doesn't look like a Heaven & Angel Scents receipt code.",
        )

    is_admin, branch_id = _scope()
    sale = _fetch_sale_for_verify(sale_id, branch_id)
    if not sale:
        message = (
            "No sale on file with that receipt code."
            if is_admin else
            "That receipt code isn't on file for your branch."
        )
        return jsonify(match=False, message=message)

    line_total = sale["qty_sold"] * sale["unit_price"]
    payment_value = sale["payment_method"]
    is_credit = sale["payment_method"] == "Credit"
    credit_settled = sale["credit_settled_at"] is not None
    if is_credit and sale["buyer_username"]:
        payment_value += f" ({sale['buyer_username']})"
    if is_credit and credit_settled:
        payment_value += " — paid"

    return jsonify(
        match=True,
        sale={
            "sale_id": sale["sale_id"],
            "receipt_no": f"SR-{sale['sale_id']:06d}",
            "item_name": sale["item_name"],
            "sku": sale["sku"],
            "variant": sale["variant"],
            "unit": sale["unit"],
            "qty_sold": sale["qty_sold"],
            "unit_price": float(sale["unit_price"]),
            "line_total": float(line_total),
            "sale_type": sale["sale_type"],
            "payment_method": payment_value,
            "customer_name": sale["customer_name"] or "Walk-in",
            "customer_address": sale["customer_address"],
            "branch_name": sale["branch_name"],
            "sold_at": sale["sold_at"].strftime("%b %d, %Y %I:%M %p"),
            "is_credit": is_credit,
            "credit_settled": credit_settled,
            "voidable": is_admin or bool(sale["recorded_today"]),
        },
    )


@bp.route("/sales/<int:sale_id>/void", methods=["POST"])
@login_required
def void_sale_scan(sale_id):
    """Void the just-scanned sale right from this page. See sale_stock.void_sale()."""
    is_admin, branch_id = _scope()
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get("reason", "")).strip()[:255]
    if not reason:
        return jsonify(ok=False, message="Give a reason for voiding this sale."), 400
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            sale = void_sale(cur, sale_id=sale_id, reason=reason,
                              user_id=session.get("user_id"), username=session.get("username"),
                              branch_id=branch_id, today_only=not is_admin)
            cur.close()
    except TransactionAborted as err:
        return jsonify(ok=False, message=str(err)), 400
    except Exception:
        current_app.logger.exception("scan void sale %s failed", sale_id)
        return jsonify(ok=False, message="Couldn't void that sale — please try again."), 500
    notify_admin_and_branch(sale["branch_id"], ["inventory", "sales", "movement_logs"])
    log_action("void_sale", target=f"sale #{sale_id} — {sale['item_name']}",
               details=f"via Scan Receipt · {sale['sale_type']} × {sale['qty_sold']} · {reason}"[:255])
    return jsonify(
        ok=True,
        message=f"Voided the {sale['sale_type'].lower()} of {sale['item_name']} — its stock was returned.",
    )


@bp.route("/sales/<int:sale_id>/settle-credit", methods=["POST"])
@login_required
def settle_credit_scan(sale_id):
    """Mark the just-scanned Credit sale as paid back. See sale_stock.settle_credit()."""
    is_admin, branch_id = _scope()
    try:
        with transaction() as conn:
            cur = conn.cursor(dictionary=True)
            sale = settle_credit(cur, sale_id=sale_id, username=session.get("username"),
                                  branch_id=branch_id)
            cur.close()
    except TransactionAborted as err:
        return jsonify(ok=False, message=str(err)), 400
    except Exception:
        current_app.logger.exception("scan settle credit %s failed", sale_id)
        return jsonify(ok=False, message="Couldn't mark that credit as paid — please try again."), 500
    log_action("settle_credit", target=f"sale #{sale_id} — {sale['item_name']}",
               details=f"via Scan Receipt · {sale.get('buyer_name') or ''}"[:255])
    return jsonify(ok=True, message="Marked that credit as paid.")
