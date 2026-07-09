"""
Weekly NADAC snapshot history + spread-widening diff.

NADAC is the only weekly-refreshed data source in this suite (Part D is
annual, SDUD is quarterly, Cost Plus's list is static until you update it).
So "is the system's gap versus Cost Plus widening week over week" reduces to:
is NADAC's own per-unit acquisition cost moving, while Cost Plus's price sits
still? That's the signal this module tracks.

Definition used throughout (documented here so it's unambiguous downstream):
  gap_nadac(t)    = costplus_per_unit - nadac_per_unit(t)
  gap_change      = gap_nadac(t) - gap_nadac(t_prev)
  "widening"      = gap_change < 0, i.e. NADAC acquisition cost rose (or Cost
                    Plus's fixed price fell further behind) since the prior
                    snapshot -- Cost Plus's margin over true acquisition cost
                    is shrinking, or the market's underlying cost is rising
                    out from under a flat consumer price.

Each fetch.nadac.load_nadac() call already downloads to a dated cache file
(cache/nadac/nadac_<snapshot_date>.csv, one per weekly refresh). This module
additionally writes a small per-NDC extract to cache/nadac_snapshots/ on every
run, purely so multiple historical weeks can be diffed without re-parsing the
full ~800k-row raw file each time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

SNAPSHOT_DIR = config.CACHE_DIR / "nadac_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def save_snapshot(nadac_df: pd.DataFrame, snapshot_date: str) -> Path:
    """Persist a lightweight (ndc, nadac_per_unit, pricing_unit) extract for
    this snapshot date so future runs can diff against it without re-fetching
    or re-parsing the full raw NADAC file."""
    dest = SNAPSHOT_DIR / f"{snapshot_date}.csv"
    if not dest.exists():
        out = nadac_df[["nadac_per_unit", "pricing_unit", "ndc_description"]].reset_index()
        out.to_csv(dest, index=False)
    return dest


def list_snapshot_dates() -> list[str]:
    return sorted(p.stem for p in SNAPSHOT_DIR.glob("*.csv"))


def load_snapshot(snapshot_date: str) -> pd.DataFrame:
    path = SNAPSHOT_DIR / f"{snapshot_date}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No cached snapshot for {snapshot_date} at {path}")
    df = pd.read_csv(path, dtype={"NDC": str})
    return df.set_index("NDC")


def most_recent_prior_snapshot(current_date: str) -> str | None:
    """The latest cached snapshot date strictly before current_date, or None
    if this is the first snapshot ever taken (nothing to diff against yet)."""
    prior = [d for d in list_snapshot_dates() if d < current_date]
    return max(prior) if prior else None


def compute_spread_changes(
    drug_level: pd.DataFrame,
    current_date: str,
    previous_date: str | None = None,
) -> pd.DataFrame:
    """Given a drug-level table (one row per resolved drug, indexed the same
    way modules/a_arbitrage.py builds its leaderboard: columns drug_term,
    rxcui, nadac_per_unit, costplus_per_unit, pricing_unit), diff the current
    NADAC per-unit price against the most recent prior snapshot and flag
    drugs whose gap vs Cost Plus is widening.

    Returns an empty frame (with a printed note) if there is no prior
    snapshot yet -- that's expected on the very first run of the week.
    """
    if previous_date is None:
        previous_date = most_recent_prior_snapshot(current_date)
    if previous_date is None:
        print(
            "[shared.snapshots] No prior NADAC snapshot found -- this looks like "
            "the first run. spread_changes.csv will be empty until a second "
            "weekly snapshot exists to diff against."
        )
        return pd.DataFrame(
            columns=[
                "drug_term",
                "rxcui",
                "nadac_per_unit_current",
                "nadac_per_unit_previous",
                "nadac_pct_change",
                "gap_nadac_current",
                "gap_nadac_previous",
                "gap_change",
                "widening",
                "current_date",
                "previous_date",
            ]
        )

    prior = load_snapshot(previous_date)
    prior_by_rxcui_ndc = prior["nadac_per_unit"]

    rows = []
    for _, row in drug_level.iterrows():
        ndcs = row.get("matched_ndcs") or []
        prior_vals = prior_by_rxcui_ndc.reindex(ndcs).dropna()
        if prior_vals.empty:
            continue
        prev_per_unit = float(prior_vals.median())
        curr_per_unit = row["nadac_per_unit"]
        if pd.isna(curr_per_unit) or prev_per_unit == 0:
            continue

        costplus_per_unit = row.get("costplus_per_unit")
        gap_curr = costplus_per_unit - curr_per_unit if pd.notna(costplus_per_unit) else None
        gap_prev = costplus_per_unit - prev_per_unit if pd.notna(costplus_per_unit) else None
        gap_change = (gap_curr - gap_prev) if (gap_curr is not None and gap_prev is not None) else None

        rows.append(
            {
                "drug_term": row["drug_term"],
                "rxcui": row.get("rxcui"),
                "nadac_per_unit_current": curr_per_unit,
                "nadac_per_unit_previous": prev_per_unit,
                "nadac_pct_change": (curr_per_unit - prev_per_unit) / prev_per_unit,
                "gap_nadac_current": gap_curr,
                "gap_nadac_previous": gap_prev,
                "gap_change": gap_change,
                "widening": (gap_change is not None and gap_change < 0),
                "current_date": current_date,
                "previous_date": previous_date,
            }
        )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("nadac_pct_change", ascending=False)
    print(
        f"[shared.snapshots] Diffed {current_date} vs {previous_date}: "
        f"{len(out)} drugs comparable, {int(out['widening'].sum()) if not out.empty else 0} widening"
    )
    return out
