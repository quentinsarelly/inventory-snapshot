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

- [ ] **Resolve Amazon duplicate SKU logic**
  See `FINDINGS.md` for details. Amazon returns multiple seller SKUs per ASIN with identical
  quantities. Need Amazon team to confirm: track by ASIN or by seller SKU? What to do with
  `-USA` suffix variants and `Stickered/Uncommingled.MSKU.*` entries (always 0 qty)?
  Once decided, update `connectors/amazon.py` and re-run.

- [ ] **Review extracted data and clean up database**
  Shopify and ShipHero rows are in `inventory_snapshots`. Before adding SKU mappings,
  review the data in Supabase table editor: check `external_sku` values look correct,
  delete any test/junk rows, confirm row counts make sense.

## SKU mappings (needed for the dashboard to show data)

The `sku_mappings` table is empty — until it's populated, all rows in
`inventory_snapshots` will have `internal_sku = NULL` and the unified view
will be blank. For each source:

- [ ] **Shopify US + MX** — after the live write, query `inventory_snapshots`
  for `source IN ('shopify_us', 'shopify_mx')` and match `external_sku`
  values to your `sku_master` internal SKUs. Many will match directly
  (same SKU code). Insert into `sku_mappings`.

- [ ] **Amazon US** — query snapshots for `source = 'amazon_us'`, match
  `external_id` (ASIN) and `external_sku` (seller SKU) to internal SKUs.

- [ ] **Amazon MX** — same as above for `source = 'amazon_mx'`.
  Also verify the MX refresh token works (currently using the same token as US).

- [ ] **ShipHero (MX 3PL)** — query snapshots for `source = 'mx_3pl'`,
  match `external_sku` to internal SKUs. ShipHero uses its own SKU codes.

## Pending connectors

- [x] **TikTok US (FBT)**
  Endpoint: `POST /fbt/202408/inventory/search` (version `202408`).
  Returns one row per product per warehouse — connector aggregates across all warehouses.
  13 products returned. Uses `goods.reference_code` (e.g. `SCL-0117`) as `external_sku`.
  Note: token refresh endpoint still returns 404 — not a blocker while access token is valid.

- [ ] **US 3PL**
  Provider not yet confirmed. Once identified, look up their REST API docs
  and implement `connectors/us_3pl.py`.

## Automation (after live writes are confirmed working)

- [ ] **Push to GitHub**
  Create a remote repo and push. The GitHub Actions workflow
  (`.github/workflows/daily_snapshot.yml`) runs automatically on push.

- [ ] **Add secrets to GitHub**
  In the repo: Settings → Secrets → Actions. Add every key from `.env`
  as a repository secret so the daily cron can authenticate.

- [ ] **Deploy dashboard**
  Go to share.streamlit.io → connect the GitHub repo → set entry point to
  `dashboard/app.py` → add `DATABASE_URL` as a secret in Streamlit Cloud.
