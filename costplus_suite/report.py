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


def write_leaderboard_by_state(state_breakdown: pd.DataFrame, is_sample: bool) -> Path:
    return write_csv(state_breakdown, "leaderboard_by_state.csv", is_sample)


def write_state_summary(state_summary: pd.DataFrame, is_sample: bool) -> Path:
    return write_csv(state_summary, "state_summary.csv", is_sample)


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


def print_trumprx_comparison(trumprx_result: dict, is_sample: bool, top_n: int = 5) -> None:
    if is_sample:
        print(f"\n{SAMPLE_BANNER}")
    print(f"\n=== TOP {top_n} -- TrumpRx brand vs Cost Plus generic equivalent ===")
    print("(brand cash price vs. generic cash price for the SAME MOLECULE, not the same drug -- see METHODOLOGY.md)")
    cols = [
        "brand_name", "dosage", "trumprx_price", "costplus_generic",
        "costplus_generic_price", "gap", "gap_pct", "canonical_unit",
    ]
    with pd.option_context("display.width", 200, "display.max_columns", 20, "display.float_format", "{:.2f}".format):
        print(trumprx_result["comparison"][cols].head(top_n).to_string(index=False))

    matched, total = trumprx_result["matched_brands"], trumprx_result["total_brands"]
    print(f"\n[report] {matched} of {total} TrumpRx brands matched to a Cost Plus generic")
    unmatched = trumprx_result["unmatched_brands"]
    if unmatched:
        print(f"[report] Unmatched ({len(unmatched)}): {', '.join(unmatched)}")
    if is_sample:
        print(f"\n{SAMPLE_BANNER}")


def write_trumprx_comparison(trumprx_result: dict, is_sample: bool) -> Path:
    print_trumprx_comparison(trumprx_result, is_sample)
    return write_csv(trumprx_result["comparison"], "trumprx_comparison.csv", is_sample)


def _fmt_billions(x: float) -> str:
    return f"${x / 1e9:.1f}B"


def print_simple_summary(result: dict, e_result: dict | None, leaderboard_path: Path, top_n: int = 10) -> None:
    """The clean, non-technical-reader summary `run.py` prints by default
    (see README.md). Everything else the pipeline prints (module toggles,
    fetch/crosswalk diagnostics, the full 25-row leaderboard, coverage
    reports) is real and still runs -- it's just not part of this block."""
    is_sample = result["is_sample"]
    print("--- Cost Plus Arbitrage Results ---")
    if is_sample:
        print(f"\n{SAMPLE_BANNER}")

    print("\nEstimated annual overpayment (generics only)")
    print(f"Medicare Part D:  {_fmt_billions(result['total_partd_savings'])}")
    print(f"Medicaid:         {_fmt_billions(result['total_medicaid_savings'])}")
    print(f"Total:            {_fmt_billions(result['total_savings'])}")

    state_summary = result.get("state_summary")
    if state_summary is not None and not state_summary.empty:
        top_states = state_summary.head(5).copy()
        state_table = pd.DataFrame({
            "State": top_states["state"],
            "Total overpayment": top_states["total_medicaid_overpayment"].map(lambda v: f"${v:,.0f}"),
            "Top drug": top_states["top_drug"],
            "Top drug overpayment": top_states["top_drug_overpayment"].map(lambda v: f"${v:,.0f}"),
        })
        print("\nTop 5 states by Medicaid overpayment:")
        with pd.option_context("display.width", 200):
            print(state_table.to_string(index=False))

    top = result["leaderboard"].head(top_n).copy()
    total_units = top["Tot_Dsg_Unts"].fillna(0) + top["medicaid_units"].fillna(0)
    top["gap_per_unit"] = (top["total_overpayment"] / total_units).where(total_units > 0)
    table = pd.DataFrame({
        "Drug": top["drug_term"],
        "Gap per unit": top["gap_per_unit"].map(lambda v: f"${v:.4f}" if pd.notna(v) else "-"),
        "Total overpayment": top["total_overpayment"].map(lambda v: f"${v:,.0f}"),
    })
    print(f"\nTop {top_n} drugs by overpayment:")
    with pd.option_context("display.width", 200):
        print(table.to_string(index=False))

    if e_result is not None and e_result.get("trumprx_comparison") is not None:
        tc = e_result["trumprx_comparison"]["comparison"].head(top_n)
        tc_table = pd.DataFrame({
            "Brand": tc["brand_name"],
            "TrumpRx price": tc["trumprx_price"].map(lambda v: f"${v:,.2f}"),
            "Cost Plus generic price": tc["costplus_generic_price"].map(lambda v: f"${v:,.2f}"),
            "Gap": tc["gap"].map(lambda v: f"${v:,.2f}"),
        })
        print("\nTrumpRx brand vs Cost Plus generic:")
        with pd.option_context("display.width", 200):
            print(tc_table.to_string(index=False))

    try:
        display_path = leaderboard_path.relative_to(config.ROOT_DIR)
    except ValueError:
        display_path = leaderboard_path
    print(f"\nFull results written to {display_path.as_posix()}")


def write_digest(text: str, filename: str, is_sample: bool = False) -> Path:
    path = config.OUTPUT_DIR / f"{_sample_prefix(is_sample)}{filename}"
    path.write_text(text, encoding="utf-8")
    print(f"[report] Wrote digest -> {path}")
    return path


def write_workbook(wb, filename: str, is_sample: bool = False) -> Path:
    path = config.OUTPUT_DIR / f"{_sample_prefix(is_sample)}{filename}"
    wb.save(path)
    print(f"[report] Wrote workbook -> {path}")
    return path
