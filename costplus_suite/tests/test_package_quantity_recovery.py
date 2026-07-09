"""
Unit tests for shared.costplus_scraper's package_quantity recovery.

Two things under test:
  1. parse_package_quantity_from_volume: pure string parsing of the
     `metafields.volume` field ("30 Tablets", "240mL", etc.) into a bare
     quantity, returning None for blank/compound/unrecognized text rather
     than guessing which number in a multi-part description is the real
     quantity.
  2. recover_package_quantity: wires that parser into the scraped catalog,
     attaching nadac_per_unit as transparency context ONLY (never as a
     pass/fail gate on the directly-stated quantity -- an earlier version of
     this function used nadac_per_unit as a hard filter and was found, during
     triage of the real 2,341-row catalog, to incorrectly reject explicitly-
     labeled cases like "Bottle of 30 Tablets" merely because Cost Plus
     priced ~3% below that week's NADAC national median).
crosswalk.crosswalk_drug is mocked so this is offline and deterministic.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import costplus_scraper as scraper  # noqa: E402
from shared import crosswalk  # noqa: E402


class TestParsePackageQuantityFromVolume(unittest.TestCase):
    def test_parses_simple_count_and_unit(self):
        self.assertEqual(scraper.parse_package_quantity_from_volume("30 Tablets"), 30.0)
        self.assertEqual(scraper.parse_package_quantity_from_volume("240mL"), 240.0)
        self.assertEqual(scraper.parse_package_quantity_from_volume("45gm"), 45.0)
        self.assertEqual(scraper.parse_package_quantity_from_volume("4 Patches"), 4.0)
        self.assertEqual(scraper.parse_package_quantity_from_volume("1 ea"), 1.0)

    def test_strips_box_of_pack_of_prefix(self):
        self.assertEqual(scraper.parse_package_quantity_from_volume("Box of 30 Vials"), 30.0)
        self.assertEqual(scraper.parse_package_quantity_from_volume("Pack of 8 Vials"), 8.0)

    def test_rejects_compound_descriptions(self):
        # Ambiguous which number is the NADAC-comparable quantity -- must not guess.
        self.assertIsNone(scraper.parse_package_quantity_from_volume("60mL (30 x 2mL)"))
        self.assertIsNone(scraper.parse_package_quantity_from_volume("1.6mL (2 Prefilled Syringes)"))
        self.assertIsNone(scraper.parse_package_quantity_from_volume("56 Ampules (224mL)"))

    def test_rejects_blank_or_missing(self):
        self.assertIsNone(scraper.parse_package_quantity_from_volume(None))
        self.assertIsNone(scraper.parse_package_quantity_from_volume(""))
        self.assertIsNone(scraper.parse_package_quantity_from_volume(float("nan")))

    def test_rejects_unrecognized_unit(self):
        self.assertIsNone(scraper.parse_package_quantity_from_volume("2 Nasal Sprays"))
        self.assertIsNone(scraper.parse_package_quantity_from_volume("100 Pieces"))


class TestRecoverPackageQuantity(unittest.TestCase):
    def _scraped_df(self):
        return pd.DataFrame(
            [
                {"drug": "DrugA", "strength": "10mg", "form": "Tablet", "final_price": 10.00, "volume_raw": "30 Tablets"},
                {"drug": "DrugB", "strength": "5mg", "form": "Tablet", "final_price": 8.00, "volume_raw": "60mL (30 x 2mL)"},  # compound, ambiguous
                {"drug": "DrugC", "strength": "1mg", "form": "Capsule", "final_price": 5.00, "volume_raw": None},  # nothing captured
                {"drug": None, "strength": None, "form": None, "final_price": None, "volume_raw": None},  # fetch_failed row
            ]
        )

    def _fake_result(self, nadac_per_unit):
        r = crosswalk.CrosswalkResult(drug_term="x")
        r.matched = True
        r.nadac_per_unit = nadac_per_unit
        return r

    def test_confirms_unambiguous_volume_text_regardless_of_nadac_comparison(self):
        # DrugA's implied per-unit (10/30 = 0.33) is BELOW this mocked nadac_per_unit (0.50) --
        # must still be confirmed, since the visible text is unambiguous and nadac_per_unit
        # is context only, not a veto (this is the exact real-world case -- Prasugrel -- that
        # motivated dropping the old economic-consistency gate).
        with patch.object(scraper.crosswalk, "crosswalk_drug", return_value=self._fake_result(0.50)), \
             patch("fetch.nadac.load_nadac", return_value=pd.DataFrame()):
            out = scraper.recover_package_quantity(self._scraped_df())

        row = out[out["drug"] == "DrugA"].iloc[0]
        self.assertEqual(row["package_quantity"], 30.0)
        self.assertEqual(row["package_quantity_status"], "confirmed")
        self.assertAlmostEqual(row["nadac_per_unit"], 0.50)  # still attached, as context

    def test_compound_volume_text_left_ambiguous(self):
        with patch.object(scraper.crosswalk, "crosswalk_drug", return_value=self._fake_result(0.01)), \
             patch("fetch.nadac.load_nadac", return_value=pd.DataFrame()):
            out = scraper.recover_package_quantity(self._scraped_df())

        row = out[out["drug"] == "DrugB"].iloc[0]
        self.assertTrue(pd.isna(row["package_quantity"]))
        self.assertEqual(row["package_quantity_status"], "ambiguous_volume_text")

    def test_missing_volume_text_flagged(self):
        with patch.object(scraper.crosswalk, "crosswalk_drug", return_value=self._fake_result(0.01)), \
             patch("fetch.nadac.load_nadac", return_value=pd.DataFrame()):
            out = scraper.recover_package_quantity(self._scraped_df())

        row = out[out["drug"] == "DrugC"].iloc[0]
        self.assertTrue(pd.isna(row["package_quantity"]))
        self.assertEqual(row["package_quantity_status"], "no_volume_text_captured")

    def test_rows_with_no_drug_identity_flagged_without_crosswalk_call(self):
        calls = []

        def fake_crosswalk_drug(term, nadac_df):
            calls.append(term)
            return self._fake_result(None)

        with patch.object(scraper.crosswalk, "crosswalk_drug", side_effect=fake_crosswalk_drug), \
             patch("fetch.nadac.load_nadac", return_value=pd.DataFrame()):
            out = scraper.recover_package_quantity(self._scraped_df())

        blank_row = out[out["drug"].isna()].iloc[0]
        self.assertEqual(blank_row["package_quantity_status"], "no_drug_strength_form")
        self.assertEqual(len(calls), 3)  # never attempted for the blank-identity row


class TestBuildRunnableCatalog(unittest.TestCase):
    def test_keeps_only_confirmed_rows(self):
        df = pd.DataFrame(
            [
                {"drug": "A", "package_quantity_status": "confirmed", "package_quantity": 30},
                {"drug": "B", "package_quantity_status": "ambiguous_volume_text", "package_quantity": None},
                {"drug": "C", "package_quantity_status": "no_drug_strength_form", "package_quantity": None},
                {"drug": "D", "package_quantity_status": "no_volume_text_captured", "package_quantity": None},
            ]
        )
        out = scraper.build_runnable_catalog(df)
        self.assertEqual(list(out["drug"]), ["A"])


if __name__ == "__main__":
    unittest.main()
