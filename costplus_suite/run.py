"""
Main CLI entry point. Runs Module A (always) plus whichever Phase 2 modules
are enabled in config.MODULES_ENABLED (or overridden with --modules), writes
CSVs/digests to output/, and prints headline numbers to stdout.

Usage:
    python run.py                                   # real data/costplus.csv
    python run.py --sample                          # data/costplus.SAMPLE.csv, clearly labeled
    python run.py --modules b,d --claims data/claims.SAMPLE.csv
    python run.py --force-refresh                   # bypass all disk caches
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import report  # noqa: E402
from modules import a_arbitrage  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cost Plus arbitrage suite")
    p.add_argument("--costplus", type=Path, default=None, help="Path to Cost Plus CSV (default: data/costplus.csv)")
    p.add_argument("--sample", action="store_true", help="Use data/costplus.SAMPLE.csv instead of real data")
    p.add_argument("--trumprx", type=Path, default=None, help="Path to TrumpRx CSV for Module E (default: data/trumprx.csv, or data/trumprx.SAMPLE.csv with --sample)")
    p.add_argument("--claims", type=Path, default=None, help="Path to a claims CSV, enables Module D")
    p.add_argument("--force-refresh", action="store_true", help="Bypass disk caches and re-fetch everything")
    p.add_argument(
        "--source", choices=["csv", "scrape", "graphql"], default="csv",
        help="csv (default): load data/costplus.csv as-is. scrape: run Module A against the "
             "already-scraped data/costplus.SCRAPED.csv (see fetch/costplus_html_scraper.py --full-catalog) "
             "instead -- acquisition_cost/markup/pharmacy_fee are never published by the site, and "
             "package_quantity is only trusted where fetch.costplus_html_scraper.recover_package_quantity "
             "can confirm it against real NADAC data; unconfirmed rows are excluded and counted, "
             "never guessed. Requires data/costplus.SCRAPED.csv to already exist (never scraped here). "
             "graphql: run Module A against the already-fetched data/costplus.GRAPHQL.csv (see "
             "fetch/costplus_graphql.py) instead -- this is Cost Plus's own storefront API and, unlike "
             "scrape, confirms package_quantity for the whole catalog rather than recovering it from "
             "free-text page fields. Requires data/costplus.GRAPHQL.csv to already exist (never fetched here).",
    )
    p.add_argument(
        "--generics-only", dest="generics_only", action="store_true", default=None,
        help=f"Restrict headline numbers to generics (default: {config.GENERICS_ONLY})",
    )
    p.add_argument("--all-drugs", dest="generics_only", action="store_false", help="Include brand rows (not recommended for headline numbers)")
    p.add_argument(
        "--modules", type=str, default=None,
        help="Comma-separated Phase 2 modules to run (b,c,d,e,f). Default: config.MODULES_ENABLED",
    )
    return p.parse_args()


def resolve_costplus_path(args: argparse.Namespace) -> Path:
    if args.costplus:
        return args.costplus
    if args.sample:
        return config.DATA_DIR / "costplus.SAMPLE.csv"
    return config.DATA_DIR / "costplus.csv"


def resolve_trumprx_path(args: argparse.Namespace) -> Path:
    if args.trumprx:
        return args.trumprx
    if args.sample:
        return config.DATA_DIR / "trumprx.SAMPLE.csv"
    return config.DATA_DIR / "trumprx.csv"


def resolve_enabled_modules(args: argparse.Namespace) -> set[str]:
    if args.modules is not None:
        return {m.strip().lower() for m in args.modules.split(",") if m.strip()}
    key_map = {
        "b": "b_intelligence", "c": "c_list_vs_net", "d": "d_employer_calculator",
        "e": "e_brand_trumprx", "f": "f_oncology",
    }
    return {short for short, full in key_map.items() if config.MODULES_ENABLED.get(full)}


def main() -> None:
    args = parse_args()
    costplus_path = resolve_costplus_path(args)
    enabled = resolve_enabled_modules(args)

    print(f"[run] Cost Plus source: {costplus_path}")
    print(f"[run] Enabled Phase 2 modules: {sorted(enabled) or '(none)'}")

    if args.source == "scrape":
        import pandas as pd
        from fetch import costplus_html_scraper

        scraped_path = config.DATA_DIR / "costplus.SCRAPED.csv"
        if not scraped_path.exists():
            raise FileNotFoundError(
                f"{scraped_path} not found. --source scrape runs against an already-scraped catalog -- "
                "generate one first with `python -m costplus_suite.fetch.costplus_html_scraper --full-catalog` "
                "(this command never scrapes automatically)."
            )
        scraped_df = pd.read_csv(scraped_path)
        recovered = costplus_html_scraper.recover_package_quantity(scraped_df, force_refresh=args.force_refresh)
        runnable = costplus_html_scraper.build_runnable_catalog(recovered)
        runnable_path = config.DATA_DIR / "costplus.RUNNABLE.csv"
        runnable.to_csv(runnable_path, index=False)
        n_confirmed, n_total = len(runnable), len(scraped_df)
        print(f"[run] --source scrape: {n_confirmed:,}/{n_total:,} scraped rows have a confirmed "
              f"package_quantity (see breakdown above) -> {runnable_path}; running Module A against those only.")
        costplus_path = runnable_path

    if args.source == "graphql":
        import pandas as pd

        graphql_path = config.DATA_DIR / "costplus.GRAPHQL.csv"
        if not graphql_path.exists():
            raise FileNotFoundError(
                f"{graphql_path} not found. --source graphql runs against an already-fetched catalog -- "
                "generate one first with `python -m costplus_suite.fetch.costplus_graphql` "
                "(this command never fetches automatically)."
            )
        graphql_df = pd.read_csv(graphql_path)
        confirmed = graphql_df[graphql_df["package_quantity_status"] == "confirmed"].copy()
        runnable_path = config.DATA_DIR / "costplus.GRAPHQL.RUNNABLE.csv"
        confirmed.to_csv(runnable_path, index=False)
        n_confirmed, n_total = len(confirmed), len(graphql_df)
        print(f"[run] --source graphql: {n_confirmed:,}/{n_total:,} catalog rows have a confirmed "
              f"package_quantity -> {runnable_path}; running Module A against those only.")
        costplus_path = runnable_path

    # --- Module A: always runs, it's the core deliverable ---
    result = a_arbitrage.run(costplus_path=costplus_path, force_refresh=args.force_refresh, generics_only=args.generics_only)
    is_sample = result["is_sample"]

    report.write_leaderboard(result["leaderboard"], is_sample)
    report.write_spread_changes(result["spread_changes"], is_sample)
    report.print_aggregate_summary(result)

    # --- Phase 2, toggle-gated ---
    if "b" in enabled:
        from modules import b_intelligence
        digest = b_intelligence.run(costplus_path)
        report.write_digest(digest, "intelligence_digest.txt", is_sample)

    if "c" in enabled:
        from modules import c_list_vs_net
        df = c_list_vs_net.run(costplus_path)
        report.write_csv(df, "list_vs_net.csv", is_sample)

    if "d" in enabled:
        if not args.claims:
            print("[run] Module D enabled but --claims not supplied -- skipping")
        else:
            from modules import d_employer_calculator
            d_result = d_employer_calculator.run(args.claims, costplus_path)
            report.write_csv(d_result["summary"], "employer_calculator_summary.csv", is_sample)

    if "e" in enabled:
        from modules import e_brand_trumprx
        e_result = e_brand_trumprx.run(costplus_path, resolve_trumprx_path(args))
        report.write_csv(e_result["brand_leaderboard"], "brand_price_increase_leaderboard.csv", is_sample)
        if e_result["trumprx_comparison"] is not None:
            report.write_csv(e_result["trumprx_comparison"], "trumprx_vs_costplus_generic.csv", is_sample)

    if "f" in enabled:
        print("[run] Module F requires an explicit oncology NDC list -- run modules.f_oncology.run(ndcs) directly "
              "(see module docstring); skipping in the default CLI flow since Cost Plus's retail catalog rarely "
              "carries physician-administered oncology drugs.")

    print("\n[run] Done.")


if __name__ == "__main__":
    main()
