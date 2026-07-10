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

```
pip install -r requirements.txt      # pandas + stdlib; see costplus_suite for imports
cd costplus_suite
python run.py --sample               # fabricated placeholder data, exercises the full pipeline
```

To run against real data, drop your own Cost Plus price list at `data/costplus.csv`
(schema documented in `data/README.md`) and run:

```
python run.py
```

`run.py` refuses to run against real data unless `data/costplus.csv` is present --
it will never silently fall back to the sample file.

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
