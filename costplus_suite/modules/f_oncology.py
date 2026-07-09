"""
Module F: oncology / Part B ASP (toggle: config.MODULES_ENABLED["f_oncology"]).

Compares Medicare Part B's ASP-based payment limit (ASP + 6%, what Medicare
reimburses physicians/hospitals for physician-administered drugs) to NADAC
acquisition cost, for physician-administered cancer drugs.

METHODOLOGY LIMITATION (required to be stated, not just coded around): ASP
itself is a volume-weighted average of a manufacturer's quarterly net sales,
already averaging in most rebates/discounts/chargebacks across all of that
manufacturer's customers before CMS ever publishes it. That is fundamentally
different from a single payer's or patient's net price -- it is a market-wide
average baked into the reimbursement rate by statute, not something this
suite computed or estimated. We surface it labeled as "ASP-based payment
limit" and never call it "net."

UNIT SAFETY: ASP's HCPCS billing unit (e.g. "10 MG", "1 ML") is frequently
NOT the same unit NADAC prices in for the same NDC (e.g. NADAC prices some
injectables "EA" per vial regardless of the vial's mg strength). Per HARD
CONSTRAINT, we only compute a direct per-unit overcharge figure when the
HCPCS dosage unit token and quantity are confirmably identical to NADAC's
Pricing Unit (e.g. HCPCS dosage "1 ML" against NADAC Pricing Unit "ML"); every
other row is reported with both prices shown side by side, in their own
labeled units, and overcharge_per_billunit left blank with an explanatory
note -- never silently divided through a mismatched unit.

SCOPE NOTE: Cost Plus's retail catalog is predominantly self-administered
oral generics; physician-administered infused/injected oncology drugs are
largely outside what a mail-order retail pharmacy carries. Zero or few
matches against a retail Cost Plus catalog is an expected, correct result of
running this module against data/costplus.csv, not a bug -- this module is
designed to run against any oncology drug/NDC list, not only what's on
Cost Plus.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fetch import asp as fetch_asp, nadac as fetch_nadac  # noqa: E402

_DOSAGE_RE = re.compile(r"^\s*([\d.]+)\s*([A-Za-z]+)\s*$")
_UNIT_ALIASES = {"ML": "ML", "MG": "GM", "GM": "GM", "G": "GM", "EA": "EA", "UNIT": "EA", "UNITS": "EA"}


def _reconcile_unit(hcpcs_dosage: str, nadac_pricing_unit: str) -> tuple[bool, str]:
    """Return (directly_comparable, note)."""
    if not isinstance(hcpcs_dosage, str) or not isinstance(nadac_pricing_unit, str):
        return False, "missing unit info"
    m = _DOSAGE_RE.match(hcpcs_dosage)
    if not m:
        return False, f"could not parse HCPCS dosage '{hcpcs_dosage}'"
    qty, unit = m.groups()
    unit_family = _UNIT_ALIASES.get(unit.upper())
    if unit_family is None:
        return False, f"unrecognized HCPCS dosage unit '{unit}'"
    if float(qty) != 1.0:
        return False, f"HCPCS bills per {qty} {unit}, not per 1 -- not a 1:1 unit match"
    if unit_family != nadac_pricing_unit:
        return False, f"HCPCS unit family '{unit_family}' != NADAC pricing unit '{nadac_pricing_unit}'"
    return True, "units match 1:1"


def compare_asp_to_nadac(ndcs: list[str], force_refresh: bool = False) -> pd.DataFrame:
    asp_hits = fetch_asp.get_asp_per_billunit(ndcs, force_refresh=force_refresh)
    if asp_hits.empty:
        print("[module_f] No requested NDCs found in the ASP NDC-HCPCS crosswalk")
        return pd.DataFrame()

    nadac_df = fetch_nadac.load_nadac(force_refresh=force_refresh)
    nadac_hits = nadac_df.loc[nadac_df.index.intersection(asp_hits["ndc"])]

    merged = asp_hits.merge(
        nadac_hits[["ndc_description", "nadac_per_unit", "pricing_unit"]],
        left_on="ndc",
        right_index=True,
        how="left",
    )

    comparable, notes = [], []
    for _, row in merged.iterrows():
        ok, note = _reconcile_unit(row.get("hcpcs_dosage"), row.get("pricing_unit"))
        comparable.append(ok)
        notes.append(note)
    merged["units_directly_comparable"] = comparable
    merged["unit_note"] = notes
    merged["overcharge_per_billunit"] = merged["payment_limit"] - merged["nadac_per_unit"]
    merged.loc[~merged["units_directly_comparable"], "overcharge_per_billunit"] = pd.NA
    merged["net_per_unit"] = "not public"

    print(
        f"[module_f] {len(merged)} NDC-HCPCS rows compared; "
        f"{int(merged['units_directly_comparable'].sum())} with directly comparable units"
    )
    return merged


def run(ndcs: list[str], force_refresh: bool = False) -> pd.DataFrame:
    if not ndcs:
        print("[module_f] No oncology NDCs supplied -- nothing to compare. This module expects an "
              "explicit oncology drug/NDC list; Cost Plus's retail catalog rarely carries "
              "physician-administered oncology drugs (see module docstring).")
        return pd.DataFrame()
    return compare_asp_to_nadac(ndcs, force_refresh=force_refresh)
