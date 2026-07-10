"""
Cost Plus Drugs GraphQL client -- replaces the HTML page-scrape's
package_quantity guesswork with the site's own storefront API.

CONTRACT SOURCE: read (not copied) from github.com/DavidOsherdiagnostica/
cost-plus-drugs at commit-current-as-of-2026-07-10. That repo has NO
AGENTS.md (checked via the GitHub API tree listing, both `main` and `master`
404 on the raw path) -- the task description's premise there was wrong; the
actual contract lives in src/services/apiClient.ts and src/types/api.ts.
Every field and endpoint below was then indepedently RE-VERIFIED against the
live site before this client was written; nothing here is copied code, and
one field (`strength`) is used here that the reference repo's own query
never requested despite being available.

ENDPOINT: POST https://www.costplusdrugs.com/graphql/, JSON body
{"query": ..., "variables": ...}. A public Saleor storefront API (the exact
API the site's own frontend calls), with Relay-style cursor pagination
(first/after, pageInfo.hasNextPage/endCursor) and a query-cost budget
surfaced in extensions.cost.requestedQueryCost / maximumAvailable.

ROBOTS.TXT (fetched live via shared.scrape_utils.RobotsRules, the same
longest-prefix-match logic fetch/costplus_html_scraper.py uses): `Allow: /` with
no Disallow rule covering /graphql/, so it's allowed.

NO CDN BLOCK, UNLIKE THE HTML SCRAPE: confirmed live -- a plain, honestly
self-identifying User-Agent (this module's own, no browser/tool
impersonation) got HTTP 200 on the first request. fetch/costplus_html_scraper.py
needed a browser UA to get past a CDN-level block on /medications/* pages;
that block does not apply to /graphql/.

THE PACKAGE_QUANTITY FIX -- AND A LOGGED CORRECTION FROM A FIELD THAT LOOKED
RIGHT BUT WASN'T: each variant carries a `package_size` metafield (a unit
count) and a `retailPricePerUnit` metafield that LOOKS like Cost Plus's own
per-unit price. It is not. Verified against ground truth (real prices
already captured in data/costplus.SCRAPED.csv from live HTML product pages
via fetch/costplus_html_scraper.py): for Ibuprofen 100mg/5mL Bottle of
Suspension (120mL), the real price is $7.89 total ($0.0658/mL); the
`retailPricePerUnit` metafield for that exact SKU reports "11.00", which
would compute to $1,320 for the bottle -- 167x too high. Same mismatch
reproduced on Dimethyl Fumarate 240mg ($19.16 real vs. an implied ~$6,600),
Nitrofurantoin 25mg/5mL suspension ($398.58 real vs. implied thousands off),
and more. `retailPricePerUnit` was used in an earlier version of this client
and produced a leaderboard full of implausible multi-thousand-dollar
negative gaps before this was caught and fixed -- left as a documented
near-miss, not scrubbed from history, per this suite's own standard of
surfacing what almost went wrong (see METHODOLOGY.md, the metformin ER
near-miss from Phase 0).

The field that DOES match ground truth exactly, checked across every variant
of that same Ibuprofen product (5/5 exact matches to the real scraped
price): `priceCalculation`, queried PER VARIANT (`variants { priceCalculation
... }`), not the product-level `priceCalculation` the reference repo's own
query uses (that one is ambiguous across a multi-variant product -- it
matched only the first-listed variant in testing). This client uses
variant-level `priceCalculation` as the real total package price
(`final_price`), and `costplus_per_unit = final_price / package_quantity`
via shared/costplus.py's existing scrape-path formula, same as the HTML
scraper's own final_price -- no per-unit shortcut, no division through a
number that isn't independently confirmed.

package_size itself is still not perfectly reliable -- verified live against
the exact case that broke the old HTML/regex recovery: Ipratropium Bromide's
"Box of 30 vials" variant (sku ...-1-Generic) still reports package_size ==
"1" via GraphQL, same as via HTML. That residual issue is inherent to Cost
Plus's own backend data, not this access method, and is called out in the
coverage report (see run output / METHODOLOGY.md) rather than silently
absorbed into a clean-looking number.

NO FEE BREAKDOWN: acquisition_cost / markup / pharmacy_fee are not present
on any product or variant field this API exposes (checked the full node
shape). Left blank, never guessed -- consistent with fetch/costplus_html_scraper.py's
independent finding on the HTML side that this breakdown is structurally
private to Cost Plus, not merely absent from one access path.

shipping_fee is also left blank here: it's a JSON-LD Offer field on the HTML
product page (fetch/costplus_html_scraper.py already captures it there), not a
GraphQL product/variant field in this schema.
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import costplus as costplus_mod  # noqa: E402
from shared import scrape_utils  # noqa: E402

BASE_URL = "https://www.costplusdrugs.com"
GRAPHQL_ENDPOINT = f"{BASE_URL}/graphql/"
USER_AGENT = "costplus-arbitrage-suite/0.1 (research use; GraphQL client -- see fetch/costplus_graphql.py)"
CACHE_SUBDIR = config.CACHE_DIR / "costplus_graphql"
CACHE_SUBDIR.mkdir(parents=True, exist_ok=True)

PAGE_SIZE = 100
MIN_DELAY_SECONDS = 1.5
MAX_DELAY_SECONDS = 2.5
MAX_429_RETRIES = 5

REQUIRED_COLUMNS = costplus_mod.REQUIRED_COLUMNS  # keep in lockstep with shared/costplus.py's schema

PRODUCT_QUERY = """
query GetAllProducts($first: Int, $after: String) {
  products(
    first: $first
    after: $after
    channel: "default-channel"
    sortBy: { direction: ASC, field: NAME }
  ) {
    totalCount
    pageInfo { endCursor hasNextPage }
    edges {
      node {
        id
        name
        slug
        isAvailable
        variants {
          id
          sku
          priceCalculation
          metafields(keys: ["retailPricePerUnit", "form", "strength", "volume", "package_size", "slug", "is_active"])
          specialtyMedication
        }
        metafields(keys: ["brandGeneric", "brandName"])
      }
    }
  }
}
"""

_robots: Optional[scrape_utils.RobotsRules] = None
_session: Optional[requests.Session] = None
_last_request_ts = 0.0


def _get_robots() -> scrape_utils.RobotsRules:
    global _robots
    if _robots is None:
        rules = scrape_utils.RobotsRules()
        sess = _get_session()
        try:
            resp = sess.get(f"{BASE_URL}/robots.txt", timeout=30)
            if resp.status_code == 200:
                rules.parse(resp.text)
            elif resp.status_code in (401, 403):
                rules.disallow_all = True
            else:
                rules.allow_all = True
        except requests.RequestException as exc:  # pragma: no cover - network failure path
            print(f"[costplus_graphql] WARNING: could not read {BASE_URL}/robots.txt ({exc}); refusing to run")
            rules.disallow_all = True
        _robots = rules
    return _robots


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    return _session


def _page_cache_path(page_index: int) -> Path:
    return CACHE_SUBDIR / f"page_{page_index:04d}.json"


def _fetch_page(after: Optional[str], force_refresh: bool = False, page_index: int = 0) -> dict:
    """One paginated GraphQL request, disk-cached by page index so a run is
    resumable: rerunning after an interruption re-reads already-cached pages
    instead of re-fetching them, and only continues live requests from the
    first missing page."""
    cache_path = _page_cache_path(page_index)
    if cache_path.exists() and not force_refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    robots = _get_robots()
    if not robots.can_fetch(GRAPHQL_ENDPOINT):
        raise RuntimeError(f"robots.txt disallows {GRAPHQL_ENDPOINT}")

    global _last_request_ts
    attempt = 0
    while True:
        elapsed = time.monotonic() - _last_request_ts
        delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
        if elapsed < delay:
            time.sleep(delay - elapsed)

        resp = _get_session().post(
            GRAPHQL_ENDPOINT,
            json={"query": PRODUCT_QUERY, "variables": {"first": PAGE_SIZE, "after": after}},
            timeout=30,
        )
        _last_request_ts = time.monotonic()

        if resp.status_code == 429 and attempt < MAX_429_RETRIES:
            attempt += 1
            retry_after = resp.headers.get("Retry-After")
            try:
                backoff = float(retry_after) if retry_after else 0.0
            except ValueError:
                backoff = 0.0
            backoff = max(backoff, min(60.0, 2.0**attempt))
            print(f"[costplus_graphql] page {page_index}: HTTP 429, backing off {backoff:.0f}s "
                  f"(attempt {attempt}/{MAX_429_RETRIES})")
            time.sleep(backoff)
            continue

        resp.raise_for_status()
        payload = resp.json()
        if "errors" in payload:
            raise RuntimeError(f"GraphQL errors on page {page_index}: {payload['errors']}")

        cache_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload


def _resume_state(force_refresh: bool) -> tuple[int, Optional[str], list[dict], bool]:
    """Scan cached pages to find where to resume: (next_page_index,
    after_cursor, already_cached_payloads, is_complete). is_complete is True
    when the last cached page already reported hasNextPage=False, meaning
    the whole catalog is already on disk and no live fetch is needed. If
    force_refresh, ignore cache and start clean."""
    if force_refresh:
        return 0, None, [], False

    cached_payloads = []
    page_index = 0
    after = None
    while True:
        path = _page_cache_path(page_index)
        if not path.exists():
            break
        payload = json.loads(path.read_text(encoding="utf-8"))
        cached_payloads.append(payload)
        page_info = payload["data"]["products"]["pageInfo"]
        if not page_info["hasNextPage"]:
            return page_index + 1, None, cached_payloads, True
        after = page_info["endCursor"]
        page_index += 1
    return page_index, after, cached_payloads, False


_STRENGTH_METAFIELD_KEYS = ("strength",)


def _rows_from_payload(payload: dict) -> list[dict]:
    rows = []
    for edge in payload["data"]["products"]["edges"]:
        node = edge["node"]
        drug = node.get("name")
        pmeta = node.get("metafields") or {}
        brand_name = pmeta.get("brandName")

        for variant in node.get("variants") or []:
            vmeta = variant.get("metafields") or {}
            package_size_raw = vmeta.get("package_size")
            retail_price_per_unit_raw = vmeta.get("retailPricePerUnit")  # kept for reference only -- see module docstring, NOT used for pricing

            package_quantity = None
            package_quantity_status = "missing"
            try:
                pq = float(package_size_raw)
                if pq > 0:
                    package_quantity = pq
                    package_quantity_status = "confirmed"
            except (TypeError, ValueError):
                pass

            retail_price_per_unit = None
            try:
                retail_price_per_unit = float(retail_price_per_unit_raw)
            except (TypeError, ValueError):
                pass

            # The real total package price, ground-truth-verified per variant
            # (see module docstring) -- NOT derived from retail_price_per_unit.
            final_price = variant.get("priceCalculation")

            rows.append(
                {
                    "drug": drug,
                    "strength": vmeta.get("strength"),
                    "form": vmeta.get("form"),
                    "package_quantity": package_quantity,
                    "acquisition_cost": None,   # never exposed -- see module docstring
                    "markup": None,              # never exposed per-SKU
                    "pharmacy_fee": None,        # never exposed per-SKU
                    "shipping_fee": None,        # not a GraphQL field; HTML JSON-LD has it (costplus_html_scraper.py)
                    "final_price": final_price,
                    "brand_name": brand_name,
                    "sku": variant.get("sku"),
                    "retail_price_per_unit_UNRELIABLE": retail_price_per_unit,
                    "package_size_raw": package_size_raw,
                    "volume_raw": vmeta.get("volume") or None,
                    "package_quantity_status": package_quantity_status,
                    "product_slug": node.get("slug"),
                    "is_available": node.get("isAvailable"),
                    "specialty_medication": variant.get("specialtyMedication"),
                }
            )
    return rows


def fetch_full_catalog(force_refresh: bool = False, page_limit: Optional[int] = None) -> pd.DataFrame:
    """Paginate the entire Cost Plus catalog via GraphQL. Resumable: cached
    pages from a prior (possibly interrupted) run are reused; only pages
    beyond the cache are fetched live. `page_limit` caps how many NEW live
    page fetches this call performs (already-cached pages don't count
    against it), for testing without a full ~11-page run.
    """
    page_index, after, payloads, is_complete = _resume_state(force_refresh)
    total_count = payloads[0]["data"]["products"]["totalCount"] if payloads else None
    if payloads:
        print(f"[costplus_graphql] Resuming from cache: {len(payloads)} page(s) already on disk")

    live_fetches = 0
    while not is_complete:
        if page_limit is not None and live_fetches >= page_limit:
            print(f"[costplus_graphql] page_limit={page_limit} reached, stopping "
                  f"(rerun without a limit, or run again, to continue -- cached pages resume automatically)")
            break

        payload = _fetch_page(after, force_refresh=force_refresh, page_index=page_index)
        payloads.append(payload)
        live_fetches += 1
        products = payload["data"]["products"]
        if total_count is None:
            total_count = products["totalCount"]
        print(f"[costplus_graphql] page {page_index}: +{len(products['edges'])} products "
              f"(total so far: {sum(len(p['data']['products']['edges']) for p in payloads)}/{total_count})")

        if not products["pageInfo"]["hasNextPage"]:
            break
        after = products["pageInfo"]["endCursor"]
        page_index += 1

    all_rows = []
    for payload in payloads:
        all_rows.extend(_rows_from_payload(payload))

    df = pd.DataFrame(all_rows)
    print(f"[costplus_graphql] {len(df):,} variant rows from {len(payloads)} page(s) "
          f"(catalog totalCount={total_count})")
    return df


def write_graphql_csv(df: pd.DataFrame, path: Optional[Path] = None) -> Path:
    path = path or (config.DATA_DIR / "costplus.GRAPHQL.csv")
    out = df.copy()
    for col in REQUIRED_COLUMNS:
        if col not in out.columns:
            out[col] = None
    ordered = REQUIRED_COLUMNS + [c for c in out.columns if c not in REQUIRED_COLUMNS]
    out[ordered].to_csv(path, index=False)
    print(f"[costplus_graphql] Wrote {len(out):,} rows -> {path}")
    return path


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Fetch the full Cost Plus catalog via GraphQL")
    p.add_argument("--force-refresh", action="store_true", help="Bypass the on-disk page cache")
    p.add_argument("--page-limit", type=int, default=None, help="Cap the number of NEW live page fetches")
    args = p.parse_args()

    catalog_df = fetch_full_catalog(force_refresh=args.force_refresh, page_limit=args.page_limit)
    write_graphql_csv(catalog_df)
