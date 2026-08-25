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
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,   -- soft delete: discontinued items are hidden, not removed
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
-- 6. Stock Requests (Requisitions)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_requests (
    request_id     INT AUTO_INCREMENT PRIMARY KEY,
    branch_id      INT NOT NULL,
    sku            VARCHAR(50) NOT NULL,
    requested_qty  INT NOT NULL,
    dispatched_qty INT NULL,                 -- filled in when HQ dispatches
    received_qty   INT NULL,                 -- filled in when branch confirms receipt
    damaged_qty    INT NOT NULL DEFAULT 0,    -- reported at receipt, logged as loss
    status         ENUM('Pending', 'In Transit', 'Fulfilled', 'Rejected') DEFAULT 'Pending',
    requested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE
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
--    payment_method + buyer_user_id: covers employees who take product
--    for themselves where the cost is deducted from their salary rather
--    than paid in cash at the register. buyer_user_id is only set when
--    payment_method = 'Salary Deduction', and identifies which login
--    account (which employee) the deduction applies to — it does not
--    have to be the same account that rang up the sale.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales (
    sale_id        INT AUTO_INCREMENT PRIMARY KEY,
    branch_id      INT NOT NULL,
    sku            VARCHAR(50) NOT NULL,
    qty_sold       INT NOT NULL,
    unit_price     DECIMAL(10, 2) NOT NULL,
    sale_type      ENUM('Sale', 'Refill') NOT NULL DEFAULT 'Sale',
    payment_method ENUM('Cash', 'Salary Deduction') NOT NULL DEFAULT 'Cash',
    buyer_user_id  INT NULL,                 -- the employee being charged, only set for Salary Deduction
    sold_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    FOREIGN KEY (buyer_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_sales_branch_sold_at (branch_id, sold_at),
    INDEX idx_sales_payment_method (payment_method)
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
-- 10. Migration block — safe to run against a database created by an
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

    -- branch_inventory.branch_price — removed; per-branch price
    -- overrides no longer exist, every branch sells at products.price.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'branch_inventory' AND column_name = 'branch_price'
    ) THEN
        ALTER TABLE branch_inventory DROP COLUMN branch_price;
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
