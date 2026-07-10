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

    def test_recognizes_units_added_during_task2_recovery(self):
        # These were "unrecognized" before the Task 2 (c)-recovery pass broadened
        # the unit list; now genuinely unambiguous, so they should parse cleanly.
        self.assertEqual(scraper.parse_package_quantity_from_volume("2 Nasal Sprays"), 2.0)
        self.assertEqual(scraper.parse_package_quantity_from_volume("100 Pieces"), 100.0)
        self.assertEqual(scraper.parse_package_quantity_from_volume("1oz"), 1.0)
        self.assertEqual(scraper.parse_package_quantity_from_volume("5 Milliliters"), 5.0)

    def test_tolerates_one_trailing_descriptor_word_but_not_a_parenthetical(self):
        # "140gm Tube" / "10.2g Inhaler": a single trailing descriptor word is fine,
        # the leading "<number> <unit>" is still unambiguous.
        self.assertEqual(scraper.parse_package_quantity_from_volume("140gm Tube"), 140.0)
        self.assertEqual(scraper.parse_package_quantity_from_volume("10.2g Inhaler"), 10.2)
        # But a trailing parenthetical must NOT be silently discarded -- it can state
        # a genuinely different, competing quantity (see test_rejects_compound_descriptions).
        self.assertIsNone(scraper.parse_package_quantity_from_volume("60 Capsules (14 Caps 120mg & 46 Caps 240mg)"))


class TestParseCompoundPackageQuantity(unittest.TestCase):
    """Task 2 (this session): recovering the (c) 'parser rejected' bucket.
    Real examples from the 2,341-row catalog -- each states two true numbers
    (a total liquid volume and a discrete item count), disambiguated by the
    drug's real NADAC Pricing Unit rather than guessed."""

    def test_total_then_count_pattern_picks_count_for_ea(self):
        self.assertEqual(scraper.parse_compound_package_quantity("60mL (30 x 2mL)", "EA"), 30.0)

    def test_total_then_count_pattern_picks_total_for_ml(self):
        self.assertEqual(scraper.parse_compound_package_quantity("60mL (30 x 2mL)", "ML"), 60.0)

    def test_total_then_item_pattern(self):
        self.assertEqual(scraper.parse_compound_package_quantity("1.6mL (2 Pens)", "EA"), 2.0)
        self.assertEqual(scraper.parse_compound_package_quantity("1.6mL (2 Pens)", "ML"), 1.6)

    def test_count_then_total_pattern_ampules(self):
        self.assertEqual(scraper.parse_compound_package_quantity("56 Ampules (224mL)", "EA"), 56.0)
        self.assertEqual(scraper.parse_compound_package_quantity("56 Ampules (224mL)", "ML"), 224.0)

    def test_bare_x_pattern_computes_total(self):
        self.assertEqual(scraper.parse_compound_package_quantity("30 x 3mL", "EA"), 30.0)
        self.assertEqual(scraper.parse_compound_package_quantity("30 x 3mL", "ML"), 90.0)

    def test_unknown_pricing_unit_stays_ambiguous(self):
        # No crosswalk match / unresolved pricing unit -- must not guess between the two numbers.
        self.assertIsNone(scraper.parse_compound_package_quantity("60mL (30 x 2mL)", None))
        self.assertIsNone(scraper.parse_compound_package_quantity("60mL (30 x 2mL)", "GM"))

    def test_non_compound_text_returns_none(self):
        self.assertIsNone(scraper.parse_compound_package_quantity("30 Tablets", "EA"))
        self.assertIsNone(scraper.parse_compound_package_quantity(None, "EA"))


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

    def _fake_result(self, nadac_per_unit, pricing_unit=None):
        r = crosswalk.CrosswalkResult(drug_term="x")
        r.matched = True
        r.nadac_per_unit = nadac_per_unit
        r.pricing_unit = pricing_unit
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

    def test_compound_volume_text_left_ambiguous_when_pricing_unit_unresolved(self):
        with patch.object(scraper.crosswalk, "crosswalk_drug", return_value=self._fake_result(0.01, pricing_unit=None)), \
             patch("fetch.nadac.load_nadac", return_value=pd.DataFrame()):
            out = scraper.recover_package_quantity(self._scraped_df())

        row = out[out["drug"] == "DrugB"].iloc[0]
        self.assertTrue(pd.isna(row["package_quantity"]))
        self.assertEqual(row["package_quantity_status"], "ambiguous_volume_text")

    def test_compound_volume_text_confirmed_when_pricing_unit_resolves_it(self):
        # Task 2 recovery: DrugB's "60mL (30 x 2mL)" is only ambiguous without a
        # pricing unit -- an EA-priced drug unambiguously means 30 (vials/tablets/etc).
        with patch.object(scraper.crosswalk, "crosswalk_drug", return_value=self._fake_result(0.01, pricing_unit="EA")), \
             patch("fetch.nadac.load_nadac", return_value=pd.DataFrame()):
            out = scraper.recover_package_quantity(self._scraped_df())

        row = out[out["drug"] == "DrugB"].iloc[0]
        self.assertEqual(row["package_quantity"], 30.0)
        self.assertEqual(row["package_quantity_status"], "confirmed")

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
