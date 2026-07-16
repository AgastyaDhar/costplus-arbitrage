"""
Module I: unpriced-drug audit.

A drug can fail to reach the leaderboard for reasons that have nothing to
do with whether Cost Plus stocks it (see modules/h_catalog_gaps.py for
that bucket). This module isolates one specific such reason: the
crosswalk resolves the drug to a real, correctly-typed generic RxCUI and
finds its NDCs, but NADAC (a voluntary pharmacy survey, not a census --
see METHODOLOGY.md) currently has a price for none of them. Ribavirin is
the case that surfaced this during the catalog_gaps.csv audit (both of
its catalog products resolve cleanly to real SCDs with real NDCs, 16 and
4 respectively, but literally zero of those 20 NDCs have a NADAC row
right now) -- an external data-coverage gap, not a bug, and not evidence
Cost Plus doesn't sell the drug.

Distinguished here from two other, different reasons a catalog row can be
missing from the leaderboard:
  - crosswalk failure (no dispensable RxCUI found at all, or found but
    zero NDCs) -- not this bucket, see crosswalk_note for those.
  - generics_only brand exclusion (a_arbitrage._exclude_brand_rows) --
    not this bucket either; that's a confirmed brand product, priced or
    not.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import crosswalk, costplus as costplus_mod  # noqa: E402
from fetch import nadac as fetch_nadac  # noqa: E402


def run(costplus_path: Path | None = None, force_refresh: bool = False) -> pd.DataFrame:
    """Scans the *entire* raw Cost Plus catalog (not just a known
    candidate list) and returns one row per drug_term that crosswalks to
    a real, correctly-typed RxCUI with at least one NDC, but has zero
    NADAC-priced NDCs. Columns: drug_term, rxcui, tty, ndc_count."""
    # RUNNABLE, not the raw GRAPHQL.csv: package_quantity_status=="confirmed"
    # rows only -- the exact set Module A's own crosswalk operates on in
    # production (see run.py's --source graphql path), so this audit can
    # never report a row the real pipeline wouldn't have looked at anyway.
    costplus_path = costplus_path or (config.DATA_DIR / "costplus.GRAPHQL.RUNNABLE.csv")
    cp_df = costplus_mod.load_costplus(costplus_path)
    nadac_df = fetch_nadac.load_nadac(force_refresh=force_refresh)

    rows = []
    for _, cp_row in cp_df.iterrows():
        r = crosswalk.crosswalk_drug(cp_row["drug_term"], nadac_df)
        if r.rxcui is not None and r.ndc_count > 0 and r.matched_ndc_count == 0:
            rows.append({
                "drug_term": cp_row["drug_term"],
                "rxcui": r.rxcui,
                "tty": r.tty,
                "ndc_count": r.ndc_count,
            })

    out = pd.DataFrame(rows, columns=["drug_term", "rxcui", "tty", "ndc_count"])
    n_distinct = out["rxcui"].nunique() if not out.empty else 0
    print(f"[module_i] unpriced-drug audit: {len(out)} catalog row(s) / {n_distinct} distinct RxCUI(s) "
          f"resolve to a real, typed RxCUI with NDCs, but none of those NDCs have a current NADAC price")
    return out


if __name__ == "__main__":
    result = run()
    print(result.to_string(index=False))
