# Cost Plus Arbitrage Suite

## What this is

This tool compares Cost Plus Drugs' prices against what Medicare and
Medicaid actually paid for the same medications, using public federal data.
It found that across 2,024 generic drugs, the government overpaid an
estimated $51 billion per year versus Cost Plus prices. Every number is
sourced, auditable, and never estimated.

## How to run it

```
pip install -r requirements.txt
python run.py
```

## What you get

- A complete ranked list of all 2,024 drugs by overpayment gap
- A total dollar figure: estimated annual overpayment across all matched
  generics
- A comparison of brand-name drugs on TrumpRx vs their generic equivalent
  at Cost Plus

## Methodology

- Prices come from Cost Plus Drugs' own website
- Government spending comes from Medicare Part D and Medicaid public
  datasets, updated weekly
- Only generic drugs are included, because brand drug rebates are not public
- Net prices are never estimated or guessed

See `METHODOLOGY.md` for the full data-source and limitation writeup.
