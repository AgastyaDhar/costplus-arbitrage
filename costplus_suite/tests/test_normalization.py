"""
Unit tests for NDC normalization and drug-name normalization (the join keys
used throughout the suite). Pure string logic, no network.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import crosswalk  # noqa: E402


class TestNormalizeNdc(unittest.TestCase):
    def test_strips_dashes(self):
        self.assertEqual(crosswalk.normalize_ndc("13533-0636-01"), "13533063601")

    def test_zero_pads_short_ndc(self):
        # a 10-digit NDC (5-3-2 or 4-4-2 formats) must zero-pad to 11 digits
        self.assertEqual(crosswalk.normalize_ndc("0093-5059-10"), "00093505910")

    def test_already_normalized_is_unchanged(self):
        self.assertEqual(crosswalk.normalize_ndc("00093505910"), "00093505910")

    def test_matches_nadac_and_rxnav_format(self):
        # Both fetch.nadac and RxNav's ndcs.json return 11-digit, no-dash
        # strings -- this is the join key the whole crosswalk depends on.
        rxnav_style = "00378395105"
        self.assertEqual(crosswalk.normalize_ndc(rxnav_style), rxnav_style)
        self.assertEqual(len(crosswalk.normalize_ndc(rxnav_style)), 11)


class TestNormalizeDrugName(unittest.TestCase):
    def test_strips_salt_suffix(self):
        self.assertEqual(crosswalk.normalize_drug_name("Atorvastatin Calcium"), "ATORVASTATIN")

    def test_ingredient_and_cms_generic_name_converge(self):
        # RxNav ingredient name vs Part D's free-text Gnrc_Name must normalize
        # to the same join key despite differing salt-form spelling.
        rxnav_ingredient = "levothyroxine"
        partd_gnrc_name = "Levothyroxine Sodium"
        self.assertEqual(
            crosswalk.normalize_drug_name(rxnav_ingredient),
            crosswalk.normalize_drug_name(partd_gnrc_name),
        )

    def test_case_insensitive(self):
        self.assertEqual(crosswalk.normalize_drug_name("METFORMIN"), crosswalk.normalize_drug_name("metformin"))

    def test_empty_input(self):
        self.assertEqual(crosswalk.normalize_drug_name(""), "")
        self.assertEqual(crosswalk.normalize_drug_name(None), "")


if __name__ == "__main__":
    unittest.main()
