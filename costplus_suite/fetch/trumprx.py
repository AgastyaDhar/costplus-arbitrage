"""
TrumpRx listed-price loader.

trumprx.gov is a client-rendered app with no discovered public bulk-data feed
or API (confirmed by inspection: the static HTML ships no pricing data, only
JS bundles) -- unlike costplusdrugs.com, whose product pages DO expose real
prices in a parseable Product/Offer block (see shared/costplus_scraper.py),
trumprx.gov exposes nothing server-rendered to even attempt that against.
Its prices are therefore supplied as a hand-populated CSV at
data/trumprx.csv (columns: brand_name, generic_name, dosage, trumprx_price,
list_price), mirroring how shared/costplus.py treats data/costplus.csv.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

REQUIRED_COLUMNS = ["brand_name", "generic_name", "dosage", "trumprx_price", "list_price"]


def load_trumprx_prices(path: Path | None = None) -> pd.DataFrame:
    """Load and validate the TrumpRx price list.

    Raises FileNotFoundError if data/trumprx.csv hasn't been populated yet --
    modules.e_brand_trumprx.trumprx_comparison() catches this and skips the
    comparison cleanly rather than fabricating one.
    """
    path = path or (config.DATA_DIR / "trumprx.csv")
    if not path.exists():
        raise FileNotFoundError(
            f"TrumpRx price list not found at {path}. trumprx.gov has no public API or bulk "
            "data feed (see module docstring) -- supply this CSV by hand (columns: brand_name, "
            "generic_name, dosage, trumprx_price, list_price)."
        )

    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")

    for col in ("trumprx_price", "list_price"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "SAMPLE" in path.name.upper():
        print(
            "[fetch.trumprx] *** WARNING: loading data/trumprx.SAMPLE.csv -- fabricated placeholder "
            "prices, NOT real TrumpRx data. Every downstream number is for pipeline testing only. ***"
        )
        df.attrs["is_sample"] = True
    else:
        df.attrs["is_sample"] = False

    print(f"[fetch.trumprx] Loaded {len(df):,} TrumpRx price rows from {path}")
    return df
