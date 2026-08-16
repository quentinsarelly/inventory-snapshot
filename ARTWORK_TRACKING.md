# Packaging Artwork Tracking via Lot Codes — Feasibility

**Date:** 2026-08-16
**Question:** Can we count old vs. new secondary-packaging artwork per warehouse, using the
lot codes in `batch_packaging_mapping.csv`?
**Answer:** Yes for the Mexico 3PL only. Today the mapping labels **31.8%** of MX stock.

All findings below come from live read-only API probes. No mutation, submit or update
operation was called on any system; nothing was written to Supabase.

## Scorecard

| System | Source | Verdict | Basis |
|---|---|---|---|
| ShipHero | `mx_3pl` | **Yes** | 52 lots across 46 SKUs, quantity per bin per lot |
| Camelot / Excalibur | `us_3pl` | **Vendor action** | All 17 SOAP ops return the same 14-field export |
| TikTok FBT | `tiktok_us` | **No** | 15 key paths in the full response, none a lot |
| Amazon FBA | `amazon_us` / `_mx` | **No** | Widest FBA inventory report has 24 columns, no lot |
| Shopify retail | 6 MX stores | **No** | Shopify has no lot dimension on inventory levels |

Nothing in the pipeline tracks lots today — `inventory_snapshots` is keyed
`(snapshot_date, source, external_id)` and has no lot column.

## ShipHero — works, and it is cheap

The load-bearing query is `item_locations`, which returns one row per SKU per bin per lot:

```graphql
query IL($sku: [String], $after: String) {
  item_locations(sku: $sku, has_inventory: true) {
    complexity
    data(first: 100, after: $after) {
      edges { node {
        sku quantity
        expiration_lot { id name expires_at }
        location { id name pickable sellable }
      } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
```

Sample row:

```json
{ "sku": "SCL-0117", "quantity": 2544,
  "expiration_lot": { "name": "732-26", "expires_at": "2028-05-31" },
  "location": { "name": "P-E1-08-A-02", "pickable": true, "sellable": true } }
```

A full sweep of all 278 in-stock SKUs cost **12 API calls, 1,212 complexity credits and 13.8
seconds**. ShipHero's pool is ~4,004 credits refilling at 60/sec, so this fits inside the
existing 6am job — no new cadence needed.

The numbers reconcile: for every SKU that is not a kit, lot quantities plus unlotted
quantities summed to exactly the `on_hand` the daily job already records.

### Coverage ceiling: 54.9%

No mapping can beat this, because it is how much stock carries a real lot at all. Of the
438,302 units that appear in `item_locations`:

| Bucket | Units | Share |
|---|---:|---:|
| On a real lot — attributable | 240,500 | 54.9% |
| On the `SINLOTE` placeholder | 116,086 | 26.5% |
| In a bin with no lot at all | 81,716 | 18.6% |

**`SINLOTE` is a real lot record meaning "no lot".** Someone created a literal lot named
`SINLOTE` with a sentinel expiry of `2050-01-01` and receives stock against it. It looks like
lot data to the API but carries no information, so it must be bucketed separately — a naive
implementation would silently count it as a real artwork version. It is concentrated in
`INS-*` SKUs (components); finished goods `SCL-*` and `WHS-*` are properly lotted.

Lot names follow two conventions: `NNN-YY` (37 distinct, e.g. `866-26`) and `Hxxxxx`
(10 distinct, e.g. `H60525`). Five entries fit neither and look like free-text scratch:
`T.ARCOIRIS`, `223499`, `GB15979-2002` (a Chinese national standard number), `5MA`, `L2348` —
tiny quantities, but worth a word with the 3PL.

## The mapping sheet against the live warehouse

`batch_packaging_mapping.csv` has 61 usable rows covering 30 SKUs. Joined to the 57 live
SKU/lot pairs holding stock:

| Bucket | Units | Share | Why |
|---|---:|---:|---|
| **Matched — labelled old/new** | 139,394 | **31.8%** | Sheet has this SKU and this lot |
| On a real lot, absent from sheet | 101,106 | 23.1% | **Fixable by us** — WMS knows the lot |
| On the `SINLOTE` placeholder | 116,086 | 26.5% | Fixable only at receiving, by the 3PL |
| In a bin with no lot | 81,716 | 18.6% | Same — a receiving-process gap |

Of what we can label: **84,512 units new, 54,882 old**. The lot codes match exactly — no
normalisation needed.

### Per-SKU split for everything we can label

| SKU | Old | New | Total | Mix |
|---|---:|---:|---:|---|
| `SCL-0033` | 0 | 22,341 | 22,341 | all new |
| `SCL-0088` | 0 | 21,893 | 21,893 | all new |
| `SCL-0156` | 0 | 18,127 | 18,127 | all new |
| `SCL-0116` | 16,271 | 0 | 16,271 | all old |
| `SCL-0134` | 10,306 | 0 | 10,306 | all old |
| `SCL-0117` | 9,747 | 0 | 9,747 | all old |
| `SCL-0155` | 1,148 | 8,186 | 9,334 | **both** |
| `SCL-0135` | 9,084 | 0 | 9,084 | all old |
| `SCL-0200` | 0 | 8,177 | 8,177 | all new |
| `SCL-0199` | 4,394 | 0 | 4,394 | all old |
| `SCL-0202` | 0 | 3,364 | 3,364 | all new |
| `SCL-0095` | 2,778 | 0 | 2,778 | all old |
| `SCL-0201` | 0 | 2,188 | 2,188 | all new |
| `SCL-0102` | 1,154 | 0 | 1,154 | all old |
| `SCL-0034` | 0 | 236 | 236 | all new |

### Adding 38 rows would take us from 31.8% to 54.9%

101,106 units sit on lots ShipHero reports correctly but the sheet does not mention. Four
entries account for over 60,000 of them, all SKUs the sheet omits entirely:

| SKU | Lot | Units |
|---|---|---:|
| `WHS-0052` | `1093-26` | 21,600 |
| `WHS-0053` | `1144-26` | 21,595 |
| `INS-0304` | `997-26` | 9,455 |
| `WHS-0051` | `1013-26` | 9,073 |

**Open question:** if `WHS-*` and `INS-*` are components rather than saleable finished goods,
they may not need artwork mapping at all. Excluding them would make real coverage of what we
care about much higher than 31.8%. Right now they are silently counted as unlabelled.

Only three of the 38 missing pairs are SKUs the sheet already covers (`SCL-0034` `1269-25`,
`SCL-0079` `H60421`, `SCL-0156` `543-26`). Separately, 42 of the sheet's 61 rows match no live
lot — expected, since it covers historical batches too.

### Three defects in the sheet

1. **Four lots are listed twice, hyphenated and not** — `468-26`/`46826`, `655-26`/`65526`,
   `605-26`/`60526`, `732-26`/`73226`. ShipHero only ever uses the hyphenated form, so the
   compact rows match nothing. Harmless today, but a problem if anyone normalises the join.
2. **`SCL-0079` lot `H60421` has a blank Packaging value** — and holds 4,099 live units. The
   single largest fixable row.
3. **Lot `662-26` is marked `old` under `SCL-0088` and `new` under `SCL-0088-SAM`.** May be
   legitimate if they are genuinely different products; worth confirming.

## Camelot — blocked on a permission, not a capability

The connector *is* working (109 rows/day). README's `—` row count and TODO.md's "provider TBD"
are both stale and should be corrected.

The export carries exactly 14 elements per item, none of them a lot:

```
Client · ClientName · ItemNumber · SubPart1Number · SubPart2Number · AltItemNo
ItemDesc1 · ItemDesc2 · UOM · QtyOnHand · QtyAvailable · QtyAvailableToOrder
QtyReserved · QtyWithStatus
```

The WSDL exposes 17 operations on codeunit `TPLWebServiceInt`; we call one. I probed every
read-only operation — the paged inventory variant, the transaction time-range query, receipt
and transaction detail across nine document types. **All returned the byte-identical
53,038-byte item-inventory document.** One call explained why:

```
Interface profile SAR_ITEM_E does not have permission
to call this function for client .
```

Our credential is scoped to one thing: the item-inventory export. This is a provisioning
decision on Camelot's side, not a limitation of Excalibur — which does track lot, batch, LPN
and code date, and picks by FEFO.

Second tell: `SubPart1Number` and `SubPart2Number` are present in every record and empty in
every record. In Excalibur those are the configurable sub-item dimensions normally used for lot
or code date, which suggests lot tracking is simply not switched on for the `SARELLY` client.

### What to ask the US 3PL

1. **Is lot tracking enabled for our client account at all?** If receiving is not capturing lot
   codes, nothing else matters — this becomes an operations change first, an API change second.
2. **If it is, can we get an interface profile that exposes it?** Either extend XMLPort
   37005331 with lot fields, populate `SubPart1Number`/`SubPart2Number` with lot and code date,
   or provision a second profile permissioned for a lot-level export.

## TikTok FBT — reports quantity, never the lot

Worth checking, because TikTok's own policy *mandates* lot codes on inbound cartons for
ingestibles and states that FBT warehouses store different lot codes separately. They hold the
data; they do not hand it back. The complete `/fbt/202408/inventory/search` response:

```
inventory[].fbt_warehouse_id
inventory[].goods.id · .name · .reference_code
inventory[].goods.skus[].id
inventory[].goods.skus[].on_hand_detail.available_quantity · .reserved_quantity · .total_quantity
inventory[].in_transit_quantity
inventory[].on_hand_detail.available_quantity · .reserved_quantity
                          · .total_quantity · .unfulfillable_quantity
next_page_token · total_count
```

Materiality is low anyway — FBT holds 23 goods in small quantities (`SCL-0033` has 55 units
there against 22,341 in the MX 3PL).

## Amazon FBA — not exposed to sellers

Lot-controlled FBA inventory is a per-seller account configuration Amazon enables through a
Technical Account Manager, and lot/expiry travel on *inbound* feeds
(`POST_FBA_INBOUND_CARTON_CONTENTS` — what we tell Amazon), not on the inventory reports.

Confirmed rather than assumed: `GET_FBA_MYI_ALL_INVENTORY_DATA`, the widest FBA inventory
report, returns 24 columns — all identifiers, prices or quantity buckets
(`afn-fulfillable-quantity`, `afn-reserved-future-supply`, `afn-fc-transfer-quantity`, …).
No lot, no batch, no expiry.

Exact evidence basis: `GET_LEDGER_DETAIL_VIEW_DATA` returned zero rows because no date range
was passed — inconclusive, not negative. Two other reports ended `FATAL` on Amazon's side.
Probing stopped there, because no report will expose lots until the account feature is enabled.

## Incidental findings (not about lots)

Both affect numbers the dashboard shows today.

### The MX 3PL total is inflated by kits

54 in-stock SKUs — **188,900 units** — report `on_hand > 0` but have no bin records at all.
Nearly every one comes back `kit: true`. They are virtual bundles whose on-hand is derived
from components that are *also* counted under their own SKUs:

```
SCL-0033        22,341   real stock, in bins, lotted
SCL-0033-FBM    22,322   kit — same physical units
SCL-0033-FBM-2  22,322   kit — same physical units again
```

Roughly 22k units of real stock reported as ~67k. Since `connectors/mx_3pl.py` writes every
`warehouse_products` node including kits, the `mx_3pl` column and the Total MX table are
overstating. When computing real physical stock, use `item_locations` (bin-level) rather than
`warehouse_products.on_hand`.

### A second ShipHero warehouse exists

Two warehouses, both named `Primary` (one in Naucalpan de Juárez, one with no address). Only
the first holds products, so nothing is wrong today — but `mx_3pl.py` keys rows on SKU alone
and does not request `warehouse_id`, so if stock ever lands in the second warehouse those rows
will silently overwrite each other on upsert rather than sum.

### Flaky Amazon report

`GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA` — the report `connectors/amazon.py` depends on every
morning — returned `FATAL` on all three retry attempts during this probe, while succeeding in
the same day's 6am job. Intermittent rather than broken, but it is a single point of failure
with a three-attempt retry budget.

## Where this leaves us

The method works and is proven against live stock. What is in question is reach: Mexico only,
and today just under a third of it.

1. **Are `WHS-*` and `INS-*` in scope?** They are the bulk of the unmapped units. If they are
   components with no secondary packaging, exclude them and coverage jumps sharply.
2. **Ask the US 3PL the two Camelot questions above.** That decides whether US stock is
   coverable at all — a conversation, not a code change.
3. **Ask the MX 3PL about `SINLOTE` and the unlotted bins.** 197,802 units (45% of the
   warehouse) carry no real lot. Only receiving can fix that, and only going forward.

### The alternative, if reach is not enough

A temporary SKU split — a distinct SKU for new-artwork units. Every system in the stack tracks
SKUs natively, so it needs no schema change and covers all channels including FBA and retail at
100%. It costs listing and receiving work at both 3PLs, and applies only to stock going
forward. Lot codes have the opposite profile: no operational cost, partial and Mexico-only
coverage, but they work retroactively on stock already in the building.

### If we proceed

The build is small and low-risk: a new `inventory_lot_snapshots` table keyed
`(snapshot_date, source, external_sku, lot_name)`, following the same pattern as the existing
`inventory_location_snapshots` (`schema.sql:60-70`, writer `upsert_location_snapshots` in
`db/client.py`). It leaves `inventory_snapshots`, the `inventory_unified` view and every other
connector untouched.

Mirror `inventory_snapshots`' nullable, unconstrained `internal_sku` rather than
`inventory_location_snapshots`' FK to `sku_master` — ~100 ShipHero SKUs per run are unmapped
(`INS-*`, `COL-*`, `WHS-*`) and an FK would reject them.
