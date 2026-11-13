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

## Data gaps

Every connector reads **current** inventory — no API here supports a historical
query. A source that fails therefore leaves a hole that can only be refilled
**on the same day**; once the date rolls over (Mexico City time, which is what
`snapshot_date` uses) that day is permanently incomplete. Backfilling later
would write today's stock under an older date, which is worse than a gap.

So when a run fails, fix it that day. `run_all.py` prints a coverage table at
the end and exits non-zero on either failure mode below. The dashboard shows a
red banner naming each affected channel, and the Excel export carries a
**Data Coverage** sheet plus a `Data complete` row, so an incomplete report
cannot be handed to finance unnoticed.

Two distinct failures each make a channel vanish:

| | what happened | why it hides |
|---|---|---|
| **no rows** | the connector failed outright | tables render a plain `0`, indistinguishable from genuinely zero stock |
| **none mapped** | rows were written but none resolved to an `internal_sku` | every table is keyed on `internal_sku`, so the channel is just as absent while the row count looks healthy |

The second mode is not hypothetical: on 2026-11-07 a `--dry-run` overwrote the
retail locations' 271 mapped rows with 514 unmapped ones, which silently zeroed
the Retail column. Row-count checks alone saw nothing wrong.

Coverage spans `inventory_location_snapshots` as well as `inventory_snapshots`,
reported under the synthetic source name `retail_locations`. Mapped ratios vary
widely by source (14% for `mx_3pl` to 80% for `tiktok_us`), so only a collapse
to zero is flagged — a percentage threshold would misfire.

> **One-time setup:** coverage reads the `snapshot_coverage` view. Run that
> statement from `schema.sql` once against Supabase. Without it both callers
> fall back to one count query per source, which works but costs ~16 round
> trips per dashboard page load.

To refill one source on the day it failed:

```bash
source /home/quent/sarelly-venv/bin/activate
PYTHONPATH=. python3 -c "
from datetime import datetime; from zoneinfo import ZoneInfo
from connectors import amazon
from db.client import refresh_unified_view
d = datetime.now(ZoneInfo('America/Mexico_City')).date()
print(amazon.run(d, marketplace='us'), 'rows')
refresh_unified_view()"
```

Known permanent gaps: `amazon_us` on 2026-09-25, 10-04, 10-14, 10-20, 10-21,
10-22; `mx_3pl` on 2026-11-01.

### Transient failures and the Amazon maintenance window

`connectors/_http.py` retries transient transport failures (429, 5xx,
connection resets, read timeouts) for every connector — 5 retries over ~60s.
Genuine 4xx responses are not retried.

Amazon additionally runs a daily maintenance pass on the FBA US dataset during
which the `amazon_us` report returns `FATAL`. Measured across 120 days, every
failure fell inside **11:30-11:50 US Eastern** and no run outside it ever
failed. The window is anchored to US local time, so in UTC it sits at
15:30-15:50 under EDT and 16:30-16:50 under EST. `connectors/amazon.py` retries
5 times with 6-minute backoff (~24 minutes) to outlast it, and the cron avoids
scheduling into it — see the comment in the workflow.

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

## Purchase orders (PO tracking sheet)

`sync_pos.py` copies the supply team's PO sheet (tabs `TRANSITO` and
`TRANSITO RECIBIDO `) into `po_lines_raw` once a day, as a separate step of
the same GitHub Actions workflow. It runs even if an inventory connector
fails, and a PO sheet problem does not mark the inventory day incomplete.

Cells are stored as the text people see. Parsing and data quality checks
happen in the `agent.po_lines` view (sarelly-inventory-agent repo). Columns
are matched by header name, so the sheet's columns can be reordered or
added to; if a required header disappears, the run fails with a clear error.

```bash
python sync_pos.py --dry-run   # fetch and print, no DB writes
python sync_pos.py             # write today's copy
```

Re-running on the same day is safe: `replace_po_lines()` deletes that day's
rows for a tab before inserting, so counts stay stable and no duplicates
appear. The fresh copy reflects the sheet at that moment, which is intended —
one copy per day, the last run wins.

To trigger just this step from the Actions tab, run the workflow manually and
set **Which sources to run** to `po-sheet`. That skips the inventory
connectors, which would otherwise be re-run and overwrite the day's snapshot
with later stock levels. `inventory` skips the PO sheet instead; `all` (the
default, and what the daily schedule always does) runs both.

**Authentication: OAuth user credentials, not a service account.** The
organization does not allow service accounts, so the sync authenticates as a
real Google account using a refresh token.

**One-time setup:**
1. Run the "Purchase orders" section of `schema.sql` in Supabase.
2. In Google Cloud, enable the Google Sheets API and create an OAuth client of
   type **Desktop app** (APIs & Services -> Credentials). Download it as
   `credentials.json` in the repo root.
3. Set the OAuth consent screen user type to **Internal**, or publish the app.
   Leaving it in **Testing** makes refresh tokens expire after 7 days, and the
   daily sync then breaks silently a week later.
4. Give the Google account you will authorize at least **Viewer** access to the
   PO sheet. Keep the sheet private otherwise; do not use "anyone with the
   link". Prefer a shared ops account over a personal one — the sync stops
   working if that person loses sheet access, revokes the app, or leaves. Note
   in your records whose account it is.
5. Generate the token locally (opens a browser):
   ```bash
   python scripts/get_google_token.py
   ```
   It writes `token.json` and checks that the granted scopes match
   `spreadsheets.readonly` and that a `refresh_token` was issued.
6. Put the contents of `token.json` (one line) into the GitHub secret
   `GOOGLE_TOKEN_JSON` and into `GOOGLE_TOKEN_JSON` in your local `.env`. Add
   `PO_SHEET_ID` (the ID from the sheet's URL) as a secret too.

`credentials.json` and `token.json` are gitignored and must never be committed:
`token.json` contains a refresh token plus the client ID and secret.

**Rotating the token:** re-run `scripts/get_google_token.py` and update both the
GitHub secret and `.env`. Google only issues a refresh token on first consent,
so if the script reports none, revoke the app at
<https://myaccount.google.com/permissions> and run it again.
