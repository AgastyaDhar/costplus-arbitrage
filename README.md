# Cost Plus Arbitrage Suite

Quantifies the gap between what the US drug system pays (Medicare Part D,
Medicaid SDUD) and Cost Plus Drugs' transparent prices, using only public data.

## Data sources

- **NADAC** -- CMS's National Average Drug Acquisition Cost survey (proxy for true acquisition cost)
- **Medicare Part D Spending by Drug** -- CMS's annual national gross-spend aggregate
- **Medicaid State Drug Utilization Data (SDUD)** -- CMS's quarterly, NDC-level utilization and reimbursement
- **Cost Plus Drugs GraphQL API** -- costplusdrugs.com's public storefront API (catalog, package sizes, prices)

All datasets are discovered at runtime (no hardcoded resource IDs, since NADAC/Part D/SDUD identifiers rotate) -- see `METHODOLOGY.md` for the full discovery/limitation writeup per source.

## Running it

Real Cost Plus prices come from the site's own GraphQL storefront API (see
`costplus_suite/fetch/costplus_graphql.py`), not a hand-supplied CSV.

```
pip install -r requirements.txt      # pandas + stdlib; see costplus_suite for imports
cd costplus_suite
python run.py --source graphql       # runs the full pipeline against the committed real catalog
```

`data/costplus.GRAPHQL.csv` is committed as a point-in-time catalog fetch.
`--source graphql` filters it to rows with a confirmed `package_quantity`,
writes the result to `data/costplus.GRAPHQL.RUNNABLE.csv`, and runs Module A
(plus any enabled Phase 2 modules) against that.

To refresh the catalog with a current snapshot before running (e.g. prices
have moved since the committed one):

```
python -m fetch.costplus_graphql     # re-fetches -> data/costplus.GRAPHQL.csv
python run.py --source graphql
```

There's also `--source scrape`, which drives Module A off an HTML page-scrape
(`data/costplus.SCRAPED.csv`) instead of the GraphQL API -- see
`fetch/costplus_html_scraper.py` and its module docstring for why the GraphQL
path is preferred (it confirms `package_quantity` for the whole catalog
rather than recovering it from free-text page fields).

Plain `python run.py` (no `--source`) instead loads `data/costplus.csv` as-is
and refuses to run unless that file is present. This is for supplying your
own price list (schema documented in `data/README.md`) rather than using this
repo's own scraped/GraphQL data.

## Headline finding

Running the pipeline against the full Cost Plus catalog (2,386 drugs, 88.1%
successfully crosswalked to NDCs/NADAC) and restricting to generics yields
**2,024 generic drugs** with a computed price gap, and a combined
**$51.0B/year estimated overpayment** (Medicare Part D: $45.7B, Medicaid SDUD: $5.3B)
versus what Cost Plus charges for the same drugs.

## Methodology note

Headline numbers are **generics only** -- Part D/Medicaid spend is gross of
manufacturer rebates, and generic rebates are small and mostly settled at the
point of sale, so gross spend is a defensible proxy for generics but would
materially overstate brand "overpayment." **Net-of-rebate prices are never
estimated or guessed** -- every `net_per_unit` field in this suite's output is
the literal string `"not public"`. See `METHODOLOGY.md` for
the full set of governing principles and per-source limitations.
