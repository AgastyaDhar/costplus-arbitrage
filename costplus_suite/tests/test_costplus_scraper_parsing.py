"""
Unit tests for shared.costplus_scraper's HTML/JSON extraction and slug
matching. No network calls -- synthetic fixtures are built by re-deriving the
exact same double-escaping a Next.js App Router page produces (verified
against real costplusdrugs.com product pages during development), so these
tests catch a real parser regression rather than testing a hand-simulated
approximation of the format.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import costplus_scraper as scraper  # noqa: E402


def _make_next_f_html(payload_source: str) -> str:
    """Wrap a raw payload string exactly as Next.js's App Router does: the
    whole thing becomes the second element of `self.__next_f.push([1, ...])`,
    JSON-string-encoded (this is real per-call escaping, not simulated)."""
    return f"<script>self.__next_f.push([1,{json.dumps(payload_source)}])</script>"


def _jsonld_payload(jsonld_dict: dict) -> str:
    # The JSON-LD text is itself JSON.stringify'd once before being embedded
    # as the __html attribute's value -- that's the second level of escaping.
    html_value = json.dumps(jsonld_dict)
    return (
        '[["$","script","product-jsonld",{"type":"application/ld+json",'
        f'"dangerouslySetInnerHTML":{{"__html":{json.dumps(html_value)}}}}}]]'
    )


def _product_details_payload(details_dict: dict) -> str:
    return f'9:[false,["$","div",null,{{"productDetails":{json.dumps(details_dict)}}}]]'


class TestScanQuotedString(unittest.TestCase):
    def test_simple(self):
        text = 'prefix "hello world" suffix'
        val, end = scraper._scan_quoted_string(text, text.index('"'))
        self.assertEqual(val, "hello world")
        self.assertEqual(text[end:], " suffix")

    def test_escaped_quotes_inside(self):
        text = 'x ' + json.dumps('he said "hi"') + ' y'
        val, end = scraper._scan_quoted_string(text, text.index('"'))
        self.assertEqual(val, 'he said "hi"')


class TestScanBalanced(unittest.TestCase):
    def test_object_with_nested_braces_and_strings(self):
        text = 'pre {"a":1,"b":{"c":"}"},"d":[1,2]} post'
        start = text.index("{")
        raw, end = scraper._scan_balanced(text, start)
        self.assertEqual(json.loads(raw), {"a": 1, "b": {"c": "}"}, "d": [1, 2]})

    def test_array(self):
        text = 'x [1,[2,3],"[nested string]"] y'
        start = text.index("[")
        raw, end = scraper._scan_balanced(text, start)
        self.assertEqual(json.loads(raw), [1, [2, 3], "[nested string]"])


class TestExtractJsonld(unittest.TestCase):
    def test_extracts_real_offer_fields(self):
        jsonld = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Atorvastatin 20mg Tablet",
            "sku": "39400010100320-Generic",
            "brand": {"@type": "Brand", "name": "Lipitor"},
            "offers": {
                "@type": "Offer",
                "priceCurrency": "USD",
                "price": 5.46,
                "shippingDetails": {"shippingRate": {"value": 5, "currency": "USD"}},
            },
        }
        html = _make_next_f_html(_jsonld_payload(jsonld))
        result = scraper.extract_jsonld(html)
        self.assertIsNotNone(result)
        self.assertEqual(result["brand"]["name"], "Lipitor")
        self.assertEqual(result["offers"]["price"], 5.46)
        self.assertEqual(result["offers"]["shippingDetails"]["shippingRate"]["value"], 5)

    def test_returns_none_when_absent(self):
        html = _make_next_f_html('[["$","div",null,{"children":"nothing here"}]]')
        self.assertIsNone(scraper.extract_jsonld(html))

    def test_ignores_unrelated_push_calls(self):
        jsonld = {"name": "Metformin 500mg Tablet", "brand": {"name": "Glucophage"}, "offers": {"price": 5.26}}
        html = (
            _make_next_f_html('7:[false,["$","noscript",null,{"children":"unrelated"}]]')
            + _make_next_f_html(_jsonld_payload(jsonld))
        )
        result = scraper.extract_jsonld(html)
        self.assertEqual(result["brand"]["name"], "Glucophage")


class TestExtractProductDetails(unittest.TestCase):
    def test_extracts_variants(self):
        details = {
            "id": "UHJvZHVjdDoxNTM2",
            "name": "Atorvastatin",
            "priceCalculation": 5.46,
            "variants": [
                {
                    "sku": "39400010100310-Generic",
                    "priceCalculation": 5.26,
                    "metafields": {"strength": "10mg", "form": "Tablet", "package_size": "1000"},
                },
                {
                    "sku": "39400010100320-Generic",
                    "priceCalculation": 5.46,
                    "metafields": {"strength": "20mg", "form": "Tablet", "package_size": "1000"},
                },
            ],
        }
        html = _make_next_f_html(_product_details_payload(details))
        result = scraper.extract_product_details(html)
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Atorvastatin")
        self.assertEqual(len(result["variants"]), 2)
        self.assertEqual(result["variants"][1]["metafields"]["strength"], "20mg")


class TestScrapeDrugParsing(unittest.TestCase):
    """End-to-end field extraction from a full synthetic page, via a monkey-
    patched _polite_get so no network/robots.txt/rate-limit path is touched."""

    def test_scrape_drug_populates_real_fields_and_blanks_unexposed_ones(self):
        jsonld = {
            "name": "Atorvastatin 20mg Tablet",
            "sku": "39400010100320-Generic",
            "brand": {"name": "Lipitor"},
            "offers": {"price": 5.46, "shippingDetails": {"shippingRate": {"value": 5}}},
        }
        details = {
            "name": "Atorvastatin",
            "variants": [
                {"priceCalculation": 5.46, "metafields": {"strength": "20mg", "form": "Tablet", "package_size": "1000"}},
            ],
        }
        html = _make_next_f_html(_jsonld_payload(jsonld)) + _make_next_f_html(_product_details_payload(details))

        original_get = scraper._polite_get
        scraper._polite_get = lambda url, force_refresh=False: html
        try:
            row = scraper.scrape_drug("atorvastatin", "20 mg", "tablet", ["atorvastatin-20mg-tablet"])
        finally:
            scraper._polite_get = original_get

        self.assertEqual(row["scrape_status"], "ok")
        self.assertEqual(row["brand_name"], "Lipitor")
        self.assertEqual(row["observed_costplus_price"], 5.46)
        self.assertEqual(row["shipping_fee"], 5)
        # never exposed by the site -- must stay blank, never back-solved
        self.assertIsNone(row["acquisition_cost"])
        self.assertIsNone(row["markup"])
        self.assertIsNone(row["pharmacy_fee"])
        self.assertIsNone(row["package_quantity"])

    def test_no_matching_slug_short_circuits_without_a_fetch(self):
        original_get = scraper._polite_get

        def _boom(url, force_refresh=False):
            raise AssertionError("should not fetch when no slug matches")

        scraper._polite_get = _boom
        try:
            row = scraper.scrape_drug("unobtainium", "1 mg", "tablet", ["atorvastatin-20mg-tablet"])
        finally:
            scraper._polite_get = original_get
        self.assertEqual(row["scrape_status"], "no_matching_slug")


class TestMatchSlugForDrug(unittest.TestCase):
    def test_matches_on_name_and_strength(self):
        slugs = ["atorvastatin-10mg-tablet", "atorvastatin-20mg-tablet", "atorvastatin-40mg-tablet"]
        self.assertEqual(scraper.match_slug_for_drug("atorvastatin", "20 mg", "tablet", slugs), "atorvastatin-20mg-tablet")

    def test_strips_salt_suffix_before_matching(self):
        slugs = ["losartan-potassium-50mg-tablet-cozaar"]
        self.assertEqual(scraper.match_slug_for_drug("losartan potassium", "50 mg", "tablet", slugs), slugs[0])

    def test_no_match_returns_none_rather_than_guessing(self):
        slugs = ["atorvastatin-20mg-tablet"]
        self.assertIsNone(scraper.match_slug_for_drug("metformin", "500 mg", "tablet", slugs))

    def test_strength_mismatch_is_rejected(self):
        slugs = ["atorvastatin-40mg-tablet"]
        self.assertIsNone(scraper.match_slug_for_drug("atorvastatin", "20 mg", "tablet", slugs))


if __name__ == "__main__":
    unittest.main()
