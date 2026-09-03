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
"""
from flask import Blueprint, jsonify, render_template, request, session

from db import query
from decorators import login_required
from utils import parse_receipt_code

bp = Blueprint("scan", __name__, url_prefix="/scan")


def _fetch_sale_for_verify(sale_id, branch_id=None):
    sql = """SELECT s.sale_id, s.sku, s.qty_sold, s.unit_price, s.sale_type, s.payment_method,
                    s.customer_name, s.customer_address, s.sold_at,
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

    is_admin = session.get("role") == "Admin"
    branch_id = None if is_admin else session.get("branch_id")
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
    if sale["payment_method"] == "Salary Deduction" and sale["buyer_username"]:
        payment_value += f" ({sale['buyer_username']})"

    return jsonify(
        match=True,
        sale={
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
        },
    )
