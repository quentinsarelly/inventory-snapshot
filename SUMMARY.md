# Inventory Snapshot System — Project Summary

## What This Does

Pulls daily inventory data from all 6 Sarelly sales channels into a single PostgreSQL database, so you have one number per SKU per source every morning. A Streamlit dashboard visualizes it; GitHub Actions automates the daily pull.

---

## Project Structure

```
inventory-snapshot-system/
├── schema.sql                        # Run once in Supabase — creates all tables and the unified view
├── requirements.txt                  # Python dependencies
├── .env.example                      # Template — copy to .env and fill in credentials
├── run_all.py                        # Runs all 6 connectors then refreshes the unified view
│
├── db/
│   └── client.py                     # Shared DB connection, upsert helper, SKU mapping lookup
│
├── connectors/
│   ├── shopify.py                    # READY — paginates all variants + inventory levels by location
│   ├── amazon.py                     # READY — FBA inventory summaries, works for US and MX
│   ├── tiktok.py                     # READY — FBT warehouse inventory (pending credentials)
│   ├── us_3pl.py                     # STUB — provider name TBD, then fill in API call
│   └── mx_3pl.py                     # STUB — pending Segmail API docs + credentials
│
├── dashboard/
│   └── app.py                        # Streamlit dashboard: date picker, SKU table, bar chart, source totals
│
└── .github/
    └── workflows/
        └── daily_snapshot.yml        # GitHub Actions cron — runs every day at 6am ET
```

---

## Database Design

Three tables + one materialized view in PostgreSQL (hosted on Supabase):

| Table | Purpose |
|-------|---------|
| `sku_master` | Internal SKU catalog — your source of truth for names and categories |
| `sku_mappings` | Maps each internal SKU to its identifier in each external system (ASIN, Shopify variant ID, etc.) |
| `inventory_snapshots` | One row per source × SKU × date — full history, raw API response stored as JSONB |
| `inventory_unified` (view) | Pivoted view: one row per internal SKU per date with a column per source + total |

Source codes used throughout: `shopify`, `amazon_us`, `amazon_mx`, `tiktok_us`, `us_3pl`, `mx_3pl`

---

## Connector Status

| Source | Status | Credentials needed |
|--------|--------|--------------------|
| Shopify | Ready to run | Already have `SHOPIFY_ACCESS_TOKEN` |
| Amazon US (FBA) | Code ready | SP-API developer registration (1–3 day approval) |
| Amazon MX (FBA) | Code ready | Same SP-API app as US, different refresh token |
| TikTok Shop US (FBT) | Code ready | TikTok Shop Open Platform developer account |
| US 3PL | Stub | Need provider name → look up their API docs |
| MX 3PL (Segmail) | Stub | Need API base URL + key from Segmail contact |

---

## Next Steps by Priority

### This week — get first numbers

1. **Create Supabase project**
   - Go to supabase.com → new project
   - Open SQL Editor → paste and run `schema.sql`
   - Copy connection string into `.env` as `DATABASE_URL`

2. **Populate `sku_master`**
   - Run this in Supabase SQL Editor for your top SKUs (add all active ones):
   ```sql
   INSERT INTO sku_master (internal_sku, display_name, category) VALUES
     ('LONG-COW-LASHES-01', 'Long Cow Lashes', 'Lashes'),
     ('TELENOVELA-LIP-01',  'Telenovela Lipstick', 'Lips');
     -- add more rows here
   ```

3. **Run Shopify connector**
   ```bash
   cp .env.example .env
   # fill in SHOPIFY_STORE and SHOPIFY_ACCESS_TOKEN
   pip install -r requirements.txt
   python -c "from connectors.shopify import run; from datetime import date; print(run(date.today()))"
   ```
   Then check Supabase — you should see rows in `inventory_snapshots`.

4. **Map Shopify SKUs to internal SKUs**
   - Query `inventory_snapshots` to see what `external_sku` values Shopify returned
   - Insert matching rows into `sku_mappings`:
   ```sql
   INSERT INTO sku_mappings (internal_sku, source, external_id, external_sku) VALUES
     ('LONG-COW-LASHES-01', 'shopify', '<inventory_item_id>', 'LASHES-01');
   ```

5. **Launch the dashboard**
   ```bash
   streamlit run dashboard/app.py
   ```

---

### Next few days — Amazon (FBA)

6. **Register for SP-API** at Seller Central → Apps & Services → Develop Apps
   - Create a self-authorized app
   - Create an IAM user with `AmazonSellingPartnerAPIRequiredRole` and generate access keys
   - Copy all values into `.env` (`AMAZON_LWA_APP_ID`, `AMAZON_LWA_CLIENT_SECRET`, `AMAZON_REFRESH_TOKEN_US`, `AMAZON_REFRESH_TOKEN_MX`, `AMAZON_AWS_ACCESS_KEY`, `AMAZON_AWS_SECRET_KEY`, `AMAZON_ROLE_ARN`)

7. **Run Amazon connectors**
   ```bash
   python -c "from connectors.amazon import run; from datetime import date; run(date.today(), marketplace='us')"
   python -c "from connectors.amazon import run; from datetime import date; run(date.today(), marketplace='mx')"
   ```

8. **Map ASINs** — same process as Shopify: look at `external_id` (ASIN) in snapshots, insert into `sku_mappings`

---

### Following week — TikTok + 3PLs

9. **TikTok Shop Open Platform**
   - Apply at partner.tiktokshop.com → create app → request `product.read` + `fulfillment.read` scopes
   - Complete OAuth flow to get `TIKTOK_ACCESS_TOKEN` for the Sarelly seller account

10. **US 3PL** — confirm provider name, look up API docs, fill in `connectors/us_3pl.py`

11. **Segmail (MX 3PL)** — get API base URL and key from your Segmail ops contact, fill in `connectors/mx_3pl.py`

---

### Automation — once all sources are green

12. **GitHub Actions**
    - Push this repo to GitHub
    - Add all `.env` values as repository secrets (Settings → Secrets → Actions)
    - The workflow at `.github/workflows/daily_snapshot.yml` will run automatically at 6am ET

13. **Deploy dashboard**
    - Push to GitHub
    - Go to share.streamlit.io → connect repo → set `dashboard/app.py` as entry point
    - Add `DATABASE_URL` as a secret in Streamlit Cloud settings

---

## How to Iterate

- **New SKU added**: insert into `sku_master`, then add rows to `sku_mappings` for each source that carries it
- **New source**: add a new connector file in `connectors/`, add a source code to the schema comment, add the call to `run_all.py`
- **Data looks wrong**: check `raw_data` column in `inventory_snapshots` — the full API response is stored there for debugging
- **Reorder alerts**: adjust the "Highlight below" threshold in the dashboard sidebar; later we can add email alerts
