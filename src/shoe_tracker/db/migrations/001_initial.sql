-- 001_initial.sql
-- Schema per plan.md. user_id is present on watchlist from day one for v2.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_shoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    version TEXT,
    gender TEXT NOT NULL,
    variant_type TEXT,
    mfr_style_prefix TEXT,
    UNIQUE (brand, model, version, gender, variant_type)
);

CREATE TABLE IF NOT EXISTS shoe_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_shoe_id INTEGER NOT NULL REFERENCES canonical_shoes(id) ON DELETE CASCADE,
    size REAL NOT NULL,
    width TEXT NOT NULL DEFAULT 'D',
    colorway_name TEXT NOT NULL,
    colorway_code TEXT,
    mfr_style_code TEXT,
    image_url TEXT,
    UNIQUE (canonical_shoe_id, size, width, colorway_name)
);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'me' REFERENCES users(id) ON DELETE CASCADE,
    canonical_shoe_id INTEGER NOT NULL REFERENCES canonical_shoes(id) ON DELETE CASCADE,
    size REAL NOT NULL,
    width TEXT NOT NULL DEFAULT 'D',
    colorway_policy TEXT NOT NULL DEFAULT 'any',
    colorway_list TEXT NOT NULL DEFAULT '[]',
    threshold_usd REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, canonical_shoe_id, size, width)
);

CREATE TABLE IF NOT EXISTS retailer_mappings (
    canonical_shoe_id INTEGER NOT NULL REFERENCES canonical_shoes(id) ON DELETE CASCADE,
    retailer TEXT NOT NULL,
    product_url TEXT NOT NULL,
    product_id TEXT,
    confidence REAL NOT NULL,
    last_verified_at TEXT,
    PRIMARY KEY (canonical_shoe_id, retailer)
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shoe_variant_id INTEGER NOT NULL REFERENCES shoe_variants(id) ON DELETE CASCADE,
    retailer TEXT NOT NULL,
    price_usd REAL NOT NULL,
    in_stock INTEGER NOT NULL,
    scraped_at TEXT NOT NULL,
    source_url TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_snapshots_variant_time
    ON price_snapshots (shoe_variant_id, scraped_at DESC);

CREATE TABLE IF NOT EXISTS notifications_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shoe_variant_id INTEGER NOT NULL REFERENCES shoe_variants(id) ON DELETE CASCADE,
    retailer TEXT NOT NULL,
    triggering_price REAL NOT NULL,
    sent_at TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'email'
);

CREATE INDEX IF NOT EXISTS idx_notifications_dedup
    ON notifications_sent (user_id, shoe_variant_id, retailer, sent_at DESC);
