# TODO — path to live daily inventory writes

## Blockers (must fix before first live write)

- [ ] **Fix Supabase IPv6 connectivity**
  WSL2 resolves the Supabase host to an IPv6 address it can't reach.
  Fix: switch to the Supabase connection pooler URL (port 6543) in `.env` —
  it resolves to IPv4. Get it from: Supabase dashboard → Project Settings → Database → Connection pooling.

- [ ] **First live write**
  Once connectivity is fixed, run without the flag and confirm rows land in Supabase:
  ```bash
  source /home/quent/sarelly-venv/bin/activate
  python run_all.py
  ```
  Then check `inventory_snapshots` in the Supabase table editor.

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

- [ ] **TikTok US (FBT)**
  The correct FBT inventory endpoint path is unknown — all candidates return
  `403 "no schema found"` (empty app_id). FBT scope is authorized.
  Next step: check the **API Explorer** in the TikTok Partner Portal
  (partner.tiktokshop.com → your app) for the exact endpoint path, then
  update `connectors/tiktok.py`. Also find the correct token refresh endpoint
  (current `/api/token/refreshToken` returns 404).

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
