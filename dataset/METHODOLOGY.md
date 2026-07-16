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
- **Dollarization is done once per molecule, not once per strength.**
  `Tot_Dsg_Unts`/`Tot_Spndng` are a single national total per molecule (see
  the granularity mismatch above), so multiplying that same national figure
  by each strength's own gap and summing across every strength row of the
  molecule would count the national total once per strength (a real bug:
  Atorvastatin's 4 strengths each carried the full national unit count,
  overcounting its overpayment ~4x; 269 molecules were affected, inflating
  the Part D total from a corrected $13.1B to a reported $45.6B). Fixed in
  `modules/a_arbitrage.attach_partd()` by choosing exactly one representative
  row per molecule to carry the dollar figure: **the strength with the
  highest `costplus_per_unit`** (minimizes the gap, so the molecule's
  overpayment is a conservative floor, never inflated). No public data
  gives a real strength-mix to weight by, so this is a deliberate,
  documented choice, not an estimate of the true weighted average -- every
  other strength row of that molecule contributes exactly $0 to
  `overpayment_partd`, flagged via `is_partd_molecule_row` /
  `partd_molecule_n_strengths` / `costplus_per_unit_partd_molecule` in
  `drug_level`/`leaderboard.csv` for transparency. Per-strength `gap_partd`
  (the per-unit price gap, never multiplied into a dollar figure) stays
  correct and visible on every row regardless.
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
   name fields. For a combination product, RxNav relates the RxCUI to
   multiple IN (ingredient) concepts; every one is collected, deduped, and
   joined (e.g. "amlodipine/atorvastatin"), not just the first, so a combo
   forms its own molecule key and never collides with a single-ingredient
   drug of the same first component.
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
gap_partd         = partd_per_unit - costplus_per_unit        (per-strength, informational; see below)

# overpayment_partd is dollarized ONCE PER MOLECULE, not once per strength --
# see "Dollarization is done once per molecule" above. costplus_per_unit_partd_molecule
# is the highest costplus_per_unit among that molecule's strengths (conservative:
# minimizes the gap). Every strength row of a molecule shares the same
# partd_per_unit/Tot_Dsg_Unts/costplus_per_unit_partd_molecule, but only the ONE
# row where costplus_per_unit == costplus_per_unit_partd_molecule
# (is_partd_molecule_row == True) gets a nonzero overpayment_partd -- every
# other strength row of that molecule is exactly 0.
gap_partd_molecule = partd_per_unit - costplus_per_unit_partd_molecule
overpayment_partd  = gap_partd_molecule * Tot_Dsg_Unts   (only on the is_partd_molecule_row == True row; 0 elsewhere)

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

## Public citation enrichment (`modules/g_public_citations.py`)

`best_confirmed_spread`/`best_confirmed_source`/`estimated_pbm_price_per_unit`
on `leaderboard.csv` come from `data/public_spreads_matched.csv` --
independently-sourced, hand-researched confirmed markup percentages from
named government/academic reports and, as of the litigation and 46brooklyn
citation extraction pass, two ERISA fiduciary-breach complaints (*Lewandowski
v. Johnson & Johnson*, D.N.J. 1:24-cv-00671; *Navarro v. Wells Fargo & Co.*,
D. Minn. 0:24-cv-03043) that plead drug-level NADAC-vs-PBM-billed-price
tables as factual allegations. Every row is matched to a specific RxCUI by
hand or, where the source table doesn't state a strength, only promoted to
`public_spreads_matched.csv` when the molecule has exactly one strength in
the leaderboard, the source states an explicit strength, or the source's
implied per-unit price is within 10% of the leaderboard's NADAC for that
strength -- never guessed. This enrichment never touches the arbitrage math
(`overpayment_partd`/`overpayment_medicaid`); it is supplementary public-
record corroboration, clearly distinct from the modeled overpayment figures.

Source types have different selection properties. Federal studies (FTC)
sample across a PBM's book of business. Litigation exhibits are selected by
plaintiffs' counsel as evidence and are not a representative sample -- they
systematically skew toward the largest markups. Where multiple sources cite
the same drug, this pipeline reports the highest confirmed figure, which
favors litigation sources. Both figures are shown where available.

Every citation carries a `source_type` -- `federal_study` (FTC interim
reports), `state_disclosure` (Maine MHDO), `peer_reviewed` (JAMA Mattingly),
or `litigation` (the J&J and Wells Fargo ERISA complaints) -- surfaced as its
own column on `leaderboard.csv` and its own color-coded column in
`PBM_Markup_Analysis.xlsx` (navy / dark green / purple / dark red
respectively) so a reader isn't left to assume a federal industry study and
a plaintiff's litigation exhibit carry the same evidentiary weight. Where a
drug has citations from more than one source type, `all_confirmed_sources`
lists every one of them (not just the max that wins `best_confirmed_spread`)
-- e.g. Abiraterone shows both Maine MHDO's 1,727.73% and Lewandowski v.
J&J's 6,391.86%, making the max-selection visible rather than hidden.

### `output/catalog_gaps.csv` -- Drugs with a confirmed public markup that Cost Plus does not currently carry (`modules/h_catalog_gaps.py`)

**Generated, not static.** This file used to be hand-built during the
litigation-disambiguation pass, and it was wrong for 3 of the 5 drugs an
external audit checked (Glatiramer, Mycophenolic Acid, and Ribavirin are
all actually present in the raw Cost Plus catalog) -- it was never
re-derived after later crosswalk fixes changed what could resolve, so it
silently drifted out of sync with reality. `modules/h_catalog_gaps.py`
removes that entire class of staleness: on every run, it takes every
`markup_pct`-type citation in `data/public_spreads.csv` (every source:
FTC, Maine MHDO, the litigation complaints), extracts an ingredient
search key and any brand name per drug, and does a live substring search
against the **raw** Cost Plus catalog (`data/costplus.GRAPHQL.csv`'s
`drug` and `brand_name` columns -- not the leaderboard, which already
excludes brand rows and unpriced rows for unrelated reasons; see
`modules/i_unpriced_drugs.py` below). A drug is only ever listed here if
that search comes back completely empty. Different sources naming the
same molecule differently (FTC's `"Octreotide (Sandostatin)
Injectable"` vs. litigation's `"Octreotide Acetate"`) are consolidated
to one row by search key, not left as separate near-duplicate rows.

As of the regeneration, 7 drugs qualify: Daptomycin, Enoxaparin Sodium,
Fondaparinux Sodium, Octreotide Acetate, Pregabalin, Sofosbuvir/
Velpatasvir, and Vigabatrin. Three of these (Daptomycin, Pregabalin,
Vigabatrin) were never previously flagged at all -- the hand-built file
only ever checked a specific 8-drug candidate list assembled during the
litigation pass, not every citation in `public_spreads.csv`; the
regenerated version checks all of them.

Where more than one source cites the same genuine gap drug, their
figures sit side by side in per-source `<tag>_markup_pct` columns (e.g.
`jj_markup_pct`, `ftc_markup_pct`) on the same row -- e.g. a federal
industry study and plaintiff litigation, sources with opposite selection
biases (see the source-type caveat above), independently confirming the
same drug is absent from the catalog is a genuine, source-independent
fact, not an artifact of who happened to go looking.

### `output/unpriced_drugs.csv` -- Drugs Cost Plus carries and the crosswalk resolves, but NADAC has no current price for (`modules/i_unpriced_drugs.py`)

A third, distinct bucket, added alongside the catalog_gaps.csv fix:
during that audit, Ribavirin turned out to crosswalk perfectly (a real,
correctly-typed SCD, 16 and 4 real NDCs for its two catalog products)
but have **zero** NADAC-priced NDCs among all 20 -- not a catalog gap
(Cost Plus sells it) and not a crosswalk bug (the RxCUI is right), just
an external data-coverage gap: NADAC is a voluntary pharmacy survey, not
a census (see the NADAC section above), and it simply has no current
acquisition-cost data for this drug right now. `modules/i_unpriced_drugs.py`
scans the *entire* catalog (not just a known candidate) for this same
pattern -- resolved to a real, typed RxCUI, at least one NDC found, but
zero of those NDCs priced -- and Ribavirin is far from alone: **204
catalog rows / 191 distinct RxCUIs** currently fall into this bucket.

### Salt/ester synonym fix in the token-overlap check (`shared/crosswalk.py`)

A second bug the catalog_gaps.csv audit surfaced: `_has_token_overlap()`
rejected a real rank-1 SCD match for Cost Plus's "Mycophenolate Sodium"
because RxNorm's canonical name for the same substance is "mycophenolic
acid" -- `"mycophenolATE"` and `"mycophenolIC"` share zero literal
tokens. Two changes, both scoped to the token-overlap relevance check
only (not `normalize_drug_name`'s separate salt-suffix list used for the
Part D/SDUD join key, left untouched): `_SALT_QUALIFIER_WORDS` (sodium,
sulfate, mesylate, hydrochloride, tartrate, fumarate, acetate, etc.) are
now excluded from the required-token set, the same way dosage-form words
already were, so a bare salt word alone can never drive a false-positive
match; and `_ester_acid_stem()` tries an ester/free-acid stem match
(`"mycophenolate"`/`"mycophenolic"` both reduce to `"mycophenol"`) as a
fallback when the literal token doesn't appear. The `tirzepatide`/
`dulaglutide`/azithromycin regression (neither word ends in `-ate` or
`-ic`) and the `amlodipine`/`atorvastatin` combo-collision regression are
both re-verified passing after this change (`tests/test_crosswalk_ftc_name_format.py`).

### FTC name-format crosswalk fix (`shared/crosswalk.py`)

The FTC Second Interim Staff Report's 35 named specialty generics are
written `"Generic (Brand) Form"` with no strength (e.g. `"Imatinib
(Gleevec) Pill"`). Two independent problems kept every one of them out of
`public_spreads_matched.csv` until this fix:

1. **Brand-name pollution.** `resolve_dispensable_rxcui()` used to accept
   the first RxNav candidate whose TTY was merely *a* dispensable type
   (`SCD/SBD/GPCK/BPCK`), in rank order. A brand name in the query text
   (`"Gleevec"`) reliably makes RxNav's fuzzy match rank the **branded**
   concept (SBD) above the **generic** one (SCD) -- a real, validly-typed
   RxCUI, just one the leaderboard (built entirely from Cost Plus's own
   generic catalog) can never join to. Fixed two ways: `strip_brand_and_form()`
   strips the parenthetical and a trailing low-signal dose-form word
   (`"Pill"`/`"Oral"`/`"Tablet"`) as a fallback query when the raw term
   doesn't resolve to a generic type, and `_TTY_PREFERENCE` makes
   `resolve_dispensable_rxcui()` prefer SCD > GPCK > SBD > BPCK among
   candidates that pass the token-overlap check, falling through to a
   branded type only when no generic candidate exists anywhere in the
   ranked results.
2. **Search depth.** A real SCD can rank as low as position 21 for a
   strength-less query (RxNav ranks strength-less concepts like `"imatinib
   Pill"` (SCDG) above any specific-strength one when there's no strength
   in the query to match against) -- `max_candidates_to_check` was raised
   from 15 to 30 to reach it.

**This does not fully solve ingredient-level resolution.** For some
drugs (Imatinib, Everolimus, Efavirenz, and 25 others of the 35), RxNav's
`approximateTerm` genuinely has no strength-specific generic candidate to
offer for a bare ingredient name -- no amount of query-text massaging
finds one, because the API needs a strength to produce one. Those cases
fall back to the same disambiguation discipline as the litigation pass
(sole leaderboard candidate for the ingredient / an explicit strength
stated in the source / never guessed among multiple strengths) -- see
`tests/test_crosswalk_ftc_name_format.py` and the FTC diagnosis for the
full 35-drug breakdown. Of the 30 FTC drugs Cost Plus's catalog actually
carries, 2 clear that bar (Dalfampridine, Teriparatide); the other 28 stay
unmatched rather than assigned to an arbitrary strength.
