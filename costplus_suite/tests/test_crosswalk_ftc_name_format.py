"""
Regression tests for the FTC name-format crosswalk fix:
  - strip_brand_and_form(): strips a parenthetical brand name and a
    trailing low-signal dose-form word (FTC's "Generic (Brand) Form"
    convention, e.g. "Imatinib (Gleevec) Pill" -> "Imatinib").
  - resolve_dispensable_rxcui()'s _TTY_PREFERENCE: prefers a generic
    (SCD/GPCK) candidate over a branded one (SBD/BPCK) whenever both pass
    the token-overlap relevance check, since the leaderboard is built
    entirely from Cost Plus's generic catalog and a branded RxCUI can
    never join to it.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import crosswalk  # noqa: E402


class TestStripBrandAndForm(unittest.TestCase):
    def test_strips_parenthetical_brand_and_trailing_pill(self):
        self.assertEqual(crosswalk.strip_brand_and_form("Imatinib (Gleevec) Pill"), "Imatinib")

    def test_strips_multi_brand_parenthetical_and_trailing_pill(self):
        self.assertEqual(
            crosswalk.strip_brand_and_form("Everolimus (Afinitor/Zortress) Pill"), "Everolimus"
        )

    def test_strips_trailing_oral(self):
        self.assertEqual(crosswalk.strip_brand_and_form("Efavirenz (Sustiva) Oral"), "Efavirenz")

    def test_leaves_a_real_dose_form_word_alone(self):
        # "Liquid" is deliberately not in _LOW_SIGNAL_TRAILING_WORDS -- it's
        # real, useful dose-form signal, unlike "Pill"/"Oral"/"Tablet". Only
        # trailing words are stripped, one at a time from the end, so
        # "Oral" survives here too: it isn't the last word, and stripping
        # stops the moment the last word ("Liquid") fails the check.
        self.assertEqual(
            crosswalk.strip_brand_and_form("Cyclosporine (Gengraf) Oral Liquid"),
            "Cyclosporine Oral Liquid",
        )

    def test_no_op_on_a_term_with_no_parenthetical_or_trailing_low_signal_word(self):
        term = "Atorvastatin 20mg Bottle of Capsules"
        self.assertEqual(crosswalk.strip_brand_and_form(term), term)

    def test_never_mutates_input_string(self):
        term = "Imatinib (Gleevec) Pill"
        crosswalk.strip_brand_and_form(term)
        self.assertEqual(term, "Imatinib (Gleevec) Pill")


class TestTtyPreference(unittest.TestCase):
    def test_prefers_scd_over_higher_ranked_sbd(self):
        # RxNav ranks the branded SBD first (rank 1) -- exactly what FTC's
        # "Generic (Brand) Form" convention triggers -- but a generic SCD
        # exists lower in the ranked list (rank 5). The SCD must win.
        candidates = [
            {"rxcui": "213460", "name": "abacavir 300 MG Oral Tablet [Ziagen]", "rank": 1, "score": 100.0},
            {"rxcui": "242679", "name": "abacavir 300 MG Oral Tablet", "rank": 5, "score": 80.0},
        ]
        tty_map = {"213460": "SBD", "242679": "SCD"}
        name_map = {
            "213460": "abacavir 300 MG Oral Tablet [Ziagen]",
            "242679": "abacavir 300 MG Oral Tablet",
        }
        with patch.object(crosswalk, "approximate_term", return_value=candidates), \
             patch.object(crosswalk, "get_rxcui_tty", side_effect=lambda r: tty_map[r]), \
             patch.object(crosswalk, "get_rxcui_name", side_effect=lambda r: name_map[r]):
            resolved = crosswalk.resolve_dispensable_rxcui("Abacavir")

        self.assertEqual(resolved["rxcui"], "242679")
        self.assertEqual(resolved["tty"], "SCD")

    def test_falls_through_to_branded_when_no_generic_exists(self):
        # A drug genuinely marketed brand-only (no SCD anywhere in the
        # ranked results) must still resolve to the branded candidate
        # rather than returning None.
        candidates = [
            {"rxcui": "111", "name": "somedrug 10 MG Oral Tablet [SomeBrand]", "rank": 1, "score": 100.0},
        ]
        with patch.object(crosswalk, "approximate_term", return_value=candidates), \
             patch.object(crosswalk, "get_rxcui_tty", return_value="SBD"), \
             patch.object(crosswalk, "get_rxcui_name", return_value="somedrug 10 MG Oral Tablet [SomeBrand]"):
            resolved = crosswalk.resolve_dispensable_rxcui("Somedrug")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["rxcui"], "111")
        self.assertEqual(resolved["tty"], "SBD")

    def test_gpck_preferred_over_sbd_but_not_over_scd(self):
        candidates = [
            {"rxcui": "1", "name": "drugx SBD", "rank": 1, "score": 100.0},
            {"rxcui": "2", "name": "drugx GPCK", "rank": 2, "score": 90.0},
            {"rxcui": "3", "name": "drugx SCD", "rank": 3, "score": 80.0},
        ]
        tty_map = {"1": "SBD", "2": "GPCK", "3": "SCD"}
        with patch.object(crosswalk, "approximate_term", return_value=candidates), \
             patch.object(crosswalk, "get_rxcui_tty", side_effect=lambda r: tty_map[r]), \
             patch.object(crosswalk, "get_rxcui_name", return_value="drugx"):
            resolved = crosswalk.resolve_dispensable_rxcui("drugx")
        self.assertEqual(resolved["rxcui"], "3")  # SCD wins even though ranked last

    def test_tirzepatide_and_dulaglutide_still_do_not_resolve_to_azithromycin(self):
        # Re-verified after the TTY-preference rewrite: the token-overlap
        # check must still gate candidates before TTY preference is ever
        # consulted, so an unrelated drug can't win just by being SBD.
        azithromycin_candidate = {"rxcui": "861417", "name": None, "rank": 4, "score": 750.0}
        for term in ("tirzepatide Single-dose Pen", "dulaglutide Single-dose Pen"):
            with patch.object(crosswalk, "approximate_term", return_value=[azithromycin_candidate]), \
                 patch.object(crosswalk, "get_rxcui_tty", return_value="SBD"), \
                 patch.object(
                     crosswalk, "get_rxcui_name",
                     return_value="azithromycin 1000 MG Powder for Oral Suspension [Zithromax]",
                 ):
                resolved = crosswalk.resolve_dispensable_rxcui(term)
            self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
