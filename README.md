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

Shopify's `client_credentials` OAuth flow is blocked from GitHub Actions IPs.
We use pre-fetched permanent tokens (`SHOPIFY_US_ACCESS_TOKEN`, `SHOPIFY_MX_ACCESS_TOKEN`)
stored as GitHub secrets. These do **not expire**.

**If Shopify tokens stop working** (e.g. after app reinstall):
1. Run locally: `python scripts/refresh_shopify_tokens.py` (or fetch manually — see below)
2. Update the two GitHub secrets with the new `shpat_...` values

To fetch tokens manually:
```bash
source /home/quent/sarelly-venv/bin/activate
python -c "
import os, requests
from dotenv import load_dotenv
load_dotenv()
for env, label in [('SHOPIFY_US_SHOP_DOMAIN','US'), ('SHOPIFY_MX_SHOP_DOMAIN','MX')]:
    domain = os.environ[env]
    resp = requests.post(
        f'https://{domain}.myshopify.com/admin/oauth/access_token',
        data={'client_id': os.environ['SHOPIFY_CLIENT_ID'],
              'client_secret': os.environ['SHOPIFY_CLIENT_SECRET'],
              'grant_type': 'client_credentials'},
    )
    print(f'SHOPIFY_{label}_ACCESS_TOKEN={resp.json()[\"access_token\"]}')
"
```

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
