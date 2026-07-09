"""
Module D: employer calculator (toggle: config.MODULES_ENABLED["d_employer_calculator"]).

Input: a de-identified claims CSV with columns NDC, units, amount_paid.
Output: per-fill and total cost at Cost Plus prices, and the implied spread
(amount actually paid minus what Cost Plus would have charged).

Per HARD CONSTRAINT, shipping_fee stays its own column -- cost_at_costplus is
the pure per-unit x units calculation; cost_at_costplus_incl_shipping adds a
flat per-fill shipping fee on top for a realistic "what would switching
actually cost" total, but the two are never blended into one silent number.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import crosswalk, costplus as costplus_mod  # noqa: E402
from fetch import nadac as fetch_nadac  # noqa: E402

REQUIRED_CLAIMS_COLUMNS = ["NDC", "units", "amount_paid"]


def build_ndc_to_costplus_map(cp_df: pd.DataFrame, nadac_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, cp_row in cp_df.iterrows():
        r = crosswalk.crosswalk_drug(cp_row["drug_term"], nadac_df)
        for ndc in r.matched_ndcs or []:
            rows.append(
                {
                    "ndc": ndc,
                    "drug_term": cp_row["drug_term"],
                    "costplus_per_unit": cp_row["costplus_per_unit"],
                    "pharmacy_fee": cp_row["pharmacy_fee"],
                    "shipping_fee": cp_row["shipping_fee"],
                }
            )
    out = pd.DataFrame(rows)
    print(f"[module_d] Built NDC -> Cost Plus price map covering {out['ndc'].nunique():,} NDCs "
          f"across {out['drug_term'].nunique()} catalog drugs")
    return out


def price_claims(claims_path: Path, costplus_path: Path | None = None, force_refresh: bool = False) -> pd.DataFrame:
    claims = pd.read_csv(claims_path, dtype={"NDC": str})
    missing = [c for c in REQUIRED_CLAIMS_COLUMNS if c not in claims.columns]
    if missing:
        raise ValueError(f"Claims CSV missing required columns: {missing}")
    claims["NDC"] = claims["NDC"].str.replace("-", "", regex=False).str.zfill(11)

    cp_df = costplus_mod.load_costplus(costplus_path)
    nadac_df = fetch_nadac.load_nadac(force_refresh=force_refresh)
    ndc_map = build_ndc_to_costplus_map(cp_df, nadac_df).set_index("ndc")

    joined = claims.join(ndc_map, on="NDC", how="left")
    joined["matched"] = joined["drug_term"].notna()
    joined["cost_at_costplus"] = joined["units"] * joined["costplus_per_unit"]
    joined["cost_at_costplus_incl_shipping"] = joined["cost_at_costplus"] + joined["shipping_fee"].fillna(0)
    joined["implied_spread"] = joined["amount_paid"] - joined["cost_at_costplus"]

    matched_n, total_n = int(joined["matched"].sum()), len(joined)
    print(f"[module_d] Priced {matched_n}/{total_n} claims against the Cost Plus catalog")
    return joined


def summarize(priced_claims: pd.DataFrame) -> pd.DataFrame:
    matched = priced_claims[priced_claims["matched"]]
    summary = (
        matched.groupby("drug_term", as_index=False)
        .agg(
            fills=("NDC", "count"),
            units=("units", "sum"),
            amount_paid=("amount_paid", "sum"),
            cost_at_costplus=("cost_at_costplus", "sum"),
            cost_at_costplus_incl_shipping=("cost_at_costplus_incl_shipping", "sum"),
            implied_spread=("implied_spread", "sum"),
        )
        .sort_values("implied_spread", ascending=False)
    )
    return summary


def run(claims_path: Path, costplus_path: Path | None = None) -> dict:
    priced = price_claims(claims_path, costplus_path)
    summary = summarize(priced)
    totals = {
        "total_amount_paid": priced.loc[priced["matched"], "amount_paid"].sum(),
        "total_cost_at_costplus": priced.loc[priced["matched"], "cost_at_costplus"].sum(),
        "total_cost_at_costplus_incl_shipping": priced.loc[priced["matched"], "cost_at_costplus_incl_shipping"].sum(),
        "total_implied_spread": priced.loc[priced["matched"], "implied_spread"].sum(),
    }
    print("\n=== EMPLOYER CALCULATOR TOTALS ===")
    for k, v in totals.items():
        print(f"  {k}: ${v:,.2f}")
    return {"priced_claims": priced, "summary": summary, "totals": totals}
