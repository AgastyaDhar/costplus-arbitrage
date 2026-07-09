"""
Module E: brand and TrumpRx (toggle: config.MODULES_ENABLED["e_brand_trumprx"]).

Two independent pieces (see METHODOLOGY.md for the full writeup of each):
  1. Brand price-increase leaderboard -- a UTILIZATION-BLENDED PROXY for
     brand price movement, not pure WAC/list price (which is proprietary,
     First Databank/Medi-Span, out of scope). It's Medicare Part D's own
     year-over-year change in gross average spend per dosage unit, for BRAND
     rows only (Brnd_Name != Gnrc_Name), Mftr_Name == "Overall" only (the
     same defensive filter Module A's attach_partd applies, so per-
     manufacturer rows are never double-counted on top of "Overall"). The
     computation itself is a pure function (_compute_brand_leaderboard) over
     an in-memory DataFrame so it's unit-testable without a network call.
  2. TrumpRx-listed-price vs Cost Plus generic price comparison -- joins each
     TrumpRx brand row (data/trumprx.csv, or data/trumprx.SCRAPED.csv via
     fetch.trumprx's live scraper) to its Cost Plus generic equivalent via
     the Phase 0 crosswalk (shared.crosswalk), and outputs the headline
     brand_name/dosage/trumprx_price/costplus_generic_price/gap/gap_pct
     exhibit, sorted by gap descending. Skips cleanly (returns None) if
     no TrumpRx price list has been populated yet. Prints an explicit
     brand-level coverage report (matched count and the unmatched brand
     list) so the crosswalk's real hit rate is visible, not just the rows
     that happened to match.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import crosswalk  # noqa: E402
from fetch import partd as fetch_partd, trumprx as fetch_trumprx  # noqa: E402

YOY_CHG_COL_RE = re.compile(r"^Chg_Avg_Spnd_Per_Dsg_Unt_\d{2}_\d{2}$")


def _compute_brand_leaderboard(partd_raw: pd.DataFrame, chg_col: str, top_n: int = 25) -> pd.DataFrame:
    """Pure function over Part D's raw columns (Brnd_Name, Gnrc_Name,
    Mftr_Name, <chg_col>) -- no file I/O, so it's directly unit-testable.
    Enforces Mftr_Name == 'Overall' defensively (mirrors modules.a_arbitrage.
    attach_partd) and brand-only rows (excludes Brnd_Name == Gnrc_Name)."""
    df = partd_raw[partd_raw["Mftr_Name"] == "Overall"].dropna(subset=[chg_col]).copy()
    df = df[~fetch_partd.is_generic_row(df)]  # brand rows only: Brnd_Name != Gnrc_Name
    df = df.rename(columns={chg_col: "gross_spend_per_unit_yoy_chg_pct"})
    df = df.sort_values("gross_spend_per_unit_yoy_chg_pct", ascending=False).head(top_n)
    return df[["Brnd_Name", "Gnrc_Name", "gross_spend_per_unit_yoy_chg_pct"]].reset_index(drop=True)


def brand_price_increase_leaderboard(top_n: int = 25) -> pd.DataFrame:
    info = fetch_partd.discover_latest_partd()
    csv_path = config.CACHE_DIR / "partd" / f"partd_{info['dataset_identifier'][:8]}.csv"
    header = pd.read_csv(csv_path, nrows=0).columns
    chg_cols = [c for c in header if YOY_CHG_COL_RE.match(c)]
    if not chg_cols:
        print("[module_e] No YoY change column available in Part D file")
        return pd.DataFrame(columns=["Brnd_Name", "Gnrc_Name", "gross_spend_per_unit_yoy_chg_pct"])
    chg_col = sorted(chg_cols)[-1]

    raw = pd.read_csv(csv_path, usecols=["Brnd_Name", "Gnrc_Name", "Mftr_Name", chg_col])
    leaderboard = _compute_brand_leaderboard(raw, chg_col, top_n=top_n)
    print(f"[module_e] Built brand price-increase leaderboard (top {top_n}, column={chg_col}, "
          "utilization-blended proxy -- see METHODOLOGY.md)")
    return leaderboard


# ---------------------------------------------------------------------------
# TrumpRx vs Cost Plus generic comparison -- the headline exhibit.
# ---------------------------------------------------------------------------
def _ingredient_norm_for_term(term: str) -> str | None:
    """Resolve a free-text drug term to the same normalized-ingredient join
    key used throughout the suite (shared.crosswalk.normalize_drug_name),
    via the untouched Phase 0 RxNav resolution. Returns None if RxNav has no
    dispensable match for the term -- never a guessed ingredient."""
    resolved = crosswalk.resolve_dispensable_rxcui(term)
    if resolved is None:
        return None
    ingredient = crosswalk.get_ingredient_name(resolved["rxcui"])
    return crosswalk.normalize_drug_name(ingredient or term)


def _join_trumprx_to_costplus(trumprx_with_ingredient: pd.DataFrame, costplus_with_ingredient: pd.DataFrame) -> pd.DataFrame:
    """Pure join/math over two already-ingredient-normalized DataFrames --
    directly unit-testable without any crosswalk/network calls.
      trumprx_with_ingredient: brand_name, dosage, trumprx_price, ingredient_norm
      costplus_with_ingredient: costplus_per_unit, package_quantity, ingredient_norm
    costplus_generic_price is a PACKAGE-level price (costplus_per_unit *
    package_quantity, both already-validated existing fields -- not a new
    estimate) so it's on the same "price per fill" basis trumprx_price is
    presumably on. See METHODOLOGY.md for the quantity-matching caveat this
    implies (trumprx.csv carries no quantity column of its own).
    """
    cp = costplus_with_ingredient.copy()
    cp["costplus_generic_price"] = cp["costplus_per_unit"] * cp["package_quantity"]
    # A drug can have several Cost Plus strengths under one ingredient (the
    # same granularity mismatch documented for Part D in METHODOLOGY.md); take
    # the median package price across them as the representative generic price.
    cp_by_ingredient = cp.groupby("ingredient_norm", as_index=False)["costplus_generic_price"].median()

    merged = trumprx_with_ingredient.merge(cp_by_ingredient, on="ingredient_norm", how="inner")
    merged["gap"] = merged["trumprx_price"] - merged["costplus_generic_price"]
    merged["gap_pct"] = (merged["gap"] / merged["trumprx_price"]) * 100
    merged = merged.sort_values("gap", ascending=False).reset_index(drop=True)
    return merged[["brand_name", "dosage", "trumprx_price", "costplus_generic_price", "gap", "gap_pct"]]


def trumprx_comparison(cp_df: pd.DataFrame, trumprx_path: Path | None = None) -> pd.DataFrame | None:
    try:
        trumprx_df = fetch_trumprx.load_trumprx_prices(trumprx_path)
    except FileNotFoundError as e:
        print(f"[module_e] Skipping TrumpRx comparison: {e}")
        return None

    trumprx_df = trumprx_df.copy()
    all_brands = sorted(trumprx_df["brand_name"].dropna().unique())

    trumprx_df["ingredient_norm"] = [
        _ingredient_norm_for_term(f"{g} {d}")
        for g, d in zip(trumprx_df["generic_name"], trumprx_df["dosage"])
    ]
    usable = trumprx_df.dropna(subset=["ingredient_norm"])

    cp = cp_df.copy()
    cp["ingredient_norm"] = [
        _ingredient_norm_for_term(term) or crosswalk.normalize_drug_name(drug)
        for term, drug in zip(cp["drug_term"], cp["drug"])
    ]

    comparison = _join_trumprx_to_costplus(usable, cp)

    matched_brands = set(comparison["brand_name"])
    unmatched_brands = [b for b in all_brands if b not in matched_brands]
    print(
        f"[module_e] TrumpRx-to-Cost-Plus-generic coverage: {len(matched_brands)}/{len(all_brands)} "
        "brand(s) matched a Cost Plus generic via the Phase 0 crosswalk"
    )
    if unmatched_brands:
        print(f"[module_e] Unmatched ({len(unmatched_brands)}): {', '.join(unmatched_brands)}")

    return comparison


def run(costplus_path: Path | None = None, trumprx_path: Path | None = None) -> dict:
    from shared import costplus as costplus_mod  # local import, avoids Module A's crosswalk cost when unused

    cp_df = costplus_mod.load_costplus(costplus_path)
    leaderboard = brand_price_increase_leaderboard()
    trumprx = trumprx_comparison(cp_df, trumprx_path)
    return {"brand_leaderboard": leaderboard, "trumprx_comparison": trumprx}
