"""
Medicaid State Drug Utilization Data (SDUD), CMS, quarterly, data.medicaid.gov.
Keyed by NDC, per state. Fields used: NDC, State, Units Reimbursed, Medicaid
Amount Reimbursed, Product Name.

Discovery mirrors fetch/nadac.py: query data.medicaid.gov's metastore for
every dataset titled "State Drug Utilization Data <year>" and take the most
recent year -- never a hardcoded identifier.

The raw annual file is large (~500MB, ~5M rows: every NDC x state x quarter x
utilization-type combination for the whole country). We never load it in
full; load_sdud() always filters to a caller-supplied NDC set via chunked
reading, since this suite only ever needs SDUD figures for drugs already
resolved through the crosswalk.

QUIRK, verified by hand on real data: CMS reports a synthetic State == "XX"
row per NDC that is the national rollup (sum across all reporting states and
territories), not a 51st jurisdiction. Confirmed by summing all non-XX states
for a sample NDC and finding it matches the XX row to within rounding. Treat
"XX" as the national total and never add it to a sum over real states, or
totals silently double. national_total() and state_level() below split the
two apart explicitly so callers can't make that mistake by accident.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import http_cache  # noqa: E402

SDUD_TITLE_RE = re.compile(r"^State Drug Utilization Data (\d{4})$")

USECOLS = [
    "NDC",
    "State",
    "Year",
    "Quarter",
    "Utilization Type",
    "Suppression Used",
    "Product Name",
    "Units Reimbursed",
    "Number of Prescriptions",
    "Medicaid Amount Reimbursed",
]


def discover_latest_sdud(force_refresh: bool = False) -> dict:
    url = f"{config.MEDICAID_METASTORE_BASE}?fulltext=SDUD"
    items = http_cache.cached_get_json(url, subdir="metastore", force_refresh=force_refresh)

    candidates = []
    for item in items:
        m = SDUD_TITLE_RE.match(item.get("title", ""))
        if not m:
            continue
        dist = item.get("distribution", [])
        if not dist:
            continue
        d0 = dist[0]
        download_url = (d0.get("data") or d0).get("downloadURL") or d0.get("downloadURL")
        candidates.append(
            {
                "year": int(m.group(1)),
                "identifier": item.get("identifier"),
                "title": item.get("title"),
                "modified": item.get("modified"),
                "download_url": download_url,
            }
        )

    if not candidates:
        raise RuntimeError(
            "Could not discover any State Drug Utilization Data dataset from "
            f"data.medicaid.gov metastore. Inspect {url} manually."
        )

    latest = max(candidates, key=lambda c: c["year"])
    print(
        f"[fetch.sdud] Resolved SDUD distribution: '{latest['title']}' "
        f"(dataset identifier={latest['identifier']}, modified={latest['modified']})"
    )
    print(f"[fetch.sdud] SDUD download URL: {latest['download_url']}")
    http_cache.cache_resolved_id("sdud", latest)
    return latest


def load_sdud(
    ndc_filter: Optional[Iterable[str]] = None,
    force_refresh: bool = False,
    chunksize: int = 500_000,
) -> pd.DataFrame:
    """Stream the latest SDUD CSV in chunks, keeping only rows whose NDC is in
    `ndc_filter` (11-digit, no-dash strings -- same normalization as
    fetch.nadac / shared.crosswalk). Aggregates non-suppressed rows across
    quarters and utilization types (FFSU + MCOU) to one row per (NDC, State)
    for the latest year in the file.

    If ndc_filter is None, every row is kept -- only do this for a small
    manual inspection, never in the main pipeline (the unfiltered file is
    ~500MB / ~5M rows).

    Columns: ndc, state, product_name, units_reimbursed,
    medicaid_amount_reimbursed.
    """
    latest = discover_latest_sdud(force_refresh=force_refresh)
    filename = f"sdud_{latest['year']}_{latest['identifier'][:8]}.csv"
    csv_path = http_cache.cached_get_file(
        latest["download_url"], subdir="sdud", filename=filename, force_refresh=force_refresh
    )

    ndc_set = None
    if ndc_filter is not None:
        ndc_set = {str(n).strip().zfill(config.NDC_LENGTH) for n in ndc_filter}
        if not ndc_set:
            print("[fetch.sdud] Empty ndc_filter -- returning empty frame")
            return pd.DataFrame(
                columns=["ndc", "state", "product_name", "units_reimbursed", "medicaid_amount_reimbursed"]
            )

    kept_chunks = []
    rows_scanned = 0
    for chunk in pd.read_csv(
        csv_path,
        usecols=USECOLS,
        dtype={"NDC": str, "State": str, "Suppression Used": str},
        chunksize=chunksize,
        low_memory=False,
    ):
        rows_scanned += len(chunk)
        chunk["NDC"] = chunk["NDC"].str.strip().str.zfill(config.NDC_LENGTH)
        if ndc_set is not None:
            chunk = chunk[chunk["NDC"].isin(ndc_set)]
        if chunk.empty:
            continue
        chunk = chunk[chunk["Suppression Used"].str.lower() == "false"]
        if chunk.empty:
            continue
        kept_chunks.append(chunk)

    print(f"[fetch.sdud] Scanned {rows_scanned:,} raw SDUD rows")

    if not kept_chunks:
        print("[fetch.sdud] No matching, non-suppressed SDUD rows found for the given NDCs")
        return pd.DataFrame(
            columns=["ndc", "state", "product_name", "units_reimbursed", "medicaid_amount_reimbursed"]
        )

    df = pd.concat(kept_chunks, ignore_index=True)
    latest_year = df["Year"].max()
    df = df[df["Year"] == latest_year]

    df["Units Reimbursed"] = pd.to_numeric(df["Units Reimbursed"], errors="coerce")
    df["Medicaid Amount Reimbursed"] = pd.to_numeric(df["Medicaid Amount Reimbursed"], errors="coerce")
    df = df.dropna(subset=["Units Reimbursed", "Medicaid Amount Reimbursed"])

    agg = (
        df.groupby(["NDC", "State"], as_index=False)
        .agg(
            product_name=("Product Name", "first"),
            units_reimbursed=("Units Reimbursed", "sum"),
            medicaid_amount_reimbursed=("Medicaid Amount Reimbursed", "sum"),
        )
        .rename(columns={"NDC": "ndc", "State": "state"})
    )
    agg = agg[agg["units_reimbursed"] > 0]
    agg.attrs["data_year"] = int(latest_year)
    print(f"[fetch.sdud] Aggregated to {len(agg):,} (NDC, state) rows for year {int(latest_year)}")
    return agg


def national_total(df: pd.DataFrame) -> pd.DataFrame:
    """Rows where state == 'XX' -- CMS's own national rollup. Use this for a
    national total; do NOT also sum state_level() on top of it."""
    return df[df["state"] == "XX"].drop(columns="state")


def state_level(df: pd.DataFrame) -> pd.DataFrame:
    """Real per-state rows only, with the 'XX' national rollup excluded so a
    sum across this frame never double-counts against national_total()."""
    return df[df["state"] != "XX"]
