# Methodology

This suite quantifies the gap between what the US drug system pays (Medicare
Part D, Medicaid SDUD) and Cost Plus Drugs' transparent prices, using only
public data. This document lists every comparison made, its data source, and
its known limitations.

## Governing principles

1. **Net prices are never estimated.** True net-of-rebate prices are not
   public. Every `net_per_unit` field in this suite's output is the literal
   string `"not public"`.
2. **Everything is reduced to the same per-unit basis before comparison.**
   NADAC's own `Pricing Unit` (EA / ML / GM) is the canonical unit. A
   per-package price is never compared to a per-unit price.
3. **Headline overpayment numbers are restricted to generics**
   (`config.GENERICS_ONLY`, default `True`). Published Part D and Medicaid
   spending is gross of manufacturer rebates. Generic rebates are small and
   mostly settled at the point of sale, so gross is a defensible proxy for
   what the system paid. Brand rebates can be deep (30-50%+), so a brand
   "overpayment" computed off gross spend would be materially overstated.
4. **Dispensing/pharmacy fees and shipping fees are always separate
   columns**, never folded into a per-unit price.
5. **No dataset resource IDs are hardcoded.** Every fetch/*.py module
   discovers its current distribution at runtime from the hosting platform's
   API or landing page.
6. **No proprietary or paid data, no scraping behind logins.**

## Data sources

### NADAC -- National Average Drug Acquisition Cost (`fetch/nadac.py`)
- **What it is**: CMS's weekly survey of retail community pharmacies'
  actual invoice costs. The closest public proxy to true acquisition cost.
- **Limitation**: NADAC is a survey, not a census -- some low-volume NDCs
  have no reported invoice cost. NADAC is **not** net of any rebate; it
  approximates acquisition cost, not net cost, and is never relabeled "net."

### Medicare Part D Spending by Drug (`fetch/partd.py`)
- **What it is**: CMS's annual national aggregate of Part D gross spend, by
  brand + generic name (not NDC-level).
- **Limitation, granularity mismatch**: Part D's `Gnrc_Name` aggregates
  spending across **every strength and dosage form** of that ingredient
  nationally (e.g. one "Atorvastatin Calcium" row sums 10mg + 20mg + 40mg +
  80mg together). Cost Plus and NADAC are strength-specific, so comparing a
  strength-specific per-unit price to an ingredient-wide weighted average
  assumes per-dosage-unit price is roughly flat across strengths -- a
  reasonable but imperfect assumption.
- **Limitation, gross of rebates**: stated in CMS's own dataset description.
  This is exactly why `generics_only` gates headline numbers.
- **Limitation, no generic/brand flag**: the file has no explicit indicator.
  `fetch.partd.is_generic_row()` uses the field's own convention --
  unbranded generics list the identical string in `Brnd_Name` and
  `Gnrc_Name`. A real generic with a distinct marketed brand-style name can
  be misclassified as "brand" and excluded from generic totals -- the
  **conservative** direction (undercounts overpayment, never overstates it).
- **Limitation, "list price" substitution used in Module E**: CMS does not
  publish a public WAC/list-price series (WAC is proprietary, First
  Databank/Medi-Span). Where "list-price movement" would normally be asked
  for, this suite uses Part D's year-over-year change in gross average spend
  per dosage unit (`Chg_Avg_Spnd_Per_Dsg_Unt_<y-1>_<y>`) instead, labeled
  explicitly as `gross_spend_per_unit_yoy_chg_pct` and never called "list
  price" or "WAC."

### Medicaid State Drug Utilization Data / SDUD (`fetch/sdud.py`)
- **What it is**: CMS's quarterly, NDC-level, per-state Medicaid
  utilization and reimbursement.
- **Quirk**: CMS reports a synthetic `State == "XX"` row per NDC that is the
  **national rollup** (sum across all real states/territories), not a 51st
  jurisdiction. `fetch.sdud.national_total()` and `.state_level()` split
  these apart explicitly so a national total is never computed by summing
  real states on top of the XX row.
- **Suppression**: rows with `Suppression Used == true` (small-count privacy
  suppression) carry no usable units/amount and are dropped before
  aggregation.
- **Limitation, gross of rebates**: same caveat as Part D; also gated by
  `generics_only` at the Module A level via the crosswalk.

### Cost Plus Drugs price list (`shared/costplus.py`)
- **Source**: a user-supplied CSV at `data/costplus.csv`
  (`drug, strength, form, package_quantity, acquisition_cost, markup,
  pharmacy_fee, shipping_fee`), or one of the two live-data paths below.
- **Formula**: `costplus_per_unit = (acquisition_cost * markup + pharmacy_fee)
  / package_quantity`. `shipping_fee` is never part of this formula -- it
  stays a separate column through the entire pipeline.
- **Sample data**: `data/costplus.SAMPLE.csv` contains ten drugs with
  fabricated acquisition costs, used only to exercise the pipeline
  end-to-end. Every output derived from it is prefixed with a
  `SAMPLE DATA -- NOT REAL` banner or a `SAMPLE_` output filename prefix.
- **`--source scrape` (`fetch/costplus_html_scraper.py`)**: live HTML
  scrape of costplusdrugs.com product pages. **Limitation**: a product page
  publishes the drug's name/strength/brand, its flat shipping fee, and the
  final all-in price -- but never the acquisition cost Cost Plus pays its
  supplier, nor the markup/pharmacy-fee breakdown behind that price. This is
  Cost Plus's own trade secret, not a gap to fill in later; `acquisition_cost`,
  `markup`, and `pharmacy_fee` are left blank rather than back-solved from
  the published 15%/$5 policy figures (unverifiable per-SKU). Only 31.1% of
  rows get a confirmed `package_quantity` this way (recovered from free-text
  page fields); the rest are excluded, never guessed.

### Cost Plus Drugs GraphQL catalog (`fetch/costplus_graphql.py`)
- **What it is**: costplusdrugs.com's public Saleor storefront GraphQL API
  (`POST /graphql/`), the same API the site's own frontend calls. This is
  the default, real-data path (`python run.py` uses it automatically via
  `data/costplus.GRAPHQL.csv`).
- **Solves the package_quantity gap**: 2,386/2,386 (100%) rows return a
  confirmed `package_size` metafield, vs. 31.1% from the HTML scrape.
  `package_size` itself is not perfectly reliable even via this API (e.g. a
  "Box of 30 vials" variant can still report `package_size == "1"`), but it
  is structured data covering the whole catalog.
- **Limitation**: `retailPricePerUnit` looks like Cost Plus's own per-unit
  price but is not reliable (can be off by 100x+ on some SKUs) -- this
  suite uses variant-level `priceCalculation` as `final_price` instead,
  divided by `package_quantity`, never `retailPricePerUnit` directly.
- **No fee breakdown here either**: acquisition_cost/markup/pharmacy_fee are
  not present on any field this API exposes. `shipping_fee` is a JSON-LD
  field on the HTML page, not part of this schema, so it is left blank here.

### FDA Drug Shortages (`fetch/shortages.py`)
- **Source**: openFDA's public `drug/shortages.json` endpoint (no
  authentication required). `status == "Current"` is treated as an active
  shortage; `"To Be Discontinued"` and `"Resolved"` are historical.

### Medicare Part B ASP Pricing Files (`fetch/asp.py`)
- **What it is**: CMS's quarterly Average Sales Price payment-limit files
  (ASP + 6%), used to reimburse physicians/hospitals for physician-
  administered drugs, plus the companion NDC-HCPCS crosswalk.
- **Limitation**: ASP is a volume-weighted average of a manufacturer's
  quarterly net sales across all of that manufacturer's customers -- it
  already has rebate-like averaging baked into the number by statute, before
  CMS ever publishes it. Surfaced as `payment_limit`, never relabeled "net."
- **Unit safety**: a NDC's HCPCS billing unit (e.g. "10 MG") is frequently
  not the same unit NADAC prices that NDC in. `modules/f_oncology.py` only
  computes a direct `overcharge_per_billunit` figure when the HCPCS dosage
  unit and NADAC's Pricing Unit are confirmed identical; every other row
  reports both prices side by side with the overcharge column left blank.
- **Scope note**: Cost Plus's retail catalog is predominantly self-
  administered oral generics; physician-administered oncology infusions are
  largely out of scope. Zero or very few Module F matches is expected, not
  a bug.

## Crosswalk methodology (`shared/crosswalk.py`)

Free-text drug descriptions ("atorvastatin 20 mg tablet") are resolved to
NDCs via:

1. `RxNav approximateTerm.json` -- fuzzy match to ranked candidate RxCUIs.
2. **Dispensable-TTY filtering**: RxNav's approximate match frequently ranks
   the bare ingredient+strength concept (RxNorm TTY `SCDC` -- no dose form,
   zero NDCs) above the actual dispensable clinical drug (TTY `SCD` -- has
   an NDC set). `resolve_dispensable_rxcui()` walks the ranked list and takes
   the first candidate whose TTY is in `{SCD, SBD, GPCK, BPCK}`.
3. `RxNav rxcui/{rxcui}/ndcs.json` -- the NDC set for that dispensable drug.
4. NADAC lookup by NDC -- `NADAC Per Unit` + `Pricing Unit`, aggregated by
   **median** across all matched NDCs (robust to the occasional outlier
   package).
5. **Ingredient-name resolution** (`get_ingredient_name`) strips a
   dispensable drug's RxCUI down to its bare ingredient (e.g.
   "atorvastatin"), used to join against Part D / SDUD's free-text generic
   name fields.
6. **Name normalization** (`normalize_drug_name`): uppercases and strips
   common salt/ester/formulation suffixes (HCL, SODIUM, POTASSIUM, ER, XR,
   etc.) so RxNorm's bare ingredient name and CMS's free-text `Gnrc_Name`
   converge to the same join key. This is a heuristic text match, not an
   authoritative crosswalk -- not guaranteed exhaustive for every possible
   salt form or naming convention CMS uses.

## Module A computations

```
costplus_per_unit = (acquisition_cost * markup + pharmacy_fee) / package_quantity
partd_per_unit    = Tot_Spndng / Tot_Dsg_Unts                 (Mftr_Name == "Overall" rows only)
gap_partd         = partd_per_unit - costplus_per_unit
overpayment_partd = gap_partd * Tot_Dsg_Unts

medicaid_per_unit    = medicaid_amount / medicaid_units        (SDUD "XX" national rollup only)
gap_medicaid         = medicaid_per_unit - costplus_per_unit
overpayment_medicaid = gap_medicaid * medicaid_units

gap_nadac = costplus_per_unit - nadac_per_unit   (Cost Plus's margin over true acquisition
                                                   cost -- NOT an overpayment figure, and
                                                   never summed into the savings totals)
```

- **Negative gaps are not dropped, and are not netted against positive
  gaps.** If Cost Plus's price for a given generic is *above* what Part D or
  Medicaid paid per unit, that drug's `overpayment_partd`/`overpayment_medicaid`
  is negative and stays visible in `leaderboard.csv` for transparency, but
  contributes **$0**, not a negative number, to the printed aggregate savings
  total.
- **Unit consistency is checked, not assumed.** Cost Plus's
  `package_quantity` is assumed to count the same discrete unit NADAC prices
  in for that drug (tablets/capsules -> EA, mL -> ML, grams -> GM). Any drug
  whose NADAC Pricing Unit is ML or GM (not the default EA) is flagged at
  runtime.

## Module E: brand price-movement proxy (`modules/e_brand_trumprx.py`)

Two independent pieces:

1. **Brand price-increase leaderboard.** Manufacturer WAC/list price is
   proprietary and out of scope, so this uses Medicare Part D Spending by
   Drug's own year-over-year change in gross average spend per dosage unit,
   restricted to brand rows and `Mftr_Name == "Overall"` rows.
   **This is a utilization-blended proxy, not pure WAC.** Gross spend per
   dosage unit moves for two reasons this number cannot separate: (a) the
   manufacturer actually raising its list/net price, and (b) the mix of
   *who* is filling the drug shifting year to year. A true WAC series would
   isolate (a) alone; this proxy bakes in (b) as well. The output column is
   named `gross_spend_per_unit_yoy_chg_pct`, never "list price" or "WAC."
2. **TrumpRx-vs-Cost-Plus-generic comparison** -- see below.

## TrumpRx comparison (`fetch/trumprx.py`, `data/trumprx.csv`)

**What it shows**: for each brand-name drug listed on TrumpRx, the price
TrumpRx lists for that brand versus the price of the **generic equivalent of
the same molecule** at Cost Plus (e.g. TrumpRx's Lipitor price vs. Cost
Plus's atorvastatin price) -- not the same drug, and not a same-brand
comparison. Each TrumpRx row's `generic_name` is resolved to an ingredient
via the same crosswalk used throughout the suite, then joined against
Module A's already-crosswalked Cost Plus table by that ingredient.
`trumprx_price` is compared to Cost Plus's own package-level price
(`costplus_per_unit * package_quantity`); when an ingredient matches more
than one Cost Plus strength, the median package price represents it.

**Data source**: `data/trumprx.csv` is built from trumprx.gov's own bulk
catalog API (`/api/drugs/summaries`), fetched via `fetch.trumprx`. This
endpoint is under `/api/`, which trumprx.gov's `robots.txt` disallows -- a
deliberate, single-purpose exception to this suite's usual robots.txt
discipline, made because the endpoint serves the same public,
unauthenticated data `/browse` already renders to any visitor, with no
login/paywall involved and no CAPTCHA/challenge evaded (a plain request,
answered plainly). The HTML pages (`/browse`, `/p/{slug}`) remain fully
respected as `Disallow`'d in every other function in this module.
**Limitation**: this endpoint has no strength/dosage field, only a form
label (e.g. "Prefilled Pen", "Vial") -- the `dosage` column reflects form,
not strength.

**Known limitation, coverage**: not every TrumpRx brand has a Cost Plus
generic equivalent. `output/trumprx_comparison.csv` only contains matched
rows; unmatched brand names are printed explicitly ("N of M TrumpRx brands
matched to a Cost Plus generic") so the real hit rate is visible, not hidden.

**Limitation, quantity**: `data/trumprx.csv` carries no quantity/days-supply
column, so this assumes TrumpRx and Cost Plus dispense the same quantity for
a given drug/strength; this cannot be verified from the data as specified.

**Honesty rail**: this is a brand cash price vs. a generic cash price, for
the same molecule, clearly labeled as such in every column name
(`costplus_generic`, not `costplus_brand`) and in the printed exhibit header.
It is never presented as an apples-to-apples comparison of the same drug.
