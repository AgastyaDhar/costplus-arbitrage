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
  - _ester_acid_stem()/_SALT_QUALIFIER_WORDS (catalog_gaps.csv audit
    follow-up): the token-overlap check used to reject a real rank-1 SCD
    match for Cost Plus's "Mycophenolate Sodium" because RxNorm's
    canonical name is "mycophenolic acid" -- zero literal shared tokens
    ("mycophenolATE" vs "mycophenolIC"). Salt-qualifier words (sodium,
    sulfate, etc.) are now excluded from the required-token set, and an
    ester/acid stem match ("mycophenol") is tried as a fallback.
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


class TestSaltEsterSynonym(unittest.TestCase):
    def test_mycophenolate_sodium_resolves_via_ester_acid_stem(self):
        # Real data observed live against RxNav: rank-1 candidate for
        # "Mycophenolate Sodium DR 180mg Delayed Release Tablet" IS the
        # correct SCD (485020), but its RxNorm name says "mycophenolic
        # acid" -- the token-overlap check must accept it via the
        # "mycophenol" stem shared between "mycophenolate" and
        # "mycophenolic", not reject it for having zero literal overlap.
        candidates = [
            {"rxcui": "485020", "name": "MYCOPHENOLATE SODIUM 180 mg ORAL TABLET, DELAYED RELEASE", "rank": 1, "score": 17.29},
        ]
        with patch.object(crosswalk, "approximate_term", return_value=candidates), \
             patch.object(crosswalk, "get_rxcui_tty", return_value="SCD"), \
             patch.object(
                 crosswalk, "get_rxcui_name",
                 return_value="mycophenolic acid 180 MG Delayed Release Oral Tablet",
             ):
            resolved = crosswalk.resolve_dispensable_rxcui("Mycophenolate Sodium DR 180mg Delayed Release Tablet")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["rxcui"], "485020")
        self.assertEqual(resolved["tty"], "SCD")

    def test_bare_salt_word_alone_cannot_drive_a_match(self):
        # A query token that IS a salt-qualifier word (here, the whole
        # query reduces to one) must never overlap-match a candidate
        # just because the word "sodium" appears in both -- salt words
        # are excluded from the required-token set entirely, matching the
        # existing false-positive guard for dosage-form-only queries.
        query_tokens = crosswalk._extract_ingredient_tokens("Sodium 10mg Tablet")
        self.assertEqual(query_tokens, [])
        self.assertFalse(crosswalk._has_token_overlap(query_tokens, "some unrelated sodium drug"))

    def test_ester_acid_stem_ignores_short_coincidental_suffixes(self):
        # "rate" ending in "ate" must not stem down to the near-useless
        # single letter "r" -- the >=4-char stem-length floor exists
        # precisely to keep this kind of match from ever firing.
        self.assertIsNone(crosswalk._ester_acid_stem("rate"))

    def test_ester_acid_stem_real_pair(self):
        self.assertEqual(crosswalk._ester_acid_stem("mycophenolate"), "mycophenol")
        self.assertEqual(crosswalk._ester_acid_stem("mycophenolic"), "mycophenol")

    def test_tirzepatide_and_dulaglutide_still_do_not_resolve_to_azithromycin(self):
        # Re-verified once more after the salt/ester stem addition: neither
        # word ends in "-ate" or "-ic" ("tirzepatIDE", "dulaglutIDE"), so
        # the new stem fallback never activates for them, and the
        # unrelated azithromycin candidate is still correctly rejected.
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
