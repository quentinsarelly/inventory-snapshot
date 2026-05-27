# TODO — path to live daily inventory writes

## Blockers (must fix before first live write)

- [x] **Fix Supabase IPv6 connectivity**
  Replaced psycopg2 with `supabase-py` (REST over HTTPS/port 443 → IPv4 via Cloudflare).
  Added `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` to `.env`. Created `refresh_inventory_unified()`
  RPC function in Supabase for the materialized view refresh.

- [x] **First live write (partial)**
  Shopify US (183 rows), Shopify MX (958 rows), ShipHero/mx_3pl (682 rows) wrote successfully.
  Amazon US/MX blocked by duplicate seller SKUs per ASIN — see `FINDINGS.md`.
  TikTok and US 3PL still pending (separate blockers below).

- [x] **Resolve Amazon duplicate SKU logic**
  US: keep the SKU ending with `-USA`. MX: keep the clean base SKU (no `-USA`, no spaces,
  no auto-generated or `Stickered/Uncommingled.MSKU.*` strings; alphabetically first among
  valid candidates). All 6 connectors now write successfully — 183 shopify_us, 961 shopify_mx,
  113 amazon_us, 113 amazon_mx, 13 tiktok_us, 682 mx_3pl.

- [ ] **Review extracted data and clean up database**
  All sources are now in `inventory_snapshots`. Review in Supabase table editor:
  check `external_sku` values look correct, delete any junk rows, confirm counts make sense.

## SKU mappings (needed for the dashboard to show data)

- [x] **350 mappings inserted across all sources**
  Shopify US (38), Shopify MX (65), Amazon US (57), Amazon MX (56),
  TikTok US (7), ShipHero/mx_3pl (117). Dashboard now shows 360 products.
  See `sku_matching_review.csv` for the full review.

- [x] **Archived Shopify products filtered out**
  Only active products written to snapshots. 6 archived MX products with
  remaining stock flagged as [LOGISTICS] warning on each run.

- [ ] **Complete remaining unmatched SKUs**
  ~100 unmatched per source remain (new products not yet in sku_master,
  non-product ShipHero SKUs like INS-*, COL-*, WHS-*). Add new products
  to sku_master as they are catalogued, then re-run matching script.

- [ ] **Fill display_name/category for SCL-0108, SCL-0111, SCL-0114**
  Added to sku_master as placeholders — details to be filled in Supabase.

## Pending connectors

- [x] **TikTok US (FBT)**
  Endpoint: `POST /fbt/202408/inventory/search` (version `202408`).
  Returns one row per product per warehouse — connector aggregates across all warehouses.
  13 products returned. Uses `goods.reference_code` (e.g. `SCL-0117`) as `external_sku`.
  Token auto-refreshed on each run via `auth.tiktok-shops.com/api/v2/token/refresh`.
  TIKTOK_REFRESH_TOKEN expires ~30 days — see README for renewal steps.

- [ ] **US 3PL**
  Provider not yet confirmed. Once identified, look up their REST API docs
  and implement `connectors/us_3pl.py`.

## Automation (after live writes are confirmed working)

- [x] **Push to GitHub**
  Create a remote repo and push. The GitHub Actions workflow
  (`.github/workflows/daily_snapshot.yml`) runs automatically on push.

- [x] **Add secrets to GitHub**
  All secrets added. Shopify uses pre-fetched tokens (client_credentials blocked in CI).
  See README for full secrets reference and maintenance notes.

- [x] **Deploy dashboard**
  Deployed on Streamlit Cloud at `dashboard/app.py`. Supabase credentials
  added as secrets. Fixed pandas wheel issue by adding `dashboard/requirements.txt`
  with lean dependencies (no python-amazon-sp-api).

- [x] **Fix Shopify and TikTok invisible in dashboard**
  Both connectors were storing a numeric platform ID as `external_id` in
  `inventory_snapshots`, but `sku_mappings` uses the SKU string (e.g. SAR-1036,
  SCL-0117). The view join on `external_id` never matched. Fixed both connectors
  to use the SKU string as `external_id`. Stale rows with numeric IDs remain in
  the DB but are harmless (ignored by the view).

## Database cleanup (once data is confirmed clean)

- [ ] **Delete stale snapshot rows and reset to clean baseline**
  Once a few daily runs have confirmed all sources look correct, run:
  ```sql
  DELETE FROM inventory_snapshots WHERE snapshot_date < 'YYYY-MM-DD';
  REFRESH MATERIALIZED VIEW inventory_unified;
  ```
  This removes old rows written with the wrong external_id (numeric Shopify
  inventory_item_id and TikTok goods_id) and any other junk from early dev runs.


## Features

- [x] **Retail location breakdown (Shopify MX stores + Julius)**
  New `inventory_location_snapshots` table. Connector fetches per-location
  inventory for 6 retail locations (Andares, Monterrey, Mérida, Perisur,
  Queretaro, Julius). Dashboard shows a separate section with one column
  per location. Run SQL in Supabase to create the table before next run.

- [x] **Inbound quantities (Amazon + TikTok)**
  Surfaces `qty_inbound` already captured by connectors. Added three columns
  to the main table: Amazon US (Inbound), Amazon MX (Inbound), TikTok US (Inbound),
  each placed next to their available column.

- [x] **In-stock rate ratio**
  Per-channel and overall rate (SKUs with ≥ 1 unit / catalog size).
  Denominator = sku_mappings count per source. Displayed as a compact table
  between source totals and the main SKU table.

- [ ] **Channel catalog (Google Sheet)**
  Goal: define which SKUs belong to each channel's catalog, to use as the
  correct denominator for the in-stock rate (currently using sku_mappings count
  which can be inflated by stale mappings).

  **Decided approach:** Google Sheet (one row per SKU, one column per channel,
  TRUE/FALSE) synced into Supabase by the daily run. Dashboard reads from Supabase.

  **Need from team before building:**
  - Confirm sharing approach: **API key + sheet shared as "anyone with link can view"**
    (simpler, no service account) vs. service account JSON (more secure, sheet stays private)
  - A Google Sheets API key — can reuse one from existing Google API projects if available
  - Once the sheet is created and shared, provide the Sheet ID (from the URL:
    `docs.google.com/spreadsheets/d/SHEET_ID/edit`)
