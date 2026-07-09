"""
CSV + plain-text digest writers. Every function here prints what it wrote and
where, and stamps SAMPLE-DATA output clearly rather than letting it look like
a real result.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

SAMPLE_BANNER = "*** SAMPLE DATA -- fabricated placeholder Cost Plus prices, NOT a real result ***"


def _sample_prefix(is_sample: bool) -> str:
    return "SAMPLE_" if is_sample else ""


def write_csv(df: pd.DataFrame, filename: str, is_sample: bool = False) -> Path:
    path = config.OUTPUT_DIR / f"{_sample_prefix(is_sample)}{filename}"
    df.to_csv(path, index=False)
    print(f"[report] Wrote {len(df):,} rows -> {path}")
    return path


def print_leaderboard(leaderboard: pd.DataFrame, is_sample: bool, top_n: int = 25) -> None:
    if is_sample:
        print(f"\n{SAMPLE_BANNER}")
    print(f"\n=== TOP {top_n} GENERICS BY TOTAL SYSTEM OVERPAYMENT VS COST PLUS ===")
    print("(canonical unit = NADAC Pricing Unit; net_per_unit is always 'not public' -- see METHODOLOGY.md)")
    cols = [
        "rank", "drug_term", "canonical_unit", "nadac_per_unit", "costplus_per_unit",
        "partd_per_unit", "overpayment_partd", "medicaid_per_unit", "overpayment_medicaid",
        "total_overpayment",
    ]
    with pd.option_context("display.width", 200, "display.max_columns", 20, "display.float_format", "{:.4f}".format):
        print(leaderboard[cols].head(top_n).to_string(index=False))
    if is_sample:
        print(f"\n{SAMPLE_BANNER}")


def write_leaderboard(leaderboard: pd.DataFrame, is_sample: bool) -> Path:
    print_leaderboard(leaderboard, is_sample)
    return write_csv(leaderboard, "leaderboard.csv", is_sample)


def write_spread_changes(spread_changes: pd.DataFrame, is_sample: bool) -> Path:
    if spread_changes.empty:
        print("\n[report] spread_changes.csv: no prior snapshot to diff against yet -- writing an empty file "
              "with headers. Rerun after the next weekly NADAC refresh to get a real diff.")
    else:
        widening = spread_changes[spread_changes["widening"]]
        print(f"\n=== SPREAD WIDENING: {len(widening)}/{len(spread_changes)} drugs, gap vs Cost Plus growing ===")
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            print(widening.head(25).to_string(index=False))
    return write_csv(spread_changes, "spread_changes.csv", is_sample)


def print_aggregate_summary(result: dict) -> None:
    if result["is_sample"]:
        print(f"\n{SAMPLE_BANNER}")
    print("\n=== AGGREGATE SAVINGS IF GENERICS WERE BOUGHT AT COST PLUS PRICES ===")
    print(f"generics_only = {result['generics_only']}  (headline numbers restricted to generics; see METHODOLOGY.md)")
    print(f"NADAC snapshot date: {result['snapshot_date']}")
    print(f"  Medicare Part D:  ${result['total_partd_savings']:,.2f}")
    print(f"  Medicaid (SDUD):  ${result['total_medicaid_savings']:,.2f}")
    print(f"  TOTAL:            ${result['total_savings']:,.2f}")

    neg = result["negative_gap_drugs"]
    if not neg.empty:
        print(
            f"\n[report] NOTE: {len(neg)} drug(s) had a NEGATIVE gap against Part D and/or Medicaid "
            "(Cost Plus priced above what the system paid for at least one program). These contribute "
            "$0 to the totals above, not a negative number, and are listed here for transparency:"
        )
        for _, row in neg.iterrows():
            print(f"    - {row['drug_term']}: gap_partd={row['gap_partd']:.4f}, gap_medicaid={row['gap_medicaid']:.4f}")
    if result["is_sample"]:
        print(f"\n{SAMPLE_BANNER}")


def write_digest(text: str, filename: str, is_sample: bool = False) -> Path:
    path = config.OUTPUT_DIR / f"{_sample_prefix(is_sample)}{filename}"
    path.write_text(text, encoding="utf-8")
    print(f"[report] Wrote digest -> {path}")
    return path
