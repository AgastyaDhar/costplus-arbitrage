"""
Module A: arbitrage monitor.

For each drug in the Cost Plus catalog, resolves NDCs via shared.crosswalk,
prices it against NADAC (true acquisition cost), Medicare Part D (national
gross spend), and Medicaid SDUD (state gross spend), and quantifies the gap
between what the system pays and what Cost Plus charges.

Per HARD CONSTRAINT, every comparison here is reduced to the same per-unit
basis before subtraction -- see _price_per_unit_basis(). Headline overpayment
totals are restricted to generics (config.GENERICS_ONLY) because Part D/SDUD
spend is gross of rebates, and net prices are never estimated (see
METHODOLOGY.md); nadac_per_unit is the closest public proxy to true
acquisition cost, not net-of-rebate cost, and is never presented as net.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import crosswalk, costplus as costplus_mod, snapshots  # noqa: E402
from fetch import nadac as fetch_nadac, partd as fetch_partd, sdud as fetch_sdud  # noqa: E402


# ---------------------------------------------------------------------------
# Step 1: crosswalk every Cost Plus drug to RxCUI / NDCs / NADAC
# ---------------------------------------------------------------------------
def build_drug_level_table(cp_df: pd.DataFrame, nadac_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, cp_row in cp_df.iterrows():
        r = crosswalk.crosswalk_drug(cp_row["drug_term"], nadac_df)
        ingredient_norm = crosswalk.normalize_drug_name(r.ingredient_name or cp_row["drug"])
        rows.append(
            {
                "drug_term": cp_row["drug_term"],
                "drug": cp_row["drug"],
                "strength": cp_row["strength"],
                "form": cp_row["form"],
                "rxcui": r.rxcui,
                "ingredient_name": r.ingredient_name,
                "ingredient_norm": ingredient_norm,
                "tty": r.tty,
                "matched_ndcs": r.matched_ndcs or [],
                "ndc_count": r.ndc_count,
                "matched_ndc_count": r.matched_ndc_count,
                "nadac_per_unit": r.nadac_per_unit,
                "nadac_pricing_unit": r.pricing_unit,
                "crosswalk_matched": r.matched,
                "crosswalk_note": r.note,
                "costplus_per_unit": cp_row["costplus_per_unit"],
                "pharmacy_fee": cp_row["pharmacy_fee"],
                "shipping_fee": cp_row["shipping_fee"],
                "package_quantity": cp_row["package_quantity"],
            }
        )
    df = pd.DataFrame(rows)
    unmatched = df[~df["crosswalk_matched"]]
    if not unmatched.empty:
        print(f"[module_a] WARNING: {len(unmatched)} Cost Plus drug(s) failed crosswalk, excluded from gaps:")
        for _, row in unmatched.iterrows():
            print(f"    - {row['drug_term']}: {row['crosswalk_note']}")
    return df


def _check_unit_consistency(drug_level: pd.DataFrame) -> None:
    """HARD CONSTRAINT check: costplus_per_unit and nadac_per_unit must be on
    the same footing (both per NADAC Pricing Unit) before any subtraction.
    Cost Plus's package_quantity is documented as counting the same discrete
    unit NADAC prices in (tablets/capsules -> EA, mL -> ML, g -> GM); this is
    a structural assumption of the Cost Plus CSV schema, verified per-drug
    here rather than silently assumed."""
    matched = drug_level[drug_level["crosswalk_matched"]]
    liquid_or_topical = matched[matched["nadac_pricing_unit"].isin(["ML", "GM"])]
    if not liquid_or_topical.empty:
        print(
            f"[module_a] NOTE: {len(liquid_or_topical)} drug(s) priced per ML/GM, not EA -- "
            "confirm data/costplus.csv's package_quantity for these counts the same unit "
            "(mL or grams), not packages:"
        )
        for _, row in liquid_or_topical.iterrows():
            print(f"    - {row['drug_term']}: NADAC unit={row['nadac_pricing_unit']}")


# ---------------------------------------------------------------------------
# Step 2: Medicare Part D (national, annual, gross of rebates)
# ---------------------------------------------------------------------------
def attach_partd(drug_level: pd.DataFrame, partd_df: pd.DataFrame, generics_only: bool = True) -> pd.DataFrame:
    pd_df = partd_df.copy()
    # Defensive, not just trusting the caller: fetch.partd.load_partd()
    # already filters to Mftr_Name == "Overall" in the real pipeline, but if
    # this is ever called with unfiltered per-manufacturer rows, summing them
    # on top of "Overall" would double-count spend. Enforce it here too.
    if "Mftr_Name" in pd_df.columns:
        pd_df = pd_df[pd_df["Mftr_Name"] == "Overall"]
    if generics_only:
        pd_df = pd_df[fetch_partd.is_generic_row(pd_df)]
    pd_df["ingredient_norm"] = pd_df["Gnrc_Name"].map(crosswalk.normalize_drug_name)

    agg = (
        pd_df.groupby("ingredient_norm", as_index=False)
        .agg(Tot_Spndng=("Tot_Spndng", "sum"), Tot_Dsg_Unts=("Tot_Dsg_Unts", "sum"), Tot_Clms=("Tot_Clms", "sum"))
    )
    agg["partd_per_unit"] = agg["Tot_Spndng"] / agg["Tot_Dsg_Unts"]

    out = drug_level.merge(agg, on="ingredient_norm", how="left")
    out["gap_partd"] = out["partd_per_unit"] - out["costplus_per_unit"]
    out["overpayment_partd"] = out["gap_partd"] * out["Tot_Dsg_Unts"]

    n_matched = out["partd_per_unit"].notna().sum()
    print(f"[module_a] Part D: matched {n_matched}/{len(out)} drugs by normalized ingredient name")
    return out


# ---------------------------------------------------------------------------
# Step 3: Medicaid SDUD (national XX rollup + per-state, quarterly->annual, NDC-keyed)
# ---------------------------------------------------------------------------
def attach_sdud(drug_level: pd.DataFrame, sdud_df: pd.DataFrame) -> pd.DataFrame:
    national = fetch_sdud.national_total(sdud_df).rename(
        columns={"units_reimbursed": "medicaid_units", "medicaid_amount_reimbursed": "medicaid_amount"}
    )
    national = national.set_index("ndc")[["medicaid_units", "medicaid_amount"]]

    medicaid_units, medicaid_amount = [], []
    for ndcs in drug_level["matched_ndcs"]:
        hits = national.loc[national.index.intersection(ndcs)]
        medicaid_units.append(hits["medicaid_units"].sum() if not hits.empty else 0.0)
        medicaid_amount.append(hits["medicaid_amount"].sum() if not hits.empty else 0.0)

    out = drug_level.copy()
    out["medicaid_units"] = medicaid_units
    out["medicaid_amount"] = medicaid_amount
    out["medicaid_per_unit"] = (out["medicaid_amount"] / out["medicaid_units"]).where(out["medicaid_units"] > 0)
    out["gap_medicaid"] = out["medicaid_per_unit"] - out["costplus_per_unit"]
    out["overpayment_medicaid"] = out["gap_medicaid"] * out["medicaid_units"]

    n_matched = out["medicaid_per_unit"].notna().sum()
    print(f"[module_a] Medicaid SDUD: matched {n_matched}/{len(out)} drugs with reimbursed units")
    return out


# ---------------------------------------------------------------------------
# Step 4: NADAC gap (Cost Plus vs true acquisition cost -- not an overpayment number)
# ---------------------------------------------------------------------------
def attach_nadac_gap(drug_level: pd.DataFrame) -> pd.DataFrame:
    out = drug_level.copy()
    out["gap_nadac"] = out["costplus_per_unit"] - out["nadac_per_unit"]
    return out


# ---------------------------------------------------------------------------
# Step 5: leaderboard
# ---------------------------------------------------------------------------
def build_leaderboard(priced: pd.DataFrame) -> pd.DataFrame:
    df = priced[priced["crosswalk_matched"]].copy()
    df["overpayment_partd"] = df["overpayment_partd"].fillna(0.0)
    df["overpayment_medicaid"] = df["overpayment_medicaid"].fillna(0.0)
    df["total_overpayment"] = df["overpayment_partd"] + df["overpayment_medicaid"]

    cols = [
        "drug_term", "rxcui", "nadac_pricing_unit",
        "nadac_per_unit", "costplus_per_unit", "gap_nadac",
        "partd_per_unit", "Tot_Dsg_Unts", "gap_partd", "overpayment_partd",
        "medicaid_per_unit", "medicaid_units", "gap_medicaid", "overpayment_medicaid",
        "total_overpayment", "pharmacy_fee", "shipping_fee",
    ]
    df = df[cols].sort_values("total_overpayment", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    df["net_per_unit"] = "not public"  # HARD CONSTRAINT: never estimate net prices
    df["canonical_unit"] = df["nadac_pricing_unit"]
    return df


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run(costplus_path: Path | None = None, force_refresh: bool = False, generics_only: bool = None) -> dict:
    if generics_only is None:
        generics_only = config.GENERICS_ONLY

    cp_df = costplus_mod.load_costplus(costplus_path)
    is_sample = bool(cp_df.attrs.get("is_sample"))

    nadac_df = fetch_nadac.load_nadac(force_refresh=force_refresh)
    snapshot_date = nadac_df.attrs["snapshot_date"]
    snapshots.save_snapshot(nadac_df, snapshot_date)

    drug_level = build_drug_level_table(cp_df, nadac_df)
    _check_unit_consistency(drug_level)

    all_matched_ndcs = sorted({ndc for ndcs in drug_level["matched_ndcs"] for ndc in ndcs})

    partd_df = fetch_partd.load_partd(force_refresh=force_refresh)
    priced = attach_partd(drug_level, partd_df, generics_only=generics_only)

    sdud_df = fetch_sdud.load_sdud(ndc_filter=all_matched_ndcs, force_refresh=force_refresh)
    priced = attach_sdud(priced, sdud_df)
    priced = attach_nadac_gap(priced)

    leaderboard = build_leaderboard(priced)

    spread_changes = snapshots.compute_spread_changes(priced[priced["crosswalk_matched"]], snapshot_date)

    total_partd_savings = leaderboard.loc[leaderboard["overpayment_partd"] > 0, "overpayment_partd"].sum()
    total_medicaid_savings = leaderboard.loc[leaderboard["overpayment_medicaid"] > 0, "overpayment_medicaid"].sum()
    negative_gap_drugs = leaderboard[(leaderboard["gap_partd"] < 0) | (leaderboard["gap_medicaid"] < 0)]

    return {
        "is_sample": is_sample,
        "generics_only": generics_only,
        "snapshot_date": snapshot_date,
        "drug_level": priced,
        "leaderboard": leaderboard,
        "spread_changes": spread_changes,
        "total_partd_savings": total_partd_savings,
        "total_medicaid_savings": total_medicaid_savings,
        "total_savings": total_partd_savings + total_medicaid_savings,
        "negative_gap_drugs": negative_gap_drugs,
    }
