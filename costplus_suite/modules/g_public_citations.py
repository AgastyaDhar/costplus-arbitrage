"""
Module G: public citation enrichment.

Attaches independently-sourced, hand-researched confirmed markup percentages
to the leaderboard by RxCUI, and computes an estimated PBM billing price from
each confirmed markup. Source data is `data/public_spreads_matched.csv` --
markup percentages extracted from named government/academic/litigation
sources (Maine MHDO, FTC, JAMA Health Forum, ERISA class-action complaints,
etc.; see costplus_suite/data/sources/ for the underlying PDFs), matched to
specific RxCUIs by hand during research.

This module does not change any arbitrage math -- overpayment_partd and
overpayment_medicaid are untouched. best_confirmed_spread/source and
estimated_pbm_price_per_unit are supplementary public-record corroboration,
clearly distinct from the modeled overpayment figures (see METHODOLOGY.md).

Every citation also carries a `source_type` (federal_study /
state_disclosure / peer_reviewed / litigation, set by hand per row in
public_spreads_matched.csv) -- these have different selection properties
(see the "Source types have different selection properties" caveat in
METHODOLOGY.md) and are never presented as equivalent: `best_confirmed_type`
picks the single highest value across all of them for the headline column,
but `all_confirmed_sources` lists every source's own figure so that
max-selection is visible rather than hidden.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

CITATIONS_PATH = config.ROOT_DIR / "data" / "public_spreads_matched.csv"

ESTIMATED_PBM_PRICE_BASIS = "Estimated from confirmed markup % — not a directly observed price"


def _format_value(value: float, spread_type: str) -> str:
    if spread_type == "spread_dollars":
        return f"${value:,.2f}"
    return f"{value:,.2f}%"


def _all_sources_string(rows: pd.DataFrame) -> str:
    """One '<value> (<source_type>, <source_name>, p.<page>)' entry per
    citation row for this RxCUI, highest value first, pipe-separated -- so a
    drug with citations from more than one source type (e.g. Abiraterone:
    Maine MHDO's 1,727.73% AND Lewandowski v. J&J's 6,391.86%) shows every
    figure, not just the max that wins best_confirmed_spread."""
    ordered = rows.sort_values(["confirmed_spread_value", "source_page"], ascending=[False, True])
    parts = [
        f"{_format_value(r.confirmed_spread_value, r.confirmed_spread_type)} "
        f"({r.source_type}, {r.source_name}, p.{int(r.source_page)})"
        for r in ordered.itertuples()
    ]
    return " | ".join(parts)


def load_citations(path: Path | None = None) -> pd.DataFrame:
    """One row per RxCUI: the strongest (highest) confirmed spread value
    documented in a named public source for that drug, regardless of which
    of the three confirmed_spread_type values it's expressed in
    (markup_pct, spread_pct, spread_dollars) -- a RxCUI with multiple rows
    across different types (e.g. RxCUI 309362/Clopidogrel bisulfate: JAMA
    Health Forum reports both a $8.59 spread_dollars figure AND a 70.2
    spread_pct figure for the same drug) simply keeps whichever single row
    has the largest raw confirmed_spread_value, tie-broken by source_page so
    the result never depends on the source file's row order.

    A single RxCUI can also have multiple rows of the SAME type (e.g. Maine
    MHDO reports a separate average payer-paid-as-%-of-WAC figure per
    manufacturer for the same drug) -- the same max-value rule applies.

    source_type carries the winning row's own type (federal_study /
    state_disclosure / peer_reviewed / litigation) alongside it, and
    all_confirmed_sources preserves every row (not just the winner) so a
    reader can see the full spread of figures across source types, not
    only the max.
    """
    path = path or CITATIONS_PATH
    df = pd.read_csv(path)
    best = (
        df.sort_values(["confirmed_spread_value", "source_page"], ascending=[False, True])
        .drop_duplicates(subset="rxcui", keep="first")
    )
    best["best_confirmed_source"] = best["source_name"] + ", p." + best["source_page"].astype(int).astype(str)

    all_sources = (
        df.groupby("rxcui", group_keys=False)
        .apply(_all_sources_string, include_groups=False)
        .rename("all_confirmed_sources")
    )
    best = best.merge(all_sources, on="rxcui", how="left")

    return best[[
        "rxcui", "confirmed_spread_value", "confirmed_spread_type", "best_confirmed_source",
        "source_type", "all_confirmed_sources",
    ]].rename(
        columns={"confirmed_spread_value": "best_confirmed_spread", "confirmed_spread_type": "best_confirmed_type"}
    )


def run(leaderboard: pd.DataFrame, citations_path: Path | None = None) -> pd.DataFrame:
    """Left-joins the citation columns onto `leaderboard` by rxcui.
    Leaderboard rows with no matching RxCUI in the citations file simply get
    NaN in all columns -- the vast majority of the catalog has no public
    per-drug markup citation, which is expected, not an error."""
    citations = load_citations(citations_path)
    # rxcui's dtype differs depending on the caller: the live pipeline's
    # leaderboard carries it as str (crosswalk.py stores RxNav's own string
    # ids), while pd.read_csv infers int64 from the plain-digit citations
    # file (and from a leaderboard.csv reloaded off disk). Join on a
    # string-normalized key so this never depends on either side's dtype,
    # without touching the real "rxcui" column's dtype in the output.
    left_key = leaderboard["rxcui"].astype(str)
    right = citations.assign(_rxcui_key=citations["rxcui"].astype(str)).drop(columns=["rxcui"])
    out = leaderboard.assign(_rxcui_key=left_key).merge(
        right, on="_rxcui_key", how="left", validate="many_to_one"
    ).drop(columns=["_rxcui_key"])

    # estimated_pbm_price_per_unit only has a defensible formula for
    # markup_pct citations (nadac_per_unit * (1 + markup_pct/100), a markup
    # OVER acquisition cost). spread_pct (intermediary profit as a % of
    # total spend) and spread_dollars (a flat per-claim dollar figure) are
    # different units with no equivalent per-unit-price formula -- a row
    # whose best citation is one of those types keeps its
    # best_confirmed_spread/source (real, sourced) but no estimated price.
    is_markup_pct = out["best_confirmed_type"] == "markup_pct"
    out["estimated_pbm_price_per_unit"] = pd.NA
    out.loc[is_markup_pct, "estimated_pbm_price_per_unit"] = (
        out.loc[is_markup_pct, "nadac_per_unit"] * (1 + out.loc[is_markup_pct, "best_confirmed_spread"] / 100.0)
    )
    out["estimated_pbm_price_per_unit"] = pd.to_numeric(out["estimated_pbm_price_per_unit"])
    out["estimated_pbm_price_basis"] = out["estimated_pbm_price_per_unit"].notna().map(
        {True: ESTIMATED_PBM_PRICE_BASIS, False: None}
    )

    n = out["best_confirmed_spread"].notna().sum()
    print(f"[module_g] Public citations: {n} leaderboard row(s) matched a confirmed public markup % "
          f"({citations['rxcui'].nunique()} distinct RxCUIs in {CITATIONS_PATH.name})")
    return out
