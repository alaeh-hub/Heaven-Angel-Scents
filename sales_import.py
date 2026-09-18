"""Bulk sales import — the "Import from Excel" flow on Record Sale.

build_sales_import_template() builds the downloadable .xlsx a branch/HQ
fills in and re-uploads; parse_sales_import_workbook() reads it back
into plain per-row tuples; import_sales_rows() then validates and
inserts the whole file as ONE atomic transaction — either every row
goes in, or nothing does, with every problem reported at once instead
of trickling out one row at a time (same "all-or-nothing" contract as
every other multi-write route in this app — see db.TransactionAborted).

Row shape mirrors exactly what routes/branch.py's and routes/admin.py's
own record_sale() insert by hand: one sales row + one stock_movement_logs
row per line, with stock checked/decremented the same way (a Refill
never draws down branch_inventory).
"""
import datetime
import decimal
import io

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.datavalidation import DataValidation

from db import TransactionAborted, transaction
from reports import XL_BORDER, XL_HEADER_FILL, XL_MONEY_FMT
from utils import (
    PAYMENT_METHODS, SALE_TYPES, ValidationError, parse_optional_text,
    parse_positive_decimal, parse_positive_int,
)

SHEET_NAME = "Import Sales"

# Column order is load-bearing — parse_sales_import_workbook() and the
# template's own header row both read/write positionally against this.
HEADERS = [
    "SKU*",
    "Item Name (reference only)",
    "Qty Sold*",
    "Price Charged* (0 or blank for Freebie)",
    "Sale Type* (Sale, Refill, or Freebie)",
    "Payment Method* (Cash or Credit — ignored for Freebie)",
    "Buyer Name (Credit sales only)",
    "Customer Name",
    "Customer Address",
    "Sale Date (YYYY-MM-DD, blank = today)",
]
_COL_WIDTHS = [16, 26, 10, 14, 20, 22, 22, 20, 26, 26]

# A generous cap on one import batch — big enough for a real backlog of
# unlogged sales, small enough that one bad file can't lock the sales
# table for a very long transaction.
MAX_IMPORT_ROWS = 500


def build_sales_import_template(inventory):
    """Build the downloadable blank template.

    `inventory` is the same branch-scoped
    `branch_inventory JOIN products` rows record_sale() already queries
    (sku, item_name, variant, unit, price, stock_qty) — used only to
    fill a read-only "Products" reference sheet so whoever fills the
    template can copy a valid SKU instead of guessing one.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    for c, label in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        cell.fill = XL_HEADER_FILL
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True)
        cell.border = XL_BORDER
        ws.column_dimensions[chr(64 + c)].width = _COL_WIDTHS[c - 1]
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"

    # Row 2 is a filled-in example so the expected shape/format is
    # obvious (dropdowns alone don't show a date format or that price is
    # plain numeric, not "₱120") — parse_sales_import_workbook() always
    # skips it, so leaving it in place (rather than deleting it first)
    # can never accidentally import a real sale.
    example_values = [
        inventory[0]["sku"] if inventory else "SKU-HERE",
        (inventory[0]["item_name"] if inventory else "Example Item") + " — example row, not imported",
        1, 120.00, "Sale", "Cash", "", "Juan Dela Cruz", "", "",
    ]
    for c, value in enumerate(example_values, start=1):
        cell = ws.cell(row=2, column=c, value=value)
        cell.font = Font(italic=True, color="8A90A0")
    ws.cell(row=2, column=4).number_format = "#,##0.00"

    # Blank working rows below the example, ready to type/paste into.
    n_rows = 200
    last_data_row = 3 + n_rows

    sale_type_dv = DataValidation(
        type="list", formula1=f'"{",".join(SALE_TYPES)}"', allow_blank=True,
        showErrorMessage=True, errorTitle="Invalid Sale Type",
        error="Pick Sale, Refill, or Freebie from the dropdown.",
    )
    ws.add_data_validation(sale_type_dv)
    sale_type_dv.add(f"E2:E{last_data_row}")

    payment_dv = DataValidation(
        type="list", formula1=f'"{",".join(PAYMENT_METHODS)}"', allow_blank=True,
        showErrorMessage=True, errorTitle="Invalid Payment Method",
        error="Pick Cash or Credit from the dropdown.",
    )
    ws.add_data_validation(payment_dv)
    payment_dv.add(f"F2:F{last_data_row}")

    for r in range(2, last_data_row + 1):
        for c in range(1, len(HEADERS) + 1):
            ws.cell(row=r, column=c).border = XL_BORDER

    # ---- reference sheet: every SKU this branch can actually sell ----
    ref = wb.create_sheet("Products (reference)")
    ref_headers = ["SKU", "Item Name", "Variant", "Unit", "Price", "Stock on hand"]
    for c, label in enumerate(ref_headers, start=1):
        cell = ref.cell(row=1, column=c, value=label)
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        cell.fill = XL_HEADER_FILL
        cell.border = XL_BORDER
    ref.row_dimensions[1].height = 20
    ref.freeze_panes = "A2"
    for r, row in enumerate(inventory, start=2):
        ref.cell(row=r, column=1, value=row["sku"]).border = XL_BORDER
        ref.cell(row=r, column=2, value=row["item_name"]).border = XL_BORDER
        ref.cell(row=r, column=3, value=row["variant"]).border = XL_BORDER
        ref.cell(row=r, column=4, value=row["unit"]).border = XL_BORDER
        price_cell = ref.cell(row=r, column=5, value=float(row["price"]))
        price_cell.number_format = XL_MONEY_FMT
        price_cell.border = XL_BORDER
        ref.cell(row=r, column=6, value=int(row["stock_qty"])).border = XL_BORDER
    for c, width in enumerate([16, 26, 10, 12, 12, 14], start=1):
        ref.column_dimensions[chr(64 + c)].width = width

    note = ref.cell(
        row=len(inventory) + 3, column=1,
        value="Copy a SKU from this list into the Import Sales sheet — only SKUs stocked at this branch can be imported.",
    )
    note.font = Font(italic=True, size=9.5, color="5B6272")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def parse_sales_import_workbook(file_storage):
    """Read the uploaded file's Import Sales sheet into a list of
    `(excel_row_number, values_tuple)` pairs, skipping the header row
    (1), the built-in example row (2, see build_sales_import_template()),
    and any fully blank row. Raises ValidationError if the file can't be
    read as an .xlsx at all.
    """
    try:
        wb = load_workbook(file_storage, data_only=True, read_only=True)
    except Exception:
        raise ValidationError(
            "That doesn't look like a valid .xlsx file — download the template and fill that in.")

    ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.worksheets[0]
    rows = []
    for excel_row_num, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
        if row is None or all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        rows.append((excel_row_num, row))
    return rows


def _cell(row, idx):
    return row[idx] if idx < len(row) else None


def _parse_import_date(raw):
    """Excel gives back a real datetime for a date-formatted cell, or a
    plain string/None for a text cell — accept either.
    """
    if isinstance(raw, datetime.datetime):
        picked = raw.date()
    elif isinstance(raw, datetime.date):
        picked = raw
    else:
        raw = str(raw or "").strip()
        if not raw:
            picked = datetime.date.today()
        else:
            try:
                picked = datetime.datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError(
                    "Sale Date must be YYYY-MM-DD, or blank for today.")
    if picked > datetime.date.today():
        raise ValidationError("Sale Date can't be in the future.")
    return datetime.datetime.combine(picked, datetime.datetime.now().time())


def _parse_row(excel_row_num, row):
    sku = str(_cell(row, 0) or "").strip()
    if not sku:
        raise ValidationError("SKU is required.")

    qty_raw = _cell(row, 2)
    # Excel stores a whole number typed into a numeric cell as a float
    # (e.g. 1.0) — str(1.0) is "1.0", which int() rejects outright, so a
    # perfectly valid quantity would otherwise fail to parse.
    if isinstance(qty_raw, float) and qty_raw.is_integer():
        qty_raw = int(qty_raw)
    qty = parse_positive_int(qty_raw, "Qty Sold")

    sale_type = str(_cell(row, 4) or "").strip().title()
    if sale_type not in SALE_TYPES:
        raise ValidationError(
            f"Sale Type must be one of: {', '.join(SALE_TYPES)}.")

    # Freebie is always ₱0 — a giveaway/comp/sample, whatever the Price
    # Charged column happens to say (blank is fine for it; every other
    # sale_type still requires a strictly positive price — see
    # utils.SALE_TYPES for why Freebie exists as its own type).
    if sale_type == "Freebie":
        unit_price = decimal.Decimal("0")
    else:
        unit_price = parse_positive_decimal(_cell(row, 3), "Price Charged")

    # Freebie is never actually paid for, so Payment Method doesn't mean
    # anything for it — forced to Cash regardless of what the column
    # says, same as the manual Record Sale form does.
    if sale_type == "Freebie":
        payment_method = "Cash"
    else:
        payment_method = str(_cell(row, 5) or "").strip().title()
        if payment_method not in PAYMENT_METHODS:
            raise ValidationError(
                f"Payment Method must be one of: {', '.join(PAYMENT_METHODS)}.")

    raw_buyer = str(_cell(row, 6) or "").strip()
    buyer_name = None
    if payment_method == "Credit":
        if not raw_buyer:
            raise ValidationError("Buyer Name is required for a Credit sale.")
        if len(raw_buyer) > 120:
            raise ValidationError("Buyer Name is too long (max 120 characters).")
        buyer_name = raw_buyer

    customer_name = parse_optional_text(_cell(row, 7), "Customer Name", 120)
    customer_address = parse_optional_text(
        _cell(row, 8), "Customer Address", 255)
    sold_at = _parse_import_date(_cell(row, 9))

    return {
        "row": excel_row_num,
        "sku": sku,
        "qty": qty,
        "unit_price": unit_price,
        "sale_type": sale_type,
        "payment_method": payment_method,
        "buyer_name": buyer_name,
        "customer_name": customer_name,
        "customer_address": customer_address,
        "sold_at": sold_at,
    }


def import_sales_rows(branch_id, raw_rows, user_id):
    """Validate and insert every row from parse_sales_import_workbook()
    as one atomic transaction. Returns the number of rows inserted.

    Raises ValidationError for problems that don't need the database
    (empty file, too many rows, bad values on individual rows) and
    TransactionAborted (rolled back, nothing written) for a problem
    only the database can tell us — an unknown SKU or not enough stock.
    Both carry a single newline-joined message; callers flash it one
    line per flash message.
    """
    if not raw_rows:
        raise ValidationError("The file has no data rows to import.")
    if len(raw_rows) > MAX_IMPORT_ROWS:
        raise ValidationError(
            f"Too many rows in one file (max {MAX_IMPORT_ROWS}) — split it into smaller batches.")

    parsed = []
    errors = []
    for excel_row_num, row in raw_rows:
        try:
            parsed.append(_parse_row(excel_row_num, row))
        except ValidationError as err:
            errors.append(f"Row {excel_row_num}: {err}")

    if errors:
        shown = errors[:20]
        if len(errors) > len(shown):
            shown.append(f"…and {len(errors) - len(shown)} more row(s) with problems.")
        raise ValidationError("\n".join(shown))

    inserted = 0
    with transaction() as conn:
        cur = conn.cursor(dictionary=True)
        # One FOR UPDATE lock per distinct SKU touched by this file —
        # same row-locking contract as the single-sale record_sale()
        # routes, just amortized across every line for that SKU instead
        # of re-locking (and re-reading a stale value for) it each time.
        stock_by_sku = {}
        for item in parsed:
            sku = item["sku"]
            if sku in stock_by_sku:
                continue
            cur.execute(
                "SELECT stock_qty FROM branch_inventory WHERE branch_id = %s AND sku = %s FOR UPDATE",
                (branch_id, sku),
            )
            stock_row = cur.fetchone()
            if not stock_row:
                cur.close()
                raise TransactionAborted(
                    f"Row {item['row']}: SKU '{sku}' isn't stocked at this branch.")
            stock_by_sku[sku] = stock_row["stock_qty"]

        for item in parsed:
            sku = item["sku"]
            is_refill = item["sale_type"] == "Refill"
            before_qty = stock_by_sku[sku]

            if not is_refill and before_qty < item["qty"]:
                cur.close()
                raise TransactionAborted(
                    f"Row {item['row']}: not enough stock for '{sku}' "
                    f"(have {before_qty}, this file needs {item['qty']} here).")

            after_qty = before_qty if is_refill else before_qty - item["qty"]
            stock_by_sku[sku] = after_qty

            cur.execute(
                """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sale_type, payment_method,
                                      buyer_name, customer_name, customer_address, sold_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (branch_id, sku, item["qty"], item["unit_price"], item["sale_type"],
                 item["payment_method"], item["buyer_name"], item["customer_name"],
                 item["customer_address"], item["sold_at"]),
            )
            if not is_refill:
                cur.execute(
                    "UPDATE branch_inventory SET stock_qty = %s WHERE branch_id = %s AND sku = %s",
                    (after_qty, branch_id, sku),
                )
            movement_type = {"Sale": "SALE", "Refill": "REFILL",
                              "Freebie": "FREEBIE"}[item["sale_type"]]
            if item["sale_type"] == "Freebie":
                notes = "Freebie / giveaway (imported)"
            else:
                notes = "Imported from Excel" if item["payment_method"] == "Cash" else f"Imported from Excel — Credit — {item['buyer_name']}"
            if is_refill:
                notes += " · no stock deducted (refill)"
            cur.execute(
                """INSERT INTO stock_movement_logs
                   (branch_id, sku, change_qty, movement_type, notes,
                    created_by_user_id, reference_type, before_qty, after_qty)
                   VALUES (%s, %s, %s, %s, %s, %s, 'SALE', %s, %s)""",
                (branch_id, sku, 0 if is_refill else -item["qty"], movement_type, notes,
                 user_id, before_qty, after_qty),
            )
            inserted += 1
        cur.close()

    return inserted
