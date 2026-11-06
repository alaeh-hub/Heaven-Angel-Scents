"""Fill a LOCAL dev database with ~18 months of believable demo history so every
page and graph in the system has something to show, or take it back out again:

    python scripts/demo_data.py --reset     # remove old demo data, then build the new set (recommended)
    python scripts/demo_data.py             # build it (refuses if demo data is already there)
    python scripts/demo_data.py --clear     # remove exactly what this added
    python scripts/demo_data.py --dry-run   # build everything, print the summary, then ROLL BACK

It is a small simulation, not random noise: HQ produces stock from bulk batches
(which use up raw materials that get restocked from suppliers), branches request
stock when they run low, HQ dispatches it, branches receive it (sometimes short or
damaged), and sales only happen against stock that is really there. So the
inventory, movement logs, cost of goods, requests and sales all agree with each
other, and the dashboards show a long, growing, seasonal revenue curve.

Everything it inserts is tagged so --clear can find it without touching real rows:
products use SKUs starting "DEMO-", extra branches have a location ending in
"demo-seed", stock requests are numbered "DEMO-xxxxxx", raw materials use receipt
numbers starting "DEMO-", and the other tables carry "demo-seed" in notes /
remarks / details. Existing branches and accounts are left alone (they get demo
sales too); demo branch logins are created for the extra branches.

Refuses to run when APP_ENV=production.
"""
import heapq
import math
import os
import random
import sys
from collections import defaultdict
from datetime import timedelta
from functools import partial

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):      # the Windows console defaults to cp1252, which has no peso sign
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from werkzeug.security import generate_password_hash  # noqa: E402

from heaven_angel.app import create_app  # noqa: E402
from heaven_angel.db import get_db, query  # noqa: E402
from heaven_angel.utils import business_now  # noqa: E402

TAG = "demo-seed"
DAYS = 540                      # how far back the history starts
DEMO_PASSWORD = "DemoPass#2026"  # login for the demo branch accounts

# code, name, variant, base 85ML price, popularity, has a bulk/refill SKU
SCENTS = [
    ("GH", "Golden Hour", "Male", 1450, 1.30, True),
    ("MO", "Midnight Oud", "Male", 1650, 1.10, True),
    ("CS", "Citrus Saint", "Male", 1250, 0.90, False),
    ("AB", "Amber Blaze", "Male", 1550, 0.80, False),
    ("RH", "Rose Halo", "Female", 1500, 1.30, True),
    ("VD", "Velvet Dawn", "Female", 1400, 1.00, True),
    ("PM", "Pearl Mist", "Female", 1300, 0.90, False),
    ("JV", "Jasmine Veil", "Female", 1450, 0.80, False),
]
# unit, price factor, sales weight, mL per bottle, flat cost of goods per bottle
UNITS = [
    ("85ML", 1.00, 0.38, 85, 300.0),
    ("50ML", 0.62, 0.30, 50, 185.0),
    ("10ML", 0.20, 0.20, 10, 52.0),
    ("3ML", 0.07, 0.12, 3, 20.0),
]
BULK_PRICE_PER_ML = 9.0

# name, unit, package qty, package cost, supplier index, receipt code
MATERIALS = [
    ("Fragrance Concentrate", "Milliliter", 1000, 6800, 0),
    ("Perfume Oil Base", "Milliliter", 5000, 8200, 0),
    ("Fixative", "Milliliter", 1000, 2400, 1),
    ("Ethanol 95%", "Liter", 20, 4400, 1),
    ("85ML Glass Bottle", "Piece", 100, 3900, 2),
    ("50ML Glass Bottle", "Piece", 100, 2700, 2),
    ("10ML Roller Bottle", "Piece", 200, 2600, 2),
    ("3ML Glass Vial", "Piece", 500, 2300, 2),
    ("Bottle Caps", "Piece", 500, 1700, 3),
    ("Labels", "Piece", 1000, 1900, 3),
    ("Gift Boxes", "Piece", 100, 2900, 3),
]
SUPPLIERS = [
    ("Manila Aroma Imports", "Rina Castillo", "0917 555 0142", "sales@manilaaroma.example", "Binondo, Manila"),
    ("Laguna Chemical Supply", "Edgar Ramos", "0918 555 0177", "orders@lagunachem.example", "Calamba, Laguna"),
    ("Bulacan Glassworks", "Teresita Cruz", "0920 555 0113", "hello@bulacanglass.example", "Meycauayan, Bulacan"),
    ("Cavite Packaging Co.", "Noel Villanueva", "0922 555 0166", "sales@cavitepack.example", "Imus, Cavite"),
]
# unit -> [(material name, qty per bottle)]
FORMULAS = {
    "85ML": [("85ML Glass Bottle", 1), ("Bottle Caps", 1), ("Labels", 1), ("Gift Boxes", 1)],
    "50ML": [("50ML Glass Bottle", 1), ("Bottle Caps", 1), ("Labels", 1)],
    "10ML": [("10ML Roller Bottle", 1), ("Labels", 1)],
    "3ML": [("3ML Glass Vial", 1), ("Labels", 1)],
}
# liquid recipe for a bulk batch: material, share of the batch volume
RECIPE = [("Fragrance Concentrate", 0.28), ("Perfume Oil Base", 0.18), ("Fixative", 0.04), ("Ethanol 95%", 0.50)]

# name, weight (relative demand), days ago opened, town. The HQ warehouse and any
# real branches already in the database are used as they are.
DEMO_BRANCHES = [
    ("Tuy Branch", 1.10, 470, "Tuy, Batangas"),
    ("Nasugbu Branch", 1.20, 360, "Nasugbu, Batangas"),
    ("Lemery Branch", 0.90, 240, "Lemery, Batangas"),
    ("Calatagan Branch", 0.80, 120, "Calatagan, Batangas"),
]
KNOWN_WEIGHTS = {"HQ Main Warehouse": 0.8, "Balayan Branch": 1.4}

SEASON = {1: 0.90, 2: 1.25, 3: 0.95, 4: 0.95, 5: 1.15, 6: 1.00,
          7: 0.90, 8: 0.95, 9: 1.00, 10: 1.10, 11: 1.25, 12: 1.70}
WEEKLY = [0.80, 0.85, 0.90, 1.00, 1.25, 1.55, 1.30]   # Monday .. Sunday
PROMO_DAYS = {(2, 14), (5, 12), (11, 11), (12, 12), (12, 24)}

FIRST = ["Ana", "Ben", "Carla", "Dino", "Ella", "Francis", "Grace", "Harold", "Iris", "Jon", "Kris",
         "Lara", "Mark", "Nina", "Oscar", "Paula", "Quin", "Rhea", "Sam", "Tina", "Uriel", "Vince"]
LAST = ["Reyes", "Santos", "Cruz", "Bautista", "Garcia", "Mendoza", "Torres", "Flores", "Ramos",
        "Aquino", "Castillo", "Villanueva", "Dela Cruz", "Navarro", "Salazar", "Mercado"]
TOWNS = ["Balayan", "Tuy", "Nasugbu", "Lemery", "Calatagan", "Lian", "Taal", "Bauan", "Batangas City", "Lipa"]
VOID_REASONS = ["Wrong item rung up", "Customer changed mind", "Duplicate entry", "Wrong quantity entered",
                "Payment declined"]

PARTNERS = [
    ("Distributor", "Luzon Scent Trading", "Marco Reyes", "Quezon City"),
    ("Distributor", "Batangas Fragrance Hub", "Allan Mercado", "Batangas City"),
    ("Distributor", "Calabarzon Aroma Depot", "Lito Fernandez", "Santa Rosa, Laguna"),
    ("Distributor", "Visayas Scent Merchants", "Corazon Uy", "Iloilo City"),
    ("Reseller", "Aroma Corner Lipa", "Joy Dela Cruz", "Lipa, Batangas"),
    ("Reseller", "Scent & Soul Tagaytay", "Mika Santos", "Tagaytay, Cavite"),
    ("Reseller", "The Perfume Nook", "Patricia Lim", "Makati City"),
    ("Reseller", "Fragrance Fix Lemery", "Dante Cabrera", "Lemery, Batangas"),
    ("Reseller", "Oud & Co. Cebu", "Hannah Tan", "Cebu City"),
    ("Reseller", "Scentsational Balayan", "Rowena Pineda", "Balayan, Batangas"),
    ("Distributor", "Mindanao Perfume Link", "Rey Abella", "Davao City"),
    ("Reseller", "Bloom & Bottle", "Sheila Ocampo", "Nasugbu, Batangas"),
]
# name, partner scope, discount %, [(sku code, unit, qty)]
PACKAGES = [
    ("Starter Reseller Bundle", "Both", 10, [("GH", "85ML", 6), ("RH", "85ML", 6), ("MO", "50ML", 6),
                                             ("VD", "50ML", 6), ("CS", "10ML", 12)]),
    ("Distributor Case Pack", "Distributor", 18, [("GH", "85ML", 24), ("MO", "85ML", 24), ("RH", "85ML", 24),
                                                  ("VD", "85ML", 12), ("PM", "50ML", 24)]),
    ("Best Sellers Trio", "Both", 12, [("GH", "50ML", 12), ("RH", "50ML", 12), ("MO", "50ML", 12)]),
    ("Refill Station Kit", "Reseller", 8, [("GH", "BULK", 5000), ("RH", "BULK", 5000), ("VD", "10ML", 24)]),
    ("Travel Size Sampler", "Both", 5, [("GH", "3ML", 60), ("MO", "3ML", 60), ("RH", "3ML", 60),
                                        ("VD", "3ML", 60), ("AB", "3ML", 60), ("JV", "3ML", 60)]),
]


def clear():
    conn = get_db()
    cur = conn.cursor()
    steps = [
        # accounts / audit trail
        ("demo branch logins", "DELETE FROM users WHERE branch_id IN (SELECT branch_id FROM branches WHERE location LIKE '%demo-seed')"),
        ("login_activity", "DELETE FROM login_activity WHERE user_agent LIKE '%demo-seed'"),
        ("admin_actions", "DELETE FROM admin_actions WHERE details LIKE '%demo-seed'"),
        # requests, drafts, voids
        ("ai_stock_drafts", "DELETE FROM ai_stock_drafts WHERE note LIKE '%demo-seed'"),
        ("stock_requests", "DELETE FROM stock_requests WHERE delivery_number LIKE 'DEMO-%'"),
        ("sale_voids", "DELETE FROM sale_voids WHERE sku LIKE 'DEMO-%'"),
        # production side
        ("material_usage_logs", "DELETE FROM material_usage_logs WHERE notes LIKE '%demo-seed'"),
        ("bulk_batches", "DELETE FROM bulk_batches WHERE notes LIKE '%demo-seed'"),
        ("unit_formula_items", "DELETE FROM unit_formula_items WHERE material_id IN "
                               "(SELECT material_id FROM raw_materials WHERE receipt_number LIKE 'DEMO-%')"),
        ("raw_materials", "DELETE FROM raw_materials WHERE receipt_number LIKE 'DEMO-%'"),
        ("suppliers", "DELETE FROM suppliers WHERE notes LIKE '%demo-seed'"),
        # partners
        ("partner_inquiries", "DELETE FROM partner_inquiries WHERE remarks LIKE '%demo-seed'"),
        ("packages", "DELETE FROM packages WHERE description LIKE '%demo-seed'"),
        ("partners", "DELETE FROM partners WHERE notes LIKE '%demo-seed'"),
        # products take sales, stock movement, cost of goods, production logs,
        # branch inventory and package items with them (ON DELETE CASCADE)
        ("products", "DELETE FROM products WHERE sku LIKE 'DEMO-%'"),
        ("branches", "DELETE FROM branches WHERE location LIKE '%demo-seed'"),
    ]
    for table, sql in steps:
        cur.execute(sql)
        print(f"  {table}: removed {cur.rowcount}")
    cur.execute("SELECT 1 FROM app_settings WHERE setting_key = 'demo_seed_costs'")
    if cur.fetchone():
        cur.execute("UPDATE unit_cogs_settings SET base_cost_per_unit = 0")
        cur.execute("UPDATE bulk_rate_settings SET rate_per_ml = 0")
        cur.execute("DELETE FROM app_settings WHERE setting_key = 'demo_seed_costs'")
        print("  cost settings: reset to 0 (they were filled by the demo)")
    conn.commit()
    cur.close()


class Sim:
    """Day-by-day business simulation that writes straight to the database."""

    def __init__(self, conn, rng, now):
        self.conn = conn
        self.cur = conn.cursor()
        self.rng = rng
        self.now = now
        self.start = (now - timedelta(days=DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
        self.heap = []
        self.seq = 0
        self.inv = defaultdict(int)
        self.ledger = []
        self.actions = []
        self.ema = defaultdict(float)
        self.sold_today = defaultdict(float)
        self.open_qty = defaultdict(int)       # requested but not yet received, per (branch, sku)
        self.requests = []
        self.batches = {}                       # scent code -> its current batch {id, remaining}
        self.batch_left = {}                    # every batch id -> mL left
        self.batch_seq = 0
        self.prod_seq = 0
        self.counts = defaultdict(int)
        self.revenue = 0.0

    # ------------------------------------------------------------------ plumbing
    def push(self, ts, fn):
        if ts > self.now:
            return
        self.seq += 1
        heapq.heappush(self.heap, (ts, self.seq, fn))

    def move(self, branch, sku, change, mtype, notes, ts, ref=None, rid=None, user=None, touch=True):
        if touch:
            before = self.inv[(branch, sku)]
            after = before + change
            if after < 0:
                raise RuntimeError(f"stock would go negative: {branch} {sku} {before} {change}")
            self.inv[(branch, sku)] = after
        else:   # damage / short-delivery notes: logged, but they never entered stock
            before = after = None
        self.ledger.append((branch, sku, change, mtype, notes, user, ref, rid, before, after, ts))

    def act(self, ts, action, target, details=None):
        text = f"{details} · {TAG}" if details else TAG
        self.actions.append((self.admin_id, "admin", action, str(target)[:120], text[:255], ts))

    def flush_ledger(self):
        sql = ("INSERT INTO stock_movement_logs (branch_id, sku, change_qty, movement_type, notes, "
               "created_by_user_id, reference_type, reference_id, before_qty, after_qty, created_at) "
               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        for i in range(0, len(self.ledger), 5000):
            self.cur.executemany(sql, self.ledger[i:i + 5000])
        n = len(self.ledger)
        self.ledger = []
        return n

    # ------------------------------------------------------------------ demand model
    def mean_count(self, o, back):
        if back > o["open_back"]:
            return 0.0
        day = self.start + timedelta(days=DAYS - back)
        growth = 0.60 + 0.90 * (DAYS - back) / DAYS
        age = o["open_back"] - back
        ramp = 0.4 + 0.6 * min(age, 30) / 30
        promo = 1.6 if (day.month, day.day) in PROMO_DAYS else 1.0
        payday = 1.2 if day.day in (15, 30) else 1.0
        return 10 * o["weight"] * growth * ramp * SEASON[day.month] * WEEKLY[day.weekday()] * promo * payday

    def total_ema(self, sku):
        return sum(self.ema[(o["id"], sku)] for o in self.outlets)

    # ------------------------------------------------------------------ materials
    def material_cost_now(self, m, ts):
        drift = 1 + 0.08 * ((ts - self.start).days / DAYS)         # prices creep up ~8% over the period
        return m["pkg_cost"] * drift * self.rng.uniform(0.97, 1.03)

    def ensure_material(self, name, need, ts):
        m = self.materials[name]
        if m["stock"] >= need:
            return
        packs = math.ceil((need - m["stock"]) / m["pkg_qty"]) + (1 if m["unit"] != "Piece" else 0)
        when = max(ts - timedelta(hours=self.rng.randint(20, 70)), self.start - timedelta(days=2))
        cost = round(self.material_cost_now(m, when) * packs, 2)
        qty = packs * m["pkg_qty"]
        sup = self.suppliers[m["supplier"]]
        self.cur.execute(
            """INSERT INTO material_restock_events (material_id, material_name_snapshot, unit_snapshot, qty, cost,
                      supplier_id, supplier_name_snapshot, purchased_at, created_by_user_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (m["id"], name, m["unit"], qty, cost, sup["id"], sup["name"], when, self.admin_id))
        m["stock"] += qty
        m["cost_per_unit"] = cost / qty
        m["pkg_cost_latest"] = cost / packs
        self.act(when, "restock_material", name, f"{qty:g} {m['unit'].lower()} from {sup['name']} for ₱{cost:,.2f}")
        self.counts["restocks"] += 1

    def new_batch(self, code, volume_ml, ts):
        scent = self.scent_by_code[code]
        self.batch_seq += 1
        litres = volume_ml / 1000
        batch_code = f"DEMO-B{ts:%y%m}-{code}-{self.batch_seq:03d}"
        lines, total = [], 0.0
        for name, share in RECIPE:
            m = self.materials[name]
            qty_unit = "Liter" if m["unit"] == "Liter" else "Milliliter"
            qty = litres * share if qty_unit == "Liter" else volume_ml * share
            self.ensure_material(name, qty, ts)
            line_cost = round(qty * m["cost_per_unit"], 4)
            total += line_cost
            lines.append((m, qty_unit, round(qty, 3), round(m["cost_per_unit"], 4), line_cost))
        total = round(total, 2)
        self.cur.execute(
            """INSERT INTO bulk_batches (batch_code, scent_name, input_qty, input_unit, total_volume_ml, total_cost,
                      cost_per_ml, remaining_ml, notes, created_by_user_id, created_at)
               VALUES (%s,%s,%s,'Liter',%s,%s,%s,%s,%s,%s,%s)""",
            (batch_code, scent[1], litres, volume_ml, total, round(total / volume_ml, 6), volume_ml,
             f"{scent[1]} liquid batch · {TAG}", self.admin_id, ts))
        batch_id = self.cur.lastrowid
        for m, qty_unit, qty, unit_cost, line_cost in lines:
            self.cur.execute(
                """INSERT INTO bulk_batch_materials (batch_id, material_id, qty_used_unit, qty_used,
                          cost_per_unit_snapshot, line_cost, is_packaging)
                   VALUES (%s,%s,%s,%s,%s,%s,0)""",
                (batch_id, m["id"], qty_unit, qty, unit_cost, line_cost))
            m["stock"] -= qty
        self.batches[code] = {"id": batch_id, "remaining": float(volume_ml)}
        self.batch_left[batch_id] = float(volume_ml)
        self.act(ts, "create_bulk_batch", scent[1], f"{volume_ml:,.0f} mL · {batch_code} · ₱{total:,.2f}")
        self.counts["batches"] += 1

    # ------------------------------------------------------------------ production
    def produce(self, sku, qty, ts):
        p = self.products[sku]
        batch_id = None
        if p["category"] == "Bottled":
            need_ml = p["ml"] * qty
            b = self.batches.get(p["code"])
            if b is None or b["remaining"] < need_ml:
                size = max(10000, math.ceil(need_ml / 5000) * 5000)
                self.new_batch(p["code"], size, ts - timedelta(hours=self.rng.randint(1, 5)))
                b = self.batches[p["code"]]
            b["remaining"] -= need_ml
            batch_id = b["id"]
            self.batch_left[batch_id] = b["remaining"]
            drift = 0.93 + 0.07 * ((ts - self.start).days / DAYS)
            per_unit = round(p["flat_cost"] * drift * self.rng.uniform(0.98, 1.02), 4)
            for mat, per in FORMULAS[p["unit"]]:
                self.ensure_material(mat, per * qty, ts)
        else:
            per_unit = round(self.rng.uniform(2.9, 3.7), 4)
        self.prod_seq += 1
        code = f"DEMO-P{ts:%y%m%d}-{self.prod_seq:04d}"
        self.cur.execute(
            "INSERT INTO production_logs (sku, batch_code, qty_produced, produced_at) VALUES (%s,%s,%s,%s)",
            (sku, code, qty, ts))
        log_id = self.cur.lastrowid
        self.move(self.hq, sku, qty, "PRODUCTION", f"Batch {code}", ts, "PRODUCTION_LOG", log_id, self.admin_id)
        self.cur.execute(
            """INSERT INTO cogs_logs (sku, production_log_id, bulk_batch_id, qty_produced, cogs_per_unit,
                      total_cogs, notes, created_by_user_id, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (sku, log_id, batch_id, qty, per_unit, round(per_unit * qty, 2), TAG, self.admin_id, ts))
        cogs_id = self.cur.lastrowid
        if p["category"] == "Bottled":
            for mat, per in FORMULAS[p["unit"]]:
                used = per * qty
                self.materials[mat]["stock"] -= used
                self.cur.execute(
                    """INSERT INTO material_usage_logs (material_id, production_log_id, cogs_log_id, qty_used, notes,
                              created_by_user_id, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (self.materials[mat]["id"], log_id, cogs_id, used, f"{code} · {TAG}", self.admin_id, ts))
        self.counts["production_runs"] += 1
        self.counts["units_produced"] += qty if p["category"] == "Bottled" else 0

    def daily_production(self, day):
        rng = self.rng
        skus = list(self.products)
        rng.shuffle(skus)
        runs = 0
        for sku in skus:
            p = self.products[sku]
            bulk = p["category"] != "Bottled"
            tot = self.total_ema(sku)
            low = max(tot * 14, 3000 if bulk else 40)
            if self.inv[(self.hq, sku)] >= low:
                continue
            raw = tot * 35
            qty = (min(30000, max(4000, round(raw / 500) * 500)) if bulk
                   else min(900, max(60, round(raw / 10) * 10)))
            ts = day + timedelta(hours=8, minutes=15 * runs + rng.randint(0, 10))
            self.push(ts, partial(self.produce, sku, qty, ts))
            runs += 1
            if runs >= 10:
                break

    # ------------------------------------------------------------------ sales
    def sale(self, o, ts):
        rng = self.rng
        b = o["id"]
        roll = rng.random()
        stype, mtype, payment = "Sale", "SALE", "Cash"
        if roll < 0.06 and self.bulk_skus:                       # refill from bulk, sold by the mL
            sku = rng.choices(self.bulk_skus, self.bulk_w)[0]
            qty = rng.choice([50, 100, 150, 250, 300])
            self.sold_today[(b, sku)] += qty          # demand counts for forecasting even if it can't be met
            avail = self.inv[(b, sku)]
            if avail < qty:
                qty = (avail // 50) * 50
            if qty <= 0:
                self.counts["lost_to_stockout"] += 1
                self.counts[f"lost:{o['name']}:refill"] += 1
                return
            stype, mtype = "Refill", "REFILL"
            price = BULK_PRICE_PER_ML
        else:
            sku, qty = None, 0
            want = rng.choices([1, 2, 3, 4, 6, 12], [0.52, 0.26, 0.10, 0.05, 0.05, 0.02])[0]
            for attempt in range(3):                             # a customer settles for another size/scent if out
                cand = rng.choices(self.bottled_skus, self.bottled_w)[0]
                if attempt == 0:
                    self.sold_today[(b, cand)] += want    # demand counts for forecasting even if it can't be met
                avail = self.inv[(b, cand)]
                if avail > 0:
                    sku, qty = cand, min(want, avail)
                    break
            if not sku:
                self.counts["lost_to_stockout"] += 1
                self.counts[f"lost:{o['name']}:{ts:%Y-%m}"] += 1
                return
            p = self.products[sku]
            since = (ts - self.start).days / DAYS
            price = p["price"] * (0.94 + 0.06 * since) * o["price_factor"]
            if (ts.month, ts.day) in PROMO_DAYS:
                price *= 0.92
            price = round(price, 2)
            if roll < 0.085:
                stype, mtype, price = "Freebie", "FREEBIE", 0
                qty = 1
            elif rng.random() < 0.09:
                payment = "Credit"
        buyer = settled_at = settled_by = customer = address = None
        if payment == "Credit":
            buyer = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if rng.random() < 0.85:
                settle = ts + timedelta(days=rng.randint(3, 40), hours=rng.randint(0, 8))
                if settle <= self.now:
                    settled_at, settled_by = settle, "admin"
        elif stype != "Freebie" and rng.random() < 0.15:
            customer = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            address = f"{rng.randint(1, 240)} {rng.choice(['Rizal', 'Mabini', 'P. Burgos', 'Quezon', 'Bonifacio'])} St., {rng.choice(TOWNS)}"
        self.cur.execute(
            """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sale_type, payment_method, buyer_name,
                      credit_settled_at, credit_settled_by, customer_name, customer_address, sold_at, recorded_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (b, sku, qty, price, stype, payment, buyer, settled_at, settled_by, customer, address, ts, ts))
        sale_id = self.cur.lastrowid
        self.move(b, sku, -qty, mtype, f"{stype} #{sale_id}", ts, "SALE", sale_id, o["user_id"])
        self.counts["sales"] += 1
        self.revenue += qty * price
        if rng.random() < 0.004:                                  # a small share get voided the same day
            at = ts + timedelta(minutes=rng.randint(20, 240))
            snap = (sale_id, b, sku, qty, price, stype, payment, buyer, customer, ts)
            self.push(at, partial(self.void_sale, o, snap, at))

    def void_sale(self, o, snap, ts):
        sale_id, b, sku, qty, price, stype, payment, buyer, customer, sold_at = snap
        p = self.products[sku]
        reason = self.rng.choice(VOID_REASONS)
        self.cur.execute("DELETE FROM sales WHERE sale_id = %s", (sale_id,))
        self.cur.execute(
            """INSERT INTO sale_voids (sale_id, branch_id, sku, item_name, unit, qty_sold, unit_price, sale_type,
                      payment_method, buyer_name, customer_name, sold_at, reason, voided_by_user_id,
                      voided_by_username, voided_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (sale_id, b, sku, p["name"], p["unit"], qty, price, stype, payment, buyer, customer, sold_at,
             f"{reason} · {TAG}", o["user_id"], o["username"], ts))
        self.move(b, sku, qty, "SALE_VOID", f"Void of sale #{sale_id}: {reason}", ts, "SALE", sale_id, o["user_id"])
        self.sold_today[(b, sku)] -= qty
        self.counts["voids"] += 1
        self.counts["sales"] -= 1
        self.revenue -= qty * price

    # ------------------------------------------------------------------ stock requests
    def make_request(self, o, ts, target_days):
        b = o["id"]
        lines = []
        for sku, p in self.products.items():
            bulk = p["category"] != "Bottled"
            ema = self.ema[(b, sku)]
            have = self.inv[(b, sku)] + self.open_qty[(b, sku)]
            if ema < 0.02 or have >= max(ema * 12, 0 if bulk else 4):
                continue
            need = ema * target_days - have
            if bulk:
                qty = max(1000, math.ceil(need / 500) * 500)
            else:
                qty = max(6, math.ceil(need / 6) * 6)
            lines.append((sku, qty))
        if not lines:
            return
        self.cur.execute(
            "INSERT INTO stock_requests (branch_id, delivery_number, status, requested_at, updated_at) "
            "VALUES (%s,%s,'Pending',%s,%s)", (b, f"DEMO-T{self.seq:09d}", ts, ts))
        rid = self.cur.lastrowid
        delivery = f"DEMO-{rid:06d}"
        self.cur.execute("UPDATE stock_requests SET delivery_number = %s, updated_at = %s WHERE request_id = %s",
                         (delivery, ts, rid))
        req = {"id": rid, "branch": o, "delivery": delivery, "status": "Pending", "items": [], "chain": 0,
               "partial_plan": self.rng.random() < 0.10, "dispatches": 0}
        for sku, qty in lines:
            self.cur.execute(
                "INSERT INTO stock_request_items (request_id, sku, requested_qty, unit_price) VALUES (%s,%s,%s,%s)",
                (rid, sku, qty, self.products[sku]["price"]))
            req["items"].append({"id": self.cur.lastrowid, "sku": sku, "req": qty, "disp": 0, "recv": 0, "dmg": 0})
            self.open_qty[(b, sku)] += qty
        self.requests.append(req)
        self.counts["requests"] += 1
        slow = self.rng.random() < 0.25          # some requests sit waiting for HQ for a few days
        when = ts + timedelta(hours=self.rng.randint(30, 90) if slow else self.rng.randint(4, 30))
        self.push(when, partial(self.dispatch, req, when, 0))

    def set_status(self, req, status, ts):
        req["status"] = status
        self.cur.execute("UPDATE stock_requests SET status = %s, updated_at = %s WHERE request_id = %s",
                         (status, ts, req["id"]))

    def release_open(self, req):
        b = req["branch"]["id"]
        for it in req["items"]:
            left = it["req"] - it["recv"]
            self.open_qty[(b, it["sku"])] = max(0, self.open_qty[(b, it["sku"])] - left)

    def dispatch(self, req, ts, attempt):
        if req["status"] in ("Fulfilled", "Rejected", "In Transit"):
            return
        rng = self.rng
        if req["dispatches"] == 0 and attempt == 0 and rng.random() < 0.04:
            self.set_status(req, "Rejected", ts)
            self.release_open(req)
            self.counts["rejected"] += 1
            return
        lines = []
        for it in req["items"]:
            rem = it["req"] - it["recv"]
            if rem <= 0:
                continue
            q = min(rem, self.inv[(self.hq, it["sku"])])
            if req["partial_plan"] and req["dispatches"] == 0:
                q = min(q, max(1, int(rem * 0.6)))
            if q > 0:
                lines.append((it, q))
        if not lines:
            if req["dispatches"] == 0 and (attempt >= 3 or rng.random() < 0.03):
                self.set_status(req, "Rejected", ts)
                self.release_open(req)
                self.counts["rejected"] += 1
            elif attempt < 3:
                when = ts + timedelta(days=1)
                self.push(when, partial(self.dispatch, req, when, attempt + 1))
            else:
                self.release_open(req)
            return
        self.cur.execute("INSERT INTO stock_request_shipments (request_id, status, dispatched_by_user_id, dispatched_at) "
                         "VALUES (%s,'In Transit',%s,%s)", (req["id"], self.admin_id, ts))
        sid = self.cur.lastrowid
        shipped = []
        for it, q in lines:
            self.cur.execute(
                "INSERT INTO stock_request_shipment_items (shipment_id, request_item_id, dispatched_qty) VALUES (%s,%s,%s)",
                (sid, it["id"], q))
            ship_item = self.cur.lastrowid
            it["disp"] += q
            self.cur.execute("UPDATE stock_request_items SET dispatched_qty = %s WHERE item_id = %s",
                             (it["disp"], it["id"]))
            self.move(self.hq, it["sku"], -q, "DISPATCH", f"Dispatched on {req['delivery']} shipment #{sid}", ts,
                      "STOCK_REQUEST", req["id"], self.admin_id)
            shipped.append((it, q, ship_item))
        req["dispatches"] += 1
        self.set_status(req, "In Transit", ts)
        when = ts + timedelta(hours=rng.randint(20, 70))
        self.push(when, partial(self.receive, req, sid, shipped, when))

    def receive(self, req, sid, shipped, ts):
        rng = self.rng
        o = req["branch"]
        b = o["id"]
        for it, q, ship_item in shipped:
            damaged = short = 0
            if rng.random() < 0.05:
                damaged = rng.randint(1, max(1, q // 10))
            if rng.random() < 0.02:
                short = rng.randint(1, max(1, q // 20))
            got = max(0, q - damaged - short)
            damaged = min(damaged, q - got)
            short = q - got - damaged
            self.cur.execute("UPDATE stock_request_shipment_items SET received_qty = %s, damaged_qty = %s "
                             "WHERE shipment_item_id = %s", (got, damaged, ship_item))
            it["recv"] += got
            it["dmg"] += damaged
            self.cur.execute("UPDATE stock_request_items SET received_qty = %s, damaged_qty = %s WHERE item_id = %s",
                             (it["recv"], it["dmg"], it["id"]))
            self.open_qty[(b, it["sku"])] = max(0, self.open_qty[(b, it["sku"])] - got)
            if got > 0:
                self.move(b, it["sku"], got, "RECEIPT",
                          f"Receipt for delivery {req['delivery']}, shipment #{sid}", ts,
                          "STOCK_REQUEST", req["id"], o["user_id"])
            if damaged:
                self.move(b, it["sku"], -damaged, "DAMAGE",
                          f"{damaged} unit(s) damaged in transit, delivery {req['delivery']}, shipment #{sid}", ts,
                          "STOCK_REQUEST", req["id"], o["user_id"], touch=False)
            if short:
                self.move(b, it["sku"], -short, "ADJUSTMENT",
                          f"{short} unit(s) dispatched but not received or reported damaged — delivery "
                          f"{req['delivery']}, shipment #{sid}, flagged for HQ follow-up", ts,
                          "STOCK_REQUEST", req["id"], o["user_id"], touch=False)
        self.cur.execute("UPDATE stock_request_shipments SET status = 'Received', received_at = %s "
                         "WHERE shipment_id = %s", (ts, sid))
        open_lines = any(it["recv"] < it["req"] for it in req["items"])
        self.set_status(req, "Partially Fulfilled" if open_lines else "Fulfilled", ts)
        if open_lines:
            if req["chain"] < 2 and rng.random() < 0.65:
                req["chain"] += 1
                when = ts + timedelta(days=rng.randint(1, 3), hours=rng.randint(0, 6))
                self.push(when, partial(self.dispatch, req, when, 0))
            else:
                self.release_open(req)     # HQ never ships the remainder; stays Partially Fulfilled

    def daily_requests(self, day, back):
        rng = self.rng
        for o in self.outlets:
            if o["is_hq"] or back > o["open_back"]:
                continue
            ts = day + timedelta(hours=rng.randint(9, 15), minutes=rng.randint(0, 59))
            if back == o["open_back"]:
                self.push(ts, partial(self.make_request, o, ts, 35))             # opening stock order
            elif day.weekday() == o["req_weekday"]:
                self.push(ts, partial(self.make_request, o, ts, 28))             # weekly top-up
            elif day.weekday() == (o["req_weekday"] + 3) % 7:
                self.push(ts, partial(self.make_request, o, ts, 21))             # mid-week top-up
            elif rng.random() < 0.04:
                self.push(ts, partial(self.make_request, o, ts, 14))             # ad-hoc top-up

    # ------------------------------------------------------------------ package orders
    def fulfill(self, inq, ts):
        rng = self.rng
        for sku, qty in inq["items"]:
            short = qty - self.inv[(self.hq, sku)]
            if short > 0:      # "produce the shortfall on Production Log first"
                p = self.products[sku]
                step = 500 if p["category"] != "Bottled" else 12
                self.produce(sku, math.ceil((short + step) / step) * step, ts - timedelta(hours=rng.randint(2, 20)))
        for sku, qty in inq["items"]:
            self.move(self.hq, sku, -qty, "PACKAGE_ORDER", f"Package order for {inq['company']}", ts,
                      "PARTNER_INQUIRY", inq["id"], self.admin_id)
        self.cur.execute("UPDATE partner_inquiries SET fulfilled_at = %s, fulfilled_by_user_id = %s WHERE inquiry_id = %s",
                         (ts, self.admin_id, inq["id"]))
        self.act(ts, "fulfill_package_order", inq["company"], f"{len(inq['items'])} item(s) from HQ stock")
        self.counts["fulfilled_orders"] += 1

    # ------------------------------------------------------------------ main loop
    def run(self):
        rng = self.rng
        for back in range(DAYS, -1, -1):
            day = self.start + timedelta(days=DAYS - back)
            self.sold_today.clear()
            self.daily_production(day)
            self.daily_requests(day, back)
            for o in self.outlets:
                mean = self.mean_count(o, back)
                if mean <= 0:
                    continue
                for _ in range(max(0, round(rng.gauss(mean, math.sqrt(mean) * 0.6)))):
                    ts = day + timedelta(hours=rng.randint(9, 20), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
                    self.push(ts, partial(self.sale, o, ts))
            end = day + timedelta(days=1)
            while self.heap and self.heap[0][0] < end:
                _, _, fn = heapq.heappop(self.heap)
                if fn is not None:
                    fn()
            for key in list(self.ema):
                self.ema[key] *= 0.93
            for key, units in self.sold_today.items():
                self.ema[key] += 0.07 * max(0, units)
            if day.day == 1:
                print(f"  ...{day:%Y-%m}: {self.counts['sales']:,} sales so far")
        while self.heap:           # anything left is stamped after "now" and was already filtered out
            _, _, fn = heapq.heappop(self.heap)
            if fn is not None:
                fn()


def verify(cur):
    """Cross-checks that the generated rows agree with each other. Returns a list of problems."""
    checks = [
        ("negative branch stock",
         "SELECT COUNT(*) FROM branch_inventory WHERE stock_qty < 0"),
        ("branch stock that does not equal its movement ledger",
         """SELECT COUNT(*) FROM branch_inventory bi
            LEFT JOIN (SELECT branch_id, sku, SUM(change_qty) AS net FROM stock_movement_logs
                       WHERE before_qty IS NOT NULL GROUP BY branch_id, sku) m
              ON m.branch_id = bi.branch_id AND m.sku = bi.sku
            WHERE bi.sku LIKE 'DEMO-%' AND bi.stock_qty <> COALESCE(m.net, 0)"""),
        ("ledger rows whose before/after do not match the change",
         "SELECT COUNT(*) FROM stock_movement_logs WHERE sku LIKE 'DEMO-%' AND before_qty IS NOT NULL "
         "AND after_qty <> before_qty + change_qty"),
        ("shipment lines that received + damaged more than was dispatched",
         "SELECT COUNT(*) FROM stock_request_shipment_items WHERE received_qty IS NOT NULL "
         "AND received_qty + damaged_qty > dispatched_qty"),
        ("request lines that received more than requested",
         "SELECT COUNT(*) FROM stock_request_items sri JOIN stock_requests sr ON sr.request_id = sri.request_id "
         "WHERE sr.delivery_number LIKE 'DEMO-%' AND COALESCE(sri.received_qty, 0) > sri.requested_qty"),
        ("bulk batches with impossible remaining mL",
         "SELECT COUNT(*) FROM bulk_batches WHERE notes LIKE '%demo-seed' "
         "AND (remaining_ml < 0 OR remaining_ml > total_volume_ml)"),
        ("raw materials below zero",
         "SELECT COUNT(*) FROM raw_materials WHERE receipt_number LIKE 'DEMO-%' AND stock_qty < 0"),
        ("sales for a branch before it existed in the demo",
         "SELECT COUNT(*) FROM sales s JOIN branches b ON b.branch_id = s.branch_id "
         "WHERE s.sku LIKE 'DEMO-%' AND b.location LIKE '%demo-seed' AND s.sold_at < b.created_at"),
    ]
    problems = []
    for label, sql in checks:
        cur.execute(sql)
        n = cur.fetchone()[0]
        if n:
            problems.append(f"{n} {label}")
    return problems


def add(dry_run=False):
    if query("SELECT 1 FROM products WHERE sku LIKE 'DEMO-%' LIMIT 1", fetchone=True):
        print("Demo data is already present. Run with --reset to rebuild it from scratch.")
        return
    rng = random.Random(42)
    conn = get_db()
    cur = conn.cursor()
    now = business_now()
    start = (now - timedelta(days=DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)

    cur.execute("SELECT user_id FROM users WHERE role = 'Admin' ORDER BY user_id LIMIT 1")
    row = cur.fetchone()
    admin_id = row[0] if row else None

    # --- branches, outlets and demo branch logins ------------------------
    outlets = []
    cur.execute("SELECT branch_id, branch_name, is_hq FROM branches ORDER BY branch_id")
    existing = cur.fetchall()
    have = {name for _, name, _ in existing}
    for name, weight, opened, town in DEMO_BRANCHES:
        if name not in have:
            cur.execute("INSERT INTO branches (branch_name, location, is_hq, created_at) VALUES (%s,%s,0,%s)",
                        (name, f"{town} · {TAG}", now - timedelta(days=opened)))
    cur.execute("SELECT branch_id, branch_name, is_hq FROM branches ORDER BY branch_id")
    demo_meta = {n: (w, d) for n, w, d, _ in DEMO_BRANCHES}
    pw_hash = generate_password_hash(DEMO_PASSWORD)
    retail_i = 0
    for bid, name, is_hq in cur.fetchall():
        weight, opened = demo_meta.get(name, (KNOWN_WEIGHTS.get(name, 1.0), DAYS))
        cur.execute("SELECT user_id, username FROM users WHERE branch_id = %s AND role = 'Branch' ORDER BY user_id LIMIT 1", (bid,))
        u = cur.fetchone()
        if not is_hq and u is None and name in demo_meta:
            username = name.split()[0].lower()
            cur.execute("SELECT user_id FROM users WHERE username = %s", (username,))
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO users (username, password_hash, role, branch_id, is_active, must_change_password, created_at) "
                    "VALUES (%s,%s,'Branch',%s,1,0,%s)", (username, pw_hash, bid, now - timedelta(days=opened)))
                u = (cur.lastrowid, username)
        outlets.append({
            "id": bid, "name": name, "is_hq": bool(is_hq), "weight": weight, "open_back": min(opened, DAYS),
            "price_factor": rng.choice([0.97, 1.0, 1.0, 1.03, 1.05]),
            "user_id": u[0] if u else None, "username": u[1] if u else "branch",
            "req_weekday": retail_i % 7,
        })
        if not is_hq:
            retail_i += 2
    hq = next(o for o in outlets if o["is_hq"])["id"]

    # --- products ---------------------------------------------------------
    products = {}
    for code, name, variant, base, pop, has_bulk in SCENTS:
        for unit, factor, _, ml, flat in UNITS:
            sku = f"DEMO-{code}-{unit}"
            price = max(10, round(base * factor / 10) * 10)
            cur.execute(
                "INSERT INTO products (sku, item_name, variant, category, unit, price, created_at) "
                "VALUES (%s,%s,%s,'Bottled',%s,%s,%s)", (sku, name, variant, unit, price, start))
            products[sku] = {"sku": sku, "code": code, "name": name, "unit": unit, "ml": ml, "price": price,
                             "category": "Bottled", "flat_cost": flat}
        if has_bulk:
            sku = f"DEMO-{code}-BULK"
            cur.execute(
                "INSERT INTO products (sku, item_name, variant, category, unit, price, created_at) "
                "VALUES (%s,%s,%s,'Bulk/Refill','BULK',%s,%s)", (sku, name, variant, BULK_PRICE_PER_ML, start))
            products[sku] = {"sku": sku, "code": code, "name": name, "unit": "BULK", "ml": 1,
                             "price": BULK_PRICE_PER_ML, "category": "Bulk/Refill", "flat_cost": 0}

    # --- suppliers, raw materials, formulas -------------------------------
    suppliers = []
    for name, contact, phone, email, address in SUPPLIERS:
        cur.execute("SELECT supplier_id, supplier_name FROM suppliers WHERE supplier_name = %s", (name,))
        row = cur.fetchone()
        if row:
            suppliers.append({"id": row[0], "name": row[1]})
            continue
        cur.execute(
            "INSERT INTO suppliers (supplier_name, contact_person, phone, email, address, notes, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)", (name, contact, phone, email, address, TAG, start))
        suppliers.append({"id": cur.lastrowid, "name": name})
    materials = {}
    for i, (name, unit, pkg_qty, pkg_cost, sup) in enumerate(MATERIALS):
        cur.execute("SELECT 1 FROM raw_materials WHERE material_name = %s", (name,))
        db_name = f"{name} (Demo)" if cur.fetchone() else name
        cur.execute(
            """INSERT INTO raw_materials (material_name, unit, purchase_mode, package_qty, package_cost, receipt_number,
                      cost_per_unit, stock_qty, supplier_id, created_at)
               VALUES (%s,%s,'Package',%s,%s,%s,%s,0,%s,%s)""",
            (db_name, unit, pkg_qty, pkg_cost, f"DEMO-{i + 1:03d}", pkg_cost / pkg_qty, suppliers[sup]["id"], start))
        materials[name] = {"id": cur.lastrowid, "db_name": db_name, "unit": unit, "pkg_qty": pkg_qty,
                           "pkg_cost": pkg_cost, "pkg_cost_latest": pkg_cost, "supplier": sup,
                           "stock": 0.0, "cost_per_unit": pkg_cost / pkg_qty}
    for unit, parts in FORMULAS.items():
        for mat, per in parts:
            m = materials[mat]
            cur.execute(
                "INSERT INTO unit_formula_items (unit, material_id, qty_per_unit, unit_cost, line_cost) "
                "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE qty_per_unit = VALUES(qty_per_unit)",
                (unit, m["id"], per, round(m["cost_per_unit"], 4), round(m["cost_per_unit"] * per, 4)))

    # --- cost settings (only if still at their untouched default of 0) ------
    cur.execute("SELECT COALESCE(SUM(base_cost_per_unit), 0) FROM unit_cogs_settings")
    cogs_total = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(rate_per_ml), 0) FROM bulk_rate_settings")
    rate_total = cur.fetchone()[0]
    if not cogs_total and not rate_total:
        for unit, _, _, _, flat in UNITS:
            cur.execute("UPDATE unit_cogs_settings SET base_cost_per_unit = %s WHERE unit = %s", (flat, unit))
        cur.execute("UPDATE bulk_rate_settings SET rate_per_ml = %s", (BULK_PRICE_PER_ML,))
        cur.execute("REPLACE INTO app_settings (setting_key, setting_value) VALUES ('demo_seed_costs', '1')")

    # --- packages and package items --------------------------------------
    package_ids = []
    for name, scope, disc, items in PACKAGES:
        cur.execute(
            "INSERT INTO packages (package_name, description, partner_scope, discount_percent, is_active, created_at) "
            "VALUES (%s,%s,%s,%s,1,%s)", (name, f"{name} · {TAG}", scope, disc, start))
        pid = cur.lastrowid
        resolved = [(f"DEMO-{c}-{u}", q) for c, u, q in items]
        for sku, qty in resolved:
            cur.execute("INSERT INTO package_items (package_id, sku, qty) VALUES (%s,%s,%s)", (pid, sku, qty))
        package_ids.append({"id": pid, "name": name, "scope": scope, "disc": disc, "items": resolved})

    # --- partners, investments and package inquiries ------------------------
    partners = []
    for ptype, pname, contact, town in PARTNERS:
        cur.execute(
            "INSERT INTO partners (partner_type, partner_name, contact_person, phone, email, address, notes, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (ptype, pname, contact, f"09{rng.randint(10, 99)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
             f"{contact.split()[0].lower()}@{pname.split()[0].lower().replace('&', '')}.example", town, TAG, start))
        partners.append({"id": cur.lastrowid, "type": ptype, "name": pname, "contact": contact, "town": town,
                         "first": None, "last": None, "count": 0})
    for p in partners:
        if p["type"] == "Distributor" or rng.random() < 0.4:
            for _ in range(rng.randint(1, 3)):
                cur.execute(
                    "INSERT INTO partner_investments (partner_id, amount, note, logged_by_user_id, created_at) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (p["id"], rng.choice([50000, 75000, 100000, 150000, 250000, 400000]),
                     rng.choice(["Territory deposit", "Opening stock investment", "Display fixtures contribution",
                                 "Marketing co-investment"]),
                     admin_id, now - timedelta(days=rng.randint(10, DAYS))))
    inquiries = []
    for _ in range(95):
        back = int(rng.betavariate(1, 1.25) * DAYS)
        created = now - timedelta(days=back, hours=rng.randint(0, 20), minutes=rng.randint(0, 59))
        if created < start:
            created = start + timedelta(hours=rng.randint(1, 100))
        partner = rng.choice(partners)
        fits = [p for p in package_ids if p["scope"] in ("Both", partner["type"])]
        pkg = rng.choice(fits)
        age = (now - created).days
        if age <= 14:
            status = rng.choices(["New", "Contacted", "Follow-up", "On Hold", "Closed", "Declined"],
                                 [0.3, 0.25, 0.2, 0.05, 0.15, 0.05])[0]
        else:
            status = rng.choices(["Closed", "Declined", "On Hold", "Follow-up", "Contacted"],
                                 [0.62, 0.17, 0.07, 0.07, 0.07])[0]
        mult = rng.choice([2, 3, 4, 5]) if partner["type"] == "Distributor" else rng.choice([1, 1, 2, 3])
        items = [(sku, qty * mult) for sku, qty in pkg["items"]]
        gross = sum(qty * products[sku]["price"] for sku, qty in items)
        amount = round(gross * (1 - pkg["disc"] / 100), 2) if status == "Closed" else None
        closed_at = fulfilled_at = None
        if status == "Closed":
            closed_at = min(created + timedelta(days=rng.randint(1, 5), hours=rng.randint(0, 6)), now)
            ful = closed_at + timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 8))
            if ful <= now and rng.random() < 0.9:
                fulfilled_at = ful
        cur.execute(
            """INSERT INTO partner_inquiries (package_id, partner_id, partner_type, company_name, contact_person, phone,
                      email, address, message, preferred_contact, remarks, package_name_snapshot, order_amount, status,
                      email_sent, fulfilled_at, fulfilled_by_user_id, closed_at, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (pkg["id"], partner["id"], partner["type"], partner["name"], partner["contact"],
             f"09{rng.randint(10, 99)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
             f"{partner['contact'].split()[0].lower()}@{partner['name'].split()[0].lower().replace('&', '')}.example",
             partner["town"],
             rng.choice(["Interested in stocking your best sellers.", "Please send the price list and terms.",
                         "We want to open a refill corner in our shop.", "Looking for a monthly supply arrangement.",
                         "Can we get a sample set before ordering?"]),
             rng.choice(["Call", "SMS", "Viber", "Email"]), TAG, pkg["name"], amount, status,
             1 if rng.random() < 0.7 else 0, None, None, closed_at, created))
        iid = cur.lastrowid
        for sku, qty in items:
            p = products[sku]
            cur.execute(
                "INSERT INTO partner_inquiry_items (inquiry_id, sku, item_name, unit, qty, unit_price) "
                "VALUES (%s,%s,%s,%s,%s,%s)", (iid, sku, p["name"], p["unit"], qty, p["price"]))
        partner["count"] += 1
        partner["first"] = created if partner["first"] is None else min(partner["first"], created)
        partner["last"] = created if partner["last"] is None else max(partner["last"], created)
        inquiries.append({"id": iid, "company": partner["name"], "items": items, "fulfilled_at": fulfilled_at,
                          "created": created, "status": status, "amount": amount})

    # --- the simulation: production, requests, shipments, sales, voids --------
    sim = Sim(conn, rng, now)
    sim.admin_id, sim.hq, sim.outlets = admin_id, hq, outlets
    sim.products, sim.materials = products, materials
    sim.suppliers, sim.scent_by_code = suppliers, {s[0]: s for s in SCENTS}
    pop = {s[0]: s[4] for s in SCENTS}
    sim.bottled_skus = [s for s, p in products.items() if p["category"] == "Bottled"]
    unit_w = {u[0]: u[2] for u in UNITS}
    sim.bottled_w = [pop[products[s]["code"]] * unit_w[products[s]["unit"]] for s in sim.bottled_skus]
    sim.bulk_skus = [s for s, p in products.items() if p["category"] != "Bottled"]
    sim.bulk_w = [pop[products[s]["code"]] for s in sim.bulk_skus]
    tot_w = sum(sim.bottled_w)
    for o in outlets:                          # demand estimates to size the very first orders
        base = 10 * o["weight"] * 0.6
        for s, w in zip(sim.bottled_skus, sim.bottled_w):
            sim.ema[(o["id"], s)] = base * w / tot_w * 1.6
        for s, w in zip(sim.bulk_skus, sim.bulk_w):
            sim.ema[(o["id"], s)] = base * 0.06 * 130 * w / sum(sim.bulk_w)
    for inq in inquiries:
        if inq["fulfilled_at"]:
            sim.push(inq["fulfilled_at"], partial(sim.fulfill, inq, inq["fulfilled_at"]))
    for ts_off, unit in ((1, "85ML"), (1, "50ML"), (1, "10ML"), (1, "3ML")):
        sim.act(start + timedelta(days=ts_off, hours=9), "save_unit_cogs", unit, "Base cost set")
    sim.act(start + timedelta(days=1, hours=9), "save_bulk_rate", None, f"₱{BULK_PRICE_PER_ML:.4f}/mL")
    for p in products.values():
        sim.act(start + timedelta(hours=rng.randint(1, 60)), "add_product", p["sku"], f"{p['name']} {p['unit']}")
    sim.run()

    # --- final state: inventory, batches, materials ------------------------------
    n_ledger = sim.flush_ledger()
    for o in outlets:
        for sku, p in products.items():
            level = 1000 if p["category"] != "Bottled" else 12
            cur.execute(
                "INSERT INTO branch_inventory (branch_id, sku, stock_qty, reorder_level) VALUES (%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE stock_qty = VALUES(stock_qty), reorder_level = VALUES(reorder_level)",
                (o["id"], sku, sim.inv[(o["id"], sku)], level))
    for bid, left in sim.batch_left.items():
        cur.execute("UPDATE bulk_batches SET remaining_ml = %s WHERE batch_id = %s", (round(max(0.0, left), 3), bid))
    for m in materials.values():
        cur.execute("UPDATE raw_materials SET stock_qty = %s, cost_per_unit = %s, package_cost = %s WHERE material_id = %s",
                    (round(max(0.0, m["stock"]), 6), round(m["cost_per_unit"], 4), round(m["pkg_cost_latest"], 2), m["id"]))
    for p in partners:
        if p["count"]:
            cur.execute("UPDATE partners SET inquiry_count = %s, last_inquiry_at = %s, created_at = %s WHERE partner_id = %s",
                        (p["count"], p["last"], p["first"] - timedelta(days=rng.randint(2, 20)), p["id"]))
    sim.act(start + timedelta(hours=2), "add_branch", "demo branches", f"{len(outlets)} outlets")

    # --- AI stock drafts ------------------------------------------------------------
    drafts = 0
    retail = [o for o in outlets if not o["is_hq"]]
    for i in range(18):
        o = rng.choice(retail)
        when = now - timedelta(days=rng.randint(1, 120), hours=rng.randint(0, 20))
        if when < now - timedelta(days=o["open_back"] - 7):
            continue
        status = "Pending Review" if when > now - timedelta(days=4) else rng.choices(
            ["Approved", "Rejected"], [0.75, 0.25])[0]
        linked = [r for r in sim.requests if r["branch"]["id"] == o["id"] and r["status"] != "Rejected"]
        req_id = rng.choice(linked)["id"] if (status == "Approved" and linked) else None
        cur.execute(
            """INSERT INTO ai_stock_drafts (branch_id, created_by_user_id, created_by_username, status, note, reasoning,
                      resulting_request_id, reviewed_by_user_id, reviewed_at, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (o["id"], o["user_id"], o["username"], status, f"Restock suggestion · {TAG}",
             rng.choice(["Top sellers are under a week of cover based on the last 4 weeks of sales.",
                         "Sales are trending up for the weekend; 50ML and 85ML sizes are running low.",
                         "Refill stock is nearly out while refills sold steadily this month."]),
             req_id, admin_id if status != "Pending Review" else None,
             when + timedelta(hours=rng.randint(1, 20)) if status != "Pending Review" else None, when))
        did = cur.lastrowid
        for sku in rng.sample(sim.bottled_skus, rng.randint(4, 8)):
            cur.execute("INSERT INTO ai_stock_draft_items (draft_id, sku, suggested_qty) VALUES (%s,%s,%s)",
                        (did, sku, rng.choice([12, 18, 24, 36, 48])))
        drafts += 1

    # --- sign-in history ----------------------------------------------------------------
    logins = []
    accounts = [("admin", "Admin", admin_id)] + [(o["username"], "Branch", o["user_id"]) for o in retail if o["user_id"]]
    for back in range(200, -1, -1):
        day = (now - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
        for username, role, uid in accounts:
            for _ in range(rng.choice([0, 1, 1, 2, 2, 3])):
                at = day + timedelta(hours=rng.randint(7, 20), minutes=rng.randint(0, 59))
                if at > now:
                    continue
                ua = f"Chrome 126 on {rng.choice(['Windows 11', 'Android 14', 'iPhone iOS 17'])} · {TAG}"
                ip = f"192.168.1.{rng.randint(10, 80)}"
                if rng.random() < 0.06:
                    logins.append((uid, username, role, 0, rng.choice(["bad_credentials", "bad_credentials", "wrong_role_tab"]), ip, ua, at - timedelta(minutes=1)))
                logins.append((uid, username, role, 1, None, ip, ua, at))
    cur.executemany(
        "INSERT INTO login_activity (user_id, username_attempted, role_attempted, success, failure_reason, ip_address, "
        "user_agent, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", logins)
    cur.executemany(
        "INSERT INTO admin_actions (actor_user_id, actor_username, action, target, details, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)", sim.actions)

    problems = verify(cur)
    if problems:
        conn.rollback()
        print("Integrity check FAILED, nothing was saved:")
        for line in problems:
            print("  -", line)
        sys.exit(1)

    cur.execute("SELECT COALESCE(SUM(bi.stock_qty), 0) FROM branch_inventory bi JOIN products p ON p.sku = bi.sku "
                "WHERE p.category = 'Bottled' AND bi.branch_id = %s", (hq,))
    hq_bottles = int(cur.fetchone()[0])
    cur.execute("SELECT COALESCE(SUM(bi.stock_qty), 0) FROM branch_inventory bi JOIN products p ON p.sku = bi.sku "
                "WHERE p.category = 'Bottled' AND bi.branch_id <> %s", (hq,))
    branch_bottles = int(cur.fetchone()[0])
    cur.execute("SELECT COALESCE(SUM(total_cogs), 0) FROM cogs_logs WHERE notes LIKE '%demo-seed'")
    cogs_total = float(cur.fetchone()[0])
    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    # --- summary ----------------------------------------------------------------------------
    c = sim.counts
    by_status = defaultdict(int)
    for r in sim.requests:
        by_status[r["status"]] += 1
    partner_revenue = sum(float(i["amount"] or 0) for i in inquiries)
    print()
    print("Demo data " + ("built, then ROLLED BACK (dry run, nothing was saved):" if dry_run else "is ready:"))
    print(f"  {len(outlets)} outlets over {DAYS} days, {len(products)} products, {len(materials)} raw materials, {len(suppliers)} suppliers")
    print(f"  Stock on hand now: {hq_bottles:,} bottles at HQ, {branch_bottles:,} across branches  ·  cost of goods logged ₱{cogs_total:,.0f}")
    print("  Integrity check: OK (inventory = movement ledger, no negative stock, shipments add up)")
    print(f"  {c['sales']:,} sales ({c['lost_to_stockout']:,} more lost to stock-outs)  ·  ₱{sim.revenue:,.0f} sales revenue  ·  {c['voids']} voided sales")
    print(f"  {c['production_runs']:,} production runs ({c['units_produced']:,} bottles) from {c['batches']} bulk batches, {c['restocks']} material restocks")
    print(f"  {c['requests']} stock requests  ·  {dict(by_status)}")
    print(f"  {len(inquiries)} package inquiries ({c['fulfilled_orders']} fulfilled) worth ₱{partner_revenue:,.0f} closed, "
          f"{len(partners)} partners, {len(package_ids)} packages")
    if os.environ.get("DEMO_DEBUG"):
        for k in sorted(c):
            if k.startswith("lost:"):
                print("   ", k, c[k])
    print(f"  {n_ledger:,} stock movement rows, {drafts} AI stock drafts, {len(logins):,} sign-in records, {len(sim.actions)} audit entries")
    print(f"  Demo branch logins (password {DEMO_PASSWORD}): " + ", ".join(o["username"] for o in retail if o["user_id"] and o["name"] in demo_meta))
    print("Remove it again with:  python scripts/demo_data.py --clear")


if __name__ == "__main__":
    if os.environ.get("APP_ENV", "development").lower() == "production":
        print("Refusing to run: APP_ENV=production.")
        sys.exit(1)
    app = create_app()
    with app.app_context():
        try:
            if "--clear" in sys.argv:
                clear()
            else:
                if "--reset" in sys.argv:
                    print("Clearing old demo data...")
                    clear()
                print("Building demo data (this takes a minute or two)...")
                add(dry_run="--dry-run" in sys.argv)
        except Exception:
            get_db().rollback()
            raise
