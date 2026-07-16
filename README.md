# Cost Plus Arbitrage Suite

## What this is

This tool compares Cost Plus Drugs' prices against what Medicare and
Medicaid actually paid for the same medications, using public federal data.
It found that across 2,012 generic drugs, the government overpaid an
estimated $50.8 billion per year versus Cost Plus prices. Every number is
sourced, auditable, and never estimated.

## How to run it

```
pip install -r requirements.txt
python run.py
```

## What you get

- A complete ranked list of all 2,012 drugs by overpayment gap
- A total dollar figure: estimated annual overpayment across all matched
  generics
- A comparison of brand-name drugs on TrumpRx vs their generic equivalent
  at Cost Plus (e.g. Azulfidine EN-tabs on TrumpRx: $130.80 vs. its
  generic, sulfasalazine, at Cost Plus: $12.42 -- a $118.38 gap)

## Output files

After running, results are written to the `costplus_suite/output/` folder:

- `costplus_suite/output/leaderboard.csv` — all 2,012 generic drugs ranked
  by total overpayment, with Cost Plus price, government price, gap per
  unit, and total dollars
- `costplus_suite/output/trumprx_comparison.csv` — brand-name drugs listed
  on TrumpRx compared to their generic equivalent at Cost Plus, sorted by
  gap

## Methodology

- Prices come from Cost Plus Drugs' own website
- Government spending comes from Medicare Part D and Medicaid public
  datasets, updated weekly
- Only generic drugs are included, because brand drug rebates are not public
- Net prices are never estimated or guessed

See `METHODOLOGY.md` for the full data-source and limitation writeup.

## License

- **Code** (everything under `costplus_suite/` except `costplus_suite/data/`,
  plus this repo's tooling generally) is licensed under the **MIT License**
  -- see `LICENSE`.
- **Data** (the output CSVs in `costplus_suite/output/`, the extraction/
  citation files in `costplus_suite/data/`, and the packaged deposit copy
  in `dataset/`) is licensed under **CC BY 4.0** -- see `data/LICENSE`.
  This does not relicense the underlying third-party sources (CMS, Cost
  Plus Drugs, RxNorm, government reports, court filings); see
  `dataset/PROVENANCE.md` for each source's own terms.
