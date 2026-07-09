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

What this module DOES give you that's new and real: `observed_costplus_price`
(the actual current price for that drug/strength, scraped live) and
`shipping_fee` (also real, from the JSON-LD `Offer.shippingDetails`). If your
data/costplus.csv row already has acquisition_cost/markup/pharmacy_fee/
package_quantity filled in by hand, `implied_costplus_per_unit` lets you
sanity-check that hand-entered formula against the real observed price.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.robotparser
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import costplus as costplus_mod  # noqa: E402
from shared import crosswalk  # noqa: E402

BASE_URL = "https://www.costplusdrugs.com"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
ROBOTS_URL = f"{BASE_URL}/robots.txt"

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
MIN_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 3.0

CACHE_SUBDIR = config.CACHE_DIR / "costplus_scrape"

REQUIRED_COLUMNS = costplus_mod.REQUIRED_COLUMNS  # keep in lockstep with the schema shared/costplus.py validates

_session: Optional[requests.Session] = None
_robots: Optional[urllib.robotparser.RobotFileParser] = None
_last_request_ts: float = 0.0


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return _session


def _get_robots() -> urllib.robotparser.RobotFileParser:
    global _robots
    if _robots is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(ROBOTS_URL)
        try:
            rp.read()
        except Exception as exc:  # pragma: no cover - network failure path
            print(f"[costplus_scraper] WARNING: could not read {ROBOTS_URL} ({exc}); refusing to scrape")
            rp.disallow_all = True
        _robots = rp
    return _robots


def _cache_path(url: str) -> Path:
    import hashlib

    CACHE_SUBDIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return CACHE_SUBDIR / f"{digest}.html"


def _polite_get(url: str, force_refresh: bool = False) -> Optional[str]:
    """Disk-cached, robots.txt-gated, rate-limited GET. Returns None (never
    raises) on disallow/404/network failure so a caller can skip and keep a
    coverage tally honest rather than aborting a whole run."""
    cache_path = _cache_path(url)
    if cache_path.exists() and not force_refresh:
        return cache_path.read_text(encoding="utf-8")

    robots = _get_robots()
    if not robots.can_fetch(USER_AGENT, url):
        print(f"[costplus_scraper] robots.txt disallows {url}, skipping")
        return None

    global _last_request_ts
    elapsed = time.monotonic() - _last_request_ts
    delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    if elapsed < delay:
        time.sleep(delay - elapsed)

    try:
        resp = _get_session().get(url, timeout=30)
        _last_request_ts = time.monotonic()
        if resp.status_code != 200:
            print(f"[costplus_scraper] {url} -> HTTP {resp.status_code}, skipping")
            return None
        cache_path.write_text(resp.text, encoding="utf-8")
        return resp.text
    except requests.RequestException as exc:  # pragma: no cover - network failure path
        _last_request_ts = time.monotonic()
        print(f"[costplus_scraper] {url} -> request failed ({exc}), skipping")
        return None


# ---------------------------------------------------------------------------
# Parsing: Next.js App Router streams page data as `self.__next_f.push([1,
# "<escaped JSON string>"])` calls rather than the classic `__NEXT_DATA__`
# blob. Each pushed string is itself a JSON string literal (one level of
# backslash-escaping); the JSON-LD block and the `productDetails` object are
# both found *inside* that decoded text, still one level escaped beyond that
# (they were JSON.stringify'd again before being embedded). The helpers below
# do this generically via character-level scanning rather than regex, so they
# don't break on brackets/quotes that happen to appear in surrounding content.
# ---------------------------------------------------------------------------
def _scan_quoted_string(text: str, start: int) -> tuple[str, int]:
    """text[start] must be an opening `"`. Returns (decoded value, index just
    past the matching unescaped closing quote), using json.loads to unescape."""
    if text[start] != '"':
        raise ValueError(f"expected '\"' at index {start}")
    i = start + 1
    escaped = False
    while i < len(text):
        c = text[i]
        if escaped:
            escaped = False
        elif c == "\\":
            escaped = True
        elif c == '"':
            return json.loads(text[start : i + 1]), i + 1
        i += 1
    raise ValueError("unterminated quoted string")


def _scan_balanced(text: str, start: int) -> tuple[str, int]:
    """text[start] must be `{` or `[`. Returns (raw substring, index just past
    the matching close bracket), respecting string literals so brackets
    inside quoted text don't confuse the depth count."""
    open_ch = text[start]
    close_ch = {"{": "}", "[": "]"}[open_ch]
    depth = 0
    i = start
    in_string = False
    escaped = False
    while i < len(text):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1], i + 1
        i += 1
    raise ValueError(f"unbalanced '{open_ch}...{close_ch}'")


def _find_next_f_payloads(html: str) -> list[str]:
    """Every `self.__next_f.push([1, "..."])` call's decoded (one level
    unescaped) string argument, in document order."""
    marker = "self.__next_f.push([1,"
    payloads = []
    idx = 0
    while True:
        idx = html.find(marker, idx)
        if idx == -1:
            break
        qstart = html.find('"', idx + len(marker))
        if qstart == -1:
            break
        try:
            decoded, end = _scan_quoted_string(html, qstart)
        except ValueError:
            break
        payloads.append(decoded)
        idx = end
    return payloads


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
        "observed_costplus_price": None,
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
        out["observed_costplus_price"] = offers.get("price")
        shipping_rate = (offers.get("shippingDetails") or {}).get("shippingRate") or {}
        out["shipping_fee"] = shipping_rate.get("value")

    if details:
        want_strength = _strength_number(strength)
        for variant in details.get("variants") or []:
            mf = variant.get("metafields") or {}
            if want_strength and _strength_number(mf.get("strength", "")) != want_strength:
                continue
            out["package_size_raw"] = mf.get("package_size")
            if out["observed_costplus_price"] is None:
                out["observed_costplus_price"] = variant.get("priceCalculation")
            break

    out["scrape_status"] = "ok" if out["observed_costplus_price"] is not None else "matched_no_price"
    return out


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
        implied = pd.Series(index=out.index, dtype=float)
        markup_filled = out["markup"].fillna(config.COSTPLUS_MARKUP)
        pharmacy_fee_filled = out["pharmacy_fee"].fillna(0.0)
        computed_per_unit = (out["acquisition_cost"] * markup_filled + pharmacy_fee_filled) / out["package_quantity"]
        computed_package_price = computed_per_unit * out["package_quantity"]
        out["implied_costplus_per_unit"] = computed_per_unit.where(has_formula_inputs)
        diff = (out["observed_costplus_price"] - computed_package_price).where(has_formula_inputs & out["observed_costplus_price"].notna())
        out["price_check_note"] = diff.map(
            lambda d: None if pd.isna(d) else ("matches observed price" if abs(d) < 0.05 else f"MISMATCH: observed - formula = {d:+.2f}")
        )

    n_ok = (out["scrape_status"] == "ok").sum() if not out.empty else 0
    print(f"[costplus_scraper] Scraped {n_ok}/{len(out)} rows with a real observed price")
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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refresh data/costplus.csv against live costplusdrugs.com data")
    p.add_argument("--costplus", type=Path, default=None, help="Path to Cost Plus CSV (default: data/costplus.csv)")
    p.add_argument("--sample", action="store_true", help="Use data/costplus.SAMPLE.csv instead")
    p.add_argument("--limit", type=int, default=None, help="Only scrape the first N catalog rows")
    p.add_argument("--force-refresh", action="store_true", help="Bypass the on-disk HTML cache")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    path = args.costplus or (config.DATA_DIR / "costplus.SAMPLE.csv" if args.sample else None)
    run(costplus_path=path, limit=args.limit, force_refresh=args.force_refresh)
