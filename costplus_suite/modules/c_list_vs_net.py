"""
Module C: list vs net (toggle: config.MODULES_ENABLED["c_list_vs_net"]).

Per drug: list-price movement, Cost Plus price, and NADAC acquisition cost
side by side. The `net_per_unit` column is always the literal string
"not public" -- per HARD CONSTRAINT, this suite never estimates net-of-rebate
prices, because true net prices are not public data.

"list-price movement" here is NOT the manufacturer's WAC list price -- WAC is
proprietary (First Databank/Medi-Span) and out of scope ("no proprietary or
paid data sources"). It is Medicare Part D's year-over-year change in gross
average spend per dosage unit, which is the closest public, price-movement-like
signal available. It is labeled exactly that in the output column name
(`gross_spend_per_unit_yoy_chg`) so it is never mistaken for WAC.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import crosswalk, costplus as costplus_mod  # noqa: E402
from fetch import nadac as fetch_nadac, partd as fetch_partd  # noqa: E402


def _load_partd_with_yoy_change() -> tuple[pd.DataFrame, str]:
    info = fetch_partd.discover_latest_partd()
    csv_path = config.CACHE_DIR / "partd" / f"partd_{info['dataset_identifier'][:8]}.csv"
    header = pd.read_csv(csv_path, nrows=0).columns
    chg_cols = [c for c in header if re.match(r"^Chg_Avg_Spnd_Per_Dsg_Unt_\d{2}_\d{2}$", c)]
    chg_col = sorted(chg_cols)[-1] if chg_cols else None

    usecols = ["Brnd_Name", "Gnrc_Name", "Mftr_Name"] + ([chg_col] if chg_col else [])
    df = pd.read_csv(csv_path, usecols=usecols)
    df = df[df["Mftr_Name"] == "Overall"].copy()
    df["ingredient_norm"] = df["Gnrc_Name"].map(crosswalk.normalize_drug_name)
    if chg_col:
        df = df.rename(columns={chg_col: "gross_spend_per_unit_yoy_chg"})
    else:
        df["gross_spend_per_unit_yoy_chg"] = pd.NA
    return df[["ingredient_norm", "gross_spend_per_unit_yoy_chg"]].drop_duplicates("ingredient_norm"), chg_col


def run(costplus_path: Path | None = None) -> pd.DataFrame:
    cp_df = costplus_mod.load_costplus(costplus_path)
    nadac_df = fetch_nadac.load_nadac()
    yoy_df, chg_col = _load_partd_with_yoy_change()

    rows = []
    for _, cp_row in cp_df.iterrows():
        r = crosswalk.crosswalk_drug(cp_row["drug_term"], nadac_df)
        ingredient_norm = crosswalk.normalize_drug_name(r.ingredient_name or cp_row["drug"])
        yoy_match = yoy_df[yoy_df["ingredient_norm"] == ingredient_norm]
        yoy_chg = yoy_match["gross_spend_per_unit_yoy_chg"].iloc[0] if not yoy_match.empty else None

        rows.append(
            {
                "drug_term": cp_row["drug_term"],
                "canonical_unit": r.pricing_unit or "unmatched",
                "costplus_per_unit": cp_row["costplus_per_unit"],
                "nadac_per_unit": r.nadac_per_unit,
                "gross_spend_per_unit_yoy_chg": yoy_chg,
                "net_per_unit": "not public",
            }
        )

    out = pd.DataFrame(rows)
    print(f"[module_c] Built list-vs-net table for {len(out)} drugs (YoY column: {chg_col or 'unavailable'})")
    return out
