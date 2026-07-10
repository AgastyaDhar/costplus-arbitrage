# data/

- `costplus.GRAPHQL.csv` -- **committed, real data.** The default source
  `python run.py` uses automatically. Fetched from costplusdrugs.com's own
  GraphQL storefront API via `fetch/costplus_graphql.py` -- see METHODOLOGY.md.
  Re-fetch a current snapshot with `python -m fetch.costplus_graphql`
  (run from `costplus_suite/`).

- `costplus.GRAPHQL.RUNNABLE.csv` -- **generated, not committed.** Written by
  `run.py` each run: the subset of `costplus.GRAPHQL.csv` with a confirmed
  `package_quantity`, which Module A actually runs against.

- `trumprx.csv` -- **committed, real data.** Fetched from trumprx.gov's own
  bulk catalog API (`/api/drugs/summaries`) via `fetch/trumprx.py` -- see
  METHODOLOGY.md for why this is a deliberate, single-purpose exception to
  the site's robots.txt. Re-fetch with `python -m fetch.trumprx` (run from
  `costplus_suite/`).

- `costplus.csv` -- **not included in this repo, and not used by default.**
  An advanced/internal path (`run.py --source csv`) for supplying your own
  Cost Plus price list instead of the committed GraphQL catalog. Schema:
  `drug, strength, form, package_quantity, acquisition_cost, markup, pharmacy_fee, shipping_fee`.

- `costplus.SAMPLE.csv` -- **fabricated placeholder data**, ten drugs with
  round, made-up acquisition costs. It exists only so the pipeline can be
  exercised end-to-end and its plumbing verified without live data. Any
  output produced from this file is clearly labeled
  `[SAMPLE DATA -- NOT REAL COST PLUS PRICES]` by the code that reads it.
  Never treat numbers derived from this file as real, and never rename it to
  `costplus.csv`.

- `costplus.SCRAPED.csv` -- **generated, not committed.** Written by
  `fetch/costplus_html_scraper.py` (`run.py --source scrape`), an older,
  lower-coverage HTML-scrape path superseded by `costplus.GRAPHQL.csv` --
  see METHODOLOGY.md for why the GraphQL path is preferred.

- `trumprx.SAMPLE.csv` -- **fabricated placeholder data**, six brand/generic
  pairs with round, made-up prices, used only to exercise Module E's TrumpRx
  comparison end-to-end. Loaded via `run.py --sample`; labeled
  `SAMPLE DATA -- NOT REAL` by the code that reads it.
