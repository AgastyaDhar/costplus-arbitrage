"""
Unit tests for shared.crosswalk's redundant-release-wording dedup fallback
(Task 3's authorized name-format fix). Found during triage of the real
full-catalog scrape: Cost Plus's own drug/form fields are sometimes
independently verbose about release timing (e.g. drug="Lithium Carbonate
Extended Release (ER)", form="Extended Release Tablet"), producing a query
with "Extended Release" repeated, which degrades RxNav's fuzzy match to a
dose-form-level concept instead of the dispensable one.

Critical invariant tested here: the fallback must NEVER be tried for a term
that already resolves successfully, so it can only rescue new matches, never
change an existing one (verified directly against real RxNav data during
triage: blindly deduping an already-resolving term could silently swap in a
different RxCUI for at least one real drug).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import crosswalk  # noqa: E402


class TestDedupeRedundantReleaseWording(unittest.TestCase):
    def test_collapses_duplicate_extended_release_phrase(self):
        term = "Lithium Carbonate Extended Release (ER) 300mg Extended Release Tablet"
        cleaned = crosswalk._dedupe_redundant_release_wording(term)
        self.assertEqual(cleaned.lower().count("extended release"), 1)
        self.assertNotIn("(ER)", cleaned)

    def test_leaves_single_occurrence_untouched(self):
        term = "Metformin Extended Release 500mg Tablet"
        self.assertEqual(crosswalk._dedupe_redundant_release_wording(term), term)

    def test_leaves_terms_without_release_wording_untouched(self):
        term = "Atorvastatin 20mg Tablet"
        self.assertEqual(crosswalk._dedupe_redundant_release_wording(term), term)

    def test_removes_abbreviation_only_when_expansion_present(self):
        # "(DR)" alone with no spelled-out "Delayed Release" elsewhere must be left alone --
        # removing it wouldn't be de-duplication, it would be deleting real information.
        term = "SomeDrug (DR) 50mg Capsule"
        self.assertEqual(crosswalk._dedupe_redundant_release_wording(term), term)


class TestCrosswalkDrugDedupFallback(unittest.TestCase):
    def _nadac_df(self):
        return pd.DataFrame(
            [{"NDC": "11111111111", "nadac_per_unit": 0.05, "pricing_unit": "EA", "ndc_description": "TEST"}]
        ).set_index("NDC")

    def test_fallback_rescues_a_term_the_raw_query_cannot_resolve(self):
        term = "Lithium Carbonate Extended Release (ER) 300mg Extended Release Tablet"

        def fake_resolve(t):
            if t == term:
                return None  # the raw, redundant term fails, as observed live
            return {"rxcui": "197891", "resolved_name": "lithium carbonate 300 MG Extended Release Oral Tablet", "tty": "SCD"}

        with patch.object(crosswalk, "resolve_dispensable_rxcui", side_effect=fake_resolve), \
             patch.object(crosswalk, "get_ndcs_for_rxcui", return_value=["11111111111"]), \
             patch.object(crosswalk, "get_ingredient_name", return_value="lithium carbonate"):
            result = crosswalk.crosswalk_drug(term, self._nadac_df())

        self.assertTrue(result.matched)
        self.assertEqual(result.rxcui, "197891")
        self.assertIn("name-normalization fallback", result.note)

    def test_brand_strip_fallback_rescues_an_ftc_style_term(self):
        term = "Imatinib (Gleevec) Pill"

        def fake_resolve(t):
            if t == term:
                return None  # raw term with the brand name in it fails/resolves wrong, as observed live
            if t == "Imatinib":  # release-dedup is a no-op here, so brand-strip sees the raw term
                return {"rxcui": "403878", "resolved_name": "imatinib 100 MG Oral Tablet", "tty": "SCD"}
            return None

        with patch.object(crosswalk, "resolve_dispensable_rxcui", side_effect=fake_resolve), \
             patch.object(crosswalk, "get_ndcs_for_rxcui", return_value=["11111111111"]), \
             patch.object(crosswalk, "get_ingredient_name", return_value="imatinib"):
            result = crosswalk.crosswalk_drug(term, self._nadac_df())

        self.assertTrue(result.matched)
        self.assertEqual(result.rxcui, "403878")
        self.assertIn("brand-strip fallback", result.note)

    def test_fallback_never_invoked_when_raw_term_already_resolves(self):
        """The critical safety property: if the raw term already succeeds,
        the deduped variant must never even be tried, so an already-correct
        match can never be silently swapped for a different one."""
        term = "Clonidine Extended Release (ER) 0.1mg Extended Release Tablet"
        calls = []

        def fake_resolve(t):
            calls.append(t)
            return {"rxcui": "ORIGINAL_RXCUI", "resolved_name": "whatever resolved first", "tty": "SCD"}

        with patch.object(crosswalk, "resolve_dispensable_rxcui", side_effect=fake_resolve), \
             patch.object(crosswalk, "get_ndcs_for_rxcui", return_value=["11111111111"]), \
             patch.object(crosswalk, "get_ingredient_name", return_value="clonidine"):
            result = crosswalk.crosswalk_drug(term, self._nadac_df())

        self.assertEqual(calls, [term])  # resolve_dispensable_rxcui called exactly once, with the RAW term
        self.assertEqual(result.rxcui, "ORIGINAL_RXCUI")
        self.assertNotIn("fallback", result.note)

    def test_no_op_when_dedup_and_brand_strip_both_produce_no_change(self):
        """A term with no redundant release wording, no parenthetical, and
        no trailing low-signal dose-form word must not trigger any fallback
        RxNav call at all when the first one fails -- "Capsule" is
        deliberately not in _LOW_SIGNAL_TRAILING_WORDS, unlike "Tablet"."""
        term = "Nonexistent Drug 5mg Capsule"
        calls = []

        def fake_resolve(t):
            calls.append(t)
            return None

        with patch.object(crosswalk, "resolve_dispensable_rxcui", side_effect=fake_resolve):
            result = crosswalk.crosswalk_drug(term, self._nadac_df())

        self.assertEqual(calls, [term])  # no fallback attempt -- both no-op on this term
        self.assertFalse(result.matched)

    def test_brand_strip_fallback_tried_when_dedup_is_a_no_op(self):
        """A term ending in a low-signal word ("Tablet") with no redundant
        release wording must skip straight from the raw term to the
        brand-strip fallback -- the release-dedup stage is a no-op here
        (nothing to dedupe) but must not block the brand-strip stage from
        still being tried."""
        term = "Nonexistent Drug 5mg Tablet"
        calls = []

        def fake_resolve(t):
            calls.append(t)
            return None

        with patch.object(crosswalk, "resolve_dispensable_rxcui", side_effect=fake_resolve):
            result = crosswalk.crosswalk_drug(term, self._nadac_df())

        self.assertEqual(calls, [term, "Nonexistent Drug 5mg"])
        self.assertFalse(result.matched)


if __name__ == "__main__":
    unittest.main()
