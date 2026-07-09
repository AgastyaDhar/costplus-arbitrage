"""
Module B: intelligence digest (toggle: config.MODULES_ENABLED["b_intelligence"]).

Weekly plain-text digest covering three signals:
  1. New NADAC entries this week vs the most recent prior snapshot.
  2. FDA drug shortages (openFDA, status=='Current') cross-referenced against
     the Cost Plus catalog by ingredient name.
  3. Large price moves on drugs Cost Plus does NOT carry.

Limitation on (3), stated plainly rather than silently substituted: the spec
asks for "single-quarter list-price moves," but CMS does not publish a public,
quarterly, retail list-price (WAC) change series -- WAC/list price itself is
proprietary (First Databank / Medi-Span). The best public proxy at any
sub-annual cadence is Medicare Part D's own year-over-year change in gross
average spend per dosage unit (Chg_Avg_Spnd_Per_Dsg_Unt_<y-1>_<y>), which is
annual, not quarterly, and is a gross reimbursement change, not a WAC change.
We use that, label it exactly as what it is, and do not call it "list price."
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import crosswalk, snapshots, costplus as costplus_mod  # noqa: E402
from fetch import nadac as fetch_nadac, partd as fetch_partd, shortages as fetch_shortages  # noqa: E402

BIG_MOVE_THRESHOLD = 0.20  # 20%+ YoY change in gross avg spend per unit


def new_nadac_entries(current_date: str) -> pd.DataFrame:
    prior_date = snapshots.most_recent_prior_snapshot(current_date)
    if prior_date is None:
        print("[module_b] No prior NADAC snapshot to diff against yet.")
        return pd.DataFrame(columns=["ndc", "ndc_description", "nadac_per_unit", "pricing_unit"])
    current = snapshots.load_snapshot(current_date)
    prior = snapshots.load_snapshot(prior_date)
    new_ndcs = current.index.difference(prior.index)
    out = current.loc[new_ndcs].reset_index().rename(columns={"index": "ndc", "NDC": "ndc"})
    print(f"[module_b] {len(out)} new NDC(s) entered NADAC between {prior_date} and {current_date}")
    return out


def shortages_on_catalog(cp_df: pd.DataFrame) -> pd.DataFrame:
    shortages_df = fetch_shortages.load_shortages(active_only=True)
    shortages_df["ingredient_norm"] = shortages_df["generic_name"].map(crosswalk.normalize_drug_name)

    cp_ingredients = {crosswalk.normalize_drug_name(d) for d in cp_df["drug"]}
    hits = shortages_df[shortages_df["ingredient_norm"].isin(cp_ingredients)]
    print(f"[module_b] {len(hits)} active FDA shortage record(s) overlap the Cost Plus catalog")
    return hits[["generic_name", "status", "dosage_form", "therapeutic_category", "update_date"]]


def big_movers_not_on_costplus(cp_df: pd.DataFrame) -> pd.DataFrame:
    partd_df = fetch_partd.load_partd()
    year = partd_df.attrs.get("data_year")
    chg_col_candidates = [c for c in partd_df.columns if c.startswith("Chg_Avg_Spnd_Per_Dsg_Unt")]
    # Chg_* isn't in our usecols by default; reload with it included.
    import fetch.partd as _fp  # local import to reuse discovery/cache without re-deriving path

    info = _fp.discover_latest_partd()
    csv_path = config.CACHE_DIR / "partd" / f"partd_{info['dataset_identifier'][:8]}.csv"
    header = pd.read_csv(csv_path, nrows=0).columns
    chg_cols = [c for c in header if re.match(r"^Chg_Avg_Spnd_Per_Dsg_Unt_\d{2}_\d{2}$", c)]
    if not chg_cols:
        print("[module_b] No YoY change column found in Part D file; skipping big-movers signal")
        return pd.DataFrame(columns=["Brnd_Name", "Gnrc_Name", "chg_pct"])
    chg_col = sorted(chg_cols)[-1]

    raw = pd.read_csv(csv_path, usecols=["Brnd_Name", "Gnrc_Name", "Mftr_Name", chg_col])
    raw = raw[raw["Mftr_Name"] == "Overall"].dropna(subset=[chg_col])
    raw["ingredient_norm"] = raw["Gnrc_Name"].map(crosswalk.normalize_drug_name)

    cp_ingredients = {crosswalk.normalize_drug_name(d) for d in cp_df["drug"]}
    not_on_costplus = raw[~raw["ingredient_norm"].isin(cp_ingredients)]
    movers = not_on_costplus[not_on_costplus[chg_col].abs() >= BIG_MOVE_THRESHOLD]
    movers = movers.sort_values(chg_col, ascending=False).rename(columns={chg_col: "chg_pct"})
    print(
        f"[module_b] {len(movers)} drug(s) not on Cost Plus with >= {BIG_MOVE_THRESHOLD:.0%} "
        f"YoY gross spend/unit change ({year - 1} -> {year})"
    )
    return movers[["Brnd_Name", "Gnrc_Name", "chg_pct"]].head(25)


def run(costplus_path: Path | None = None) -> str:
    cp_df = costplus_mod.load_costplus(costplus_path)
    nadac_df = fetch_nadac.load_nadac()
    snapshot_date = nadac_df.attrs["snapshot_date"]
    snapshots.save_snapshot(nadac_df, snapshot_date)

    new_entries = new_nadac_entries(snapshot_date)
    shortage_hits = shortages_on_catalog(cp_df)
    movers = big_movers_not_on_costplus(cp_df)

    lines = [
        f"COST PLUS ARBITRAGE INTELLIGENCE DIGEST -- {snapshot_date}",
        "=" * 60,
        "",
        f"1. NEW NADAC ENTRIES ({len(new_entries)})",
        "-" * 40,
    ]
    if new_entries.empty:
        lines.append("  (none, or no prior snapshot to diff against)")
    else:
        for _, r in new_entries.head(25).iterrows():
            lines.append(f"  {r['ndc']}  {r['ndc_description']}  ${r['nadac_per_unit']:.5f}/{r['pricing_unit']}")

    lines += ["", f"2. ACTIVE FDA SHORTAGES OVERLAPPING COST PLUS CATALOG ({len(shortage_hits)})", "-" * 40]
    if shortage_hits.empty:
        lines.append("  (none)")
    else:
        for _, r in shortage_hits.iterrows():
            lines.append(f"  {r['generic_name']} -- {r['dosage_form']} -- updated {r['update_date']}")

    lines += [
        "",
        f"3. LARGE YoY GROSS SPEND/UNIT MOVERS NOT ON COST PLUS ({len(movers)})",
        "   (Part D gross reimbursement change, NOT a WAC/list-price series -- see module docstring)",
        "-" * 40,
    ]
    if movers.empty:
        lines.append("  (none above threshold)")
    else:
        for _, r in movers.iterrows():
            lines.append(f"  {r['Gnrc_Name']} ({r['Brnd_Name']}): {r['chg_pct']:+.1%}")

    digest = "\n".join(lines)
    print("\n" + digest)
    return digest
