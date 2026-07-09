"""
NADAC (National Average Drug Acquisition Cost), CMS, weekly, data.medicaid.gov.
Keyed by NDC. Fields used: NDC, NDC Description, NADAC Per Unit, Effective
Date, Pricing Unit.

The distribution ID is discovered live from data.medicaid.gov's metastore API
every run (cached to disk) -- it is never hardcoded, since NADAC republishes a
new distribution weekly and a new dataset identifier every calendar year.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import http_cache  # noqa: E402

NADAC_TITLE_RE = re.compile(
    r"^NADAC \(National Average Drug Acquisition Cost\) (\d{4})$"
)


def discover_latest_nadac(force_refresh: bool = False) -> dict:
    """Query data.medicaid.gov's metastore for every dataset titled
    'NADAC (National Average Drug Acquisition Cost) <year>' and return the
    metadata for the most recent year.
    """
    url = f"{config.MEDICAID_METASTORE_BASE}?fulltext=NADAC"
    items = http_cache.cached_get_json(url, subdir="metastore", force_refresh=force_refresh)

    candidates = []
    for item in items:
        m = NADAC_TITLE_RE.match(item.get("title", ""))
        if not m:
            continue
        year = int(m.group(1))
        dist = item.get("distribution", [])
        if not dist:
            continue
        d0 = dist[0]
        download_url = (d0.get("data") or d0).get("downloadURL") or d0.get("downloadURL")
        candidates.append(
            {
                "year": year,
                "identifier": item.get("identifier"),
                "title": item.get("title"),
                "modified": item.get("modified"),
                "download_url": download_url,
            }
        )

    if not candidates:
        raise RuntimeError(
            "Could not discover any NADAC dataset from data.medicaid.gov metastore. "
            f"The API shape may have changed -- inspect {url} manually."
        )

    latest = max(candidates, key=lambda c: c["year"])

    # NADAC republishes the SAME dataset identifier every week within a
    # calendar year -- only the download URL's embedded date changes (e.g.
    # ".../nadac-...-07-08-2026.csv"). Weekly snapshot caching (shared/
    # snapshots.py) needs a real per-week key, so pull the date out of the
    # URL; fall back to the metastore 'modified' date if the filename ever
    # stops following that convention.
    url_date_match = re.search(r"(\d{2})-(\d{2})-(\d{4})\.csv$", latest["download_url"] or "")
    if url_date_match:
        mm, dd, yyyy = url_date_match.groups()
        snapshot_date = f"{yyyy}-{mm}-{dd}"
    else:
        snapshot_date = latest["modified"][:10]
    latest["snapshot_date"] = snapshot_date

    print(
        f"[fetch.nadac] Resolved NADAC distribution: '{latest['title']}' "
        f"(dataset identifier={latest['identifier']}, modified={latest['modified']}, "
        f"snapshot_date={snapshot_date})"
    )
    print(f"[fetch.nadac] NADAC download URL: {latest['download_url']}")
    http_cache.cache_resolved_id("nadac", latest)
    return latest


def load_nadac(force_refresh: bool = False) -> pd.DataFrame:
    """Return the latest NADAC file as a DataFrame indexed by 11-digit NDC,
    deduplicated to each NDC's most recent Effective Date row (the annual
    CSV accumulates one row per NDC per weekly refresh).

    Columns: ndc_description, nadac_per_unit (float), pricing_unit,
    effective_date (datetime64), as_of_date.
    """
    latest = discover_latest_nadac(force_refresh=force_refresh)
    filename = f"nadac_{latest['snapshot_date']}.csv"
    csv_path = http_cache.cached_get_file(
        latest["download_url"], subdir="nadac", filename=filename, force_refresh=force_refresh
    )

    df = pd.read_csv(csv_path, dtype=str)
    df["NDC"] = df["NDC"].str.strip().str.zfill(config.NDC_LENGTH)
    df["NADAC Per Unit"] = df["NADAC Per Unit"].astype(float)
    df["Effective Date"] = pd.to_datetime(df["Effective Date"], format="%m/%d/%Y")

    df = df.sort_values("Effective Date").drop_duplicates(subset="NDC", keep="last")
    df = df.rename(
        columns={
            "NDC Description": "ndc_description",
            "NADAC Per Unit": "nadac_per_unit",
            "Pricing Unit": "pricing_unit",
            "Effective Date": "effective_date",
            "As of Date": "as_of_date",
        }
    )
    df = df.set_index("NDC")
    print(f"[fetch.nadac] Loaded NADAC: {len(df):,} unique NDCs (as of {latest['modified']})")
    out = df[["ndc_description", "nadac_per_unit", "pricing_unit", "effective_date", "as_of_date"]]
    out.attrs["snapshot_date"] = latest["snapshot_date"]
    return out
