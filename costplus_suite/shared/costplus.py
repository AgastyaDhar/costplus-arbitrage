"""
Cost Plus Drugs price list loader.

Cost Plus's prices are supplied as a CSV at data/costplus.csv (columns: drug,
strength, form, package_quantity, acquisition_cost, markup, pharmacy_fee,
shipping_fee) -- this is never scraped in Phase 1; see METHODOLOGY.md for why.

Per-unit math (config.COSTPLUS_MARKUP defaults to Cost Plus's published 15%):
    costplus_per_unit = (acquisition_cost * markup + pharmacy_fee) / package_quantity

shipping_fee is deliberately NOT part of that per-unit price -- it is a
flat, order-level fee, not a per-dose acquisition cost, so folding it in would
misrepresent the unit price on comparisons. It stays its own column all the
way through the pipeline (HARD CONSTRAINT).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

REQUIRED_COLUMNS = [
    "drug",
    "strength",
    "form",
    "package_quantity",
    "acquisition_cost",
    "markup",
    "pharmacy_fee",
    "shipping_fee",
]


def load_costplus(path: Path | None = None) -> pd.DataFrame:
    """Load and validate the Cost Plus price list, compute costplus_per_unit.

    Returns a DataFrame with all input columns plus:
      - drug_term: "<drug> <strength> <form>", the free-text query fed to the
        crosswalk (shared.crosswalk.crosswalk_drug) to resolve NDCs/NADAC.
      - costplus_per_unit: acquisition_cost*markup + pharmacy_fee, divided by
        package_quantity. This is a per-unit price in whatever unit
        package_quantity counts in (tablets, mL, g) -- the caller is
        responsible for confirming that unit matches the NADAC Pricing Unit
        for the same drug before comparing the two (see modules/a_arbitrage.py).
      - shipping_fee stays a separate column, untouched, never folded into
        costplus_per_unit.
    """
    path = path or (config.DATA_DIR / "costplus.csv")
    if not path.exists():
        raise FileNotFoundError(
            f"Cost Plus price list not found at {path}. This suite does not "
            "scrape Cost Plus's site in Phase 1 -- supply the CSV yourself "
            "(columns: drug, strength, form, package_quantity, "
            "acquisition_cost, markup, pharmacy_fee, shipping_fee)."
        )

    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"data/costplus.csv is missing required columns: {missing}")

    for col in ("package_quantity", "acquisition_cost", "markup", "pharmacy_fee", "shipping_fee"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    bad_rows = df[df["package_quantity"].isna() | (df["package_quantity"] <= 0)]
    if not bad_rows.empty:
        raise ValueError(
            "data/costplus.csv has rows with missing/non-positive package_quantity, "
            f"can't compute a per-unit price:\n{bad_rows[['drug', 'strength', 'form']]}"
        )

    df["markup"] = df["markup"].fillna(config.COSTPLUS_MARKUP)
    df["pharmacy_fee"] = df["pharmacy_fee"].fillna(0.0)
    df["shipping_fee"] = df["shipping_fee"].fillna(0.0)

    df["drug_term"] = (
        df["drug"].str.strip() + " " + df["strength"].str.strip() + " " + df["form"].str.strip()
    )
    df["costplus_per_unit"] = (
        df["acquisition_cost"] * df["markup"] + df["pharmacy_fee"]
    ) / df["package_quantity"]

    if "SAMPLE" in path.name.upper():
        print(
            "[shared.costplus] *** WARNING: loading data/costplus.SAMPLE.csv -- "
            "fabricated placeholder prices, NOT real Cost Plus data. Every "
            "downstream number is for pipeline testing only. ***"
        )
        df.attrs["is_sample"] = True
    else:
        df.attrs["is_sample"] = False

    print(f"[shared.costplus] Loaded {len(df):,} Cost Plus price rows from {path}")
    return df
