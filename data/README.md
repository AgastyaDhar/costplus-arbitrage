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
