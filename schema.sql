-- Run this once against your Supabase PostgreSQL instance

CREATE TABLE sku_master (
    internal_sku  VARCHAR(100) PRIMARY KEY,
    display_name  VARCHAR(255),
    category      VARCHAR(100),
    is_bundle     BOOLEAN DEFAULT FALSE,
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE sku_mappings (
    id            SERIAL PRIMARY KEY,
    internal_sku  VARCHAR(100) REFERENCES sku_master(internal_sku),
    source        VARCHAR(50)  NOT NULL,
    external_id   VARCHAR(200) NOT NULL,
    external_sku  VARCHAR(200),
    UNIQUE(source, external_id)
);
-- source values: 'shopify', 'amazon_us', 'amazon_mx', 'tiktok_us', 'us_3pl', 'mx_3pl'

CREATE TABLE inventory_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    snapshot_date DATE         NOT NULL,
    source        VARCHAR(50)  NOT NULL,
    internal_sku  VARCHAR(100),
    external_id   VARCHAR(200) NOT NULL,
    external_sku  VARCHAR(200),
    qty_on_hand   INTEGER,
    qty_reserved  INTEGER,
    qty_available INTEGER,
    qty_inbound   INTEGER,
    raw_data      JSONB,
    fetched_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(snapshot_date, source, external_id)
);

CREATE MATERIALIZED VIEW inventory_unified AS
SELECT
    s.snapshot_date,
    m.internal_sku,
    sk.display_name,
    sk.category,
    SUM(CASE WHEN s.source = 'shopify_us' THEN s.qty_available ELSE 0 END) AS shopify_us,
    SUM(CASE WHEN s.source = 'shopify_mx' THEN s.qty_available ELSE 0 END) AS shopify_mx,
    SUM(CASE WHEN s.source = 'amazon_us'  THEN s.qty_available ELSE 0 END) AS amazon_us,
    SUM(CASE WHEN s.source = 'amazon_mx'  THEN s.qty_available ELSE 0 END) AS amazon_mx,
    SUM(CASE WHEN s.source = 'tiktok_us'  THEN s.qty_available ELSE 0 END) AS tiktok_us,
    SUM(CASE WHEN s.source = 'us_3pl'     THEN s.qty_available ELSE 0 END) AS us_3pl,
    SUM(CASE WHEN s.source = 'mx_3pl'     THEN s.qty_available ELSE 0 END) AS mx_3pl,
    SUM(s.qty_available) AS total_available
FROM inventory_snapshots s
JOIN sku_mappings m ON m.source = s.source AND m.external_id = s.external_id
JOIN sku_master sk   ON sk.internal_sku = m.internal_sku
GROUP BY s.snapshot_date, m.internal_sku, sk.display_name, sk.category;

CREATE UNIQUE INDEX ON inventory_unified (snapshot_date, internal_sku);
