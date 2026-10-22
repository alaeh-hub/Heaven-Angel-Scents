"""Fill a LOCAL dev database with fake history so the stats pages have
something to show, or take it back out again:

    python scripts/demo_data.py          # add ~150 days of fake data
    python scripts/demo_data.py --clear  # remove exactly what this added

Everything it inserts is tagged so --clear can find it without touching
real rows: products use SKUs starting "DEMO-", extra branches have the
location "demo-seed", and the other tables carry "demo-seed" in notes /
remarks (raw materials use receipt numbers starting "DEMO-"). Existing
branches and accounts are left alone; sales are spread over the existing
branches plus two demo ones.

Refuses to run when APP_ENV=production.
"""
import os
import random
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heaven_angel.app import create_app  # noqa: E402
from heaven_angel.db import get_db, query  # noqa: E402
from heaven_angel.utils import business_now  # noqa: E402

TAG = "demo-seed"
DAYS = 150

SCENTS = [  # (code, name, variant, base 85ML price)
    ("GH", "Golden Hour", "Male", 1450),
    ("MO", "Midnight Oud", "Male", 1650),
    ("CS", "Citrus Saint", "Male", 1250),
    ("RH", "Rose Halo", "Female", 1500),
    ("VD", "Velvet Dawn", "Female", 1400),
    ("PM", "Pearl Mist", "Female", 1300),
]
UNITS = [("85ML", 1.0, 0.45), ("50ML", 0.62, 0.30), ("10ML", 0.20, 0.12)]  # unit, price factor, sales weight

PACKAGES = [("Starter Reseller Bundle", "Both", 10), ("Distributor Case Pack", "Distributor", 18)]
PARTNERS = [
    ("Distributor", "Luzon Scent Trading", "Marco Reyes"),
    ("Reseller", "Aroma Corner Lipa", "Joy Dela Cruz"),
    ("Distributor", "Batangas Fragrance Hub", "Allan Mercado"),
    ("Reseller", "Scent & Soul Tagaytay", "Mika Santos"),
]
MATERIALS = [
    ("Perfume Oil Base", "Milliliter", 5000, 7800), ("Fixative", "Milliliter", 1000, 2300),
    ("Ethanol 95%", "Liter", 20, 4200), ("85ML Glass Bottle", "Piece", 500, 9500),
    ("50ML Glass Bottle", "Piece", 500, 7400), ("10ML Roller Bottle", "Piece", 1000, 5200),
    ("Bottle Caps", "Piece", 1000, 3100), ("Labels", "Piece", 2000, 2800),
    ("Gift Boxes", "Piece", 300, 4500), ("Fragrance Concentrate", "Milliliter", 2000, 12500),
]


def clear():
    cur = get_db().cursor()
    steps = [
        ("sales", "DELETE FROM sales WHERE sku LIKE 'DEMO-%'"),
        ("stock_movement_logs", "DELETE FROM stock_movement_logs WHERE sku LIKE 'DEMO-%'"),
        ("cogs_logs", "DELETE FROM cogs_logs WHERE sku LIKE 'DEMO-%'"),
        ("branch_inventory", "DELETE FROM branch_inventory WHERE sku LIKE 'DEMO-%'"),
        ("products", "DELETE FROM products WHERE sku LIKE 'DEMO-%'"),
        ("raw_materials", "DELETE FROM raw_materials WHERE receipt_number LIKE 'DEMO-%'"),
        ("partner_inquiries", f"DELETE FROM partner_inquiries WHERE remarks = '{TAG}'"),
        ("packages", f"DELETE FROM packages WHERE description = '{TAG}'"),
        ("partners", f"DELETE FROM partners WHERE notes = '{TAG}'"),
        ("branches", f"DELETE FROM branches WHERE location = '{TAG}'"),
    ]
    for table, sql in steps:
        cur.execute(sql)
        print(f"  {table}: removed {cur.rowcount}")
    get_db().commit()
    cur.close()


def add():
    if query("SELECT 1 FROM products WHERE sku LIKE 'DEMO-%' LIMIT 1", fetchone=True):
        print("Demo data is already present — run with --clear first to rebuild it.")
        return
    rng = random.Random(42)
    conn = get_db()
    cur = conn.cursor()
    now = business_now()

    # --- branches: reuse the real ones, add two demo retail branches ----
    for name in ("Tuy Branch", "Nasugbu Branch"):
        cur.execute("SELECT branch_id FROM branches WHERE branch_name = %s", (name,))
        if not cur.fetchone():
            cur.execute("INSERT INTO branches (branch_name, location, is_hq) VALUES (%s, %s, 0)", (name, TAG))
    cur.execute("SELECT branch_id, is_hq FROM branches")
    branches = cur.fetchall()
    hq_id = next(b for b, hq in branches if hq)
    retail = [b for b, hq in branches if not hq]
    selling = [hq_id] + retail
    # relative size of each outlet so the donuts aren't all even
    weight = {b: w for b, w in zip(selling, [0.7, 1.4, 1.1, 0.8, 0.6][:len(selling)])}
    price_factor = {b: rng.choice([0.97, 1.0, 1.03, 1.05]) for b in selling}

    # --- products + inventory ------------------------------------------
    products = []  # (sku, unit, price)
    for code, name, variant, base in SCENTS:
        for unit, factor, _ in UNITS:
            sku = f"DEMO-{code}-{unit}"
            price = round(base * factor / 10) * 10
            cur.execute(
                "INSERT INTO products (sku, item_name, variant, category, unit, price) VALUES (%s,%s,%s,'Bottled',%s,%s)",
                (sku, f"{name} {unit}", variant, unit, price))
            products.append((sku, unit, price))
    cur.execute(
        "INSERT INTO products (sku, item_name, variant, category, unit, price) VALUES ('DEMO-BULK-GH','Golden Hour Bulk','Male','Bulk/Refill','BULK',9)")
    for b in selling:
        for sku, unit, _ in products + [("DEMO-BULK-GH", "BULK", 9)]:
            qty = rng.randint(4, 140) if unit != "BULK" else rng.randint(500, 4000)
            low = rng.random() < 0.12 and unit != "BULK"
            cur.execute(
                "INSERT INTO branch_inventory (branch_id, sku, stock_qty, reorder_level) VALUES (%s,%s,%s,%s)",
                (b, sku, rng.randint(1, 8) if low else qty, 10))

    weights = [w for _, _, w in UNITS]
    by_unit = {u: [p for p in products if p[1] == u] for u, _, _ in UNITS}

    def pick_product():
        unit = rng.choices([u for u, _, _ in UNITS], weights)[0]
        return rng.choice(by_unit[unit])

    # --- sales + SALE ledger rows --------------------------------------
    sales_n = 0
    for back in range(DAYS, -1, -1):
        day = (now - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
        growth = 0.75 + 0.5 * (DAYS - back) / DAYS            # business slowly growing
        weekly = [0.8, 0.85, 0.9, 1.0, 1.2, 1.5, 1.3][day.weekday()]
        for b in selling:
            count = max(0, round(rng.gauss(6 * weight[b] * growth * weekly, 1.6)))
            for _ in range(count):
                at = day + timedelta(hours=rng.randint(9, 20), minutes=rng.randint(0, 59))
                if at > now:
                    continue
                roll = rng.random()
                if roll < 0.06:   # refill from bulk, sold by the mL
                    sku, qty, price, stype, mtype = "DEMO-BULK-GH", rng.choice([50, 100, 150, 250]), 9, "Refill", "REFILL"
                else:
                    sku, _, base = pick_product()
                    qty = rng.choices([1, 2, 3, 4], [0.55, 0.28, 0.12, 0.05])[0]
                    price = round(base * price_factor[b], 2)
                    stype, mtype = ("Freebie", "FREEBIE") if roll < 0.085 else ("Sale", "SALE")
                    if stype == "Freebie":
                        price = 0
                credit = stype == "Sale" and rng.random() < 0.08
                cur.execute(
                    """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sale_type, payment_method,
                                          buyer_name, sold_at, recorded_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (b, sku, qty, price, stype, "Credit" if credit else "Cash",
                     rng.choice(["Ana", "Ben", "Carla", "Dino", "Ella"]) if credit else None, at, at))
                cur.execute(
                    """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s)""", (b, sku, -qty, mtype, TAG, at))
                sales_n += 1

    # --- production batches (cogs_logs + PRODUCTION / DISPATCH / RECEIPT) -
    batches = 0
    for back in range(DAYS, -1, -4):
        at = (now - timedelta(days=back)).replace(hour=rng.randint(8, 11), minute=rng.randint(0, 59), second=0, microsecond=0)
        for sku, unit, price in rng.sample(products, 5):
            qty = rng.randint(40, 160)
            per_unit = round(price * rng.uniform(0.30, 0.42), 4)
            cur.execute(
                """INSERT INTO cogs_logs (sku, qty_produced, cogs_per_unit, total_cogs, notes, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s)""", (sku, qty, per_unit, round(qty * per_unit, 2), TAG, at))
            cur.execute(
                """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes, created_at)
                   VALUES (%s,%s,%s,'PRODUCTION',%s,%s)""", (hq_id, sku, qty, TAG, at))
            if rng.random() < 0.7 and retail:
                to = rng.choice(retail)
                ship = max(1, qty // 3)
                cur.execute(
                    """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes, created_at)
                       VALUES (%s,%s,%s,'DISPATCH',%s,%s)""", (hq_id, sku, -ship, TAG, at + timedelta(hours=3)))
                cur.execute(
                    """INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes, created_at)
                       VALUES (%s,%s,%s,'RECEIPT',%s,%s)""", (to, sku, ship, TAG, at + timedelta(days=1)))
            batches += 1

    # --- raw materials bought over time ---------------------------------
    for i, (name, unit, pkg_qty, cost) in enumerate(MATERIALS):
        for k in range(rng.randint(1, 3)):
            at = now - timedelta(days=rng.randint(2, DAYS))
            c = round(cost * rng.uniform(0.9, 1.1), 2)
            cur.execute(
                """INSERT INTO raw_materials (material_name, unit, purchase_mode, package_qty, package_cost,
                                              receipt_number, cost_per_unit, stock_qty, created_at)
                   VALUES (%s,%s,'Package',%s,%s,%s,%s,%s,%s)""",
                (f"{name} ({TAG[:4]}{k})", unit, pkg_qty, c, f"DEMO-{i:02d}{k}", c / pkg_qty, pkg_qty * rng.uniform(0.2, 1), at))

    # --- packages, partners and closed package orders --------------------
    package_ids = []
    for name, scope, disc in PACKAGES:
        cur.execute(
            "INSERT INTO packages (package_name, description, partner_scope, discount_percent, is_active) VALUES (%s,%s,%s,%s,1)",
            (name, TAG, scope, disc))
        package_ids.append((cur.lastrowid, name))
    partner_ids = []
    for ptype, pname, contact in PARTNERS:
        cur.execute(
            "INSERT INTO partners (partner_type, partner_name, contact_person, notes) VALUES (%s,%s,%s,%s)",
            (ptype, pname, contact, TAG))
        partner_ids.append((cur.lastrowid, ptype, pname, contact))
    orders = 0
    for _ in range(26):
        pid, ptype, pname, contact = rng.choice(partner_ids)
        pkg_id, pkg_name = rng.choice(package_ids)
        status = rng.choices(["Closed", "New", "Contacted", "Follow-up", "Declined"], [0.62, 0.1, 0.1, 0.1, 0.08])[0]
        at = now - timedelta(days=rng.randint(0, DAYS), hours=rng.randint(0, 20))
        amount = round(rng.uniform(18000, 95000), 2) if status == "Closed" else None
        closed_at = min(at + timedelta(days=rng.randint(1, 6)), now) if status == "Closed" else None
        cur.execute(
            """INSERT INTO partner_inquiries (package_id, partner_id, partner_type, company_name, contact_person,
                                              remarks, package_name_snapshot, order_amount, status, created_at, closed_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (pkg_id, pid, ptype, pname, contact, TAG, pkg_name, amount, status, at, closed_at))
        inquiry_id = cur.lastrowid
        for sku, unit, price in rng.sample(products, 3):
            cur.execute(
                """INSERT INTO partner_inquiry_items (inquiry_id, sku, item_name, unit, qty, unit_price)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (inquiry_id, sku, sku, unit, rng.choice([12, 24, 36, 48]), price))
        orders += status == "Closed"

    conn.commit()
    cur.close()
    print(f"Added {sales_n} sales, {batches} production batches, {len(MATERIALS)} materials, "
          f"{orders} closed package orders across {len(selling)} outlets.")
    print("Remove it again with:  python scripts/demo_data.py --clear")


if __name__ == "__main__":
    if os.environ.get("APP_ENV", "development").lower() == "production":
        print("Refusing to run: APP_ENV=production.")
        sys.exit(1)
    app = create_app()
    with app.app_context():
        if "--clear" in sys.argv:
            clear()
        else:
            add()
