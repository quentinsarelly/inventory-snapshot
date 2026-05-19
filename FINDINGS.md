# Amazon US Inventory Extract — Findings

**Date:** 2026-05-18  
**Total rows returned by API:** 161  
**ASINs with duplicate seller SKUs:** 35

## What the API returns

Amazon's Inventory API returns one row **per seller SKU**, not per ASIN. A single ASIN
can have multiple seller SKUs pointing to the same physical inventory pool. The quantities
(`on_hand`, `reserved`, `available`) are identical across all seller SKUs for the same ASIN.

## Observed SKU patterns per ASIN

For most duplicated ASINs, Amazon returns **three seller SKUs**:

| SKU type | Example | Description |
|---|---|---|
| Auto-generated | `85-XLO1-P11I` | Created by Amazon, random alphanumeric |
| Internal SKU | `SCL-0035` | Sarelly's own SKU code |
| USA-suffixed SKU | `SCL-0035-USA` | Sarelly's SKU with `-USA` suffix |

A smaller number of ASINs also have a fourth SKU:

| SKU type | Example | Description |
|---|---|---|
| Stickered MSKU | `Stickered.MSKU.1749232139898` | Amazon virtual SKU for re-labeling batches |
| Uncommingled MSKU | `Uncommingled.MSKU.1736359095447` | Amazon virtual SKU for commingling control |
| These always have **qty = 0** across all fields. |

## Sample duplicate group

ASIN `B0D4F8NHHY` — *SARELLY Lash Kit (Cow Lashes Mascara + Lavadero)*

| Seller SKU | on_hand | reserved | available |
|---|---|---|---|
| `85-XLO1-P11I` (auto-generated) | 218 | 11 | 207 |
| `SCL-0035` (internal SKU) | 218 | 11 | 207 |
| `SCL-0035-USA` (USA variant) | 218 | 11 | 207 |

All three rows reflect the exact same physical inventory at Amazon's fulfillment center.

## Question for Amazon team

Our inventory system uses **one row per product per day per channel**. We need to decide
how to handle these duplicates. The two options are:

**Option A — Track by seller SKU (use seller SKU as the unique identifier)**
- Each seller SKU becomes its own row in our database
- No deduplication needed
- SKU mappings table maps seller SKU → internal SKU
- The `-USA` variant and auto-generated SKU would each need a mapping entry (or be ignored)
- **Question:** Are `SCL-0035` and `SCL-0035-USA` different active listings, or should one be retired?

**Option B — Track by ASIN (deduplicate, keep one seller SKU per ASIN)**
- One row per ASIN per day
- Need to decide which seller SKU to keep (the internal one, e.g. `SCL-0035`)
- Auto-generated and `-USA` SKUs would be discarded
- **Question:** Is there a consistent rule for which seller SKU should be the "canonical" one?

**Either way:** The `Stickered.MSKU.*` and `Uncommingled.MSKU.*` entries (always 0 qty)
should probably be excluded — are these ever meaningful to track?
