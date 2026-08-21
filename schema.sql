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
    user_id       INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          ENUM('Admin', 'Branch') NOT NULL,
    branch_id     INT NULL,                -- NULL for Admin, set for Branch users
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE SET NULL
);

-- ----------------------------------------------------------------------------
-- 3. Master Products Catalog
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    sku         VARCHAR(50) PRIMARY KEY,
    item_name   VARCHAR(100) NOT NULL,
    variant     ENUM('Male', 'Female', 'Unisex') NOT NULL,
    price       DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,   -- soft delete: discontinued items are hidden, not removed
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- 4. Branch Inventory Levels
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
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales (
    sale_id     INT AUTO_INCREMENT PRIMARY KEY,
    branch_id   INT NOT NULL,
    sku         VARCHAR(50) NOT NULL,
    qty_sold    INT NOT NULL,
    unit_price  DECIMAL(10, 2) NOT NULL,
    sold_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 8. Universal Stock Movement Logs (Audit Trail / Ledger)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_movement_logs (
    log_id        INT AUTO_INCREMENT PRIMARY KEY,
    branch_id     INT NOT NULL,
    sku           VARCHAR(50) NOT NULL,
    change_qty    INT NOT NULL,               -- positive for additions, negative for deductions
    movement_type ENUM('PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'ADJUSTMENT', 'DAMAGE') NOT NULL,
    notes         VARCHAR(255),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE
);

