"""
FDA Drug Shortages database, via the public openFDA API (no key required for
this call volume). https://api.fda.gov/drug/shortages.json

Used by Module B to cross-reference active shortages against the Cost Plus
catalog: a drug the system is short on, that Cost Plus also carries, is a
different kind of story than a plain price gap.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import http_cache  # noqa: E402

SHORTAGES_URL = "https://api.fda.gov/drug/shortages.json"
PAGE_LIMIT = 1000


def load_shortages(force_refresh: bool = False, active_only: bool = True) -> pd.DataFrame:
    """Page through openFDA's drug shortage endpoint and return a flat
    DataFrame: generic_name, status, dosage_form, therapeutic_category,
    rxcuis (list), update_date. status == 'Current' means an active
    shortage; 'To Be Discontinued' and 'Resolved' are historical/closing.
    """
    all_results = []
    skip = 0
    while True:
        url = f"{SHORTAGES_URL}?limit={PAGE_LIMIT}&skip={skip}"
        data = http_cache.cached_get_json(url, subdir="fda_shortages", force_refresh=force_refresh)
        results = data.get("results", [])
        all_results.extend(results)
        total = data.get("meta", {}).get("results", {}).get("total", 0)
        skip += PAGE_LIMIT
        if skip >= total or not results:
            break

    rows = []
    for r in all_results:
        openfda = r.get("openfda", {})
        rows.append(
            {
                "generic_name": r.get("generic_name"),
                "status": r.get("status"),
                "dosage_form": r.get("dosage_form"),
                "therapeutic_category": ", ".join(r.get("therapeutic_category", []) or []),
                "company_name": r.get("company_name"),
                "update_date": r.get("update_date"),
                "rxcuis": openfda.get("rxcui", []),
                "substance_names": openfda.get("substance_name", []),
            }
        )
    df = pd.DataFrame(rows)
    print(f"[fetch.shortages] Loaded {len(df):,} FDA shortage records (all statuses)")
    if active_only:
        df = df[df["status"] == "Current"]
        print(f"[fetch.shortages] {len(df):,} are status=='Current' (active shortages)")
    return df
