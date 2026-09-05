CREATE DATABASE IF NOT EXISTS heaven_and_angel_scents
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE heaven_and_angel_scents;

-- ----------------------------------------------------------------------------
-- 1. Branches
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS branches (
    branch_id   INT AUTO_INCREMENT PRIMARY KEY,
    branch_name VARCHAR(100) NOT NULL UNIQUE,
    location    VARCHAR(150),
    is_hq       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO branches (branch_id, branch_name, location, is_hq) VALUES
    (1, 'HQ Main Warehouse', 'Central Office', TRUE),
    (2, 'Manila Branch',     'SM Megamall',    FALSE),
    (3, 'Cebu Branch',       'Ayala Center',   FALSE)
ON DUPLICATE KEY UPDATE branch_name = branch_name;

-- ----------------------------------------------------------------------------
-- 2. Users  (Admin = HQ office staff, Branch = retail branch staff)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id              INT AUTO_INCREMENT PRIMARY KEY,
    username             VARCHAR(50) NOT NULL UNIQUE,
    password_hash        VARCHAR(255) NOT NULL,
    role                 ENUM('Admin', 'Branch') NOT NULL,
    branch_id            INT NULL,                -- NULL for Admin, set for Branch users
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    -- Forces a password change on next login. Set TRUE for freshly
    -- seeded/reset accounts so a known temp/starter password can't
    -- linger indefinitely.
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE SET NULL
);

-- ----------------------------------------------------------------------------
-- 3. Master Products Catalog
--
--    `price` is now the ONLY price the catalog carries — the single HQ /
--    original price for the item. There is no per-branch price override
--    anymore (see branch_inventory below): every branch sells at this
--    price by default, but the actual amount charged is captured per
--    transaction on the `sales` row itself (see section 7), because in
--    practice branches don't always charge exactly this — a bit of
--    negotiation, a loyal customer discount, etc. `price` stays as the
--    reference/"should be" price shown everywhere in the app; it is not
--    recalculated from sales.
--
--    `unit` is the packaging size for this specific SKU (e.g. two
--    different bottle sizes of the same scent are two different rows/
--    SKUs, each with its own `unit`).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    sku         VARCHAR(50) PRIMARY KEY,
    item_name   VARCHAR(100) NOT NULL,
    variant     ENUM('Male', 'Female', 'Unisex') NOT NULL,
    unit        ENUM('85ML', '50ML', '1L', '100ML', '10ML', '3ML Tester') NOT NULL DEFAULT '50ML',
    price       DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    -- Path to the uploaded product photo, relative to the Flask app's
    -- static folder (e.g. 'uploads/products/<uuid>.jpg'), so it can be
    -- rendered anywhere with url_for('static', filename=image_path).
    -- NULL means no image has been uploaded for this SKU yet.
    image_path  VARCHAR(255) NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    -- No is_active column anymore: products are edited in place from the
    -- admin Products page rather than discontinued/reactivated. See the
    -- migration block below for the DROP COLUMN that removes it from an
    -- existing database.
);

-- ----------------------------------------------------------------------------
-- 3a. Suppliers
--
--    Vendors raw materials are purchased from. Kept separate from
--    raw_materials (rather than a free-text field on it) so the same
--    supplier can be reused across many materials and edited in one
--    place. supplier_id on raw_materials is nullable — a material
--    doesn't have to have a supplier on record yet.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id     INT AUTO_INCREMENT PRIMARY KEY,
    supplier_name   VARCHAR(100) NOT NULL UNIQUE,
    contact_person  VARCHAR(100) NULL,
    phone           VARCHAR(30) NULL,
    email           VARCHAR(120) NULL,
    address         VARCHAR(255) NULL,
    notes           VARCHAR(255) NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- 3b. Raw Materials
--
--    Tracked the way it's actually purchased (package_qty of `unit` for
--    package_cost), with cost_per_unit worked out from that at insert
--    time. No is_active column: materials are edited in place from the
--    admin Materials page rather than deactivated/reactivated. See the
--    migration block below for the DROP COLUMN that removes it from an
--    existing database.
--
--    package_cost is now also the ONLY figure "money spent on materials"
--    is ever computed from — see routes/admin.py's materials()/
--    dashboard()/reports_data(), which all do SUM(package_cost) over this
--    table. Usage (material_usage_logs below) no longer carries its own
--    cost at all; logging usage only records a quantity and reduces
--    stock_qty here. This intentionally replaces the old model where
--    "total materials spent" was derived from SUM(line_cost) over every
--    usage entry — that double-counted the same peso value on every
--    withdrawal instead of once, at purchase.
--
--    stock_qty is the remaining quantity on hand, in `unit`. It starts
--    at package_qty the moment a material is added (i.e. "I just bought
--    this package") and is decremented by qty_used every time usage is
--    logged (see material_usage_logs below) — the same
--    reduce-on-withdrawal pattern branch_inventory.stock_qty already
--    uses for finished products, just applied to raw materials instead.
--    It is NOT recomputed from package_qty on every edit — editing a
--    material's purchase details (e.g. a new price for the next batch)
--    does not restock it; use Log material usage in reverse (or a
--    future restock action) for that. See the migration block below for
--    the ADD COLUMN + backfill that adds this to an existing database.
--
--    supplier_id is nullable — a material can exist before its supplier
--    is on record — and set to NULL (not cascaded) if its supplier is
--    ever removed, so a material never disappears because of that.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_materials (
    material_id    INT AUTO_INCREMENT PRIMARY KEY,
    material_name  VARCHAR(100) NOT NULL UNIQUE,
    unit           ENUM('Gram', 'Milliliter', 'Liter', 'Gallon', 'Piece') NOT NULL DEFAULT 'Piece',
    -- purchase_mode is purely how the purchase is described on the Materials
    -- page ("Package (bulk)" vs "Individual unit") — it no longer implies
    -- package_qty = 1. Buying 5 pieces one at a time (not as a bulk deal)
    -- is Individual with package_qty = 5; buying a 140g bulk bag is Package.
    -- Before this column existed, "Individual" was inferred purely from
    -- package_qty == 1, which made it impossible to record "5 individually
    -- purchased pieces" — see migration 19 below for the backfill that
    -- preserves that old inference for existing rows.
    purchase_mode  ENUM('Package', 'Individual') NOT NULL DEFAULT 'Package',
    package_qty    DECIMAL(10, 3) NOT NULL DEFAULT 1.000,
    package_cost   DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    -- Receipt / OR number for this purchase, so a material row can be
    -- traced back to the physical receipt it came from. Free text (some
    -- receipts are numeric, some are alphanumeric) and optional — older
    -- purchases logged before this existed won't have one on file.
    receipt_number VARCHAR(60) NULL,
    cost_per_unit  DECIMAL(10, 4) NOT NULL DEFAULT 0.0000,
    stock_qty      DECIMAL(10, 3) NOT NULL DEFAULT 0.000,   -- remaining on hand, in `unit`; reduced as usage is logged
    supplier_id    INT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id) ON DELETE SET NULL
);

-- ----------------------------------------------------------------------------
-- 4. Branch Inventory Levels
--
--    No per-branch price column anymore — every branch (including HQ's
--    own warehouse, branch_id=1) sells at products.price. Only stock
--    levels and reorder thresholds are branch-specific now.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS branch_inventory (
    inventory_id   INT AUTO_INCREMENT PRIMARY KEY,
    branch_id      INT NOT NULL,
    sku            VARCHAR(50) NOT NULL,
    stock_qty      INT NOT NULL DEFAULT 0,
    reorder_level  INT NOT NULL DEFAULT 10,   -- low-stock threshold, editable per branch/SKU
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    UNIQUE KEY unique_branch_sku (branch_id, sku)
);

-- ----------------------------------------------------------------------------
-- 5. HQ Production Logs
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS production_logs (
    log_id        INT AUTO_INCREMENT PRIMARY KEY,
    sku           VARCHAR(50) NOT NULL,
    batch_code    VARCHAR(50),               -- lets branches sell FIFO by batch
    qty_produced  INT NOT NULL,
    produced_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 5b. Material Usage Logs
--
--    A plain quantity log, nothing else — no cost is computed or stored
--    here anymore (see raw_materials above for why: money spent on
--    materials is now purely SUM(package_cost) at purchase time, so a
--    per-usage cost would just double-count it). Logging usage does two
--    things: inserts this row, and decrements the matching
--    raw_materials.stock_qty by qty_used — the same "log an event, move
--    the stock" pattern stock_movement_logs uses for products.
--    production_log_id is optional — usage doesn't have to be tied to a
--    specific run. See the migration block below for the DROP COLUMN
--    that removes unit_cost_snapshot/line_cost from an existing
--    database.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS material_usage_logs (
    usage_id            INT AUTO_INCREMENT PRIMARY KEY,
    material_id         INT NOT NULL,
    production_log_id   INT NULL,
    qty_used            DECIMAL(10, 3) NOT NULL,
    notes               VARCHAR(255),
    created_by_user_id  INT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (material_id) REFERENCES raw_materials(material_id),
    FOREIGN KEY (production_log_id) REFERENCES production_logs(log_id) ON DELETE SET NULL,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_material_usage_material (material_id),
    INDEX idx_material_usage_production (production_log_id)
);

-- ----------------------------------------------------------------------------
-- 6. Stock Requests (Requisitions) — delivery header
--
--    A stock request now represents one delivery from HQ to a branch,
--    which can carry any number of different products. The line-item
--    detail (which SKUs, how many, at what price) lives in
--    stock_request_items below; this table is just the header — who
--    requested it, its delivery number, and its overall status.
--
--    delivery_number is the human-facing identifier shown throughout the
--    app instead of listing every item inline (e.g. on the Stock
--    Requests and Inventory Log pages) — the full item breakdown is
--    always one click away on the goods-received receipt (see
--    receipts.py). It's generated right after insert as
--    DR-<request_id zero-padded to 6 digits>, mirroring the existing
--    GR-<request_id> receipt numbering already used by receipts.py.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_requests (
    request_id      INT AUTO_INCREMENT PRIMARY KEY,
    branch_id        INT NOT NULL,
    delivery_number  VARCHAR(20) NOT NULL UNIQUE,
    status           ENUM('Pending', 'In Transit', 'Fulfilled', 'Rejected') DEFAULT 'Pending',
    requested_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 6b. Stock Request Items — one row per product on a delivery
--
--     unit_price is snapshotted from products.price the moment the
--     branch submits the request — same philosophy as
--     material_usage_logs.cost_per_unit: it's what the line was worth
--     at request time, fixed permanently, so a later HQ price change
--     never rewrites the value of a past delivery. The line total
--     (qty * unit_price) is always computed on read, never stored.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_request_items (
    item_id         INT AUTO_INCREMENT PRIMARY KEY,
    request_id      INT NOT NULL,
    sku             VARCHAR(50) NOT NULL,
    requested_qty   INT NOT NULL,
    unit_price      DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    dispatched_qty  INT NULL,                 -- filled in when HQ dispatches
    received_qty    INT NULL,                 -- filled in when branch confirms receipt
    damaged_qty     INT NOT NULL DEFAULT 0,    -- reported at receipt, logged as loss
    FOREIGN KEY (request_id) REFERENCES stock_requests(request_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    INDEX idx_sri_request (request_id)
);

-- ----------------------------------------------------------------------------
-- 7. Sales — completes the inventory lifecycle at the point of customer sale
--
--    sale_type: a plain 'Sale' (customer buys with a bottle) vs. a
--    'Refill' (customer brings back a bottle and only pays for product) —
--    both consume stock and both carry a manually-entered unit_price,
--    since refills are usually charged a different amount than a full
--    sale of the same SKU.
--
--    payment_method + buyer_name: covers employees who take product for
--    themselves where the cost is deducted from their salary rather than
--    paid in cash at the register. buyer_name is free text (whatever
--    name the person recording the sale types in) and is only set when
--    payment_method = 'Salary Deduction' — it is NOT tied to a login
--    account, so it does not have to match any real username.
--    buyer_user_id is legacy: kept only so sales recorded before this
--    change still show who the deduction was against.
--
--    customer_name / customer_address: the walk-in customer a sale is
--    for — both optional (a lot of cash sales are anonymous), free
--    text, and unrelated to buyer_user_id/buyer_name above (which are
--    only ever about an *employee* being charged via payroll, not a
--    paying customer). customer_name is what the Record Sale page's
--    autocomplete suggests from and groups by; customer_address is
--    what gets auto-filled when a suggested name is picked, sourced
--    from that customer's earliest sale row (see routes' customer
--    lookup query — ORDER BY sold_at ASC LIMIT 1 per name), so a
--    customer's address on file always reflects the first time it was
--    entered rather than whatever the most recent typo left behind.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales (
    sale_id          INT AUTO_INCREMENT PRIMARY KEY,
    branch_id        INT NOT NULL,
    sku              VARCHAR(50) NOT NULL,
    qty_sold         INT NOT NULL,
    unit_price       DECIMAL(10, 2) NOT NULL,
    sale_type        ENUM('Sale', 'Refill') NOT NULL DEFAULT 'Sale',
    payment_method   ENUM('Cash', 'Salary Deduction') NOT NULL DEFAULT 'Cash',
    buyer_user_id    INT NULL,                 -- the employee being charged, only set for Salary Deduction
    customer_name    VARCHAR(120) NULL,
    customer_address VARCHAR(255) NULL,
    sold_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    FOREIGN KEY (buyer_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_sales_branch_sold_at (branch_id, sold_at),
    INDEX idx_sales_payment_method (payment_method),
    INDEX idx_sales_customer_name (customer_name)
);

-- ----------------------------------------------------------------------------
-- 8. Universal Stock Movement Logs (Audit Trail / Ledger)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_movement_logs (
    log_id             INT AUTO_INCREMENT PRIMARY KEY,
    branch_id          INT NOT NULL,
    sku                VARCHAR(50) NOT NULL,
    change_qty         INT NOT NULL,               -- positive for additions, negative for deductions
    movement_type      ENUM('PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'REFILL', 'ADJUSTMENT', 'DAMAGE') NOT NULL,
    notes              VARCHAR(255),
    -- Who/what caused this entry, and the stock level immediately
    -- before/after it, so disputes ("where did these units go?") can
    -- be traced without cross-referencing other tables by timestamp.
    created_by_user_id INT NULL,
    reference_type     VARCHAR(30) NULL,           -- e.g. 'STOCK_REQUEST', 'PRODUCTION_LOG', 'SALE'
    reference_id       INT NULL,
    before_qty         INT NULL,
    after_qty          INT NULL,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_sml_reference (reference_type, reference_id)
);

-- ----------------------------------------------------------------------------
-- 9. Admin Actions (Audit Trail) — non-inventory admin activity: creating
--    or deactivating a login account, resetting a password, adding or
--    discontinuing a product, adding a branch. Inventory/stock events have
--    their own, more detailed ledger in stock_movement_logs above; this
--    table covers everything else that used to leave no record beyond a
--    flash message that vanished after a few seconds.
--
--    See audit.py — its ensure_table() is only a defensive fallback for a
--    database that hasn't picked up this table yet; on a database that
--    already has it (the normal case, once this file has been applied),
--    it's a single cheap existence check and does nothing further.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_actions (
    action_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    actor_user_id  INT NULL,
    actor_username VARCHAR(80) NULL,   -- kept alongside actor_user_id so the log still reads clearly if the account is later deleted
    action         VARCHAR(50) NOT NULL,
    target         VARCHAR(120) NULL,
    details        VARCHAR(255) NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (actor_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_admin_actions_created_at (created_at)
);

-- ----------------------------------------------------------------------------
-- 10. Capital — no longer its own table.
--
--     There used to be a capital_contributions ledger here that an admin
--     logged entries into by hand. "Total Capital" is now a derived
--     number instead: SUM(package_cost) over raw_materials — i.e. capital
--     equals what has been spent buying material packages, computed on
--     the fly wherever it's shown (dashboard, reports), same as any
--     other rollup in this schema. See the migration block below for the
--     DROP TABLE that removes capital_contributions from an existing
--     database, and raw_materials above for where the figure now comes
--     from.
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- 10b. Partners (Distributors &amp; Resellers)
--
--      A distributor/reseller is a bulk buyer outside the retail branch
--      network — they don't get a branch_inventory row or a login of
--      their own here, they're just a record of who they are. Ordering
--      (what they can buy, at what package discount) is separate — see
--      package_orders in a later migration.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partners (
    partner_id      INT AUTO_INCREMENT PRIMARY KEY,
    partner_type    ENUM('Distributor', 'Reseller') NOT NULL,
    partner_name    VARCHAR(150) NOT NULL,
    contact_person  VARCHAR(100) NULL,
    phone           VARCHAR(30) NULL,
    email           VARCHAR(120) NULL,
    address         VARCHAR(255) NULL,
    notes           VARCHAR(255) NULL,
    -- Denormalized rollup of partner_inquiries, NOT a separate source of
    -- truth — both are recomputed/maintained by routes/portal.py every
    -- time a new inquiry comes in (see _find_or_create_partner()), purely
    -- so the Partners list can show "last heard from" / "X inquiries"
    -- without a join+GROUP BY on every page load. The real, permanent,
    -- never-overwritten history of what was submitted each time lives in
    -- partner_inquiries itself (see 10f below) — on a repeat inquiry from
    -- an already-known email/phone, THESE columns above (partner_name,
    -- contact_person, phone, email, address) are deliberately left
    -- alone; only last_inquiry_at/inquiry_count move.
    last_inquiry_at TIMESTAMP NULL,
    inquiry_count   INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_partners_type (partner_type)
);

-- ----------------------------------------------------------------------------
-- 10c. Partner Investments
--
--      What a distributor/reseller has put in — a running ledger: an
--      admin logs a new entry any time (an initial buy-in, a later
--      top-up), a partner's
--      "total invested" is always SUM(amount) over their own rows, and
--      nothing here is ever edited or deleted — a correction is logged
--      as its own entry so the ledger stays an honest history.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner_investments (
    investment_id      INT AUTO_INCREMENT PRIMARY KEY,
    partner_id          INT NOT NULL,
    amount               DECIMAL(12, 2) NOT NULL,
    note                 VARCHAR(255) NULL,
    logged_by_user_id   INT NULL,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (partner_id) REFERENCES partners(partner_id) ON DELETE CASCADE,
    FOREIGN KEY (logged_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_partner_investments_partner (partner_id)
);

-- ----------------------------------------------------------------------------
-- 10d. Packages
--
--      A curated bundle of products that HQ offers to distributors and/or
--      resellers at a discount off the reference price. discount_percent
--      is a plain admin-editable number (0–100), applied to the sum of
--      each item's products.price * qty at order time — see
--      package_orders in a later migration for how an order snapshots
--      this the same way stock_request_items snapshots unit_price, so a
--      later change to a package's discount or a product's price never
--      rewrites the value of a past order.
--
--      partner_scope restricts who can see/order it: 'Both' (default),
--      or locked to just 'Distributor' or just 'Reseller' pricing tiers.
--      is_active lets HQ retire a package from the order screen without
--      deleting its history.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS packages (
    package_id       INT AUTO_INCREMENT PRIMARY KEY,
    package_name     VARCHAR(150) NOT NULL,
    description      VARCHAR(255) NULL,
    partner_scope    ENUM('Both', 'Distributor', 'Reseller') NOT NULL DEFAULT 'Both',
    discount_percent DECIMAL(5, 2) NOT NULL DEFAULT 0.00,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_packages_active (is_active)
);

-- ----------------------------------------------------------------------------
-- 10e. Package Items — one row per product in a package, and how many
--      units of it make up one "set" of the package.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS package_items (
    package_item_id INT AUTO_INCREMENT PRIMARY KEY,
    package_id      INT NOT NULL,
    sku             VARCHAR(50) NOT NULL,
    qty             INT NOT NULL,
    FOREIGN KEY (package_id) REFERENCES packages(package_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    UNIQUE KEY unique_package_sku (package_id, sku)
);

-- ----------------------------------------------------------------------------
-- 10f. Partner Inquiries — leads from the public partner portal
--
--      Anyone can submit one of these without logging in (see
--      routes/portal.py) — a distributor/reseller browsing the public
--      packages page and asking about one. This is the "history" of
--      that pipeline: every inquiry ever submitted, kept permanently,
--      never edited except for `status` (an admin-side triage field —
--      New -> Contacted -> Closed — that doesn't affect anything else).
--
--      package_name_snapshot freezes what the package was called and
--      discounted at the moment of inquiry — same "snapshot, don't
--      recompute later" philosophy as stock_request_items.unit_price —
--      so a later package rename or discount change never rewrites the
--      story of a past inquiry. package_id/partner_id are both
--      nullable + ON DELETE SET NULL for the same reason: deleting a
--      package or partner later should never erase inquiry history,
--      only the live link to it.
--
--      partner_id is filled in automatically at submit time — the
--      portal matches an existing partner by email/phone, or creates a
--      new one, and links it here. See routes/portal.py's inquire().
--
--      email_sent records whether the HQ notification email actually
--      went out (mail can be unconfigured or fail) — the inquiry
--      itself is always saved either way, so a broken mailer never
--      loses a lead, just the immediate notification.
--
--      order_amount snapshots what the partner would actually pay for
--      this package — reference price minus the package's discount at
--      the moment of inquiry (same "freeze it, don't recompute later"
--      philosophy as package_name_snapshot and
--      stock_request_items.unit_price). This is what package-sales
--      figures (top package, top partner, partner "total invested",
--      and the revenue/profit totals on the dashboard) are computed
--      from — but ONLY once status = 'Closed', since that's the point
--      an inquiry represents money actually received rather than just
--      a lead. New/Contacted inquiries are not counted as sales.
--      Nullable so older rows created before this column existed don't
--      silently read as ₱0 — see the migration block below, which
--      backfills what it safely can and leaves the rest NULL (treated
--      as "unknown", not "zero", everywhere this is summed).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner_inquiries (
    inquiry_id             INT AUTO_INCREMENT PRIMARY KEY,
    package_id              INT NULL,
    partner_id               INT NULL,
    partner_type             ENUM('Distributor', 'Reseller') NOT NULL,
    company_name              VARCHAR(150) NOT NULL,
    contact_person            VARCHAR(100) NULL,
    phone                     VARCHAR(30) NULL,
    email                     VARCHAR(120) NULL,
    address                   VARCHAR(255) NULL,
    message                   VARCHAR(500) NULL,
    remarks                   TEXT NULL,
    package_name_snapshot     VARCHAR(180) NOT NULL,
    order_amount              DECIMAL(12, 2) NULL,
    status                    ENUM('New', 'Contacted', 'Follow-up', 'On Hold', 'Closed', 'Declined')
                                  NOT NULL DEFAULT 'New',
    email_sent                BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (package_id) REFERENCES packages(package_id) ON DELETE SET NULL,
    FOREIGN KEY (partner_id) REFERENCES partners(partner_id) ON DELETE SET NULL,
    INDEX idx_partner_inquiries_created (created_at),
    INDEX idx_partner_inquiries_partner (partner_id),
    INDEX idx_partner_inquiries_status (status)
);

-- ----------------------------------------------------------------------------
-- 11. Migration block — safe to run against a database created by an
--     earlier version of this file. Every step here first checks
--     information_schema before touching anything, so re-running this
--     whole script (fresh install or upgrade) is always safe: on a
--     database that's already current, every step below is a no-op.
--
--     If you're setting up a brand-new database, this block simply does
--     nothing (all the checks find "already present") — you don't need
--     to do anything differently.
-- ----------------------------------------------------------------------------
DELIMITER $$

CREATE PROCEDURE _has_migrations_applied()
BEGIN
    -- products.unit -----------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'unit'
    ) THEN
        ALTER TABLE products
            ADD COLUMN unit ENUM('85ML', '50ML', '1L', '100ML', '10ML', '3ML Tester')
                NOT NULL DEFAULT '50ML' AFTER variant;
    END IF;

    -- products.image_path -------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'image_path'
    ) THEN
        ALTER TABLE products
            ADD COLUMN image_path VARCHAR(255) NULL AFTER price;
    END IF;

    -- branch_inventory.branch_price — removed; per-branch price
    -- overrides no longer exist, every branch sells at products.price.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'branch_inventory' AND column_name = 'branch_price'
    ) THEN
        ALTER TABLE branch_inventory DROP COLUMN branch_price;
    END IF;

    -- products.is_active — removed; products are now edited in place
    -- from the admin Products page instead of being discontinued /
    -- reactivated, so the soft-delete flag no longer applies.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'is_active'
    ) THEN
        ALTER TABLE products DROP COLUMN is_active;
    END IF;

    -- raw_materials.is_active — removed for the same reason: materials
    -- are edited in place from the admin Materials page instead of being
    -- deactivated / reactivated.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'is_active'
    ) THEN
        ALTER TABLE raw_materials DROP COLUMN is_active;
    END IF;

    -- raw_materials.supplier_id -------------------------------------------
    -- Added alongside the new suppliers table (see section 3a above,
    -- which is always created earlier in this script, so the FK target
    -- exists by the time this ALTER runs on either a fresh install or
    -- an upgrade).
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'supplier_id'
    ) THEN
        ALTER TABLE raw_materials
            ADD COLUMN supplier_id INT NULL AFTER cost_per_unit;
        ALTER TABLE raw_materials ADD CONSTRAINT fk_raw_materials_supplier
            FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id) ON DELETE SET NULL;
    END IF;

    -- sales.sale_type -----------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'sale_type'
    ) THEN
        ALTER TABLE sales
            ADD COLUMN sale_type ENUM('Sale', 'Refill') NOT NULL DEFAULT 'Sale' AFTER unit_price;
    END IF;

    -- sales.payment_method --------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'payment_method'
    ) THEN
        ALTER TABLE sales
            ADD COLUMN payment_method ENUM('Cash', 'Salary Deduction') NOT NULL DEFAULT 'Cash' AFTER sale_type;
    END IF;

    -- sales.buyer_user_id -----------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'buyer_user_id'
    ) THEN
        ALTER TABLE sales ADD COLUMN buyer_user_id INT NULL AFTER payment_method;
        ALTER TABLE sales ADD CONSTRAINT fk_sales_buyer_user
            FOREIGN KEY (buyer_user_id) REFERENCES users(user_id) ON DELETE SET NULL;
        ALTER TABLE sales ADD INDEX idx_sales_payment_method (payment_method);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'buyer_name'
    ) THEN
        ALTER TABLE sales ADD COLUMN buyer_name VARCHAR(120) NULL AFTER buyer_user_id;
    END IF;

    -- sales.customer_name / customer_address -------------------------------
    -- The walk-in customer a sale is for — see the CREATE TABLE comment
    -- above. Added together since neither is useful without the other
    -- (a name with no address just means the autofill has nothing to
    -- offer next time).
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'customer_name'
    ) THEN
        ALTER TABLE sales ADD COLUMN customer_name VARCHAR(120) NULL AFTER buyer_name;
        ALTER TABLE sales ADD COLUMN customer_address VARCHAR(255) NULL AFTER customer_name;
        ALTER TABLE sales ADD INDEX idx_sales_customer_name (customer_name);
    END IF;

    -- stock_movement_logs.movement_type gains 'REFILL' -------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'stock_movement_logs'
              AND column_name = 'movement_type' AND column_type LIKE '%REFILL%'
    ) THEN
        ALTER TABLE stock_movement_logs
            MODIFY COLUMN movement_type
            ENUM('PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'REFILL', 'ADJUSTMENT', 'DAMAGE') NOT NULL;
    END IF;
END$$

DELIMITER ;

CALL _has_migrations_applied();
DROP PROCEDURE _has_migrations_applied;

-- ----------------------------------------------------------------------------
-- 12. Migration — stock_requests: single item -> delivery header + items
--
--     Only runs anything if it finds the OLD stock_requests shape (a
--     'sku' column directly on the table). On a database that's already
--     current, or a brand-new one where the CREATE TABLE above already
--     made the new shape, this is a single cheap existence check and a
--     no-op — same "safe to re-run" contract as the block above.
--
--     What it does, in order, entirely inside one transaction-safe
--     procedure:
--       1. Copies every existing request row into stock_request_items
--          (one row each, since every old request was already exactly
--          one product), pricing it at that product's current price —
--          the closest available stand-in for "what it was worth then",
--          since the old schema never recorded that separately.
--       2. Drops the old sku -> products FK (name looked up rather than
--          hardcoded, since MySQL auto-generates it and it can differ
--          between installs).
--       3. Adds delivery_number, backfills it as DR-<request_id>, then
--          makes it NOT NULL + UNIQUE.
--       4. Drops the now-migrated single-item columns from the header
--          table (sku, requested_qty, dispatched_qty, received_qty,
--          damaged_qty), leaving it as the header-only shape defined
--          above.
-- ----------------------------------------------------------------------------
DELIMITER $$

CREATE PROCEDURE _migrate_stock_requests_to_multi_item()
BEGIN
    DECLARE fk_name VARCHAR(128) DEFAULT NULL;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET fk_name = NULL;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'stock_requests' AND column_name = 'sku'
    ) THEN
        INSERT INTO stock_request_items
            (request_id, sku, requested_qty, unit_price, dispatched_qty, received_qty, damaged_qty)
        SELECT sr.request_id, sr.sku, sr.requested_qty,
               COALESCE(p.price, 0.00), sr.dispatched_qty, sr.received_qty, sr.damaged_qty
        FROM stock_requests sr
        LEFT JOIN products p ON p.sku = sr.sku;

        SELECT CONSTRAINT_NAME INTO fk_name
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'stock_requests'
              AND COLUMN_NAME = 'sku' AND REFERENCED_TABLE_NAME = 'products'
        LIMIT 1;

        IF fk_name IS NOT NULL THEN
            SET @drop_fk_sql = CONCAT('ALTER TABLE stock_requests DROP FOREIGN KEY `', fk_name, '`');
            PREPARE stmt FROM @drop_fk_sql;
            EXECUTE stmt;
            DEALLOCATE PREPARE stmt;
        END IF;

        ALTER TABLE stock_requests
            ADD COLUMN delivery_number VARCHAR(20) NULL AFTER branch_id;

        UPDATE stock_requests
        SET delivery_number = CONCAT('DR-', LPAD(request_id, 6, '0'))
        WHERE delivery_number IS NULL;

        ALTER TABLE stock_requests
            MODIFY COLUMN delivery_number VARCHAR(20) NOT NULL,
            ADD UNIQUE KEY unique_delivery_number (delivery_number),
            DROP COLUMN sku,
            DROP COLUMN requested_qty,
            DROP COLUMN dispatched_qty,
            DROP COLUMN received_qty,
            DROP COLUMN damaged_qty;
    END IF;
END$$

DELIMITER ;

CALL _migrate_stock_requests_to_multi_item();
DROP PROCEDURE _migrate_stock_requests_to_multi_item;

-- ----------------------------------------------------------------------------
-- 13. Migration — partners: inquiry history rollup columns
--
--     Adds last_inquiry_at / inquiry_count to an existing `partners`
--     table that predates them (a fresh install already has both, from
--     the CREATE TABLE above). Guarded the same way as every other step
--     in this file — safe to re-run, no-op once applied.
--
--     Also backfills both columns from partner_inquiries for partners
--     that already have inquiries on file, so existing data shows
--     correct values immediately instead of sitting at 0/NULL until
--     their next new inquiry. This is a plain recompute (COUNT/MAX
--     grouped by partner_id), safe to re-run any time.
-- ----------------------------------------------------------------------------
DELIMITER $$

CREATE PROCEDURE _migrate_partner_inquiry_rollup()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partners' AND column_name = 'last_inquiry_at'
    ) THEN
        ALTER TABLE partners ADD COLUMN last_inquiry_at TIMESTAMP NULL AFTER notes;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partners' AND column_name = 'inquiry_count'
    ) THEN
        ALTER TABLE partners ADD COLUMN inquiry_count INT NOT NULL DEFAULT 0 AFTER last_inquiry_at;
    END IF;
END$$

DELIMITER ;

CALL _migrate_partner_inquiry_rollup();
DROP PROCEDURE _migrate_partner_inquiry_rollup;

UPDATE partners p
LEFT JOIN (
    SELECT partner_id, COUNT(*) AS cnt, MAX(created_at) AS last_at
    FROM partner_inquiries
    WHERE partner_id IS NOT NULL
    GROUP BY partner_id
) agg ON agg.partner_id = p.partner_id
SET p.inquiry_count = COALESCE(agg.cnt, 0),
    p.last_inquiry_at = agg.last_at;

-- ----------------------------------------------------------------------------
-- 14. Migration — partner_inquiries.order_amount
--
--     Adds the column to an existing database that predates it (a fresh
--     install already has it from the CREATE TABLE above). Guarded and
--     re-run-safe like every other step in this file.
--
--     Backfill for rows that already exist: their order_amount is
--     computed from package_items joined through package_id at CURRENT
--     product prices/discount — the closest available stand-in for
--     "what it was worth then", same limitation the stock_requests
--     migration above already documents (the old schema never recorded
--     this separately, so this is a best-effort reconstruction, not a
--     true historical value). Only rows whose package still exists (and
--     still has products in it) can be backfilled this way; anything
--     else — package_id IS NULL, or the package/its items were deleted
--     since — is left NULL rather than guessed at, so it's excluded
--     from sums instead of silently contributing ₱0 or a wrong figure.
-- ----------------------------------------------------------------------------
DELIMITER $$

CREATE PROCEDURE _migrate_partner_inquiry_order_amount()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partner_inquiries' AND column_name = 'order_amount'
    ) THEN
        ALTER TABLE partner_inquiries
            ADD COLUMN order_amount DECIMAL(12, 2) NULL AFTER package_name_snapshot;
    END IF;
END$$

DELIMITER ;

CALL _migrate_partner_inquiry_order_amount();
DROP PROCEDURE _migrate_partner_inquiry_order_amount;

UPDATE partner_inquiries pinq
JOIN packages pkg ON pkg.package_id = pinq.package_id
JOIN (
    SELECT pi.package_id, COALESCE(SUM(pi.qty * p.price), 0) AS reference_total
    FROM package_items pi JOIN products p ON p.sku = pi.sku
    GROUP BY pi.package_id
) ref ON ref.package_id = pkg.package_id
SET pinq.order_amount = ref.reference_total * (1 - (pkg.discount_percent / 100))
WHERE pinq.order_amount IS NULL;

-- ----------------------------------------------------------------------------
-- 15. Migration — raw_materials.stock_qty
--
--     Adds the column to an existing database that predates it (a fresh
--     install already has it from the CREATE TABLE above). Guarded and
--     re-run-safe like every other step in this file.
--
--     Backfill: stock_qty starts at package_qty (what's on hand at the
--     package's current definition) minus whatever has already been
--     logged as used against that material in material_usage_logs, i.e.
--     "however much of the package is left after existing usage
--     history", floored at 0 so a material that was over-logged in the
--     old cost-only model doesn't backfill to a negative number.
-- ----------------------------------------------------------------------------
DELIMITER $$

CREATE PROCEDURE _migrate_raw_materials_stock_qty()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'stock_qty'
    ) THEN
        ALTER TABLE raw_materials
            ADD COLUMN stock_qty DECIMAL(10, 3) NOT NULL DEFAULT 0.000 AFTER cost_per_unit;

        UPDATE raw_materials rm
        LEFT JOIN (
            SELECT material_id, COALESCE(SUM(qty_used), 0) AS used
            FROM material_usage_logs GROUP BY material_id
        ) u ON u.material_id = rm.material_id
        SET rm.stock_qty = GREATEST(rm.package_qty - COALESCE(u.used, 0), 0);
    END IF;
END$$

DELIMITER ;

CALL _migrate_raw_materials_stock_qty();
DROP PROCEDURE _migrate_raw_materials_stock_qty;

-- ----------------------------------------------------------------------------
-- 16. Migration — material_usage_logs: drop cost columns
--
--     Removes unit_cost_snapshot/line_cost from an existing database —
--     usage is now a plain quantity log (see the table's comment above).
--     Guarded and re-run-safe like every other step in this file.
-- ----------------------------------------------------------------------------
DELIMITER $$

CREATE PROCEDURE _migrate_drop_material_usage_cost_columns()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'material_usage_logs' AND column_name = 'line_cost'
    ) THEN
        ALTER TABLE material_usage_logs
            DROP COLUMN unit_cost_snapshot,
            DROP COLUMN line_cost;
    END IF;
END$$

DELIMITER ;

CALL _migrate_drop_material_usage_cost_columns();
DROP PROCEDURE _migrate_drop_material_usage_cost_columns;

-- ----------------------------------------------------------------------------
-- 17. Migration — drop capital_contributions
--
--     Removes the old manual capital ledger from an existing database.
--     "Total Capital" is now derived from raw_materials.package_cost
--     instead — see section 10's comment above. This intentionally
--     drops the table (and its logged history) rather than leaving it
--     around unused, since nothing in the app reads from it anymore.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS capital_contributions;

-- ----------------------------------------------------------------------------
-- 18. Migration — partner_inquiries: remarks + expanded status pipeline
--
--     Adds a free-text `remarks` column an admin can use to leave an
--     internal note on an inquiry independent of its status (e.g. "on
--     hold, distributor still deciding" or "call back next week"), and
--     widens the `status` ENUM past the original New/Contacted/Closed
--     triage into a fuller pipeline: New -> Contacted -> Follow-up /
--     On Hold -> Closed / Declined. Existing rows keep whatever status
--     they already have — this only adds new allowed values, it never
--     changes a stored one. Nothing here touches the "Closed only"
--     revenue rule used elsewhere (see order_amount's note above and
--     routes/admin.py's dashboard/partners queries) since 'Closed'
--     itself is untouched. Guarded and re-run-safe like every other
--     step in this file.
-- ----------------------------------------------------------------------------
DELIMITER $$

CREATE PROCEDURE _migrate_partner_inquiries_remarks_and_status()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partner_inquiries' AND column_name = 'remarks'
    ) THEN
        ALTER TABLE partner_inquiries
            ADD COLUMN remarks TEXT NULL AFTER message;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partner_inquiries'
              AND column_name = 'status' AND column_type LIKE '%Declined%'
    ) THEN
        ALTER TABLE partner_inquiries
            MODIFY COLUMN status
            ENUM('New', 'Contacted', 'Follow-up', 'On Hold', 'Closed', 'Declined')
            NOT NULL DEFAULT 'New';
    END IF;
END$$

DELIMITER ;

CALL _migrate_partner_inquiries_remarks_and_status();
DROP PROCEDURE _migrate_partner_inquiries_remarks_and_status;

-- ----------------------------------------------------------------------------
-- 19. Migration — raw_materials: purchase_mode + receipt_number
--
--     Adds an explicit purchase_mode column instead of the old "package_qty
--     == 1 means Individual unit" inference (see raw_materials's comment
--     above), and a free-text receipt_number for tracing a material row
--     back to its physical receipt. Backfill preserves the exact same
--     Package/Individual split existing rows already displayed as, so
--     nothing on the Materials page changes for data logged before this
--     migration — only new/edited rows can now have package_qty > 1 while
--     still being marked Individual.
-- ----------------------------------------------------------------------------
DELIMITER $$

CREATE PROCEDURE _migrate_raw_materials_purchase_mode_and_receipt()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'purchase_mode'
    ) THEN
        ALTER TABLE raw_materials
            ADD COLUMN purchase_mode ENUM('Package', 'Individual') NOT NULL DEFAULT 'Package' AFTER unit;
        UPDATE raw_materials SET purchase_mode = 'Individual' WHERE package_qty = 1;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'receipt_number'
    ) THEN
        ALTER TABLE raw_materials
            ADD COLUMN receipt_number VARCHAR(60) NULL AFTER package_cost;
    END IF;
END$$

DELIMITER ;

CALL _migrate_raw_materials_purchase_mode_and_receipt();
DROP PROCEDURE _migrate_raw_materials_purchase_mode_and_receipt;

-- ----------------------------------------------------------------------------
-- 20. AI Stock Drafts — human-in-the-loop proposals from the AI assistant
--
--     A draft is inert until an admin (or branch staff, for their own
--     branch) approves it on the /ai/drafts page — approval is what
--     actually inserts into stock_requests / stock_request_items,
--     using the same DR-<request_id zero-padded to 6> numbering as any
--     other delivery (see section 6's comment above and the migration
--     in section 12), so an AI-originated delivery is indistinguishable
--     from one requested by hand once approved.
--
--     Both created_by_user_id and reviewed_by_user_id follow the same
--     "kept for traceability, set NULL if the account is later removed"
--     pattern as every other actor column in this schema (see
--     material_usage_logs.created_by_user_id, admin_actions.actor_user_id).
--     created_by_username is denormalized alongside created_by_user_id
--     for the same reason admin_actions.actor_username is: the log still
--     reads clearly if the account is later deleted.
--
--     resulting_request_id is nullable and ON DELETE SET NULL rather
--     than CASCADE — a draft's own history (what was proposed, by
--     whom, why) should survive even if the delivery it produced is
--     ever removed from stock_requests.
--
--     Being a brand-new table (not an ALTER on an existing one), a
--     plain guarded CREATE TABLE IF NOT EXISTS is sufficient and
--     idempotent on its own — unlike the ALTER-based migrations above,
--     it doesn't need the DELIMITER/PROCEDURE guard pattern.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_stock_drafts (
    draft_id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    branch_id             INT NOT NULL,
    created_by_user_id    INT NULL,
    created_by_username   VARCHAR(80) NULL,
    status                ENUM('Pending Review', 'Approved', 'Rejected') NOT NULL DEFAULT 'Pending Review',
    note                  VARCHAR(255) NULL,
    reasoning              VARCHAR(500) NULL,
    resulting_request_id  INT NULL,
    reviewed_by_user_id   INT NULL,
    reviewed_at           DATETIME NULL,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    FOREIGN KEY (reviewed_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    FOREIGN KEY (resulting_request_id) REFERENCES stock_requests(request_id) ON DELETE SET NULL,
    INDEX idx_ai_stock_drafts_branch (branch_id),
    INDEX idx_ai_stock_drafts_status (status)
);

-- ----------------------------------------------------------------------------
-- 20b. AI Stock Draft Items — one row per proposed SKU on a draft
--
--      Deliberately thin (sku, suggested_qty only) — unlike
--      stock_request_items, a draft has no unit_price of its own; the
--      price is snapshotted from products.price at the moment a draft
--      is *approved* (see routes/ai.py's approve_draft()), not at the
--      moment the AI proposes it, so a draft that sits unreviewed for a
--      while doesn't lock in a stale price.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_stock_draft_items (
    draft_item_id  BIGINT AUTO_INCREMENT PRIMARY KEY,
    draft_id       BIGINT NOT NULL,
    sku            VARCHAR(50) NOT NULL,
    suggested_qty  INT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES ai_stock_drafts(draft_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    INDEX idx_ai_stock_draft_items_draft (draft_id)
);

-- ----------------------------------------------------------------------------
-- 21. Login Activity — every sign-in attempt, successful or not
--
--     Nothing previously tracked sign-ins or failed attempts at all —
--     admin_actions only ever logs account/product/branch *changes* an
--     already-authenticated admin makes, not the act of signing in
--     itself. This is a separate, append-only table for that: one row
--     per submission of the login form (see routes/auth.py's login()),
--     whether it succeeded or not.
--
--     user_id is nullable and ON DELETE SET NULL (same "kept for
--     traceability" pattern as admin_actions.actor_user_id) because a
--     failed attempt very often won't match a real account at all —
--     a typo'd username, or someone probing for valid logins — so this
--     also stores whatever username/role was actually typed
--     (username_attempted / role_attempted) regardless of whether it
--     matched anything, which user_id alone could never capture for a
--     failed attempt.
--
--     failure_reason is a short fixed label (see
--     routes/auth.py's _LOGIN_FAILURE_REASONS), not a free-text
--     message — keeps this filterable/groupable rather than every row
--     carrying a slightly different hand-written string.
--
--     ip_address is whatever the WSGI layer reports as the remote
--     address (request.remote_addr) — this app doesn't sit behind a
--     trusted reverse proxy that sets X-Forwarded-For today, so this
--     column is deliberately not trusting that header. If a proxy is
--     added later, populate it from the proxy's real client-IP header
--     instead once that's actually trustworthy.
--
--     Being a brand-new table, a plain guarded CREATE TABLE IF NOT
--     EXISTS is sufficient and idempotent on its own, same as
--     ai_stock_drafts above.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS login_activity (
    activity_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id           INT NULL,
    username_attempted VARCHAR(80) NOT NULL,
    role_attempted    VARCHAR(20) NOT NULL,
    success           BOOLEAN NOT NULL,
    failure_reason    VARCHAR(30) NULL,
    ip_address        VARCHAR(45) NULL,
    user_agent        VARCHAR(255) NULL,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_login_activity_created_at (created_at),
    INDEX idx_login_activity_username (username_attempted),
    INDEX idx_login_activity_success (success)
);
