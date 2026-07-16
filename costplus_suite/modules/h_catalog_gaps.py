"""
Module H: catalog gap audit.

Cross-references every drug with a confirmed public markup citation
(`data/public_spreads.csv` -- the raw extraction layer covering every
source: FTC, Maine MHDO, JAMA, the litigation complaints, etc.) against
the raw Cost Plus catalog (`data/costplus.GRAPHQL.csv`, 2,386 rows) to
find genuine catalog gaps: drugs Cost Plus simply doesn't stock, as
opposed to drugs that are stocked but fell out of the leaderboard for an
unrelated pipeline reason (a crosswalk bug, no current NADAC price for
any resolved NDC, etc. -- see modules/i_unpriced_drugs.py for that
second bucket).

This was a static, hand-built CSV until the "catalog_gaps.csv is wrong
for 3 of 5 drugs" audit: Glatiramer, Mycophenolic Acid, and Ribavirin
were all hand-listed as catalog gaps during the litigation-disambiguation
pass, but are all actually present in the raw catalog -- the file was
never re-derived after later crosswalk fixes changed what could resolve.
Regenerating it directly from the raw catalog on every run removes that
class of staleness entirely: a drug can only ever be listed here if a
substring search of the *entire* raw catalog (`drug` and `brand_name`
columns) for its ingredient name and every brand name attached to its
citations comes back empty.

Deliberately checks the RAW catalog, not the leaderboard: the
leaderboard already excludes brand rows (generics_only) and rows that
failed to price against NADAC, neither of which means Cost Plus doesn't
sell the drug (see modules/i_unpriced_drugs.py).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

PUBLIC_SPREADS_PATH = config.ROOT_DIR / "data" / "public_spreads.csv"
COSTPLUS_CATALOG_PATH = config.DATA_DIR / "costplus.GRAPHQL.csv"

_PARENTHETICAL_RE = re.compile(r"\(([^)]*)\)")
# Stripped from the front of a drug_name before taking its first word as
# the catalog search key -- these are qualifier words some source rows
# lead with, not part of the ingredient name itself.
_LEADING_STRIP_WORDS = {"generic"}


def _search_key_and_brand(drug_name: str) -> tuple[str, str | None]:
    """First significant word of the ingredient name (catalog search key)
    and, if present, the parenthetical's contents (a brand name for
    FTC/DrugPatentWatch rows, a manufacturer name for Maine rows -- either
    way, worth searching too since brand_name is a real catalog column).
    "Abiraterone (Zytiga) Pill" -> ("abiraterone", "Zytiga").
    "Mycophenolate Sodium Tablet" -> ("mycophenolate", None).
    """
    paren_match = _PARENTHETICAL_RE.search(drug_name)
    brand = paren_match.group(1).strip() if paren_match else None
    without_paren = _PARENTHETICAL_RE.sub(" ", drug_name)
    words = re.findall(r"[a-zA-Z]+", without_paren)
    words = [w for w in words if w.lower() not in _LEADING_STRIP_WORDS]
    first = words[0].lower() if words else ""
    return first, brand


def _in_raw_catalog(search_key: str, brand: str | None, catalog: pd.DataFrame) -> bool:
    if not search_key:
        return False
    hit = catalog["drug"].str.lower().str.contains(re.escape(search_key), na=False)
    if brand:
        hit = hit | catalog["brand_name"].astype(str).str.lower().str.contains(re.escape(brand.lower()), na=False)
    return bool(hit.any())


def run(
    public_spreads_path: Path | None = None,
    costplus_catalog_path: Path | None = None,
) -> pd.DataFrame:
    """Returns one row per drug_name with zero hits anywhere in the raw
    Cost Plus catalog, columns: drug_name, <source>_markup_pct... (one
    column per distinct source_name that cites the drug, taking the
    highest confirmed value from that source), source, source_page.
    Empty DataFrame (not an error) if every cited drug is stocked."""
    spreads = pd.read_csv(public_spreads_path or PUBLIC_SPREADS_PATH)
    catalog = pd.read_csv(costplus_catalog_path or COSTPLUS_CATALOG_PATH)

    # Scoped to markup_pct rows: catalog_gaps.csv's columns are named
    # "<source>_markup_pct", so a drug whose only citation is a
    # spread_dollars/spread_pct figure (a different unit, not comparable
    # via a single "highest value wins" pick) doesn't belong in this
    # specific audit -- see g_public_citations.load_citations() for how
    # the main pipeline already handles mixing those types correctly.
    spreads = spreads[spreads["metric_type"] == "markup_pct"].copy()
    # Group by the ingredient search key, not the literal drug_name string
    # -- different sources name the same molecule differently (FTC:
    # "Octreotide (Sandostatin) Injectable", litigation: "Octreotide
    # Acetate"), and without this a single real gap drug would appear as
    # multiple, confusingly-separate rows, one per source's own spelling.
    keys_and_brands = spreads["drug_name"].apply(_search_key_and_brand)
    spreads["_search_key"] = [kb[0] for kb in keys_and_brands]
    spreads["_brand"] = [kb[1] for kb in keys_and_brands]

    gap_keys = set()
    for key, sub in spreads.groupby("_search_key"):
        brands = [b for b in sub["_brand"].unique() if isinstance(b, str) and b]
        if not any(_in_raw_catalog(key, b, catalog) for b in (brands or [None])):
            gap_keys.add(key)

    if not gap_keys:
        print("[module_h] catalog gap audit: every cited drug is present in the raw Cost Plus catalog")
        return pd.DataFrame(columns=["drug_name", "source", "source_page"])

    gap_rows = spreads[spreads["_search_key"].isin(gap_keys)]

    records = []
    for key, group in gap_rows.groupby("_search_key"):
        # Shortest distinct drug_name spelling as the display name -- a
        # simple, deterministic proxy for "the plain ingredient name"
        # (FTC/litigation names are longer -- brand parentheticals, dose
        # forms -- than a bare ingredient string).
        display_name = min(group["drug_name"].unique(), key=len)
        sources = []
        pages = []
        record = {"drug_name": display_name}
        for source_name, sub in group.groupby("source_name"):
            best = sub.loc[sub["value"].idxmax()]
            col = _source_column_name(source_name)
            # Two sources can share a column tag (both FTC reports ->
            # "ftc"); keep the higher value if that happens.
            if col not in record or best["value"] > record[col]:
                record[col] = best["value"]
            sources.append(source_name)
            pages.append(f"{int(best['source_page'])} ({col.replace('_markup_pct', '')})"
                          if str(best["source_page"]).replace(".", "", 1).isdigit()
                          else f"N/A ({col.replace('_markup_pct', '')})")
        record["source"] = "; ".join(sources)
        record["source_page"] = " / ".join(pages)
        records.append(record)

    out = pd.DataFrame(records).sort_values("drug_name").reset_index(drop=True)
    print(f"[module_h] catalog gap audit: {len(out)} drug(s) with a confirmed public markup and "
          f"zero hits in the raw Cost Plus catalog ({len(catalog)} rows)")
    return out


# Short, stable column-name tag per source, used as the "<tag>_markup_pct"
# column header -- keeps catalog_gaps.csv readable regardless of how many
# distinct sources end up contributing a gap drug in the future.
_SOURCE_TAGS = {
    "Lewandowski v. Johnson & Johnson, ERISA Class Action Complaint (D.N.J. 1:24-cv-00671, filed Feb. 2024)": "jj",
    "Navarro v. Wells Fargo & Co., ERISA Class Action Complaint (D. Minn. 0:24-cv-03043, filed Jul. 2024)": "wf",
    "FTC Second Interim Staff Report (Jan 2025)": "ftc",
    "FTC First Interim Staff Report (Jul 2024)": "ftc",
}


def _source_column_name(source_name: str) -> str:
    tag = _SOURCE_TAGS.get(source_name)
    if tag is None:
        tag = re.sub(r"[^a-z0-9]+", "_", source_name.lower())[:20].strip("_")
    return f"{tag}_markup_pct"


if __name__ == "__main__":
    result = run()
    print(result.to_string(index=False))
