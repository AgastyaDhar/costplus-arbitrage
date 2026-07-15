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


BRAND_TTYS = {"SBD", "BPCK"}  # RxNorm term types for branded dispensable drugs


def _exclude_brand_rows(drug_level: pd.DataFrame, generics_only: bool) -> pd.DataFrame:
    """HARD CONSTRAINT enforcement point: when generics_only, no brand drug's
    NDCs may reach EITHER the Part D or the Medicaid gap computation.

    fetch.partd.is_generic_row() only filters Part D's own spend table by its
    Brnd_Name==Gnrc_Name convention -- it says nothing about which CostPlus
    catalog rows get compared in the first place, and attach_sdud has no
    brand/generic awareness of its own at all (SDUD is NDC-keyed with no
    brand flag). A brand product sitting in the Cost Plus catalog (e.g.
    Eliquis, RxNorm TTY=SBD) would otherwise sail straight through the
    Medicaid side and land in a "generics only" leaderboard uncontested --
    caught during real-catalog testing, where it contributed a $480M+ single
    -drug figure to a nominally generics-only total.

    Reuses the crosswalk's own RxNorm TTY (already resolved for every row,
    not a separate signal to trust) -- SBD/BPCK are branded dispensable
    drugs. Rows with a confirmed brand TTY are marked unmatched (empty
    matched_ndcs, crosswalk_matched=False) so they fall out of every
    downstream computation via the same path an ordinary crosswalk miss
    already does, rather than needing a second filter applied consistently
    everywhere.
    """
    if not generics_only:
        return drug_level
    out = drug_level.copy()
    is_brand = out["tty"].isin(BRAND_TTYS)
    n_excluded = int(is_brand.sum())
    if n_excluded:
        print(f"[module_a] generics_only=True: excluding {n_excluded} confirmed-brand drug(s) "
              f"(RxNorm TTY in {BRAND_TTYS}) from Part D and Medicaid gap computation:")
        for _, row in out[is_brand].iterrows():
            print(f"    - {row['drug_term']} (tty={row['tty']})")
    out.loc[is_brand, "matched_ndcs"] = out.loc[is_brand, "matched_ndcs"].apply(lambda _: [])
    out.loc[is_brand, "crosswalk_matched"] = False
    out.loc[is_brand, "crosswalk_note"] = "excluded: confirmed brand drug (generics_only=True)"
    return out


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
    # Per-strength per-unit gap: THIS strength's Cost Plus price against
    # Part D's national ingredient-wide average price. Legitimate at any
    # granularity (see METHODOLOGY.md's granularity-mismatch note) --
    # informational only, never itself multiplied into a dollar figure below.
    out["gap_partd"] = out["partd_per_unit"] - out["costplus_per_unit"]

    # --- Option A: dollarize Part D overpayment once per molecule --------
    # Part D's Tot_Dsg_Unts/Tot_Spndng are already a molecule-wide (every
    # strength combined) national total in the CMS source. The old code
    # multiplied that single national figure by each strength's own gap and
    # merged the result onto every strength row of the molecule -- summing
    # overpayment_partd across the leaderboard then counted the same
    # national unit total once per strength (e.g. a 4-strength molecule's
    # overpayment counted 4x; see METHODOLOGY.md). Fixed by choosing exactly
    # one representative row per molecule to carry the dollarized figure:
    # the strength with the HIGHEST costplus_per_unit, which minimizes the
    # gap and makes the molecule's overpayment a conservative floor, never
    # an inflated ceiling (no public data exists to weight strengths by
    # actual dispensing volume -- see METHODOLOGY.md). Every other strength
    # row of that molecule contributes $0, so summing overpayment_partd
    # across the leaderboard can no longer multiply-count a molecule.
    matched = out[out["crosswalk_matched"]]
    molecule_row_idx = (
        matched.sort_values("costplus_per_unit", ascending=False)
        .drop_duplicates(subset="ingredient_norm", keep="first")
        .index
    )
    representative_price = matched.loc[molecule_row_idx].set_index("ingredient_norm")["costplus_per_unit"]
    n_strengths = matched.groupby("ingredient_norm").size()

    out["costplus_per_unit_partd_molecule"] = out["ingredient_norm"].map(representative_price)
    out["partd_molecule_n_strengths"] = out["ingredient_norm"].map(n_strengths).fillna(0).astype(int)
    out["is_partd_molecule_row"] = out.index.isin(molecule_row_idx)

    gap_partd_molecule = out["partd_per_unit"] - out["costplus_per_unit_partd_molecule"]
    out["overpayment_partd"] = 0.0
    primary = out["is_partd_molecule_row"]
    out.loc[primary, "overpayment_partd"] = gap_partd_molecule.loc[primary] * out.loc[primary, "Tot_Dsg_Unts"]

    n_matched = out["partd_per_unit"].notna().sum()
    print(f"[module_a] Part D: matched {n_matched}/{len(out)} drugs by normalized ingredient name")
    n_dup_molecules = int((n_strengths > 1).sum())
    if n_dup_molecules:
        excess_rows = int((n_strengths[n_strengths > 1] - 1).sum())
        print(f"[module_a] Part D: {n_dup_molecules} molecule(s) matched to >1 strength "
              f"({excess_rows} extra strength rows) -- overpayment_partd dollarized once per molecule "
              f"(highest-price strength), not once per strength")
    return out


def build_partd_molecule_table(priced: pd.DataFrame) -> pd.DataFrame:
    """One row per molecule's Part D overpayment. This is the correct thing
    to read/sum for a per-molecule Part D figure -- every strength row of a
    drug_level/leaderboard DataFrame carries the SAME molecule-wide
    partd_per_unit/Tot_Dsg_Unts (see attach_partd), but only the row flagged
    is_partd_molecule_row=True carries a nonzero overpayment_partd; every
    other strength row of that molecule is $0 by construction, so this just
    selects the one real row per molecule rather than re-deriving anything."""
    rows = priced[priced["crosswalk_matched"] & priced["is_partd_molecule_row"]].copy()
    rows["overpayment_partd_floored"] = rows["overpayment_partd"].clip(lower=0.0)
    cols = [
        "ingredient_norm", "drug_term", "partd_molecule_n_strengths",
        "costplus_per_unit_partd_molecule", "partd_per_unit", "Tot_Dsg_Unts",
        "gap_partd", "overpayment_partd", "overpayment_partd_floored",
    ]
    out = rows[cols].rename(columns={"drug_term": "representative_drug_term"})
    return out.sort_values("overpayment_partd_floored", ascending=False).reset_index(drop=True)


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
# Step 3b: Medicaid SDUD, per-state (same NDC matching as attach_sdud, but
# kept split out by state via fetch_sdud.state_level() instead of collapsed
# to the national "XX" rollup).
# ---------------------------------------------------------------------------
def build_state_breakdown(drug_level: pd.DataFrame, sdud_df: pd.DataFrame) -> pd.DataFrame:
    """One row per (state, drug) with the same costplus_per_unit each drug
    uses everywhere else in Module A, joined against that state's own SDUD
    reimbursement for that drug's matched NDCs. States with zero reimbursed
    units for a drug simply produce no row for that (state, drug) pair --
    there is nothing to zero-fill against real utilization data."""
    per_state = fetch_sdud.state_level(sdud_df).rename(
        columns={"units_reimbursed": "medicaid_units", "medicaid_amount_reimbursed": "medicaid_amount"}
    )

    rows = []
    for _, drug_row in drug_level.iterrows():
        if not drug_row["crosswalk_matched"]:
            continue
        ndcs = drug_row["matched_ndcs"]
        hits = per_state[per_state["ndc"].isin(ndcs)]
        if hits.empty:
            continue
        by_state = hits.groupby("state", as_index=False).agg(
            medicaid_units=("medicaid_units", "sum"),
            medicaid_amount=("medicaid_amount", "sum"),
        )
        by_state = by_state[by_state["medicaid_units"] > 0]
        for _, s in by_state.iterrows():
            medicaid_per_unit = s["medicaid_amount"] / s["medicaid_units"]
            gap = medicaid_per_unit - drug_row["costplus_per_unit"]
            rows.append({
                "state": s["state"],
                "rxcui": drug_row["rxcui"],
                "drug_name": drug_row["drug_term"],
                "costplus_per_unit": drug_row["costplus_per_unit"],
                "medicaid_per_unit": medicaid_per_unit,
                "gap_medicaid": gap,
                "units_reimbursed": s["medicaid_units"],
                "overpayment_medicaid": gap * s["medicaid_units"],
            })

    out = pd.DataFrame(rows, columns=[
        "state", "rxcui", "drug_name", "costplus_per_unit", "medicaid_per_unit",
        "gap_medicaid", "units_reimbursed", "overpayment_medicaid",
    ])
    out = out.sort_values(["state", "overpayment_medicaid"], ascending=[True, False]).reset_index(drop=True)
    print(f"[module_a] State breakdown: {len(out):,} (state, drug) rows across {out['state'].nunique()} states")
    return out


def build_state_summary(state_breakdown: pd.DataFrame) -> pd.DataFrame:
    """One row per state. total_medicaid_overpayment sums only positive
    overpayment_medicaid rows within that state, mirroring the same
    positive-only convention print_aggregate_summary uses for the national
    total_medicaid_savings figure (a negative gap means Cost Plus priced
    above Medicaid for that drug, contributing $0, not a negative number)."""
    rows = []
    for state, grp in state_breakdown.groupby("state"):
        positive = grp[grp["overpayment_medicaid"] > 0]
        total = positive["overpayment_medicaid"].sum()
        if positive.empty:
            top_drug, top_drug_overpayment = None, 0.0
        else:
            top = positive.loc[positive["overpayment_medicaid"].idxmax()]
            top_drug, top_drug_overpayment = top["drug_name"], top["overpayment_medicaid"]
        rows.append({
            "state": state,
            "total_medicaid_overpayment": total,
            "top_drug": top_drug,
            "top_drug_overpayment": top_drug_overpayment,
            "drugs_analyzed": grp["rxcui"].nunique(),
        })
    out = pd.DataFrame(rows).sort_values("total_medicaid_overpayment", ascending=False).reset_index(drop=True)
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
    # Floored at 0, not just NaN-filled: a negative value here means Cost Plus
    # priced above what Part D/Medicaid paid (visible in the true, unfloored
    # gap_partd/gap_medicaid columns), which is a real finding but not an
    # "overpayment" -- same treatment print_aggregate_summary already gives
    # negative-gap drugs when summing total_partd_savings/total_medicaid_savings
    # ("contribute $0 to the totals above, not a negative number"). Without
    # this, a single ingredient-level Part D/NDC granularity mismatch (e.g. a
    # $57-patch blended against a $0.23 same-ingredient tablet in Part D's
    # per-unit figure) can otherwise show as a multi-billion-dollar negative
    # "overpayment" on an individual row.
    df["overpayment_partd"] = df["overpayment_partd"].fillna(0.0).clip(lower=0.0)
    df["overpayment_medicaid"] = df["overpayment_medicaid"].fillna(0.0).clip(lower=0.0)
    df["total_overpayment"] = df["overpayment_partd"] + df["overpayment_medicaid"]

    cols = [
        "drug_term", "rxcui", "nadac_pricing_unit",
        "nadac_per_unit", "costplus_per_unit", "gap_nadac",
        "partd_per_unit", "Tot_Dsg_Unts", "gap_partd",
        "partd_molecule_n_strengths", "costplus_per_unit_partd_molecule", "is_partd_molecule_row",
        "overpayment_partd",
        "medicaid_per_unit", "medicaid_units", "gap_medicaid", "overpayment_medicaid",
        "total_overpayment", "pharmacy_fee", "shipping_fee",
    ]
    df = df[cols].sort_values("total_overpayment", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    df["net_per_unit"] = "not public"  # HARD CONSTRAINT: never estimate net prices
    df["canonical_unit"] = df["nadac_pricing_unit"]
    # Every row here is crosswalk_matched (filtered above), i.e. it passed
    # resolve_dispensable_rxcui's token-overlap relevance check -- excluded
    # (unmatched) drugs never reach the leaderboard at all, so this column
    # lets a reviewer confirm that fact per row rather than trusting it
    # blindly.
    df["match_confidence"] = "token_overlap"
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
    drug_level = _exclude_brand_rows(drug_level, generics_only)

    all_matched_ndcs = sorted({ndc for ndcs in drug_level["matched_ndcs"] for ndc in ndcs})

    partd_df = fetch_partd.load_partd(force_refresh=force_refresh)
    priced = attach_partd(drug_level, partd_df, generics_only=generics_only)

    sdud_df = fetch_sdud.load_sdud(ndc_filter=all_matched_ndcs, force_refresh=force_refresh)
    priced = attach_sdud(priced, sdud_df)
    priced = attach_nadac_gap(priced)

    leaderboard = build_leaderboard(priced)
    partd_by_molecule = build_partd_molecule_table(priced)

    state_breakdown = build_state_breakdown(priced, sdud_df)
    state_summary = build_state_summary(state_breakdown)

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
        "partd_by_molecule": partd_by_molecule,
        "state_breakdown": state_breakdown,
        "state_summary": state_summary,
        "spread_changes": spread_changes,
        "total_partd_savings": total_partd_savings,
        "total_medicaid_savings": total_medicaid_savings,
        "total_savings": total_partd_savings + total_medicaid_savings,
        "negative_gap_drugs": negative_gap_drugs,
    }
