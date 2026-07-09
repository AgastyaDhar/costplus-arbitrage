"""
Drug-name -> RxCUI -> NDC set -> NADAC price crosswalk.

This is the hardest plumbing in the whole suite: free-text drug descriptions
("atorvastatin 20 mg tablet") have to be turned into NDCs that actually show
up in CMS pricing files, and every price has to land on the same *per-unit*
basis (NADAC's own Pricing Unit -- EA / ML / GM) before anything gets
compared. Nothing here estimates a price; it only resolves identifiers and
passes NADAC's own per-unit number through unchanged.

Resolution pipeline for one free-text drug term:
    1. RxNav approximateTerm.json   -> ranked candidate RxCUIs (fuzzy match)
    2. filter candidates to "dispensable" term types (SCD/SBD/GPCK/BPCK) --
       RxNav's approximate match frequently ranks the bare ingredient+strength
       concept (SCDC, e.g. "atorvastatin 20 MG") above the dispensable
       clinical drug (SCD, e.g. "atorvastatin 20 MG Oral Tablet"). Only the
       latter carries an NDC set, so we walk the ranked list and take the
       first candidate whose term type is dispensable rather than trusting
       rank 1 blindly.
    3. RxNav rxcui/{rxcui}/ndcs.json -> the NDC set for that dispensable drug
    4. NADAC lookup by NDC          -> NADAC Per Unit + Pricing Unit

The NADAC distribution ID is never hardcoded: discover_latest_nadac() queries
data.medicaid.gov's metastore API at runtime, prints what it resolved, and
caches both the metadata and the CSV to disk so reruns are offline.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import http_cache  # noqa: E402
from fetch.nadac import discover_latest_nadac, load_nadac  # noqa: E402,F401

cached_get_json = http_cache.cached_get_json  # local alias, used throughout below

# RxNorm term types that denote a *dispensable* drug (has a real dose form,
# therefore has an NDC set). SCDC/IN/PIN etc. are ingredient-level concepts
# with no packaging and no NDCs.
DISPENSABLE_TTYS = {"SCD", "SBD", "GPCK", "BPCK"}


# ---------------------------------------------------------------------------
# RxNav: term -> RxCUI -> NDCs
# ---------------------------------------------------------------------------
def approximate_term(term: str, max_entries: int = 20) -> list[dict]:
    """Fuzzy-match free text to RxNorm candidates, deduplicated by RxCUI in
    rank order (RxNav returns one row per source vocabulary match, so the
    same RxCUI often repeats)."""
    url = (
        f"{config.RXNAV_BASE}/approximateTerm.json"
        f"?term={requests.utils.quote(term)}&maxEntries={max_entries}"
    )
    data = cached_get_json(url, subdir="rxnav_approx")
    raw_candidates = data.get("approximateGroup", {}).get("candidate") or []

    seen = set()
    deduped = []
    for c in raw_candidates:
        rxcui = c.get("rxcui")
        if not rxcui or rxcui in seen:
            continue
        seen.add(rxcui)
        deduped.append(
            {
                "rxcui": rxcui,
                "name": c.get("name"),
                "rank": int(c.get("rank", 0)),
                "score": float(c.get("score", 0)),
            }
        )
    deduped.sort(key=lambda c: c["rank"])
    return deduped


def get_rxcui_tty(rxcui: str) -> Optional[str]:
    url = f"{config.RXNAV_BASE}/rxcui/{rxcui}/properties.json"
    data = cached_get_json(url, subdir="rxnav_props")
    return (data.get("properties") or {}).get("tty")


def get_rxcui_name(rxcui: str) -> Optional[str]:
    url = f"{config.RXNAV_BASE}/rxcui/{rxcui}/properties.json"
    data = cached_get_json(url, subdir="rxnav_props")
    return (data.get("properties") or {}).get("name")


def resolve_dispensable_rxcui(term: str, max_candidates_to_check: int = 15) -> Optional[dict]:
    """Walk approximateTerm candidates in rank order and return the first one
    whose RxNorm term type is dispensable (SCD/SBD/GPCK/BPCK). Returns None
    if no dispensable candidate is found among the top N.
    """
    candidates = approximate_term(term, max_entries=max_candidates_to_check)
    for c in candidates[:max_candidates_to_check]:
        tty = get_rxcui_tty(c["rxcui"])
        if tty in DISPENSABLE_TTYS:
            return {**c, "tty": tty, "resolved_name": get_rxcui_name(c["rxcui"])}
    return None


def normalize_ndc(raw_ndc: str) -> str:
    digits = re.sub(r"\D", "", raw_ndc)
    return digits.zfill(config.NDC_LENGTH)


# Common salt/ester suffixes that RxNorm ingredient names and CMS's free-text
# Gnrc_Name / Product Name fields disagree on including (e.g. RxNav ingredient
# "atorvastatin" vs Part D Gnrc_Name "Atorvastatin Calcium"). Stripped before
# matching so the join isn't defeated by salt-form spelling differences.
_SALT_SUFFIXES = [
    "HYDROCHLORIDE", "HCL", "SODIUM", "POTASSIUM", "CALCIUM", "MAGNESIUM",
    "SUCCINATE", "TARTRATE", "MALEATE", "MESYLATE", "SULFATE", "FUMARATE",
    "CITRATE", "PHOSPHATE", "ACETATE", "BESYLATE", "DIPROPIONATE",
    "HYDROBROMIDE", "HYDROGEN", "EXTENDED", "RELEASE", "ER", "XR", "SR",
]
_SALT_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(_SALT_SUFFIXES) + r")\b"
)


def normalize_drug_name(name: str) -> str:
    """Uppercase, strip salt/ester/formulation suffixes and non-letters, so
    RxNorm ingredient names can be joined against CMS's free-text generic
    name fields (Part D Gnrc_Name, SDUD Product Name)."""
    if not name:
        return ""
    upper = name.upper()
    stripped = _SALT_SUFFIX_RE.sub(" ", upper)
    letters_only = re.sub(r"[^A-Z ]", " ", stripped)
    return re.sub(r"\s+", " ", letters_only).strip()


# A scraped Cost Plus catalog term is built by naive concatenation of the
# site's own drug/strength/form fields (see shared/costplus.py), and those
# two fields are sometimes independently verbose about release timing --
# e.g. drug="Lithium Carbonate Extended Release (ER)", form="Extended
# Release Tablet" -- producing a query with "Extended Release" (or its
# "(ER)" abbreviation) repeated. Confirmed directly against RxNav during
# development: the redundant, over-long query can surface only a dose-form-
# level concept (TTY SCDF/SCDFP) instead of the actual dispensable (SCD)
# one; removing ONE of the duplicate occurrences is enough to fix it. This
# never changes which ingredient/strength/release-type is being asked about,
# only removes literal word-for-word repetition -- pure formatting.
_RELEASE_PHRASES = [
    "extended release", "extended-release", "delayed release", "delayed-release",
    "sustained release", "controlled release", "immediate release",
]
_RELEASE_ABBREV_RE = re.compile(r"\s*\((er|xr|dr|sr|cr|ir)\)", re.IGNORECASE)
_RELEASE_ABBREV_EXPANSIONS = {
    "er": "extended release", "xr": "extended release", "dr": "delayed release",
    "sr": "sustained release", "cr": "controlled release", "ir": "immediate release",
}


def _dedupe_redundant_release_wording(term: str) -> str:
    """Collapse a release-timing phrase (or its parenthetical abbreviation)
    that appears more than once in `term` down to a single occurrence."""
    t = term
    m = _RELEASE_ABBREV_RE.search(t)
    if m and _RELEASE_ABBREV_EXPANSIONS.get(m.group(1).lower(), "\0") in t.lower():
        t = _RELEASE_ABBREV_RE.sub("", t)
    lowered = t.lower()
    for phrase in _RELEASE_PHRASES:
        first = lowered.find(phrase)
        if first == -1:
            continue
        second = lowered.find(phrase, first + len(phrase))
        if second != -1:
            t = t[:second] + t[second + len(phrase):]
            lowered = t.lower()
    return re.sub(r"\s+", " ", t).strip()


def get_ingredient_name(rxcui: str) -> Optional[str]:
    """Resolve a dispensable drug's RxCUI down to its bare ingredient name
    (RxNorm TTY=IN), e.g. 617310 'atorvastatin 20 MG Oral Tablet' -> 'atorvastatin'.
    Used to join against Part D / SDUD, which are keyed by generic ingredient
    name text rather than NDC and aggregate across all strengths of that
    ingredient (see METHODOLOGY.md for the granularity mismatch this implies).
    """
    url = f"{config.RXNAV_BASE}/rxcui/{rxcui}/related.json?tty=IN"
    data = cached_get_json(url, subdir="rxnav_related")
    groups = (data.get("relatedGroup") or {}).get("conceptGroup") or []
    for g in groups:
        if g.get("tty") == "IN":
            props = g.get("conceptProperties") or []
            if props:
                return props[0]["name"]
    return None


def get_ndcs_for_rxcui(rxcui: str) -> list[str]:
    url = f"{config.RXNAV_BASE}/rxcui/{rxcui}/ndcs.json"
    data = cached_get_json(url, subdir="rxnav_ndcs")
    ndcs = (data.get("ndcGroup") or {}).get("ndcList", {}).get("ndc") or []
    return [normalize_ndc(n) for n in ndcs]


# ---------------------------------------------------------------------------
# End-to-end per-drug resolution
# ---------------------------------------------------------------------------
@dataclass
class CrosswalkResult:
    drug_term: str
    rxcui: Optional[str] = None
    resolved_name: Optional[str] = None
    ingredient_name: Optional[str] = None
    tty: Optional[str] = None
    ndc_count: int = 0
    matched_ndc_count: int = 0
    matched_ndcs: list = None
    nadac_per_unit: Optional[float] = None
    pricing_unit: Optional[str] = None
    pricing_unit_consistent: bool = True
    matched: bool = False
    note: str = ""


def crosswalk_drug(term: str, nadac_df: pd.DataFrame) -> CrosswalkResult:
    result = CrosswalkResult(drug_term=term)

    resolved = resolve_dispensable_rxcui(term)
    fallback_used = False
    if resolved is None:
        # Only ever tried after the raw term has already failed -- never
        # overrides an existing successful resolution, so this can't change
        # any drug that already matches, only rescue a subset of failures.
        cleaned = _dedupe_redundant_release_wording(term)
        if cleaned != term:
            resolved = resolve_dispensable_rxcui(cleaned)
            fallback_used = resolved is not None
    if resolved is None:
        result.note = "no dispensable (SCD/SBD) RxCUI found in top candidates"
        return result

    fallback_tag = "[via name-normalization fallback] " if fallback_used else ""
    result.rxcui = resolved["rxcui"]
    result.resolved_name = resolved["resolved_name"]
    result.tty = resolved["tty"]
    result.ingredient_name = get_ingredient_name(result.rxcui)

    ndcs = get_ndcs_for_rxcui(result.rxcui)
    result.ndc_count = len(ndcs)
    if not ndcs:
        result.note = fallback_tag + "RxCUI resolved but RxNav returned zero NDCs"
        return result

    hits = nadac_df.loc[nadac_df.index.intersection(ndcs)]
    result.matched_ndc_count = len(hits)
    if hits.empty:
        result.note = fallback_tag + "resolved NDCs, but none priced in the current NADAC file"
        return result

    if fallback_used:
        result.note = fallback_tag.strip()
    units = hits["pricing_unit"].unique().tolist()
    result.pricing_unit_consistent = len(units) == 1
    result.pricing_unit = hits["pricing_unit"].mode().iloc[0]
    result.nadac_per_unit = float(hits["nadac_per_unit"].median())
    result.matched_ndcs = list(hits.index)
    result.matched = True
    if not result.pricing_unit_consistent:
        result.note = f"mixed pricing units across matched NDCs: {units}"
    return result
