"""
Unit tests for shared.costplus_scraper.fetch_missing_variants (Task 3: fetch
ONLY the specific slugs whose own page was never a direct fetch target) and
merge_scraped_rows (folding those results back into the full catalog).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import costplus_scraper as scraper  # noqa: E402
from shared import scrape_utils  # noqa: E402
from tests.test_costplus_scraper_parsing import _make_next_f_html, _jsonld_payload, _product_details_payload  # noqa: E402


def _fake_page(drug, strength, volume, slug, price=5.0):
    jsonld = {"name": drug, "sku": "SKU1", "offers": {"price": price}}
    details = {
        "name": drug,
        "variants": [
            {"priceCalculation": price, "sku": "SKU1", "metafields": {"strength": strength, "form": "Tablet", "volume": volume, "slug": slug}},
        ],
    }
    return _make_next_f_html(_jsonld_payload(jsonld)) + _make_next_f_html(_product_details_payload(details))


class TestFetchMissingVariants(unittest.TestCase):
    def test_fetches_each_target_slug_and_extracts_volume(self):
        pages = {
            "drug-a-10mg-tablet": _fake_page("Drug A", "10mg", "30 Tablets", "drug-a-10mg-tablet"),
            "drug-b-20mg-tablet": _fake_page("Drug B", "20mg", "", "drug-b-20mg-tablet"),
        }
        with patch.object(scrape_utils.PoliteFetcher, "get", side_effect=lambda url, force_refresh=False: pages.get(url.rsplit("/", 2)[-2])):
            out = scraper.fetch_missing_variants(["drug-a-10mg-tablet", "drug-b-20mg-tablet"])

        self.assertEqual(len(out), 2)
        row_a = out[out["scrape_matched_slug"] == "drug-a-10mg-tablet"].iloc[0]
        self.assertEqual(row_a["volume_raw"], "30 Tablets")
        row_b = out[out["scrape_matched_slug"] == "drug-b-20mg-tablet"].iloc[0]
        self.assertTrue(pd.isna(row_b["volume_raw"]) or row_b["volume_raw"] is None)

    def test_fetch_failure_recorded_not_raised(self):
        with patch.object(scrape_utils.PoliteFetcher, "get", return_value=None):
            out = scraper.fetch_missing_variants(["dead-slug"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["scrape_status"], "fetch_failed")

    def test_stops_and_reports_on_challenge_detected(self):
        call_count = [0]

        def fake_get(url, force_refresh=False):
            call_count[0] += 1
            if call_count[0] == 2:
                raise scrape_utils.ChallengeDetectedError("boom")
            return _fake_page("Drug", "10mg", "30 Tablets", "slug")

        with patch.object(scrape_utils.PoliteFetcher, "get", side_effect=fake_get):
            out = scraper.fetch_missing_variants(["slug-1", "slug-2", "slug-3"])

        self.assertEqual(len(out), 1)  # only slug-1 completed before the challenge on slug-2
        self.assertEqual(call_count[0], 2)  # never attempted slug-3


class TestMergeScrapedRows(unittest.TestCase):
    def test_replaces_matching_slugs_and_keeps_others(self):
        base = pd.DataFrame(
            [
                {"scrape_matched_slug": "a", "volume_raw": None},
                {"scrape_matched_slug": "b", "volume_raw": None},
                {"scrape_matched_slug": "c", "volume_raw": "30 Tablets"},
            ]
        )
        updated = pd.DataFrame([{"scrape_matched_slug": "a", "volume_raw": "60mL"}])
        out = scraper.merge_scraped_rows(base, updated)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[out["scrape_matched_slug"] == "a"].iloc[0]["volume_raw"], "60mL")
        self.assertEqual(out[out["scrape_matched_slug"] == "c"].iloc[0]["volume_raw"], "30 Tablets")

    def test_empty_updated_rows_returns_base_unchanged(self):
        base = pd.DataFrame([{"scrape_matched_slug": "a", "volume_raw": None}])
        out = scraper.merge_scraped_rows(base, pd.DataFrame())
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
