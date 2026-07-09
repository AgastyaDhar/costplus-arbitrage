"""
Module E: brand and TrumpRx (toggle: config.MODULES_ENABLED["e_brand_trumprx"]).

Two pieces:
  1. Brand list-price increase leaderboard -- same caveat as Modules B/C:
     "list price" here is Part D's gross average-spend-per-dosage-unit YoY
     change for BRAND rows (Brnd_Name != Gnrc_Name), not manufacturer WAC
     (proprietary, out of scope). Labeled accordingly.
  2. TrumpRx-listed-price vs Cost Plus comparison for overlapping drugs --
     NOT IMPLEMENTED. fetch.trumprx has no working data source (see its
     docstring); this piece raises/skips cleanly rather than fabricating
     numbers against a benchmark we can't actually verify.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from fetch import partd as fetch_partd, trumprx as fetch_trumprx  # noqa: E402


def brand_price_increase_leaderboard(top_n: int = 25) -> pd.DataFrame:
    info = fetch_partd.discover_latest_partd()
    csv_path = config.CACHE_DIR / "partd" / f"partd_{info['dataset_identifier'][:8]}.csv"
    header = pd.read_csv(csv_path, nrows=0).columns
    chg_cols = [c for c in header if re.match(r"^Chg_Avg_Spnd_Per_Dsg_Unt_\d{2}_\d{2}$", c)]
    if not chg_cols:
        print("[module_e] No YoY change column available in Part D file")
        return pd.DataFrame(columns=["Brnd_Name", "Gnrc_Name", "chg_pct"])
    chg_col = sorted(chg_cols)[-1]

    df = pd.read_csv(csv_path, usecols=["Brnd_Name", "Gnrc_Name", "Mftr_Name", chg_col])
    df = df[df["Mftr_Name"] == "Overall"].dropna(subset=[chg_col])
    df = df[~fetch_partd.is_generic_row(df)]  # brand rows only: Brnd_Name != Gnrc_Name
    df = df.rename(columns={chg_col: "gross_spend_per_unit_yoy_chg_pct"})
    df = df.sort_values("gross_spend_per_unit_yoy_chg_pct", ascending=False).head(top_n)
    print(f"[module_e] Built brand price-increase leaderboard (top {top_n}, column={chg_col})")
    return df[["Brnd_Name", "Gnrc_Name", "gross_spend_per_unit_yoy_chg_pct"]]


def trumprx_comparison(cp_df: pd.DataFrame) -> pd.DataFrame | None:
    try:
        fetch_trumprx.load_trumprx_prices()
    except NotImplementedError as e:
        print(f"[module_e] Skipping TrumpRx comparison: {e}")
        return None


def run(costplus_path: Path | None = None) -> dict:
    from shared import costplus as costplus_mod  # local import, avoids Module A's crosswalk cost when unused

    cp_df = costplus_mod.load_costplus(costplus_path)
    leaderboard = brand_price_increase_leaderboard()
    trumprx = trumprx_comparison(cp_df)
    return {"brand_leaderboard": leaderboard, "trumprx_comparison": trumprx}
