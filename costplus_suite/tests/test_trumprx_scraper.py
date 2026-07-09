"""
Unit tests for fetch.trumprx's live-scrape parsing: catalog discovery
(/browse's `drugs` array, including the split-across-payloads case found
during reconnaissance) and per-product scraping (/p/{slug}'s `drugVariants`
array, with JSON-LD and rendered-text fallbacks). No network -- fetch.trumprx
._polite_get is monkey-patched with in-memory fixtures built the same way
tests/test_costplus_scraper_parsing.py builds its Next.js push fixtures.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fetch import trumprx  # noqa: E402
from tests.test_costplus_scraper_parsing import _make_next_f_html  # noqa: E402


def _drugs_catalog_payload(drugs: list) -> str:
    return f'1c:["$","$L2e",null,{{"drugs":{json.dumps(drugs)}}}]'


def _drug_variants_payload(medication_name: str, slug: str, variants: list) -> str:
    return (
        f'20:["$","$L22",null,{{"medicationName":"{medication_name}","medicationSlug":"{slug}"}}]\n'
        f'19:[false,["$","div",null,{{"drugVariants":{json.dumps(variants)}}}]]'
    )


def _ldjson_drug_payload(drug_jsonld: dict) -> str:
    html_value = json.dumps(drug_jsonld)
    return f'1b:[["$","script",null,{{"type":"application/ld+json","dangerouslySetInnerHTML":{{"__html":{json.dumps(html_value)}}}}}]]'


class TestDiscoverCatalog(unittest.TestCase):
    def test_parses_drugs_array_from_single_payload(self):
        drugs = [{"slug": "lantus", "drugName": "Lantus", "genericName": "insulin glargine"}]
        html = _make_next_f_html(_drugs_catalog_payload(drugs))
        trumprx._polite_get = lambda path, force_refresh=False: html
        try:
            result = trumprx.discover_catalog()
        finally:
            trumprx._polite_get = trumprx._fetcher.get
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["slug"], "lantus")

    def test_parses_drugs_array_split_across_multiple_push_calls(self):
        """Reconnaissance finding: /browse's real catalog array (79 entries)
        was split across at least two self.__next_f.push calls -- a
        per-payload search came up empty, concatenation found it. Simulate
        that by cutting the payload string in half across two push calls."""
        drugs = [
            {"slug": "lantus", "drugName": "Lantus", "genericName": "insulin glargine"},
            {"slug": "humira-pen", "drugName": "Humira Pen", "genericName": "adalimumab"},
        ]
        full_payload = _drugs_catalog_payload(drugs)
        midpoint = len(full_payload) // 2
        html = _make_next_f_html(full_payload[:midpoint]) + _make_next_f_html(full_payload[midpoint:])
        trumprx._polite_get = lambda path, force_refresh=False: html
        try:
            result = trumprx.discover_catalog()
        finally:
            trumprx._polite_get = trumprx._fetcher.get
        self.assertEqual(len(result), 2)
        self.assertEqual({d["slug"] for d in result}, {"lantus", "humira-pen"})

    def test_missing_catalog_marker_returns_empty_list_not_error(self):
        html = _make_next_f_html('["$","div",null,{"children":"nothing here"}]')
        trumprx._polite_get = lambda path, force_refresh=False: html
        try:
            result = trumprx.discover_catalog()
        finally:
            trumprx._polite_get = trumprx._fetcher.get
        self.assertEqual(result, [])

    def test_fetch_failure_returns_empty_list(self):
        trumprx._polite_get = lambda path, force_refresh=False: None
        try:
            result = trumprx.discover_catalog()
        finally:
            trumprx._polite_get = trumprx._fetcher.get
        self.assertEqual(result, [])


class TestScrapeProduct(unittest.TestCase):
    def test_prefers_drug_variants_multi_dosage(self):
        # Lantus: SoloStar Pen and Vial, same trxPrice, different beforePrice.
        variants = [
            {"form": "SoloStar Pen", "strength": "100", "quantity": "5", "price": {"trxPrice": 3500, "beforePrice": 9638}},
            {"form": "Vial", "strength": "100", "quantity": "1", "price": {"trxPrice": 3500, "beforePrice": 6426}},
        ]
        html = _make_next_f_html(_drug_variants_payload("Lantus", "lantus", variants))
        trumprx._polite_get = lambda path, force_refresh=False: html
        try:
            rows = trumprx.scrape_product("lantus", generic_name_hint="insulin glargine")
        finally:
            trumprx._polite_get = trumprx._fetcher.get

        self.assertEqual(len(rows), 2)
        pen_row = next(r for r in rows if r["form"] == "SoloStar Pen")
        vial_row = next(r for r in rows if r["form"] == "Vial")
        self.assertEqual(pen_row["brand_name"], "Lantus")
        self.assertAlmostEqual(pen_row["trumprx_price"], 35.00, places=2)
        self.assertAlmostEqual(pen_row["list_price"], 96.38, places=2)
        self.assertAlmostEqual(vial_row["list_price"], 64.26, places=2)
        self.assertEqual(pen_row["scrape_status"], "ok")
        self.assertIn("qty 5", pen_row["dosage"])

    def test_falls_back_to_jsonld_when_no_drug_variants(self):
        jsonld = {
            "@type": "Drug", "name": "Zyvox", "dosageForm": "Oral Suspension",
            "offers": {"@type": "AggregateOffer", "lowPrice": "122.74", "highPrice": "122.74"},
        }
        html = _make_next_f_html(_ldjson_drug_payload(jsonld))
        trumprx._polite_get = lambda path, force_refresh=False: html
        try:
            rows = trumprx.scrape_product("zyvox", generic_name_hint="linezolid")
        finally:
            trumprx._polite_get = trumprx._fetcher.get

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scrape_status"], "ok_jsonld_fallback")
        self.assertAlmostEqual(rows[0]["trumprx_price"], 122.74, places=2)
        self.assertIsNone(rows[0]["list_price"])

    def test_falls_back_to_rendered_text_when_no_structured_data(self):
        html = _make_next_f_html('["$","div",null,{"children":"nothing structured"}]') + "<div>Starting at $950.00</div>"
        trumprx._polite_get = lambda path, force_refresh=False: html
        try:
            rows = trumprx.scrape_product("humira-pen", brand_name_hint="Humira Pen")
        finally:
            trumprx._polite_get = trumprx._fetcher.get

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scrape_status"], "fallback_text_parse")
        self.assertAlmostEqual(rows[0]["trumprx_price"], 950.00, places=2)

    def test_fetch_failure_recorded_not_raised(self):
        trumprx._polite_get = lambda path, force_refresh=False: None
        try:
            rows = trumprx.scrape_product("dead-slug")
        finally:
            trumprx._polite_get = trumprx._fetcher.get
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scrape_status"], "fetch_failed")

    def test_unparseable_page_recorded_not_raised(self):
        html = _make_next_f_html('["$","div",null,{"children":"nothing at all"}]')
        trumprx._polite_get = lambda path, force_refresh=False: html
        try:
            rows = trumprx.scrape_product("mystery-drug")
        finally:
            trumprx._polite_get = trumprx._fetcher.get
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scrape_status"], "unparseable_page")


class TestLoadTrumprxPricesFallback(unittest.TestCase):
    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            trumprx.load_trumprx_prices(Path("/nonexistent/trumprx.csv"))


if __name__ == "__main__":
    unittest.main()
