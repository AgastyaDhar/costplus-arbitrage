# Methodology

This suite quantifies the gap between what the US drug system pays (Medicare
Part D, Medicaid SDUD) and Cost Plus Drugs' transparent prices, using only
public data. This document lists every comparison made, its data source, its
known limitations, and how the code handles (or deliberately does not paper
over) each one.

## Governing principles

1. **Net prices are never estimated.** True net-of-rebate prices are not
   public. Every `net_per_unit` field in this suite's output is the literal
   string `"not public"`. This is intentional, not a missing feature.
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
   API or landing page, prints what it resolved, and caches the resolution to
   `cache/resolved_ids.json`.
6. **No proprietary or paid data, no scraping behind logins.**

## Data sources

### NADAC -- National Average Drug Acquisition Cost (`fetch/nadac.py`)
- **What it is**: CMS's weekly survey of retail community pharmacies'
  actual invoice costs. The closest public proxy to true acquisition cost.
- **Discovery**: queries `data.medicaid.gov`'s metastore API
  (`/api/1/metastore/schemas/dataset/items?fulltext=NADAC`), filters titles
  matching `NADAC (National Average Drug Acquisition Cost) <year>`, takes the
  max year. The weekly refresh date is parsed out of the download URL itself
  (e.g. `...-07-08-2026.csv`) since the dataset identifier stays constant for
  an entire calendar year and only the URL's embedded date changes week to
  week -- that date is what `shared/snapshots.py` keys weekly history on.
- **Limitation**: NADAC is a survey, not a census -- some low-volume NDCs
  have no reported invoice cost. NADAC's own "Corresponding Generic Drug
  NADAC Per Unit" is a different concept from a drug's own reported price and
  is not used here. NADAC is **not** net of any rebate; it approximates
  acquisition cost, not net cost, and is never relabeled "net" anywhere in
  this suite.
- **Code handling**: the raw file accumulates one row per NDC per weekly
  refresh within a calendar year; `fetch.nadac.load_nadac()` deduplicates to
  each NDC's most recent `Effective Date` before use.

### Medicare Part D Spending by Drug (`fetch/partd.py`)
- **What it is**: CMS's annual national aggregate of Part D gross spend, by
  brand + generic name (not NDC-level).
- **Discovery**: queries `data.cms.gov/data.json` (CMS's DCAT catalog) for the
  dataset titled exactly `"Medicare Part D Spending by Drug"`, then resolves
  the actual CSV via that dataset's `resourcesAPI`
  (`data.cms.gov/data-api/v1/dataset-resources/<uuid>`). Note: the catalog's
  own `distribution[].downloadURL` field currently contains a live templating
  bug (literal host `https://default`) -- the resourcesAPI path was used
  specifically because it returns fully-qualified URLs and sidesteps that bug.
- **Limitation, granularity mismatch**: Part D's `Gnrc_Name` aggregates
  spending across **every strength and dosage form** of that ingredient
  nationally (e.g. one "Atorvastatin Calcium" row sums 10mg + 20mg + 40mg +
  80mg together). Cost Plus and NADAC are strength-specific. Comparing a
  strength-specific per-unit price to an ingredient-wide weighted average
  assumes per-dosage-unit price is roughly flat across strengths for that
  drug, which is a reasonable but imperfect assumption for most oral
  generics, and worse for some.
- **Limitation, gross of rebates**: stated in CMS's own dataset description.
  This is exactly why `generics_only` gates headline numbers.
- **Limitation, no generic/brand flag**: the file has no explicit indicator.
  `fetch.partd.is_generic_row()` uses the field's own convention --
  unbranded generics list the identical string in `Brnd_Name` and
  `Gnrc_Name` -- to classify rows. This is CMS's own naming convention, not
  something invented here, but it is a heuristic: a real generic that
  happens to carry a distinct marketed brand-style name (observed for some
  levothyroxine listings during testing) will be misclassified as "brand"
  and excluded from generic totals. This is the **conservative** direction
  (undercounts overpayment rather than overstating it).
- **Limitation, "list price" substitution used in Modules B/C/E**: CMS does
  not publish a public WAC/list-price series (WAC is proprietary,
  First Databank/Medi-Span). Where the original spec asks for "list-price
  movement," this suite uses Part D's year-over-year change in gross average
  spend per dosage unit (`Chg_Avg_Spnd_Per_Dsg_Unt_<y-1>_<y>`) instead,
  labeled explicitly as `gross_spend_per_unit_yoy_chg`, annual (not
  quarterly -- no public quarterly retail price-change series exists either),
  and never called "list price" or "WAC" in any output column.
- **Code handling**: the CSV ships wide, one column trio
  (`Tot_Spndng_<year>`, `Tot_Dsg_Unts_<year>`, `Tot_Clms_<year>`) per
  calendar year. `fetch.partd.load_partd()` detects the latest year present
  from the column names via regex rather than assuming one, and renames to
  generic column names for downstream use.

### Medicaid State Drug Utilization Data / SDUD (`fetch/sdud.py`)
- **What it is**: CMS's quarterly, NDC-level, per-state Medicaid
  utilization and reimbursement.
- **Discovery**: same metastore pattern as NADAC --
  `"State Drug Utilization Data <year>"`, max year.
- **Scale**: the annual file is ~500MB / ~5M rows (every NDC x state x
  quarter x utilization-type combination nationally). `load_sdud()` never
  loads it in full; it streams in chunks and keeps only rows whose NDC is in
  a caller-supplied filter set (in practice, the NDCs already resolved
  through the crosswalk).
- **Quirk, verified by hand on real data**: CMS reports a synthetic
  `State == "XX"` row per NDC that is the **national rollup** (sum across all
  real states/territories), not a 51st jurisdiction. Verified by summing all
  non-XX states for a sample NDC (atorvastatin, NDC 60505257908) and finding
  it matched the XX row to within rounding (25,775,645 vs 25,777,460 units).
  `fetch.sdud.national_total()` and `.state_level()` split these apart
  explicitly so a national total is never computed by (incorrectly) summing
  real states on top of the XX row.
- **Suppression**: rows with `Suppression Used == true` (small-count privacy
  suppression) carry no usable units/amount and are dropped before
  aggregation.
- **Limitation, gross of rebates**: same caveat as Part D; also gated by
  `generics_only` at the Module A level via the crosswalk (SDUD itself is
  NDC-keyed, so it doesn't need Part D's brand/generic name heuristic -- it
  inherits the generic/brand status of the underlying Cost Plus catalog row).

### Cost Plus Drugs price list (`shared/costplus.py`)
- **Source**: a user-supplied CSV at `data/costplus.csv`
  (`drug, strength, form, package_quantity, acquisition_cost, markup,
  pharmacy_fee, shipping_fee`). **Not scraped in this phase** -- Cost Plus's
  own pricing pages are the only public source of this data, and scraping
  them was deliberately deferred (see "Deferred: Cost Plus / TrumpRx
  scraping" below) rather than silently built into the pipeline.
- **Formula**: `costplus_per_unit = (acquisition_cost * markup + pharmacy_fee)
  / package_quantity`. `shipping_fee` is never part of this formula -- it
  stays a separate column through the entire pipeline (HARD CONSTRAINT).
- **Sample data**: `data/costplus.SAMPLE.csv` contains ten drugs with
  round, fabricated acquisition costs, used only to exercise the pipeline
  end-to-end before real prices are supplied. Every function that loads it
  stamps `is_sample=True` on the returned frame's `.attrs`, and every
  printed/written output derived from it is prefixed with a
  `SAMPLE DATA -- NOT REAL` banner or a `SAMPLE_` output filename prefix.

### FDA Drug Shortages (`fetch/shortages.py`)
- **Source**: openFDA's public `drug/shortages.json` endpoint (no
  authentication required). `status == "Current"` is treated as an active
  shortage; `"To Be Discontinued"` and `"Resolved"` are historical.

### Medicare Part B ASP Pricing Files (`fetch/asp.py`)
- **What it is**: CMS's quarterly Average Sales Price payment-limit files
  (ASP + 6%), used to reimburse physicians/hospitals for physician-
  administered drugs, plus the companion NDC-HCPCS crosswalk.
- **Discovery**: these files are **not** in a metastore/DCAT catalog -- they
  are plain zip links on a landing page
  (`cms.gov/medicare/payment/part-b-drugs/asp-pricing-files`), whose *own
  URL* has moved before (the older `/medicare/payment/fee-schedules/drugs/...`
  path 404s as of this build). Discovery parses the current landing page's
  HTML for zip links matching the payment-limit/crosswalk naming pattern and
  takes the first (the page lists newest quarter first) -- never a hardcoded
  quarter, filename, or landing-page path assumption beyond "this is CMS's
  current URL as of today."
- **HARD LIMITATION, stated explicitly per the build spec's requirement**:
  ASP is a volume-weighted average of a manufacturer's quarterly net sales
  across all of that manufacturer's customers -- it already has rebate-like
  averaging baked into the number by statute, *before* CMS ever publishes it.
  This is fundamentally different from any single payer's or patient's net
  price. It is surfaced as `payment_limit` ("ASP-based payment limit"),
  never relabeled "net."
- **Unit safety**: a NDC's HCPCS billing unit (e.g. "10 MG") is frequently
  not the same unit NADAC prices that NDC in (e.g. some injectables price
  "EA" per vial in NADAC regardless of the vial's mg strength).
  `modules/f_oncology.py` only computes a direct `overcharge_per_billunit`
  figure when the HCPCS dosage unit and NADAC's Pricing Unit are confirmed
  identical (1:1); every other row reports both prices side by side, in
  their own labeled units, with the overcharge column left blank and a note
  explaining why -- never a silent division through mismatched units.
- **Scope note**: Cost Plus's retail catalog is predominantly self-
  administered oral generics; physician-administered oncology infusions are
  largely outside what a mail-order retail pharmacy carries. Running Module F
  against `data/costplus.csv` is expected to find zero or very few matches --
  that is a correct result of the drug universes not overlapping, not a bug.
  The module is designed to accept any oncology NDC list.

## Crosswalk methodology (`shared/crosswalk.py`)

Free-text drug descriptions ("atorvastatin 20 mg tablet") are resolved to
NDCs via:

1. `RxNav approximateTerm.json` -- fuzzy match to ranked candidate RxCUIs.
2. **Dispensable-TTY filtering**: RxNav's approximate match frequently ranks
   the bare ingredient+strength concept (RxNorm TTY `SCDC`, e.g.
   "atorvastatin 20 MG" -- no dose form, zero NDCs) above the actual
   dispensable clinical drug (TTY `SCD`, e.g. "atorvastatin 20 MG Oral
   Tablet" -- has an NDC set). Verified during Phase 0: for atorvastatin,
   RxCUI 597966 (SCDC) ranked #1 with zero NDCs; RxCUI 617310 (SCD) ranked #2
   and had 395 NDCs. `resolve_dispensable_rxcui()` walks the ranked list and
   takes the first candidate whose TTY is in `{SCD, SBD, GPCK, BPCK}`.
3. `RxNav rxcui/{rxcui}/ndcs.json` -- the NDC set for that dispensable drug.
4. NADAC lookup by NDC -- `NADAC Per Unit` + `Pricing Unit`, aggregated by
   **median** across all matched NDCs (robust to the occasional outlier
   package; verified on metformin 500mg, where a naive text search across
   the whole NADAC file turned up unrelated extended-release variants at
   ~20x the price, but the RxCUI's *actual* NDC set correctly excluded them
   and all 61 matched NDCs were immediate-release, median exactly matching
   two independently spot-checked rows).
5. **Ingredient-name resolution** (`get_ingredient_name`, `RxNav
   rxcui/{rxcui}/related.json?tty=IN`) strips a dispensable drug's RxCUI down
   to its bare ingredient (e.g. "atorvastatin"), used to join against Part D
   / SDUD's free-text generic name fields.
6. **Name normalization** (`normalize_drug_name`): uppercases and strips
   common salt/ester/formulation suffixes (HCL, SODIUM, POTASSIUM, ER, XR,
   etc.) so RxNorm's bare ingredient name and CMS's free-text `Gnrc_Name`
   (e.g. "Atorvastatin Calcium") converge to the same join key. This is a
   heuristic text match, not an authoritative crosswalk -- verified
   correct for the drugs tested, but not guaranteed exhaustive for every
   possible salt form or naming convention CMS uses.

Hand-verified against raw source rows during the Phase 0 gate (20/20 drugs
crosswalked, 100% match rate): atorvastatin 20mg tablet (0.02851/EA, exact
match on 2 independently spot-checked NDCs), metformin 500mg tablet
(0.01398/EA median, confirmed IR/ER disambiguation via RxCUI), warfarin
sodium 5mg tablet (0.0876/EA, uniform across 13 matched NDCs). These three
are pinned as regression fixtures in `tests/test_crosswalk_fixtures.py`.

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
  total (`report.print_aggregate_summary` floors each drug's contribution at
  zero and separately lists which drugs had a negative gap, so this doesn't
  silently net against real overpayment elsewhere).
- **Unit consistency is checked, not assumed.** Cost Plus's
  `package_quantity` is assumed to count the same discrete unit NADAC prices
  in for that drug (tablets/capsules -> EA, mL -> ML, grams -> GM); this is a
  structural assumption of the `data/costplus.csv` schema. Any drug whose
  NADAC Pricing Unit is ML or GM (not the default EA) is flagged at runtime
  so a human can confirm `package_quantity` for that row counts the right
  thing before trusting its `costplus_per_unit`.

## Deferred: Cost Plus / TrumpRx scraping

Per the build spec, Cost Plus prices are supplied as a CSV, not scraped, in
this phase -- Cost Plus's benchmark price is the yardstick the entire
analysis measures against, so an unattended, unreviewed HTML scraper feeding
it directly was deliberately avoided (fragile to markup changes, no versioned
distribution ID to cite as a source, unlike every CMS/FDA dataset this suite
uses). The same reasoning was applied to `fetch/trumprx.py`: trumprx.gov is a
client-rendered app with no static pricing data and no discovered public API;
that module is an explicit `NotImplementedError` stub rather than a
best-effort scraper, and Module E runs its brand price-increase leaderboard
without the TrumpRx comparison until this is deliberately wired up.

## Unresolved: `fetch/brooklyn.py`

The original project layout names a `fetch/brooklyn.py` alongside the other
fetch modules, but no dataset called "Brooklyn" is described anywhere else in
the build spec, and nothing by that name surfaced as a recognizable public
CMS/FDA/HHS drug-pricing dataset during Phase 1/2 research. Left as an
explicit stub rather than guessed at.

## Testing (`tests/`)

- `test_costplus_math.py` -- the per-unit formula, markup default,
  shipping-fee isolation (HARD CONSTRAINT), and sample-file flagging, against
  synthetic CSV fixtures.
- `test_normalization.py` -- NDC normalization (dash-stripping,
  zero-padding to match NADAC/RxNav's 11-digit format) and drug-name
  normalization (salt-suffix stripping so RxNav ingredient names and CMS's
  free-text generic names converge).
- `test_crosswalk_fixtures.py` -- regression-pins `crosswalk_drug()`'s
  aggregation math against the three drugs hand-verified in the Phase 0 gate
  (atorvastatin, metformin, warfarin), with RxNav network calls mocked to
  their known-correct values so the test is offline and deterministic; also
  covers the mixed-pricing-unit and no-match code paths.
- `test_module_a_math.py` -- gap/overpayment arithmetic against small
  synthetic DataFrames with hand-computed expected values; specifically
  checks that a per-manufacturer Part D breakdown row is never double-counted
  against its "Overall" row, and that SDUD's "XX" national row is never
  summed on top of real state rows. (This test suite caught a real bug during
  development: `attach_partd` originally trusted its caller to have
  pre-filtered to `Mftr_Name == "Overall"` and would silently double-count
  spend if given unfiltered input -- fixed to filter defensively itself.)

Run with: `python -m unittest discover -s tests`
