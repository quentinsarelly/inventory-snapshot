# Inventory Snapshot System

Daily inventory snapshots from all Sarelly sales channels into a Supabase database.

## Architecture

- **Connectors** (`connectors/`) — one file per source, each fetches and writes to Supabase
- **Database** (`db/client.py`) — supabase-py over HTTPS (required for WSL2 IPv6 workaround)
- **Scheduler** — GitHub Actions cron at 6am ET daily (`.github/workflows/daily_snapshot.yml`)
- **Dashboard** (`dashboard/app.py`) — Streamlit app reading from `inventory_unified` view

### Sources

| Source | Rows (approx) | Notes |
|---|---|---|
| `shopify_us` | ~88 | Active products only; archived filtered out |
| `shopify_mx` | ~276 | Active products only; archived with stock flagged |
| `amazon_us` | ~113 | Deduplicated by ASIN; keeps `-USA` seller SKU |
| `amazon_mx` | ~113 | Deduplicated by ASIN; keeps base SKU (no `-USA`) |
| `tiktok_us` | ~13 | FBT only; aggregated across warehouses |
| `mx_3pl` | ~689 | ShipHero GraphQL; all warehouse products |
| `us_3pl` | — | Provider TBD |

## Running locally

```bash
source /home/quent/sarelly-venv/bin/activate

python run_all.py            # live write to Supabase
python run_all.py --dry-run  # fetch only, print quality report, no DB writes
```

### Dashboard

```bash
streamlit run dashboard/app.py
```

Pick a **Snapshot date** in the sidebar, then **Download Excel report** to get a
single `.xlsx` for sharing with finance. The workbook has one sheet per table —
Report Info, All Sources, Total Mexico, Total US, In-Stock Rates, and Retail by
Location — and respects the sidebar Category filter.

> **One-time setup:** the date picker reads the `snapshot_dates` view. Run that
> statement from `schema.sql` once against Supabase. Without it PostgREST caps
> responses at 1000 rows — about one day of snapshots — so only the most recent
> date is selectable and past reports cannot be pulled. The dashboard shows a
> warning in the sidebar while the view is missing.

## Maintenance

### TikTok — refresh token expires every ~30 days

The TikTok access token is auto-refreshed on every run using `TIKTOK_REFRESH_TOKEN`.
The **refresh token itself** expires in ~30 days and must be renewed manually.

**Symptoms:** GitHub Actions shows `401 Unauthorized` on the TikTok step.

**Fix:**
1. Go to [TikTok Partner Portal](https://partner.tiktokshop.com) → your app → Auth
2. Generate a new access token (this also gives a new refresh token)
3. Update two GitHub secrets: `TIKTOK_ACCESS_TOKEN` and `TIKTOK_REFRESH_TOKEN`
4. Update `.env` locally with the same values
5. Trigger a manual run to confirm it works

---

### Shopify — access tokens

We use permanent offline OAuth tokens (`shpat_...`) stored as GitHub secrets.
These do **not expire** unless the app is uninstalled from the store.

**Do not** use the `client_credentials` grant — it generates short-lived rotating
tokens that break within 24 hours.

**If Shopify tokens stop working** (e.g. after app reinstall):

**One-time setup** (only needed if `http://localhost:3000/callback` is not yet in
the app's allowed redirect URLs): Partner Dashboard → your app → App setup → URLs
→ add `http://localhost:3000/callback` → Save.

```bash
source /home/quent/sarelly-venv/bin/activate
python scripts/get_shopify_tokens.py us   # opens browser, approve on US store
python scripts/get_shopify_tokens.py mx   # opens browser, approve on MX store
```

Each command prints a `shpat_...` token. Update:
1. `.env` — `SHOPIFY_US_ACCESS_TOKEN` / `SHOPIFY_MX_ACCESS_TOKEN`
2. GitHub secrets — same names

---

### Amazon — refresh tokens

Amazon LWA refresh tokens are long-lived (no expiry). No routine maintenance needed.
If the SP-API returns auth errors, check that the IAM role ARN and app credentials
in GitHub secrets match what's in the Amazon Developer Console.

---

### Shopify — archived products with stock

Each run prints a `[LOGISTICS]` warning for archived Shopify products that still
have inventory on hand. These appear in the GitHub Actions run log.
Logistics team should investigate whether this is a system error or real stock.

---

### Adding new products to the dashboard

When new products are launched they won't appear in `inventory_unified` until
they are added to `sku_master` and `sku_mappings`. Steps:

1. Add the product to `sku_master` in Supabase (internal_sku, display_name, category)
2. Run the matching script locally to generate a new `sku_matching_review.csv`:
   ```bash
   python scripts/match_skus.py   # (or run the inline script from the session notes)
   ```
3. Review the CSV, mark confirmed mappings as `TRUE`
4. Insert confirmed mappings:
   ```bash
   python scripts/insert_sku_mappings.py
   ```

---

### GitHub Actions secrets reference

All secrets are in: GitHub repo → Settings → Secrets and variables → Actions

| Secret | Description | Expires? |
|---|---|---|
| `SUPABASE_URL` | Supabase project URL | Never |
| `SUPABASE_SERVICE_KEY` | Supabase service role JWT | ~2030 (see JWT exp) |
| `SHOPIFY_CLIENT_ID` | Shopify app client ID | Never |
| `SHOPIFY_CLIENT_SECRET` | Shopify app client secret | Never |
| `SHOPIFY_US_SHOP_DOMAIN` | `sarellysarelly-us` | Never |
| `SHOPIFY_MX_SHOP_DOMAIN` | `sarellysarelly` | Never |
| `SHOPIFY_US_ACCESS_TOKEN` | Pre-fetched US store token | Never |
| `SHOPIFY_MX_ACCESS_TOKEN` | Pre-fetched MX store token | Never |
| `AMAZON_LWA_APP_ID` | Amazon LWA client ID | Never |
| `AMAZON_LWA_CLIENT_SECRET` | Amazon LWA client secret | Never |
| `AMAZON_REFRESH_TOKEN_US` | Amazon US refresh token | Never |
| `AMAZON_REFRESH_TOKEN_MX` | Amazon MX refresh token | Never |
| `AMAZON_AWS_ACCESS_KEY` | AWS IAM access key | Never |
| `AMAZON_AWS_SECRET_KEY` | AWS IAM secret key | Never |
| `AMAZON_ROLE_ARN` | AWS IAM role ARN | Never |
| `TIKTOK_APP_KEY` | TikTok app key | Never |
| `TIKTOK_APP_SECRET` | TikTok app secret | Never |
| `TIKTOK_ACCESS_TOKEN` | TikTok access token | ~24h (auto-refreshed) |
| `TIKTOK_REFRESH_TOKEN` | TikTok refresh token | **~30 days — renew manually** |
| `TIKTOK_SHOP_ID` | TikTok shop ID | Never |
| `TIKTOK_SHOP_CYPHER` | TikTok shop cipher | Never |
| `SHIPHERO_REFRESH_TOKEN` | ShipHero JWT refresh token | Long-lived |
