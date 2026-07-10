# data/

- `costplus.csv` -- **not included in this repo.** Supply your own Cost Plus
  Drugs price list here before running the real pipeline. Schema:
  `drug, strength, form, package_quantity, acquisition_cost, markup, pharmacy_fee, shipping_fee`.
  `run.py` and `modules/a_arbitrage.py` refuse to run without this file present
  (they will not silently fall back to the sample file).

- `costplus.SAMPLE.csv` -- **fabricated placeholder data**, ten drugs with
  round, made-up acquisition costs. It exists only so the pipeline can be
  exercised end-to-end and its plumbing verified before real Cost Plus prices
  are available. Any output produced from this file is clearly labeled
  `[SAMPLE DATA -- NOT REAL COST PLUS PRICES]` by the code that reads it.
  Never treat numbers derived from this file as real, and never rename it to
  `costplus.csv`.

- `costplus.SCRAPED.csv` -- **generated, not committed.** Written by
  `fetch/costplus_html_scraper.py` (`run.py --source scrape`) refreshing whatever
  is currently in `costplus.csv`/`costplus.SAMPLE.csv` against live
  costplusdrugs.com data. `acquisition_cost`/`markup`/`pharmacy_fee`/
  `package_quantity` are carried over from the input file unchanged (the site
  doesn't expose them -- see METHODOLOGY.md); review before promoting any of
  it into `costplus.csv` by hand.

- `trumprx.csv` -- **not included in this repo.** Supply your own TrumpRx
  price list here to enable the TrumpRx-vs-Cost-Plus-generic comparison in
  Module E. Schema: `brand_name, generic_name, dosage, trumprx_price,
  list_price`. trumprx.gov has no public API or bulk-data feed, so this is
  populated by hand from the site, same as `costplus.csv` originally was.

- `trumprx.SAMPLE.csv` -- **fabricated placeholder data**, six brand/generic
  pairs with round, made-up prices, used only to exercise Module E's TrumpRx
  comparison end-to-end. Loaded via `run.py --sample`; labeled
  `SAMPLE DATA -- NOT REAL` by the code that reads it.
