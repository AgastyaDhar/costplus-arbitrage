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


# Dosage-form, route, and strength-unit words that carry no ingredient
# identity of their own -- stripped from a query term before the token-
# overlap relevance check in resolve_dispensable_rxcui() so a bare dose-form
# match (e.g. "pen", "tablet") can never substitute for an actual ingredient
# match.
_DOSAGE_FORM_WORDS = {
    "tablet", "tablets", "capsule", "capsules", "pen", "injector", "injection",
    "vial", "syringe", "kit", "cream", "ointment", "patch", "patches",
    "solution", "suspension", "powder", "single", "dose", "bottle", "of",
    "film", "coated", "chewable", "disintegrating", "spray", "drops",
    "gel", "lotion", "foam", "suppository", "inhaler", "elixir", "syrup",
    "packet", "packets", "strip", "strips", "device", "prefilled", "cartridge",
}
_ROUTE_WORDS = {
    "oral", "subcutaneous", "intravenous", "iv", "im", "intramuscular",
    "topical", "ophthalmic", "otic", "nasal", "rectal", "vaginal",
    "sublingual", "buccal", "transdermal", "inhalation",
}
_RELEASE_WORDS = {
    "extended", "delayed", "immediate", "sustained", "controlled", "release",
    "er", "xr", "dr", "sr", "cr", "ir",
}
_UNIT_WORDS = {
    "mg", "mcg", "g", "ml", "iu", "units", "unit", "meq", "mmol", "mL",
}
_RELEVANCE_STOPWORDS = _DOSAGE_FORM_WORDS | _ROUTE_WORDS | _RELEASE_WORDS | _UNIT_WORDS


def _extract_ingredient_tokens(term: str) -> list[str]:
    """Pull the meaningful (ingredient-identifying) tokens out of a free-text
    drug query: letters-only words, lowercased, with dosage-form/route/
    release/strength-unit words and short tokens dropped."""
    words = re.findall(r"[a-zA-Z]+", term.lower())
    return [w for w in words if w not in _RELEVANCE_STOPWORDS and len(w) > 2]


def _has_token_overlap(query_tokens: list[str], candidate_name: Optional[str]) -> bool:
    """True if at least one meaningful query token appears in the resolved
    RxNorm drug name (case-insensitive substring match)."""
    if not query_tokens or not candidate_name:
        return False
    lowered = candidate_name.lower()
    return any(tok in lowered for tok in query_tokens)


# Preference order among the dispensable TTYs once a candidate has passed
# the token-overlap relevance check: generic (SCD/GPCK) beats branded
# (SBD/BPCK) every time. The leaderboard is built entirely from Cost Plus's
# own generic catalog, so a branded RxCUI can never join to it even when
# it's a real, correctly-resolved drug -- and RxNav's fuzzy match reliably
# ranks a branded concept above the generic one whenever the query text
# contains a brand name (e.g. FTC's "Generic (Brand) Form" convention) or
# omits a strength (branded concepts often have a shorter/looser name that
# scores higher against an under-specified query). Only fall through to a
# branded type if no generic candidate appears anywhere in the ranked
# results -- some drugs are genuinely marketed brand-only.
_TTY_PREFERENCE = ["SCD", "GPCK", "SBD", "BPCK"]


def resolve_dispensable_rxcui(term: str, max_candidates_to_check: int = 30) -> Optional[dict]:
    """Walk approximateTerm candidates in rank order and return the
    best-preferred one whose RxNorm term type is dispensable
    (SCD/SBD/GPCK/BPCK, generic types preferred over branded -- see
    _TTY_PREFERENCE above) AND whose resolved name shares at least one
    meaningful token with the query (the token-overlap relevance check --
    RxNav's fuzzy match can rank an unrelated dispensable drug above rank 1
    when the query is dominated by generic dosage-form words like
    "single-dose pen"; without this check that unrelated candidate gets
    accepted as if it were correct, e.g. "tirzepatide Single-dose Pen"
    resolving to azithromycin). Default depth raised from 15 to 30 during
    the FTC name-format fix: a strength-less query (e.g. brand-stripped
    "Dalfampridine") can genuinely rank its only real SCD candidate as far
    down as rank 21 -- RxNav ranks strength-less SCDG/SCDF/IN concepts
    above it when there's no strength in the query text to match against.
    Scans stop early once an SCD passes (the best possible type -- nothing
    can outrank it), otherwise every candidate in the top N is checked so a
    later, more-generic type can
    still beat an earlier, more-branded one. Returns None if no candidate
    among the top N passes both checks.
    """
    candidates = approximate_term(term, max_entries=max_candidates_to_check)
    query_tokens = _extract_ingredient_tokens(term)
    best = None
    best_pref = None
    for c in candidates[:max_candidates_to_check]:
        tty = get_rxcui_tty(c["rxcui"])
        if tty not in DISPENSABLE_TTYS:
            continue
        resolved_name = get_rxcui_name(c["rxcui"])
        if not _has_token_overlap(query_tokens, resolved_name):
            continue
        pref = _TTY_PREFERENCE.index(tty) if tty in _TTY_PREFERENCE else len(_TTY_PREFERENCE)
        if best is None or pref < best_pref:
            best = {**c, "tty": tty, "resolved_name": resolved_name}
            best_pref = pref
            if best_pref == 0:  # SCD -- can't do better, stop scanning
                break
    if best is None:
        print(f"crosswalk: no confident match for {term}, excluded")
    return best


# Words that carry no ingredient/strength signal of their own and, when they
# are the *entire* remainder of a query after the ingredient name, only
# dilute RxNav's fuzzy match rather than helping it. Deliberately a small,
# explicit list (not the broader _DOSAGE_FORM_WORDS/_ROUTE_WORDS sets used
# for the token-overlap relevance check above) -- this is query
# construction, not relevance filtering, and stripping a real dose-form
# signal like "Injectable" or "Oral Liquid" could hurt a query that
# otherwise has nothing else to distinguish it.
_LOW_SIGNAL_TRAILING_WORDS = {"pill", "oral", "tablet"}
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")


def strip_brand_and_form(name: str) -> str:
    """Strip a parenthetical brand name and any trailing low-signal
    dose-form word, e.g. "Imatinib (Gleevec) Pill" -> "Imatinib". Named
    government/academic reports that study a drug at the ingredient level
    (no single strength) commonly write "Generic (Brand) Form" -- a brand
    name in the query text reliably makes RxNav's fuzzy match rank a
    branded (SBD) concept above the generic (SCD) one the leaderboard
    needs, even with _TTY_PREFERENCE in play, because the generic concept
    may not appear in the top-N ranked candidates at all when a brand name
    dominates the query. Pure function, never mutates the input -- a
    caller that needs the original string for citation display simply
    keeps its own reference to it; this only returns a cleaned-up query,
    it never used to build any output alongside it (the caller is Task 1's
    "separate field" for citation display).
    """
    stripped = _PARENTHETICAL_RE.sub(" ", name)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    words = stripped.split(" ")
    while words and words[-1].lower() in _LOW_SIGNAL_TRAILING_WORDS:
        words.pop()
    cleaned = " ".join(words).strip()
    return cleaned or stripped


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
    """Resolve a dispensable drug's RxCUI down to its ingredient name(s)
    (RxNorm TTY=IN), e.g. 617310 'atorvastatin 20 MG Oral Tablet' -> 'atorvastatin'.
    Used to join against Part D / SDUD, which are keyed by generic ingredient
    name text rather than NDC and aggregate across all strengths of that
    ingredient (see METHODOLOGY.md for the granularity mismatch this implies).

    A combination product's RxCUI relates to MULTIPLE IN concepts (one per
    active ingredient) -- RxNav's response lists all of them under the same
    "IN" conceptGroup. Taking only the first (as this function used to)
    silently drops every ingredient but one, so e.g. "Amlodipine Besylate-
    Atorvastatin Calcium" resolved to bare "amlodipine" and collided with
    plain amlodipine tablets under the same molecule join key downstream --
    a real bug caught when 33 strength/combo rows all landed on one
    "Amlodipine" Part D figure. Every IN name is now collected, deduped, and
    joined (sorted for determinism) so a combo forms its own distinct key
    that can never equal a single-ingredient drug's key.
    """
    url = f"{config.RXNAV_BASE}/rxcui/{rxcui}/related.json?tty=IN"
    data = cached_get_json(url, subdir="rxnav_related")
    groups = (data.get("relatedGroup") or {}).get("conceptGroup") or []
    for g in groups:
        if g.get("tty") == "IN":
            props = g.get("conceptProperties") or []
            names = sorted({p["name"] for p in props if p.get("name")}, key=str.casefold)
            if names:
                return "/".join(names)
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


_GENERIC_TTYS = {"SCD", "GPCK"}


def crosswalk_drug(term: str, nadac_df: pd.DataFrame) -> CrosswalkResult:
    result = CrosswalkResult(drug_term=term)

    resolved = resolve_dispensable_rxcui(term)
    fallback_used = None

    def _try_variant(variant_term: str, tag: str) -> None:
        """Try an alternate query text, keeping it only if it beats what
        we already have by _TTY_PREFERENCE -- never overrides an existing
        *generic* resolution, but a raw-term success that only found a
        branded (SBD/BPCK) candidate is not final: a fallback variant that
        finds a generic one instead must still win. `resolved`/
        `fallback_used` are captured by reference via nonlocal."""
        nonlocal resolved, fallback_used
        if variant_term == term:
            return
        candidate = resolve_dispensable_rxcui(variant_term)
        if candidate is None:
            return
        if resolved is None or _TTY_PREFERENCE.index(candidate["tty"]) < _TTY_PREFERENCE.index(resolved["tty"]):
            resolved = candidate
            fallback_used = tag

    # Both fallbacks below are skipped entirely once `resolved` is already
    # generic-tier (SCD/GPCK) -- nothing can improve on that, so a term
    # that already resolves cleanly never triggers a single extra RxNav
    # call. They keep firing, in order, as long as the current best is
    # None or branded (SBD/BPCK): a raw-term "success" that only found a
    # branded candidate is exactly the FTC "Generic (Brand) Form" failure
    # mode (_TTY_PREFERENCE alone can't fix it when no generic candidate
    # ranks in the raw query's top N at all -- the brand name has to come
    # out of the query text first for a generic one to surface).
    if resolved is None or resolved["tty"] not in _GENERIC_TTYS:
        _try_variant(_dedupe_redundant_release_wording(term), "name-normalization")
    if resolved is None or resolved["tty"] not in _GENERIC_TTYS:
        _try_variant(strip_brand_and_form(term), "brand-strip")

    if resolved is None:
        result.note = "no dispensable (SCD/SBD) RxCUI found in top candidates"
        return result

    fallback_tag = f"[via {fallback_used} fallback] " if fallback_used else ""
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
