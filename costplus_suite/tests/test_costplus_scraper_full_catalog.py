"""
Unit tests for shared.costplus_scraper's full-catalog enumeration:
_rows_from_product_page (variant extraction) and scrape_full_catalog's
sibling-slug dedup/limit bookkeeping. No network -- _polite_get is
monkey-patched with an in-memory fixture map.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import costplus_scraper as scraper  # noqa: E402
from tests.test_costplus_scraper_parsing import _make_next_f_html, _jsonld_payload, _product_details_payload  # noqa: E402


def _atorvastatin_page() -> str:
    jsonld = {
        "name": "Atorvastatin 20mg Tablet",
        "sku": "39400010100320-Generic",
        "brand": {"name": "Lipitor"},
        "offers": {"price": 5.46, "shippingDetails": {"shippingRate": {"value": 5}}},
    }
    details = {
        "name": "Atorvastatin",
        "variants": [
            {"priceCalculation": 5.26, "sku": "SKU10", "metafields": {"strength": "10mg", "form": "Tablet", "package_size": "1000", "slug": "atorvastatin-10mg-tablet"}},
            {"priceCalculation": 5.46, "sku": "SKU20", "metafields": {"strength": "20mg", "form": "Tablet", "package_size": "1000", "slug": "atorvastatin-20mg-tablet"}},
            {"priceCalculation": 5.74, "sku": "SKU40", "metafields": {"strength": "40mg", "form": "Tablet", "package_size": "1000", "slug": "atorvastatin-40mg-tablet"}},
        ],
    }
    return _make_next_f_html(_jsonld_payload(jsonld)) + _make_next_f_html(_product_details_payload(details))


def _jsonld_only_page() -> str:
    jsonld = {"name": "Simple Drug 10mg", "sku": "SKU1", "offers": {"price": 12.0}}
    return _make_next_f_html(_jsonld_payload(jsonld))


class TestRowsFromProductPage(unittest.TestCase):
    def test_extracts_one_row_per_sibling_variant(self):
        rows = scraper._rows_from_product_page(_atorvastatin_page())
        self.assertEqual(len(rows), 3)
        strengths = {r["strength"] for r in rows}
        self.assertEqual(strengths, {"10mg", "20mg", "40mg"})

    def test_every_row_carries_real_shipping_and_brand_blank_breakdown(self):
        rows = scraper._rows_from_product_page(_atorvastatin_page())
        for r in rows:
            self.assertEqual(r["shipping_fee"], 5)
            self.assertEqual(r["brand_name"], "Lipitor")
            self.assertIsNone(r["acquisition_cost"])
            self.assertIsNone(r["markup"])
            self.assertIsNone(r["pharmacy_fee"])
            self.assertIsNone(r["package_quantity"])

    def test_falls_back_to_jsonld_only_single_row(self):
        rows = scraper._rows_from_product_page(_jsonld_only_page())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["drug"], "Simple Drug 10mg")
        self.assertEqual(rows[0]["final_price"], 12.0)

    def test_no_data_returns_empty_list(self):
        rows = scraper._rows_from_product_page(_make_next_f_html('[["$","div",null,{"children":"nothing"}]]'))
        self.assertEqual(rows, [])


class TestScrapeFullCatalog(unittest.TestCase):
    def test_sibling_slugs_are_skipped_after_first_fetch(self):
        """The 3 sibling slugs of atorvastatin should cost exactly 1 real fetch."""
        fetches = []

        def fake_get(url, force_refresh=False):
            fetches.append(url)
            return _atorvastatin_page()

        original = scraper._polite_get
        scraper._polite_get = fake_get
        try:
            slugs = ["atorvastatin-10mg-tablet", "atorvastatin-20mg-tablet", "atorvastatin-40mg-tablet"]
            original_discover = scraper.discover_catalog_slugs
            scraper.discover_catalog_slugs = lambda force_refresh=False: slugs
            try:
                df, total_catalog_size = scraper.scrape_full_catalog()
            finally:
                scraper.discover_catalog_slugs = original_discover
        finally:
            scraper._polite_get = original

        self.assertEqual(len(fetches), 1)  # only 1 real HTTP fetch for all 3 sibling slugs
        self.assertEqual(total_catalog_size, 3)
        self.assertEqual(len(df), 3)  # but 3 rows of real data recovered

    def test_limit_caps_real_fetches_not_covered_slugs(self):
        fetches = []

        def fake_get(url, force_refresh=False):
            fetches.append(url)
            return _jsonld_only_page()

        original = scraper._polite_get
        scraper._polite_get = fake_get
        try:
            slugs = ["drug-a", "drug-b", "drug-c", "drug-d"]
            original_discover = scraper.discover_catalog_slugs
            scraper.discover_catalog_slugs = lambda force_refresh=False: slugs
            try:
                df, total_catalog_size = scraper.scrape_full_catalog(limit=2)
            finally:
                scraper.discover_catalog_slugs = original_discover
        finally:
            scraper._polite_get = original

        self.assertEqual(len(fetches), 2)
        self.assertEqual(total_catalog_size, 4)

    def test_fetch_failure_recorded_not_raised(self):
        original = scraper._polite_get
        scraper._polite_get = lambda url, force_refresh=False: None
        try:
            original_discover = scraper.discover_catalog_slugs
            scraper.discover_catalog_slugs = lambda force_refresh=False: ["dead-slug"]
            try:
                df, total_catalog_size = scraper.scrape_full_catalog()
            finally:
                scraper.discover_catalog_slugs = original_discover
        finally:
            scraper._polite_get = original

        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["scrape_status"], "fetch_failed")


if __name__ == "__main__":
    unittest.main()
