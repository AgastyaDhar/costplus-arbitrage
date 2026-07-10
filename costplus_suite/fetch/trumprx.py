"""
TrumpRx listed-price loader/scraper.

Fallback path (unchanged): a hand-populated CSV at data/trumprx.csv
(brand_name, generic_name, dosage, trumprx_price, list_price) via
load_trumprx_prices(). Kept as the default/safe path -- and
scrape_full_trumprx_catalog()'s output (data/trumprx.SCRAPED.csv) is written
in this exact same shape, so it loads right back through
load_trumprx_prices() too.

Scrape path: trumprx.gov turns out to be scrapable after all, despite the
site being entirely Next.js-rendered. It streams page data the same way
costplusdrugs.com does (`self.__next_f.push([1, "<escaped JSON>"])` calls --
see shared/scrape_utils.py, shared by both scrapers), and:
  - /browse embeds the ENTIRE drug catalog as one `drugs` array (79 entries
    as of this build -- matches the expected catalog size exactly -- each:
    slug, drugName, genericName, lowestTrxPriceCents, lowestBeforePriceCents,
    hasMultipleVariants). This array is large enough that Next.js splits it
    across several push calls (confirmed live: a per-payload search came up
    empty; concatenating all decoded payloads before searching found it
    immediately), so discover_catalog() always searches the concatenation,
    never a single payload in isolation.
  - each /p/{slug} product page embeds a `drugVariants` array: one entry per
    dosage/form/quantity combination the drug is actually sold in (this is
    exactly what "Starting at $X" means for a `hasMultipleVariants` drug --
    e.g. Lantus lists both a SoloStar Pen and a Vial, each its own
    trxPrice/beforePrice in cents). This is richer than the page's own
    `application/ld+json` Drug/AggregateOffer block, which only publishes a
    single low/high price pair for the whole page and loses which dosage
    costs what -- so scrape_product() prefers drugVariants, falls back to
    the JSON-LD aggregate (one row, page-level) if that's missing, and only
    as a last resort regex-scans the rendered page text for a visible
    "$NNN.NN" price. Every row's `scrape_status` records which path produced
    it, so a fallback is visible, not silent.

robots.txt: trumprx.gov's own robots.txt is `Allow: /`, `Disallow: /api/` --
this module never touches /api/, only /browse and /p/*, and goes through
shared.scrape_utils.PoliteFetcher for the same rate-limit/disk-cache
behavior as fetch/costplus_html_scraper.py.

KNOWN ENVIRONMENT LIMITATION: trumprx.gov's CDN appears to fingerprint at
the TLS/transport layer, not just headers -- `curl` with this exact User-
Agent gets a clean 200 on /browse and /p/{slug}, while Python's `requests`
library (identical headers, same rate limit) gets a 403. This is not a
robots.txt or header problem (both were independently confirmed correct
during development: robots.txt is fetched and parsed correctly -- see
shared/scrape_utils.RobotsRules -- and can_fetch() returns True for these
paths) and there is no attempt here to spoof a TLS fingerprint to get past
it, since that crosses from "identify honestly as a browser" into active
evasion. Every function below is verified correct against real HTML
captured live via `curl` (the full 79-drug /browse catalog, plus
individual /p/{slug} pages including a multi-variant one); an unattended
`requests`-based run may need to be executed from an environment/IP this
CDN doesn't challenge, or through a real browser automation tool.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import scrape_utils  # noqa: E402

REQUIRED_COLUMNS = ["brand_name", "generic_name", "dosage", "trumprx_price", "list_price"]

BASE_URL = "https://trumprx.gov"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CACHE_SUBDIR = config.CACHE_DIR / "trumprx_scrape"

_fetcher = scrape_utils.PoliteFetcher(base_url=BASE_URL, user_agent=USER_AGENT, cache_dir=CACHE_SUBDIR)
_polite_get = _fetcher.get  # module-level name so tests can monkeypatch fetch.trumprx._polite_get


def load_trumprx_prices(path: Path | None = None) -> pd.DataFrame:
    """Load and validate the TrumpRx price list.

    Raises FileNotFoundError if data/trumprx.csv hasn't been populated yet --
    modules.e_brand_trumprx.trumprx_comparison() catches this and skips the
    comparison cleanly rather than fabricating one.
    """
    path = path or (config.DATA_DIR / "trumprx.csv")
    if not path.exists():
        raise FileNotFoundError(
            f"TrumpRx price list not found at {path}. Either supply this CSV by hand (columns: "
            "brand_name, generic_name, dosage, trumprx_price, list_price) or run "
            "fetch.trumprx.run_full_trumprx_scrape() to generate data/trumprx.SCRAPED.csv from live "
            "trumprx.gov data."
        )

    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")

    for col in ("trumprx_price", "list_price"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "SAMPLE" in path.name.upper():
        print(
            "[fetch.trumprx] *** WARNING: loading data/trumprx.SAMPLE.csv -- fabricated placeholder "
            "prices, NOT real TrumpRx data. Every downstream number is for pipeline testing only. ***"
        )
        df.attrs["is_sample"] = True
    else:
        df.attrs["is_sample"] = False

    print(f"[fetch.trumprx] Loaded {len(df):,} TrumpRx price rows from {path}")
    return df


# ---------------------------------------------------------------------------
# Catalog discovery: /browse's embedded `drugs` array
# ---------------------------------------------------------------------------
def discover_catalog(force_refresh: bool = False) -> list[dict]:
    """Every drug-core entry from /browse's embedded catalog: slug, drugName,
    genericName, lowestTrxPriceCents, lowestBeforePriceCents,
    hasMultipleVariants. See module docstring for why every decoded payload
    is concatenated before searching."""
    html = _polite_get("/browse", force_refresh=force_refresh)
    if html is None:
        return []
    combined = "".join(scrape_utils.find_next_f_payloads(html))
    marker = '"drugs":['
    pos = combined.find(marker)
    if pos == -1:
        print("[fetch.trumprx] Could not find the 'drugs' catalog array on /browse -- page structure may have changed")
        return []
    arr_start = pos + len(marker) - 1
    try:
        raw, _ = scrape_utils.scan_balanced(combined, arr_start)
        drugs = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"[fetch.trumprx] Failed to parse the 'drugs' catalog array: {exc}")
        return []
    print(f"[fetch.trumprx] Discovered {len(drugs):,} drugs from {BASE_URL}/browse")
    return drugs


# ---------------------------------------------------------------------------
# Per-product scrape: /p/{slug}'s embedded `drugVariants` array
# ---------------------------------------------------------------------------
def _extract_ldjson_drug(html: str) -> Optional[dict]:
    """The page's `application/ld+json` block whose @type is "Drug" (a page
    also carries a BreadcrumbList ld+json block, which this skips)."""
    combined = "".join(scrape_utils.find_next_f_payloads(html))
    marker = '"__html":"'
    idx = 0
    while True:
        pos = combined.find(marker, idx)
        if pos == -1:
            return None
        qstart = pos + len(marker) - 1
        try:
            text, end = scrape_utils.scan_quoted_string(combined, qstart)
        except ValueError:
            return None
        try:
            data = json.loads(text)
            if data.get("@type") == "Drug":
                return data
        except json.JSONDecodeError:
            pass
        idx = end


def _extract_drug_variants(html: str) -> tuple[Optional[str], list[dict]]:
    """Returns (medicationName, drugVariants) from a /p/{slug} page. Each
    drugVariants entry: form, strength, quantity, optionally size, and
    price.trxPrice / price.beforePrice in cents."""
    combined = "".join(scrape_utils.find_next_f_payloads(html))

    medication_name = None
    m = re.search(r'"medicationName":"([^"]*)"', combined)
    if m:
        medication_name = m.group(1)

    marker = '"drugVariants":['
    pos = combined.find(marker)
    if pos == -1:
        return medication_name, []
    arr_start = pos + len(marker) - 1
    try:
        raw, _ = scrape_utils.scan_balanced(combined, arr_start)
        variants = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return medication_name, []
    return medication_name, variants


def _format_dosage(strength: Optional[str], form: Optional[str], quantity: Optional[str], size: Optional[str]) -> Optional[str]:
    parts = [p for p in (strength, form) if p]
    dosage = " ".join(parts) if parts else None
    extras = []
    if quantity:
        extras.append(f"qty {quantity}")
    if size:
        extras.append(f"size {size}")
    if extras:
        dosage = f"{dosage}, {', '.join(extras)}" if dosage else ", ".join(extras)
    return dosage


def scrape_product(
    slug: str,
    generic_name_hint: Optional[str] = None,
    brand_name_hint: Optional[str] = None,
    force_refresh: bool = False,
) -> list[dict]:
    """Scrape one /p/{slug} page. Returns one row per dosage/form/quantity
    variant (REQUIRED_COLUMNS shape plus form/strength/quantity/slug/
    scrape_status), preferring drugVariants -> JSON-LD aggregate -> a last-
    resort rendered-text price scan (see module docstring). Never raises,
    never guesses a price that isn't shown somewhere on the page."""
    base_row = {
        "brand_name": brand_name_hint,
        "generic_name": generic_name_hint,
        "dosage": None,
        "trumprx_price": None,
        "list_price": None,
        "form": None,
        "strength": None,
        "quantity": None,
        "slug": slug,
        "scrape_status": "unmatched",
    }

    html = _polite_get(f"/p/{slug}", force_refresh=force_refresh)
    if html is None:
        return [{**base_row, "scrape_status": "fetch_failed"}]

    medication_name, variants = _extract_drug_variants(html)
    brand_name = medication_name or brand_name_hint

    if variants:
        rows = []
        for v in variants:
            price = v.get("price") or {}
            trx = price.get("trxPrice")
            before = price.get("beforePrice")
            form, strength, quantity, size = v.get("form"), v.get("strength"), v.get("quantity"), v.get("size")
            rows.append(
                {
                    "brand_name": brand_name,
                    "generic_name": generic_name_hint,
                    "dosage": _format_dosage(strength, form, quantity, size),
                    "trumprx_price": trx / 100 if trx is not None else None,
                    "list_price": before / 100 if before is not None else None,
                    "form": form,
                    "strength": strength,
                    "quantity": quantity,
                    "slug": slug,
                    "scrape_status": "ok" if trx is not None else "matched_no_price",
                }
            )
        return rows

    ldjson = _extract_ldjson_drug(html)
    if ldjson:
        offers = ldjson.get("offers") or {}
        low = offers.get("lowPrice")
        dosage_form = ldjson.get("dosageForm")
        return [
            {
                "brand_name": brand_name or ldjson.get("name"),
                "generic_name": generic_name_hint,
                "dosage": dosage_form,
                "trumprx_price": float(low) if low is not None else None,
                # JSON-LD's AggregateOffer never publishes a separate list/WAC price, only lowPrice/highPrice.
                "list_price": None,
                "form": dosage_form,
                "strength": None,
                "quantity": None,
                "slug": slug,
                "scrape_status": "ok_jsonld_fallback" if low is not None else "matched_no_price",
            }
        ]

    m = re.search(r"Starting at \$([0-9,]+\.\d{2})", html) or re.search(r"\$([0-9,]+\.\d{2})", html)
    if m:
        return [
            {
                **base_row,
                "brand_name": brand_name,
                "trumprx_price": float(m.group(1).replace(",", "")),
                "scrape_status": "fallback_text_parse",
            }
        ]

    return [{**base_row, "brand_name": brand_name, "scrape_status": "unparseable_page"}]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def scrape_full_trumprx_catalog(limit: int | None = None, force_refresh: bool = False) -> pd.DataFrame:
    catalog = discover_catalog(force_refresh=force_refresh)
    total_catalog_size = len(catalog)
    if limit is not None:
        catalog = catalog[:limit]

    rows = []
    for i, drug in enumerate(catalog, 1):
        slug = drug.get("slug")
        print(f"[fetch.trumprx] ({i}/{len(catalog)}) scraping {drug.get('drugName')} ({slug})...")
        rows.extend(
            scrape_product(
                slug,
                generic_name_hint=drug.get("genericName"),
                brand_name_hint=drug.get("drugName"),
                force_refresh=force_refresh,
            )
        )

    out = pd.DataFrame(rows)
    n_ok = out["scrape_status"].isin(["ok", "ok_jsonld_fallback", "fallback_text_parse"]).sum() if not out.empty else 0
    print(
        f"[fetch.trumprx] Scraped {len(out):,} row(s) across {len(catalog):,} drug(s) "
        f"(catalog has {total_catalog_size:,} total drugs), {n_ok} with a real price"
    )
    return out


def run_full_trumprx_scrape(limit: int | None = None, force_refresh: bool = False) -> dict:
    scraped_df = scrape_full_trumprx_catalog(limit=limit, force_refresh=force_refresh)
    out_path = config.DATA_DIR / "trumprx.SCRAPED.csv"
    scraped_df.to_csv(out_path, index=False)
    print(f"[fetch.trumprx] Wrote {len(scraped_df):,} rows -> {out_path}")
    return {"scraped": scraped_df, "output_path": out_path}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Scrape trumprx.gov for real TrumpRx prices")
    p.add_argument("--limit", type=int, default=None, help="Cap the number of drugs scraped this run")
    p.add_argument("--force-refresh", action="store_true", help="Bypass the on-disk HTML cache")
    args = p.parse_args()
    run_full_trumprx_scrape(limit=args.limit, force_refresh=args.force_refresh)
