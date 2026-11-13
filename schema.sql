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

-- Per-location inventory for Shopify MX retail locations (stores + Julius)
CREATE TABLE inventory_location_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    snapshot_date DATE         NOT NULL,
    location_id   BIGINT       NOT NULL,
    location_name VARCHAR(100) NOT NULL,
    internal_sku  VARCHAR(100) REFERENCES sku_master(internal_sku),
    external_sku  VARCHAR(200) NOT NULL,
    qty_available INTEGER      NOT NULL DEFAULT 0,
    fetched_at    TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE(snapshot_date, location_id, external_sku)
);

-- Distinct snapshot dates, for the dashboard's date picker.
-- Querying inventory_snapshots directly and de-duplicating client-side only
-- reaches back a few days once row counts grow, which hides older reports.
-- internal_sku IS NOT NULL keeps out the earliest snapshots, which predate the
-- SKU mappings: they hold raw rows but nothing the dashboard can render, so
-- offering them in the picker is a dead end.
CREATE OR REPLACE VIEW snapshot_dates AS
SELECT DISTINCT snapshot_date
FROM inventory_snapshots
WHERE internal_sku IS NOT NULL
ORDER BY snapshot_date DESC;

-- Per-date, per-source coverage, for gap detection.
-- Two failure modes have cost real days of data:
--   * a source writes no rows at all (connector failed), which the dashboard
--     would otherwise render as a plain 0, indistinguishable from zero stock;
--   * a source writes rows but none resolve to an internal_sku, which empties
--     the dashboard's SKU-keyed tables just as thoroughly while the row count
--     still looks healthy. This happened to the retail locations on
--     2026-11-07, when 271 mapped rows were replaced by 514 unmapped ones.
-- count(internal_sku) counts non-null values, so rows_mapped is exactly the
-- number of rows the dashboard can actually use.
--
-- inventory_location_snapshots is folded in under a synthetic source name so
-- callers get one uniform shape from a single query. Checking it per-source
-- with count queries took 14+ round trips per page load.
CREATE OR REPLACE VIEW snapshot_coverage AS
SELECT snapshot_date,
       source,
       count(*)                AS rows_written,
       count(internal_sku)     AS rows_mapped
FROM inventory_snapshots
GROUP BY snapshot_date, source
UNION ALL
SELECT snapshot_date,
       'retail_locations'      AS source,
       count(*)                AS rows_written,
       count(internal_sku)     AS rows_mapped
FROM inventory_location_snapshots
GROUP BY snapshot_date;

-- ─────────────────────────────────────────────────────────────────────────────
-- Access control
-- ─────────────────────────────────────────────────────────────────────────────
-- Supabase grants the anon and authenticated roles full privileges on new
-- objects in the public schema by default. On 2026-11-07 that was found to
-- include INSERT, UPDATE, DELETE and TRUNCATE on every table here, plus SELECT
-- on inventory_unified — the whole cross-channel history in one object, from
-- which per-SKU sell-through can be derived by differencing dates.
--
-- Nothing in this project uses the anon key: both the connectors (db/client.py)
-- and the dashboard (dashboard/app.py) authenticate as service_role, which
-- bypasses RLS. So revoking these grants costs nothing and no RLS policies are
-- needed — with RLS on and no policy, anon and authenticated are denied.
--
-- Revoking is the load-bearing step, for two reasons RLS cannot cover:
--   * RLS does not apply to TRUNCATE. CREATE POLICY accepts only SELECT,
--     INSERT, UPDATE and DELETE, so RLS alone would still let anon empty these
--     tables — and the connectors read CURRENT inventory with no historical
--     query, so a wipe is not recoverable from the upstream APIs.
--   * Materialized views do not support RLS at all, so inventory_unified can
--     only be protected by revoking access to it.

REVOKE ALL ON public.inventory_unified            FROM anon, authenticated;
REVOKE ALL ON public.snapshot_dates               FROM anon, authenticated;
REVOKE ALL ON public.snapshot_coverage            FROM anon, authenticated;
REVOKE ALL ON public.sku_master                   FROM anon, authenticated;
REVOKE ALL ON public.sku_mappings                 FROM anon, authenticated;
REVOKE ALL ON public.inventory_snapshots          FROM anon, authenticated;
REVOKE ALL ON public.inventory_location_snapshots FROM anon, authenticated;

-- Second layer, for the commands RLS does cover.
ALTER TABLE public.sku_master                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sku_mappings                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_snapshots          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventory_location_snapshots ENABLE ROW LEVEL SECURITY;

-- Keep objects added later from inheriting the same grants. This governs only
-- objects created by the role that runs it, so it is a guard, not a guarantee.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;

-- Verify: both queries should come back empty / false.
--   SELECT table_name, grantee, privilege_type
--     FROM information_schema.role_table_grants
--    WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated');
--   -- note: information_schema omits materialized views, so check those too
--   SELECT c.relname, has_table_privilege('anon', 'public.'||c.relname, 'SELECT')
--     FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname = 'public' AND c.relkind = 'm';


-- ============================================================================
-- Purchase orders: daily copy of the supply team's PO tracking sheet
-- (DASHBOARD_SARELLY__OFICIAL, tabs "TRANSITO" and "TRANSITO RECIBIDO ").
-- Written by sync_pos.py. One-time setup: run this section once.
--
-- Every cell is stored as the text people see in the sheet, exactly as typed.
-- Parsing happens downstream (agent.po_lines in the sarelly-inventory-agent
-- repo), so a badly typed date never blocks the load and never gets "fixed"
-- silently. Reading the displayed text also avoids the time zone shift seen
-- in the SYNC_TRANSITO tab, where 29-May-2026 became 2026-05-28 18:00.
--
-- One full copy per day is kept, not overwritten: ~150 rows/day is tiny, and
-- the history is what shows how ETAs moved over time.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.po_lines_raw (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_date      DATE        NOT NULL,   -- Mexico City date of the sync
    tab                TEXT        NOT NULL,   -- sheet tab the row came from
    row_number         INT         NOT NULL,   -- row in the sheet (header = 1)
    po_number          TEXT,
    po_date            TEXT,
    sku                TEXT,
    description        TEXT,
    supplier           TEXT,
    po_qty             TEXT,
    qty_confirmed      TEXT,
    status             TEXT,
    payment_status     TEXT,                   -- the unnamed column after STATUS
    prepayment_date    TEXT,
    pickup_ready_date  TEXT,
    eta                TEXT,
    final_payment_date TEXT,
    final_wh           TEXT,
    comment            TEXT,
    synced_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_date, tab, row_number)
);

CREATE INDEX IF NOT EXISTS po_lines_raw_date_idx ON public.po_lines_raw (snapshot_date);

REVOKE ALL ON public.po_lines_raw FROM anon, authenticated;
ALTER TABLE public.po_lines_raw ENABLE ROW LEVEL SECURITY;
