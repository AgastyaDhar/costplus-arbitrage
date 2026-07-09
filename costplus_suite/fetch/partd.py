"""
Medicare Part D Spending by Drug, CMS, annual, data.cms.gov.
Keyed by brand + generic name (national aggregate, not NDC-level).

IMPORTANT (see METHODOLOGY.md): Part D spending here is GROSS of manufacturer
rebates -- CMS is contractually prohibited from disclosing net prices. That is
exactly why config.GENERICS_ONLY defaults to True: generic rebates are small
and largely priced out at the point of sale, so gross ~= a defensible proxy
for what the system actually paid. Brand rebates can be deep (30-50%+), so a
brand "overpayment" computed off this gross number would be overstated and is
never surfaced as a headline number.

Discovery: data.cms.gov/data.json is CMS's DCAT catalog. We find the dataset
titled exactly "Medicare Part D Spending by Drug" (not the quarterly variant),
then call its dataset-resources API to get the actual CSV downloadURL for the
current year -- never a hardcoded identifier or URL.

The CSV itself ships in a wide format with one column trio per calendar year
(Tot_Spndng_2024, Tot_Dsg_Unts_2024, Tot_Clms_2024, ...). We detect the latest
year present from the column names rather than assuming a year, and expose it
generically as Tot_Spndng / Tot_Dsg_Unts / Tot_Clms plus a `data_year` field
on the returned frame's attrs.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import http_cache  # noqa: E402

DATASET_TITLE = "Medicare Part D Spending by Drug"
YEAR_COL_RE = re.compile(r"^Tot_Spndng_(\d{4})$")


def discover_latest_partd(force_refresh: bool = False) -> dict:
    """Discover the current Part D Spending by Drug distribution via
    data.cms.gov's DCAT catalog + dataset-resources API."""
    catalog = http_cache.cached_get_json(
        config.CMS_DATA_METASTORE_BASE, subdir="metastore", force_refresh=force_refresh
    )
    datasets = catalog.get("dataset", [])
    match = next((d for d in datasets if d.get("title") == DATASET_TITLE), None)
    if match is None:
        raise RuntimeError(
            f"Could not find dataset titled '{DATASET_TITLE}' in {config.CMS_DATA_METASTORE_BASE}. "
            "CMS may have renamed it -- inspect the catalog manually."
        )

    # The dataset's own `identifier`/`distribution[].downloadURL` fields on
    # data.cms.gov currently contain a templating bug (host literally
    # "https://default"), so we resolve the real CSV via the dataset's
    # resourcesAPI, which returns fully-qualified data.cms.gov URLs.
    resources_api = next(
        (d.get("resourcesAPI") for d in match.get("distribution", []) if d.get("resourcesAPI")),
        None,
    )
    if resources_api is None:
        raise RuntimeError(f"No resourcesAPI found for dataset '{DATASET_TITLE}'.")

    dataset_uuid = resources_api.rstrip("/").rsplit("/", 1)[-1]
    resources = http_cache.cached_get_json(
        resources_api, subdir="metastore", force_refresh=force_refresh
    ).get("data", [])

    csv_resources = [
        r
        for r in resources
        if r.get("downloadURL", "").endswith(".csv")
        and DATASET_TITLE in r.get("name", "")
        and "Dictionary" not in r.get("name", "")
        and "Methodology" not in r.get("name", "")
    ]
    if not csv_resources:
        raise RuntimeError(
            f"No CSV resource found among dataset-resources for '{DATASET_TITLE}': {resources}"
        )
    resource = csv_resources[0]

    info = {
        "dataset_identifier": dataset_uuid,
        "title": match.get("title"),
        "modified": match.get("modified"),
        "resource_name": resource.get("name"),
        "download_url": resource.get("downloadURL"),
    }
    print(
        f"[fetch.partd] Resolved Part D distribution: '{info['resource_name']}' "
        f"(dataset identifier={dataset_uuid}, modified={info['modified']})"
    )
    print(f"[fetch.partd] Part D download URL: {info['download_url']}")
    http_cache.cache_resolved_id("partd", info)
    return info


def load_partd(force_refresh: bool = False) -> pd.DataFrame:
    """Return the latest Part D Spending by Drug file with generic column
    names for the most recent year present in the CSV.

    Columns: Brnd_Name, Gnrc_Name, Tot_Mftr, Mftr_Name, Tot_Spndng,
    Tot_Dsg_Unts, Tot_Clms. Filtered to Mftr_Name == "Overall" rows so
    per-manufacturer breakdowns aren't double-counted. df.attrs['data_year']
    holds the resolved year.
    """
    info = discover_latest_partd(force_refresh=force_refresh)
    filename = f"partd_{info['dataset_identifier'][:8]}.csv"
    csv_path = http_cache.cached_get_file(
        info["download_url"], subdir="partd", filename=filename, force_refresh=force_refresh
    )

    header = pd.read_csv(csv_path, nrows=0).columns
    years = [int(m.group(1)) for c in header if (m := YEAR_COL_RE.match(c))]
    if not years:
        raise RuntimeError(f"No Tot_Spndng_<year> columns found in {csv_path}")
    latest_year = max(years)

    usecols = [
        "Brnd_Name",
        "Gnrc_Name",
        "Tot_Mftr",
        "Mftr_Name",
        f"Tot_Spndng_{latest_year}",
        f"Tot_Dsg_Unts_{latest_year}",
        f"Tot_Clms_{latest_year}",
    ]
    df = pd.read_csv(csv_path, usecols=usecols)
    df = df.rename(
        columns={
            f"Tot_Spndng_{latest_year}": "Tot_Spndng",
            f"Tot_Dsg_Unts_{latest_year}": "Tot_Dsg_Unts",
            f"Tot_Clms_{latest_year}": "Tot_Clms",
        }
    )
    df = df[df["Mftr_Name"] == "Overall"].copy()
    df = df.dropna(subset=["Tot_Spndng", "Tot_Dsg_Unts"])
    df = df[df["Tot_Dsg_Unts"] > 0]
    df.attrs["data_year"] = latest_year
    print(f"[fetch.partd] Loaded Part D {latest_year}: {len(df):,} brand/generic rows (Overall)")
    return df


def is_generic_row(df: pd.DataFrame) -> pd.Series:
    """Heuristic generic flag: unbranded generics list the same string in
    Brnd_Name and Gnrc_Name (e.g. Brnd_Name='Atorvastatin Calcium',
    Gnrc_Name='Atorvastatin Calcium'). This is CMS's own convention for this
    file, not something we invented, but it is a heuristic -- see
    METHODOLOGY.md for the (rare) cases it can misclassify.
    """
    return df["Brnd_Name"].str.strip().str.upper() == df["Gnrc_Name"].str.strip().str.upper()
