"""Stock side of recording and voiding a sale — shared by admin/branch
Record Sale and the Excel sales import, so every way a sale enters the
system moves stock the same way.

Recording (apply_sale_stock):
  - Sale / Freebie: the product's own stock at that location goes down
    by the quantity sold.
  - Refill: HQ-only (branches never hold bulk). The customer brings their
    own bottle, so no bottle leaves stock — the scent's *bulk* does: the
    product with the same base code and unit BULK (A1-50ML -> A1-BULK),
    by bottles x bottle size in mL, or by the typed mL when the bulk
    product itself is sold. Blocked when there isn't enough bulk.
  Each movement log row carries reference_id = sale_id, so a void can
  reverse exactly what the sale took.

Voiding (void_sale): puts that stock back (logged as SALE_VOID), keeps a
permanent snapshot in sale_voids with who voided it and why, then deletes
the sales row so every revenue / units / customer / credit total
corrects itself without each query having to filter voids out.

Settling (settle_credit): a Credit sale was never "wrong" the way a voided
one is — the product really did leave on credit — so this doesn't touch
stock or delete anything. It just stamps credit_settled_at/_by once the
buyer pays it off, so routes' credit_purchases() (branch and admin) can
exclude it from what's still outstanding.

All three run inside the caller's transaction() on its dictionary cursor
and raise TransactionAborted (rolled back, nothing written) with a
message fit to flash.
"""
from db import TransactionAborted
from utils import bottle_size_ml

HQ_BRANCH_ID = 1

_MOVEMENT_BY_SALE_TYPE = {"Sale": "SALE", "Refill": "REFILL", "Freebie": "FREEBIE"}


def refill_source(product, qty):
    """(bulk_sku, ml) a refill of `qty` of `product` draws from.

    product: dict with sku and unit. A bulk product is its own source
    (qty is already mL); a bottle size maps to its scent's BULK SKU.
    """
    if product["unit"] == "BULK":
        return product["sku"], qty
    suffix = f"-{product['unit']}"
    base = product["sku"][:-len(suffix)] if product["sku"].endswith(suffix) else product["sku"]
    return f"{base}-BULK", qty * int(bottle_size_ml(product["unit"]))


def apply_sale_stock(cur, *, sale_id, branch_id, product, qty, sale_type, notes, user_id):
    """Deduct the stock a just-inserted sale consumes and log it."""
    if sale_type == "Refill":
        if branch_id != HQ_BRANCH_ID:
            raise TransactionAborted("Refills are only recorded at HQ — branches don't hold bulk stock.")
        source_sku, amount = refill_source(product, qty)
        cur.execute("SELECT item_name FROM products WHERE sku = %s", (source_sku,))
        if not cur.fetchone():
            raise TransactionAborted(
                f"No bulk product ({source_sku}) exists for {product['item_name']} to refill from.")
        unit_label = " mL"
        what = f"{product['item_name']} bulk"
        notes = f"{notes} · refill of {qty} × {product['unit']}" if product["unit"] != "BULK" else notes
    else:
        source_sku, amount = product["sku"], qty
        unit_label = " mL" if product["unit"] == "BULK" else ""
        what = product["item_name"]

    cur.execute(
        "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE",
        (branch_id, source_sku),
    )
    row = cur.fetchone()
    have = row["stock_qty"] if row else 0
    if have < amount:
        raise TransactionAborted(
            f"Not enough {what} on hand: need {amount}{unit_label}, have {have}{unit_label}.")
    after_qty = have - amount
    cur.execute(
        "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
        (after_qty, branch_id, source_sku),
    )
    cur.execute(
        """INSERT INTO stock_movement_logs
           (branch_id, sku, change_qty, movement_type, notes,
            created_by_user_id, reference_type, reference_id, before_qty, after_qty)
           VALUES (%s, %s, %s, %s, %s, %s, 'SALE', %s, %s, %s)""",
        (branch_id, source_sku, -amount, _MOVEMENT_BY_SALE_TYPE[sale_type], notes,
         user_id, sale_id, have, after_qty),
    )


def void_sale(cur, *, sale_id, reason, user_id, username, branch_id=None, today_only=False):
    """Void one sale: return its stock, snapshot it, delete it.

    branch_id / today_only restrict a branch user to their own branch's
    sales recorded today. Returns the voided sale's snapshot dict.
    """
    cur.execute(
        """SELECT s.*, p.item_name, p.unit, DATE(s.recorded_at) = CURDATE() AS recorded_today
           FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.sale_id = %s FOR UPDATE""",
        (sale_id,),
    )
    sale = cur.fetchone()
    if not sale or (branch_id is not None and sale["branch_id"] != branch_id):
        raise TransactionAborted("That sale no longer exists.")
    if today_only and not sale["recorded_today"]:
        raise TransactionAborted(
            "Branch staff can only void sales recorded today — ask HQ to void older ones.")

    # What this sale actually took out. Sales recorded before sale logs
    # carried reference_id have no linked rows: a Sale/Freebie took its
    # own qty then, and an old Refill took nothing.
    cur.execute(
        """SELECT branch_id, sku, change_qty FROM stock_movement_logs
           WHERE reference_type = 'SALE' AND reference_id = %s AND change_qty < 0""",
        (sale_id,),
    )
    taken = [(r["branch_id"], r["sku"], -r["change_qty"]) for r in cur.fetchall()]
    if not taken and sale["sale_type"] != "Refill":
        taken = [(sale["branch_id"], sale["sku"], sale["qty_sold"])]

    for loc, sku, qty in taken:
        cur.execute(
            """INSERT INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE stock_qty = stock_qty + VALUES(stock_qty)""",
            (loc, sku, qty),
        )
        cur.execute(
            "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s", (loc, sku))
        after_qty = cur.fetchone()["stock_qty"]
        cur.execute(
            """INSERT INTO stock_movement_logs
               (branch_id, sku, change_qty, movement_type, notes,
                created_by_user_id, reference_type, reference_id, before_qty, after_qty)
               VALUES (%s, %s, %s, 'SALE_VOID', %s, %s, 'SALE_VOID', %s, %s, %s)""",
            (loc, sku, qty, f"Voided {sale['sale_type'].lower()} #{sale_id} — {reason}"[:255],
             user_id, sale_id, after_qty - qty, after_qty),
        )

    cur.execute(
        """INSERT INTO sale_voids
           (sale_id, branch_id, sku, item_name, unit, qty_sold, unit_price, sale_type, payment_method,
            buyer_name, customer_name, sold_at, reason, voided_by_user_id, voided_by_username)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (sale_id, sale["branch_id"], sale["sku"], sale["item_name"], sale["unit"], sale["qty_sold"],
         sale["unit_price"], sale["sale_type"], sale["payment_method"], sale.get("buyer_name"),
         sale.get("customer_name"), sale["sold_at"], reason, user_id, username),
    )
    cur.execute("DELETE FROM sales WHERE sale_id = %s", (sale_id,))
    return sale


def settle_credit(cur, *, sale_id, username, branch_id=None):
    """Mark one Credit sale as paid back. branch_id restricts a branch
    user to their own branch's sales, same convention as void_sale.
    Returns the settled sale's row.
    """
    cur.execute(
        """SELECT s.*, p.item_name FROM sales s JOIN products p ON p.sku = s.sku
           WHERE s.sale_id = %s FOR UPDATE""",
        (sale_id,),
    )
    sale = cur.fetchone()
    if not sale or (branch_id is not None and sale["branch_id"] != branch_id):
        raise TransactionAborted("That sale no longer exists.")
    if sale["payment_method"] != "Credit":
        raise TransactionAborted("That sale isn't a Credit sale.")
    if sale["credit_settled_at"] is not None:
        raise TransactionAborted("That credit was already marked as paid.")
    cur.execute(
        "UPDATE sales SET credit_settled_at = NOW(), credit_settled_by = %s WHERE sale_id = %s",
        (username, sale_id),
    )
    return sale
