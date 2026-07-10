"""
Cost Plus Drugs live product-page scraper -- the refresh path for data/costplus.csv.

shared/costplus.py (the primary path) stays untouched: it loads a human-
supplied CSV and refuses to guess. This module instead pulls REAL data
straight from costplusdrugs.com product pages for whatever drugs are already
in that CSV, and reports back what it found. Robots.txt is checked live
(urllib.robotparser) before every fetch; costplusdrugs.com's own robots.txt
explicitly `Allow: /medications/*`, which is the only path this module ever
requests. Every response is cached to disk (cache/costplus_scrape/) so reruns
are offline, and consecutive live requests are spaced 2-3s apart.

IMPORTANT LIMITATION, found during live reconnaissance and worth being loud
about: a costplusdrugs.com product page publishes the drug's name/strength/
form, its brand name, its flat shipping fee, and the final all-in price a
customer pays -- via a clean `application/ld+json` Product/Offer block plus a
richer (but undocumented, React Server Component-serialized) `productDetails`
object carrying sibling strengths. It does NOT publish, anywhere, the
acquisition cost Cost Plus pays its supplier or the markup/pharmacy-fee
breakdown that produces the final price -- those are exactly the inputs Cost
Plus's cost-plus pricing model keeps private; only the output is public. This
isn't a temporary gap in this scraper, it's structural, the same way NADAC is
never net-of-rebate (see METHODOLOGY.md). Per instruction, this module leaves
`acquisition_cost`, `markup`, `pharmacy_fee`, and `package_quantity` (no
verified per-fill tablet count is exposed either -- see NOTE on package_size
below) BLANK in its output rather than back-solving them from the published
15% markup / $5 fee policy, which would silently assume that policy applies
uniformly per SKU (it may not -- `specialtyMedication` products are flagged
separately on the site, suggesting it doesn't always).

NOTE on `package_size`: each product-page variant carries a `package_size`
metafield, but it was observed IDENTICAL ("1000") across four different
strengths of the same drug family during reconnaissance -- inconsistent with
it being a literal per-fill tablet count for an oral solid Cost Plus sells in
30/90-count fills. Rather than guess what it means, it is surfaced verbatim
as `package_size_raw` for a human to interpret, never renamed to
`package_quantity`.

What this module DOES give you that's new and real: `final_price` (the
actual current price for that drug/strength, scraped live -- shared/
costplus.py's source-aware costplus_per_unit falls back to
final_price/package_quantity exactly when the breakdown fields are blank)
and `shipping_fee` (also real, from the JSON-LD `Offer.shippingDetails`). If
your data/costplus.csv row already has acquisition_cost/markup/pharmacy_fee/
package_quantity filled in by hand, `implied_costplus_per_unit` lets you
sanity-check that hand-entered formula against the real observed price.

Two scrape modes:
  - refresh_catalog() / run(): refreshes whatever drugs are ALREADY in a
    data/costplus.csv-shaped file (the original Task 1 scope).
  - scrape_full_catalog() / run_full_catalog(): enumerates and scrapes EVERY
    product on costplusdrugs.com via the live sitemap, one row per strength/
    form variant. A product page's sibling variants (e.g. atorvastatin's
    10/20/40/80mg) are all returned from a SINGLE fetch of any one of their
    slugs (they share one `productDetails.variants[]` payload), so slugs
    already covered by an earlier fetch are skipped -- fewer live requests
    for the same real data, not a shortcut on correctness.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import costplus as costplus_mod  # noqa: E402
from shared import crosswalk  # noqa: E402
from shared import scrape_utils  # noqa: E402

BASE_URL = "https://www.costplusdrugs.com"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"

# A generic non-browser UA (matching shared/http_cache.py's suite-wide identity)
# is blocked at the CDN edge before robots.txt is ever consulted, regardless of
# what robots.txt itself allows -- confirmed live: this suite's own UA got a
# bare 403 on every /medications/* page. This UA is a standard browser string,
# nothing more (no header spoofing, no evasion of rate limits or challenges);
# costplusdrugs.com's own robots.txt explicitly invites crawling of this exact
# path (`Allow: /medications/*`), so the CDN-layer block is a volume/bot-abuse
# mitigation, not this site's stated policy, which this module respects by
# self-throttling below regardless of what it could get away with.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CACHE_SUBDIR = config.CACHE_DIR / "costplus_scrape"

REQUIRED_COLUMNS = costplus_mod.REQUIRED_COLUMNS  # keep in lockstep with the schema shared/costplus.py validates

_fetcher = scrape_utils.PoliteFetcher(base_url=BASE_URL, user_agent=USER_AGENT, cache_dir=CACHE_SUBDIR)
_polite_get = _fetcher.get  # module-level name so tests can monkeypatch `costplus_scraper._polite_get`
_cache_path = _fetcher.cache_path

# Shared Next.js payload-scanning helpers (see shared/scrape_utils.py) -- bound
# here under their original names so existing tests referencing
# `costplus_scraper._scan_quoted_string` / `._scan_balanced` keep working.
_scan_quoted_string = scrape_utils.scan_quoted_string
_scan_balanced = scrape_utils.scan_balanced
_find_next_f_payloads = scrape_utils.find_next_f_payloads


def extract_jsonld(html: str) -> Optional[dict]:
    """The page's `<script type="application/ld+json" id="product-jsonld">`
    block: a clean schema.org Product/Offer object with the real final price
    and flat shipping fee for THIS page's specific strength."""
    for payload in _find_next_f_payloads(html):
        if "product-jsonld" not in payload:
            continue
        marker = '"__html":"'
        pos = payload.find(marker)
        if pos == -1:
            continue
        qstart = pos + len(marker) - 1
        try:
            jsonld_text, _ = _scan_quoted_string(payload, qstart)
            return json.loads(jsonld_text)
        except (ValueError, json.JSONDecodeError):
            continue
    return None


def extract_product_details(html: str) -> Optional[dict]:
    """The richer (undocumented) `productDetails` object: ingredient name plus
    every sibling strength/form variant with its own price, so one page fetch
    often yields several catalog rows' worth of real data."""
    for payload in _find_next_f_payloads(html):
        marker = '"productDetails":'
        pos = payload.find(marker)
        if pos == -1:
            continue
        obj_start = pos + len(marker)
        if obj_start >= len(payload) or payload[obj_start] != "{":
            continue
        try:
            raw, _ = _scan_balanced(payload, obj_start)
            return json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Catalog discovery + fuzzy slug matching
# ---------------------------------------------------------------------------
def discover_catalog_slugs(force_refresh: bool = False) -> list[str]:
    """Every `/medications/<slug>/` product URL in the live sitemap (one
    fetch, cached). Category/listing pages are excluded."""
    xml = _polite_get(SITEMAP_URL, force_refresh=force_refresh)
    if xml is None:
        return []
    locs = re.findall(r"<loc>([^<]*)</loc>", xml)
    slugs = []
    for loc in locs:
        m = re.match(r"^https?://(?:www\.)?costplusdrugs\.com/medications/([a-z0-9][a-z0-9_-]*)/?$", loc)
        if m and m.group(1) != "categories":
            slugs.append(m.group(1))
    print(f"[costplus_scraper] Discovered {len(slugs):,} product slugs from {SITEMAP_URL}")
    return slugs


def _tokenize(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _strength_number(s: str) -> Optional[str]:
    m = re.search(r"(\d+(?:\.\d+)?)", s.replace(",", ""))
    return m.group(1).rstrip("0").rstrip(".") if m else None


def match_slug_for_drug(drug: str, strength: str, form: str, slugs: list[str]) -> Optional[str]:
    """Best-effort fuzzy match from a catalog row to a real product slug:
    every significant drug-name token must appear in the slug, and the
    strength's leading number must match. Returns None (never a low-confidence
    guess) if nothing clears that bar."""
    drug_norm = crosswalk.normalize_drug_name(drug)  # strips salt suffixes so e.g. "losartan potassium" -> "LOSARTAN"
    drug_tokens = _tokenize(drug_norm) or _tokenize(drug)
    want_strength = _strength_number(strength)
    form_tokens = _tokenize(form)

    best, best_score = None, 0
    for slug in slugs:
        slug_tokens = _tokenize(slug.replace("-", " ").replace("_", "."))
        if not drug_tokens.issubset(slug_tokens):
            continue
        slug_strength = _strength_number(slug)
        if want_strength and slug_strength != want_strength:
            continue
        score = len(drug_tokens) + (1 if form_tokens & slug_tokens else 0)
        if score > best_score:
            best, best_score = slug, score
    return best


# ---------------------------------------------------------------------------
# Row-level scrape
# ---------------------------------------------------------------------------
def scrape_drug(drug: str, strength: str, form: str, slugs: list[str], force_refresh: bool = False) -> dict:
    """Scrape one catalog row. Always returns a dict with every REQUIRED_COLUMNS
    key present (blank/None where the site doesn't expose the value) plus
    scraper-specific metadata columns, never raises."""
    out = {
        "drug": drug,
        "strength": strength,
        "form": form,
        "package_quantity": None,   # not exposed with confidence -- see module docstring
        "acquisition_cost": None,   # never public -- Cost Plus's own supplier cost
        "markup": None,             # not exposed per-SKU (shared/costplus.py already
                                     # defaults this to config.COSTPLUS_MARKUP if blank)
        "pharmacy_fee": None,       # not exposed per-SKU
        "shipping_fee": None,
        "brand_name": None,
        "sku": None,
        "package_size_raw": None,
        "final_price": None,
        "scrape_matched_slug": None,
        "scrape_status": "unmatched",
    }

    slug = match_slug_for_drug(drug, strength, form, slugs)
    if slug is None:
        out["scrape_status"] = "no_matching_slug"
        return out
    out["scrape_matched_slug"] = slug

    html = _polite_get(f"{BASE_URL}/medications/{slug}/", force_refresh=force_refresh)
    if html is None:
        out["scrape_status"] = "fetch_failed"
        return out

    jsonld = extract_jsonld(html)
    details = extract_product_details(html)
    if jsonld is None and details is None:
        out["scrape_status"] = "unparseable_page"
        return out

    if jsonld:
        out["brand_name"] = (jsonld.get("brand") or {}).get("name")
        out["sku"] = jsonld.get("sku")
        offers = jsonld.get("offers") or {}
        out["final_price"] = offers.get("price")
        shipping_rate = (offers.get("shippingDetails") or {}).get("shippingRate") or {}
        out["shipping_fee"] = shipping_rate.get("value")

    if details:
        want_strength = _strength_number(strength)
        for variant in details.get("variants") or []:
            mf = variant.get("metafields") or {}
            if want_strength and _strength_number(mf.get("strength", "")) != want_strength:
                continue
            out["package_size_raw"] = mf.get("package_size")
            if out["final_price"] is None:
                out["final_price"] = variant.get("priceCalculation")
            break

    out["scrape_status"] = "ok" if out["final_price"] is not None else "matched_no_price"
    return out


# ---------------------------------------------------------------------------
# Full-catalog scrape: every variant on every product page, not just rows
# already present in an existing costplus.csv.
# ---------------------------------------------------------------------------
def _rows_from_product_page(html: str) -> list[dict]:
    """Every catalog row obtainable from a single product page fetch (one per
    strength/form variant if `productDetails.variants[]` is present, else the
    single JSON-LD-described row). Real fields only; acquisition_cost/markup/
    pharmacy_fee/package_quantity are always blank -- see module docstring."""
    jsonld = extract_jsonld(html)
    details = extract_product_details(html)
    if details is None and jsonld is None:
        return []

    brand_name = (jsonld.get("brand") or {}).get("name") if jsonld else None
    shipping_fee = None
    if jsonld:
        shipping_rate = ((jsonld.get("offers") or {}).get("shippingDetails") or {}).get("shippingRate") or {}
        shipping_fee = shipping_rate.get("value")

    def _blank_row(**overrides) -> dict:
        row = {
            "drug": None, "strength": None, "form": None, "package_quantity": None,
            "acquisition_cost": None, "markup": None, "pharmacy_fee": None,
            "shipping_fee": shipping_fee, "final_price": None, "brand_name": brand_name,
            "sku": None, "package_size_raw": None, "volume_raw": None,
            "scrape_matched_slug": None, "scrape_status": "ok",
        }
        row.update(overrides)
        return row

    if details and details.get("variants"):
        drug_name = details.get("name")
        rows = []
        for variant in details["variants"]:
            mf = variant.get("metafields") or {}
            price = variant.get("priceCalculation")
            rows.append(
                _blank_row(
                    drug=drug_name,
                    strength=mf.get("strength"),
                    form=mf.get("form"),
                    final_price=price,
                    sku=variant.get("sku"),
                    package_size_raw=mf.get("package_size"),
                    volume_raw=mf.get("volume") or None,
                    scrape_matched_slug=mf.get("slug"),
                    scrape_status="ok" if price is not None else "matched_no_price",
                )
            )
        return rows

    if jsonld:
        price = (jsonld.get("offers") or {}).get("price")
        return [_blank_row(drug=jsonld.get("name"), final_price=price, sku=jsonld.get("sku"))]

    return []


def discover_catalog_size(force_refresh: bool = False) -> int:
    return len(discover_catalog_slugs(force_refresh=force_refresh))


def scrape_full_catalog(limit: int | None = None, force_refresh: bool = False) -> tuple[pd.DataFrame, int]:
    """Enumerate and scrape the ENTIRE Cost Plus catalog via the live
    sitemap. `limit` caps the number of REAL page fetches performed this run
    (slugs already covered by an earlier fetch's sibling variants don't count
    against it), so a full run can be resumed across invocations via the
    on-disk HTML cache. Returns (rows_df, total_catalog_size)."""
    slugs = discover_catalog_slugs(force_refresh=force_refresh)
    total_catalog_size = len(slugs)

    covered: set[str] = set()
    rows: list[dict] = []
    fetch_count = 0
    for slug in slugs:
        if slug in covered:
            continue
        if limit is not None and fetch_count >= limit:
            break
        html = _polite_get(f"{BASE_URL}/medications/{slug}/", force_refresh=force_refresh)
        fetch_count += 1
        covered.add(slug)
        if html is None:
            rows.append(
                {
                    "drug": None, "strength": None, "form": None, "package_quantity": None,
                    "acquisition_cost": None, "markup": None, "pharmacy_fee": None,
                    "shipping_fee": None, "final_price": None, "brand_name": None, "sku": None,
                    "package_size_raw": None, "volume_raw": None,
                    "scrape_matched_slug": slug, "scrape_status": "fetch_failed",
                }
            )
            continue

        page_rows = _rows_from_product_page(html)
        if not page_rows:
            rows.append(
                {
                    "drug": None, "strength": None, "form": None, "package_quantity": None,
                    "acquisition_cost": None, "markup": None, "pharmacy_fee": None,
                    "shipping_fee": None, "final_price": None, "brand_name": None, "sku": None,
                    "package_size_raw": None, "volume_raw": None,
                    "scrape_matched_slug": slug, "scrape_status": "unparseable_page",
                }
            )
            continue

        rows.extend(page_rows)
        for r in page_rows:
            if r.get("scrape_matched_slug"):
                covered.add(r["scrape_matched_slug"])
        print(
            f"[costplus_scraper] fetch {fetch_count} ({len(covered)} slug(s) covered, {len(rows)} row(s) so far): "
            f"{slug} -> {len(page_rows)} variant(s)"
        )

    out = pd.DataFrame(rows)
    n_ok = (out["scrape_status"] == "ok").sum() if not out.empty else 0
    print(
        f"[costplus_scraper] Full catalog scrape: {fetch_count} page fetch(es) covering {len(covered)} slug(s), "
        f"{len(out)} row(s), {n_ok} with a real final_price (catalog has {total_catalog_size:,} total product slugs)"
    )
    return out, total_catalog_size


def fetch_missing_variants(target_slugs: list[str], min_delay_seconds: float = 3.0, max_delay_seconds: float = 3.5) -> pd.DataFrame:
    """Directly fetch a specific, targeted list of slugs -- e.g. Task 1's (a)
    bucket: rows whose own product page was never a direct fetch target
    during the earlier full-catalog run (their data came entirely from being
    listed as a sibling on some other page). Unlike scrape_full_catalog, this
    does NOT skip a slug just because a sibling already "covered" it -- the
    whole point is to check whether that variant's OWN page reveals data
    (specifically `metafields.volume`) the sibling-embedded copy didn't have.

    Uses a dedicated, stricter-than-default PoliteFetcher (3-3.5s between
    requests, same cache dir so it resumes cleanly across interrupted runs)
    and stops immediately -- raising, not swallowing -- if a real
    anti-automation challenge appears (see shared.scrape_utils.
    ChallengeDetectedError); costplusdrugs.com is known to throttle but not
    to present one, so this would be a real, reportable surprise.
    """
    fetcher = scrape_utils.PoliteFetcher(
        base_url=BASE_URL, user_agent=USER_AGENT, cache_dir=CACHE_SUBDIR,
        min_delay_seconds=min_delay_seconds, max_delay_seconds=max_delay_seconds,
    )

    rows: list[dict] = []
    for i, slug in enumerate(target_slugs, 1):
        try:
            html = fetcher.get(f"{BASE_URL}/medications/{slug}/")
        except scrape_utils.ChallengeDetectedError as exc:
            print(f"[costplus_scraper] STOPPING: {exc}")
            print(f"[costplus_scraper] Completed {i - 1}/{len(target_slugs)} target slugs before the challenge appeared.")
            break

        if html is None:
            rows.append(
                {
                    "drug": None, "strength": None, "form": None, "package_quantity": None,
                    "acquisition_cost": None, "markup": None, "pharmacy_fee": None,
                    "shipping_fee": None, "final_price": None, "brand_name": None, "sku": None,
                    "package_size_raw": None, "volume_raw": None,
                    "scrape_matched_slug": slug, "scrape_status": "fetch_failed",
                }
            )
            continue

        page_rows = _rows_from_product_page(html)
        if not page_rows:
            rows.append(
                {
                    "drug": None, "strength": None, "form": None, "package_quantity": None,
                    "acquisition_cost": None, "markup": None, "pharmacy_fee": None,
                    "shipping_fee": None, "final_price": None, "brand_name": None, "sku": None,
                    "package_size_raw": None, "volume_raw": None,
                    "scrape_matched_slug": slug, "scrape_status": "unparseable_page",
                }
            )
            continue

        rows.extend(page_rows)
        if i % 25 == 0 or i == len(target_slugs):
            print(f"[costplus_scraper] fetched {i}/{len(target_slugs)} missing variant pages ({len(rows)} rows so far)")

    out = pd.DataFrame(rows)
    n_with_volume = out["volume_raw"].notna().sum() if not out.empty and "volume_raw" in out.columns else 0
    print(
        f"[costplus_scraper] Missing-variant fetch: {len(out)}/{len(target_slugs)} target slugs processed, "
        f"{n_with_volume} now have volume_raw"
    )
    return out


def merge_scraped_rows(base_df: pd.DataFrame, updated_rows: pd.DataFrame) -> pd.DataFrame:
    """Replace rows in base_df with fresher data from updated_rows, matched on
    scrape_matched_slug -- used to fold fetch_missing_variants' results back
    into the full catalog DataFrame without re-fetching anything already there."""
    if updated_rows.empty:
        return base_df.copy()
    updated_slugs = set(updated_rows["scrape_matched_slug"].dropna())
    kept = base_df[~base_df["scrape_matched_slug"].isin(updated_slugs)]
    return pd.concat([kept, updated_rows], ignore_index=True)


def crosswalk_coverage_for_scraped(scraped_df: pd.DataFrame, force_refresh: bool = False) -> dict:
    """Run every successfully-scraped row (drug/strength/form all present)
    through the untouched Phase 0 crosswalk against NADAC and report the
    match rate AND the unmatched list -- independent of whether cost-
    breakdown fields are populated, since the crosswalk only needs
    drug/strength/form."""
    from fetch import nadac as fetch_nadac

    nadac_df = fetch_nadac.load_nadac(force_refresh=force_refresh)

    usable = scraped_df.dropna(subset=["drug", "strength", "form"]).copy() if not scraped_df.empty else scraped_df
    if not usable.empty:
        usable["drug_term"] = (
            usable["drug"].astype(str).str.strip()
            + " "
            + usable["strength"].astype(str).str.strip()
            + " "
            + usable["form"].astype(str).str.strip()
        )

    matched, unmatched = 0, []
    for term in (usable["drug_term"] if not usable.empty else []):
        result = crosswalk.crosswalk_drug(term, nadac_df)
        if result.matched:
            matched += 1
        else:
            unmatched.append((term, result.note))

    total = len(usable)
    pct = (matched / total * 100) if total else 0.0
    print("\n[costplus_scraper] === FULL-CATALOG CROSSWALK COVERAGE REPORT ===")
    print(f"[costplus_scraper] {len(scraped_df):,} row(s) scraped this run ({total:,} with usable drug/strength/form)")
    print(f"[costplus_scraper] {matched}/{total} ({pct:.1f}%) resolved through the Phase 0 crosswalk to a NADAC match")
    if unmatched:
        print(f"[costplus_scraper] unmatched ({len(unmatched)}):")
        for term, note in unmatched:
            print(f"[costplus_scraper]   - {term} ({note})")
    return {"scraped_rows": len(scraped_df), "usable_rows": total, "matched": matched, "unmatched": unmatched, "pct": pct}


def run_full_catalog(limit: int | None = None, force_refresh: bool = False) -> dict:
    scraped_df, total_catalog_size = scrape_full_catalog(limit=limit, force_refresh=force_refresh)
    out_path = config.DATA_DIR / "costplus.SCRAPED.csv"
    scraped_df.to_csv(out_path, index=False)
    print(
        f"[costplus_scraper] Wrote {len(scraped_df):,} rows -> {out_path} "
        "(acquisition_cost/markup/pharmacy_fee/package_quantity are blank for every row -- not exposed by the site)"
    )
    coverage = crosswalk_coverage_for_scraped(scraped_df, force_refresh=force_refresh)
    coverage["catalog_size"] = total_catalog_size
    print(f"[costplus_scraper] Cost Plus catalog size (total product slugs discovered from the sitemap): {total_catalog_size:,}")
    return {"scraped": scraped_df, "output_path": out_path, "coverage": coverage}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def refresh_catalog(costplus_path: Path | None = None, limit: int | None = None, force_refresh: bool = False) -> pd.DataFrame:
    """Refresh every row of an EXISTING, schema-valid data/costplus.csv (or
    data/costplus.SAMPLE.csv) against live costplusdrugs.com data. Original
    acquisition_cost/markup/pharmacy_fee/package_quantity values are preserved
    as-is (this scraper cannot verify them, so it never overwrites them); real
    shipping_fee/brand_name/observed price are added/refreshed alongside."""
    cp_df = costplus_mod.load_costplus(costplus_path)
    slugs = discover_catalog_slugs(force_refresh=force_refresh)

    rows = cp_df.to_dict("records")
    if limit is not None:
        rows = rows[:limit]

    scraped = []
    for i, row in enumerate(rows, 1):
        print(f"[costplus_scraper] ({i}/{len(rows)}) scraping {row['drug']} {row['strength']} {row['form']}...")
        result = scrape_drug(row["drug"], row["strength"], row["form"], slugs, force_refresh=force_refresh)
        for col in ("package_quantity", "acquisition_cost", "markup", "pharmacy_fee"):
            if result[col] is None:
                result[col] = row.get(col)  # preserve hand-entered value; never overwrite with a guess
        if result["shipping_fee"] is None:
            result["shipping_fee"] = row.get("shipping_fee")
        scraped.append(result)

    out = pd.DataFrame(scraped)
    if not out.empty:
        has_formula_inputs = out[["package_quantity", "acquisition_cost", "markup", "pharmacy_fee"]].notna().all(axis=1)
        markup_filled = out["markup"].fillna(config.COSTPLUS_MARKUP)
        pharmacy_fee_filled = out["pharmacy_fee"].fillna(0.0)
        computed_per_unit = (out["acquisition_cost"] * markup_filled + pharmacy_fee_filled) / out["package_quantity"]
        computed_package_price = computed_per_unit * out["package_quantity"]
        out["implied_costplus_per_unit"] = computed_per_unit.where(has_formula_inputs)
        diff = (out["final_price"] - computed_package_price).where(has_formula_inputs & out["final_price"].notna())
        out["price_check_note"] = diff.map(
            lambda d: None if pd.isna(d) else ("matches observed price" if abs(d) < 0.05 else f"MISMATCH: observed - formula = {d:+.2f}")
        )

    n_ok = (out["scrape_status"] == "ok").sum() if not out.empty else 0
    print(f"[costplus_scraper] Scraped {n_ok}/{len(out)} rows with a real final_price")
    return out


def print_crosswalk_coverage(cp_df: pd.DataFrame, force_refresh: bool = False) -> dict:
    """Run every catalog drug through the untouched Phase 0 crosswalk
    (shared.crosswalk.crosswalk_drug) against NADAC and report the match
    rate -- independent of whether cost-breakdown fields are populated, since
    the crosswalk only needs drug/strength/form."""
    from fetch import nadac as fetch_nadac

    nadac_df = fetch_nadac.load_nadac(force_refresh=force_refresh)
    matched, total = 0, len(cp_df)
    unmatched_terms = []
    for term in cp_df["drug_term"]:
        result = crosswalk.crosswalk_drug(term, nadac_df)
        if result.matched:
            matched += 1
        else:
            unmatched_terms.append((term, result.note))

    pct = (matched / total * 100) if total else 0.0
    print(f"\n[costplus_scraper] === CROSSWALK COVERAGE REPORT ===")
    print(f"[costplus_scraper] {matched}/{total} ({pct:.1f}%) catalog drugs resolved through the Phase 0 "
          f"crosswalk to a NADAC match")
    for term, note in unmatched_terms:
        print(f"[costplus_scraper]   - unmatched: {term} ({note})")
    return {"matched": matched, "total": total, "pct": pct, "unmatched": unmatched_terms}


def run(costplus_path: Path | None = None, limit: int | None = None, force_refresh: bool = False) -> dict:
    scraped_df = refresh_catalog(costplus_path, limit=limit, force_refresh=force_refresh)
    out_path = config.DATA_DIR / "costplus.SCRAPED.csv"
    scraped_df.to_csv(out_path, index=False)
    print(f"[costplus_scraper] Wrote {len(scraped_df):,} rows -> {out_path} "
          "(review acquisition_cost/markup/pharmacy_fee/package_quantity by hand before using it as data/costplus.csv)")

    cp_df = costplus_mod.load_costplus(costplus_path)
    coverage = print_crosswalk_coverage(cp_df, force_refresh=force_refresh)
    return {"scraped": scraped_df, "output_path": out_path, "coverage": coverage}


# ---------------------------------------------------------------------------
# Package-quantity recovery.
#
# The `package_size` metafield (captured as `package_size_raw`) was the first
# candidate investigated and REJECTED: it was identical ("1000") across four
# different atorvastatin strengths, and more damningly, it directly
# contradicts the site's own VISIBLE text in cases like Ipratropium Bromide
# (form field literally says "Box of 30 vials"; package_size_raw was "1").
# It is not used here at all.
#
# The real signal lives in a *different*, previously-uninspected field on the
# same Next.js payload: each variant's `metafields.volume` (e.g. "30 Tablets",
# "240mL", "45gm", "4 Patches") -- a clean, human-readable package description,
# populated for 772/2,341 real scraped rows. `parse_package_quantity_from_volume`
# accepts ONLY the subset that parses as an unambiguous single "<number>
# <unit>" (728 of those 772); compound/multi-part descriptions (e.g. "60mL
# (30 x 2mL)", where it's unclear whether the NADAC-comparable quantity is 60
# or 30) are deliberately left unresolved rather than guessed.
#
# An earlier version of this function ALSO required final_price/package_size
# to be >= the drug's real nadac_per_unit (reasoning: Cost Plus can't sell
# below acquisition cost). That check is INTENTIONALLY NOT applied as a
# pass/fail gate: it rejected several drugs (e.g. Prasugrel HCl, explicitly
# labeled "Bottle of 30 Tablets") that were only ~3% below NADAC's national
# median -- entirely explained by Cost Plus's own negotiated cost being
# slightly better than the survey average, not a wrong count. nadac_per_unit
# is still attached to every row for transparency/context, just not used to
# override what the page's own visible text says.
#
# Some `volume` strings are COMPOUND (e.g. "60mL (30 x 2mL)", "56 Ampules
# (224mL)"): they state two true numbers -- a total liquid volume and a
# discrete count of vials/pens/syringes/ampules -- and simple regex can't
# tell which one is "the" package_quantity on its own. It doesn't need to
# guess: NADAC's own Pricing Unit for that exact drug (EA vs ML, from the
# untouched Phase 0 crosswalk) says which basis Module A will actually
# divide by, so the OTHER number is simply irrelevant to this drug's
# per-unit price. A compound value is accepted only when the pricing unit
# picks out exactly one of the two numbers; if the crosswalk doesn't
# resolve (no pricing unit to check against), it's left blank.
# ---------------------------------------------------------------------------
_VOLUME_PREFIX_RE = re.compile(r"^(box of|pack of|carton of)\s+", re.IGNORECASE)
_VOLUME_QTY_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*"
    r"(mL|ml|milliliters?|g|gm|grams?|oz|liters?|ea|tablets?|capsules?|patches?|doses?|unit doses?|packets?|pack|"
    r"blisters?|rings?|suppositories?|lozenges?|test strips?|kits?|pens?|syringes?|lancets?|swabs?|pads?|cans?|vials?|"
    r"pieces?|nasal sprays?|odt tablets?|chewable tablets?)"
    r"(?:\s+[a-zA-Z]+)?$",  # tolerate ONE trailing descriptor word (e.g. "140gm Tube") -- NOT a trailing
    # parenthetical: "60mL (30 x 2mL)" states a genuinely different competing quantity (30, for an
    # EA-priced drug) in there, which must go through parse_compound_package_quantity, never be
    # silently discarded as "just extra detail" the way a real descriptor word would be.
    re.IGNORECASE,
)

# "60mL (30 x 2mL)" / "90mL (30 x 3mL Vials)" -> (total=60/90, count=30)
_COMPOUND_TOTAL_THEN_COUNT_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(?:mL|ml)\s*\(\s*(\d+(?:\.\d+)?)\s*x\s*[\d.]+\s*(?:mL|ml)?\s*[a-zA-Z]*\s*\)$",
    re.IGNORECASE,
)
# "1.6mL (2 Pens)" / "0.8mL (2 Auto-injectors)" / "1mL (2 Syringes)" -> (total=1.6, count=2)
_COMPOUND_TOTAL_THEN_ITEM_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(?:mL|ml)\s*\(\s*(\d+(?:\.\d+)?)\s*(?:pens?|syringes?|prefilled syringes?|auto-?injectors?)\s*\)$",
    re.IGNORECASE,
)
# "56 Ampules (224mL)" -> (count=56, total=224)
_COMPOUND_COUNT_THEN_TOTAL_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*ampules?\s*\(\s*(\d+(?:\.\d+)?)\s*(?:mL|ml)\s*\)$",
    re.IGNORECASE,
)
# "60 x 3mL" / "30 x 2.5mL" (no parens, no leading total) -> count=60, total=60*3
_COMPOUND_X_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(?:mL|ml)$", re.IGNORECASE)


def parse_package_quantity_from_volume(volume_raw) -> Optional[float]:
    """Parse a `metafields.volume` string (e.g. "30 Tablets", "240mL") into a
    bare numeric quantity. Returns None for blank or otherwise-unrecognized
    text -- never guesses. Compound descriptions are handled separately by
    parse_compound_package_quantity, which needs the drug's NADAC pricing
    unit to disambiguate."""
    if not isinstance(volume_raw, str) or not volume_raw.strip():
        return None
    stripped = _VOLUME_PREFIX_RE.sub("", volume_raw.strip())
    m = _VOLUME_QTY_RE.match(stripped)
    return float(m.group(1)) if m else None


def parse_compound_package_quantity(volume_raw, pricing_unit: Optional[str]) -> Optional[float]:
    """Parse a compound `volume` string (states both a total liquid volume AND
    a discrete item count) by picking whichever number matches the drug's real
    NADAC Pricing Unit (EA -> the count, ML -> the total volume). Returns None
    if no compound pattern matches, or if pricing_unit doesn't resolve to
    exactly one of the two numbers -- never guesses between them."""
    if not isinstance(volume_raw, str) or not volume_raw.strip():
        return None
    v = volume_raw.strip()

    for pattern, order in (
        (_COMPOUND_TOTAL_THEN_COUNT_RE, "total_count"),
        (_COMPOUND_TOTAL_THEN_ITEM_RE, "total_count"),
        (_COMPOUND_COUNT_THEN_TOTAL_RE, "count_total"),
    ):
        m = pattern.match(v)
        if m:
            a, b = float(m.group(1)), float(m.group(2))
            total, count = (a, b) if order == "total_count" else (b, a)
            break
    else:
        m = _COMPOUND_X_RE.match(v)
        if m:
            count, per_unit = float(m.group(1)), float(m.group(2))
            total = count * per_unit
        else:
            return None

    if pricing_unit == "ML":
        return total
    if pricing_unit == "EA":
        return count
    return None


def recover_package_quantity(scraped_df: pd.DataFrame, force_refresh: bool = False) -> pd.DataFrame:
    from fetch import nadac as fetch_nadac

    nadac_df = fetch_nadac.load_nadac(force_refresh=force_refresh)

    out = scraped_df.copy()
    out["package_quantity"] = pd.NA  # always start blank; only ever filled in by the "confirmed" branch below
    out["nadac_per_unit"] = pd.NA
    out["package_quantity_status"] = "no_drug_strength_form"

    usable_mask = out["drug"].notna() & out["strength"].notna() & out["form"].notna()
    for idx in out[usable_mask].index:
        row = out.loc[idx]

        # nadac_per_unit/pricing_unit are resolved first (not just as context
        # this time): a compound volume string needs the pricing unit to
        # disambiguate which number it states is the real quantity.
        term = f"{row['drug']} {row['strength']} {row['form']}"
        result = crosswalk.crosswalk_drug(term, nadac_df)
        if result.matched and result.nadac_per_unit is not None:
            out.at[idx, "nadac_per_unit"] = result.nadac_per_unit

        qty = parse_package_quantity_from_volume(row.get("volume_raw"))
        if qty is not None:
            out.at[idx, "package_quantity"] = qty
            out.at[idx, "package_quantity_status"] = "confirmed"
        elif pd.notna(row.get("volume_raw")):
            compound_qty = parse_compound_package_quantity(row.get("volume_raw"), result.pricing_unit if result.matched else None)
            if compound_qty is not None:
                out.at[idx, "package_quantity"] = compound_qty
                out.at[idx, "package_quantity_status"] = "confirmed"
            else:
                out.at[idx, "package_quantity_status"] = "ambiguous_volume_text"
        else:
            out.at[idx, "package_quantity_status"] = "no_volume_text_captured"

    counts = out["package_quantity_status"].value_counts()
    print("\n[costplus_scraper] === PACKAGE QUANTITY RECOVERY ===")
    for status, n in counts.items():
        print(f"[costplus_scraper]   {status}: {n:,}")
    return out


def build_runnable_catalog(recovered_df: pd.DataFrame) -> pd.DataFrame:
    """Filter to rows Module A can actually price: a confirmed package_quantity
    (see recover_package_quantity) is required, since shared/costplus.py's
    loader rejects any row missing it. acquisition_cost/markup/pharmacy_fee
    stay blank -- costplus_per_unit falls back to final_price/package_quantity
    for these, per the source-aware fix already in shared/costplus.py."""
    return recovered_df[recovered_df["package_quantity_status"] == "confirmed"].copy()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape costplusdrugs.com for real Cost Plus prices")
    p.add_argument("--costplus", type=Path, default=None, help="Path to Cost Plus CSV (default: data/costplus.csv)")
    p.add_argument("--sample", action="store_true", help="Use data/costplus.SAMPLE.csv instead")
    p.add_argument("--full-catalog", action="store_true", help="Scrape the ENTIRE Cost Plus catalog instead of just refreshing --costplus's existing rows")
    p.add_argument("--limit", type=int, default=None, help="Cap the number of real page fetches performed this run")
    p.add_argument("--force-refresh", action="store_true", help="Bypass the on-disk HTML cache")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.full_catalog:
        run_full_catalog(limit=args.limit, force_refresh=args.force_refresh)
    else:
        path = args.costplus or (config.DATA_DIR / "costplus.SAMPLE.csv" if args.sample else None)
        run(costplus_path=path, limit=args.limit, force_refresh=args.force_refresh)
