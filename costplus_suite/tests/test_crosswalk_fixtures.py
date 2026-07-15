"""
Regression tests for shared.crosswalk.crosswalk_drug's per-unit aggregation
logic, pinned to the 3 drugs hand-verified against raw NADAC rows during the
Phase 0 gate (see conversation record / METHODOLOGY.md):

  - atorvastatin 20 mg tablet: NDCs 00093505910 / 00378395105 both showed
    NADAC Per Unit 0.02851, Pricing Unit EA in the raw file.
  - metformin 500 mg tablet: all 61 RxCUI-861007-matched NDCs were
    "METFORMIN HCL 500 MG TABLET" (immediate-release only, ER correctly
    excluded), median 0.01398 EA.
  - warfarin sodium 5 mg tablet: 13 matched NDCs, uniform 0.0876 EA.

RxNav network calls (resolve_dispensable_rxcui, get_ndcs_for_rxcui) are
mocked to their known-correct fixture values so this test is offline and
deterministic -- it is testing crosswalk_drug's aggregation math (median,
pricing-unit-consistency check), not RxNav's live API.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import crosswalk  # noqa: E402


def _make_nadac_df(rows: dict) -> pd.DataFrame:
    """rows: {ndc: (nadac_per_unit, pricing_unit, description)}"""
    df = pd.DataFrame(
        [
            {"NDC": ndc, "nadac_per_unit": v[0], "pricing_unit": v[1], "ndc_description": v[2]}
            for ndc, v in rows.items()
        ]
    ).set_index("NDC")
    return df


class TestCrosswalkFixtures(unittest.TestCase):
    def test_atorvastatin_20mg_tablet(self):
        nadac_df = _make_nadac_df(
            {
                "00093505910": (0.02851, "EA", "ATORVASTATIN 20 MG TABLET"),
                "00378395105": (0.02851, "EA", "ATORVASTATIN 20 MG TABLET"),
            }
        )
        with patch.object(
            crosswalk, "resolve_dispensable_rxcui",
            return_value={"rxcui": "617310", "resolved_name": "atorvastatin 20 MG Oral Tablet", "tty": "SCD"},
        ), patch.object(
            crosswalk, "get_ndcs_for_rxcui", return_value=["00093505910", "00378395105"]
        ), patch.object(
            crosswalk, "get_ingredient_name", return_value="atorvastatin"
        ):
            result = crosswalk.crosswalk_drug("atorvastatin 20 mg tablet", nadac_df)

        self.assertTrue(result.matched)
        self.assertEqual(result.rxcui, "617310")
        self.assertAlmostEqual(result.nadac_per_unit, 0.02851, places=5)
        self.assertEqual(result.pricing_unit, "EA")
        self.assertTrue(result.pricing_unit_consistent)
        self.assertEqual(result.matched_ndc_count, 2)

    def test_metformin_500mg_tablet_median_and_ir_er_exclusion(self):
        # Simulates the real finding: all RxCUI-861007 NDCs are IR tablets at
        # 0.01397/0.01398; the ER outlier (0.28979, a different NDC/RxCUI)
        # must never enter this set, matching what get_ndcs_for_rxcui('861007')
        # actually returned when hand-verified.
        nadac_df = _make_nadac_df(
            {
                "71093013206": (0.01397, "EA", "METFORMIN HCL 500 MG TABLET"),
                "49483062281": (0.01398, "EA", "METFORMIN HCL 500 MG TABLET"),
            }
        )
        with patch.object(
            crosswalk, "resolve_dispensable_rxcui",
            return_value={"rxcui": "861007", "resolved_name": "metformin hydrochloride 500 MG Oral Tablet", "tty": "SCD"},
        ), patch.object(
            crosswalk, "get_ndcs_for_rxcui", return_value=["71093013206", "49483062281"]
        ), patch.object(
            crosswalk, "get_ingredient_name", return_value="metformin"
        ):
            result = crosswalk.crosswalk_drug("metformin 500 mg tablet", nadac_df)

        self.assertTrue(result.matched)
        self.assertAlmostEqual(result.nadac_per_unit, 0.01398, places=5)  # median of the two
        self.assertEqual(result.pricing_unit, "EA")

    def test_warfarin_5mg_tablet(self):
        nadac_df = _make_nadac_df({"76282033201": (0.0876, "EA", "WARFARIN SODIUM 5 MG TABLET")})
        with patch.object(
            crosswalk, "resolve_dispensable_rxcui",
            return_value={"rxcui": "855332", "resolved_name": "warfarin sodium 5 MG Oral Tablet", "tty": "SCD"},
        ), patch.object(
            crosswalk, "get_ndcs_for_rxcui", return_value=["76282033201"]
        ), patch.object(
            crosswalk, "get_ingredient_name", return_value="warfarin"
        ):
            result = crosswalk.crosswalk_drug("warfarin sodium 5 mg tablet", nadac_df)

        self.assertTrue(result.matched)
        self.assertAlmostEqual(result.nadac_per_unit, 0.0876, places=4)
        self.assertEqual(result.pricing_unit, "EA")

    def test_mixed_pricing_units_flagged_not_silently_averaged(self):
        # HARD CONSTRAINT check: if a drug's matched NDCs ever disagree on
        # Pricing Unit, that must be surfaced, never silently blended.
        nadac_df = _make_nadac_df(
            {
                "11111111111": (0.05, "EA", "FAKE DRUG TABLET"),
                "22222222222": (0.05, "ML", "FAKE DRUG SOLUTION"),
            }
        )
        with patch.object(
            crosswalk, "resolve_dispensable_rxcui",
            return_value={"rxcui": "999999", "resolved_name": "fake drug", "tty": "SCD"},
        ), patch.object(
            crosswalk, "get_ndcs_for_rxcui", return_value=["11111111111", "22222222222"]
        ), patch.object(
            crosswalk, "get_ingredient_name", return_value="fake drug"
        ):
            result = crosswalk.crosswalk_drug("fake drug", nadac_df)

        self.assertFalse(result.pricing_unit_consistent)
        self.assertIn("mixed pricing units", result.note)

    def test_no_dispensable_rxcui_found(self):
        with patch.object(crosswalk, "resolve_dispensable_rxcui", return_value=None):
            result = crosswalk.crosswalk_drug("nonsense drug xyz", pd.DataFrame())
        self.assertFalse(result.matched)
        self.assertIn("no dispensable", result.note)

    def test_get_ingredient_name_combo_returns_all_components_not_just_first(self):
        # Regression test for the real bug: RxNav relates a combo RxCUI to
        # MULTIPLE IN concepts. The old code took props[0] only, so
        # "Amlodipine Besylate-Atorvastatin Calcium" silently resolved to
        # bare "amlodipine" and collided with plain amlodipine tablets under
        # the same Part D molecule join key -- contributing 33 strength/combo
        # rows to a single "Amlodipine" national figure.
        related_json = {
            "relatedGroup": {
                "conceptGroup": [
                    {
                        "tty": "IN",
                        "conceptProperties": [
                            {"rxcui": "17767", "name": "atorvastatin"},
                            {"rxcui": "17767", "name": "atorvastatin"},  # duplicate, must be deduped
                            {"rxcui": "17767", "name": "amlodipine"},
                        ],
                    }
                ]
            }
        }
        with patch.object(crosswalk, "cached_get_json", return_value=related_json):
            combo_name = crosswalk.get_ingredient_name("999999")
        self.assertEqual(combo_name, "amlodipine/atorvastatin")

    def test_amlodipine_atorvastatin_combo_does_not_collide_with_plain_amlodipine(self):
        # The actual regression this fix is for: the combo's normalized
        # molecule key must differ from plain amlodipine's, so Part D
        # aggregation never merges the combo's utilization into the single-
        # ingredient molecule's bucket.
        combo_key = crosswalk.normalize_drug_name("amlodipine/atorvastatin")
        plain_key = crosswalk.normalize_drug_name("amlodipine")
        self.assertNotEqual(combo_key, plain_key)

    def test_tirzepatide_and_dulaglutide_do_not_resolve_to_azithromycin(self):
        # Regression test for a real false match: RxNav's approximateTerm
        # ranks an unrelated dispensable drug (azithromycin, rxcui 861417)
        # above any true tirzepatide/dulaglutide dispensable candidate for
        # queries dominated by generic dosage-form words like "single-dose
        # pen". Without the token-overlap relevance check in
        # resolve_dispensable_rxcui(), the old first-dispensable-wins logic
        # accepted that unrelated candidate, silently matching Mounjaro and
        # Trulicity to an antibiotic.
        azithromycin_candidate = {
            "rxcui": "861417", "name": None, "rank": 4, "score": 750.0,
        }
        for term in ("tirzepatide Single-dose Pen", "dulaglutide Single-dose Pen"):
            with patch.object(
                crosswalk, "approximate_term", return_value=[azithromycin_candidate]
            ), patch.object(
                crosswalk, "get_rxcui_tty", return_value="SBD"
            ), patch.object(
                crosswalk, "get_rxcui_name",
                return_value="azithromycin 1000 MG Powder for Oral Suspension [Zithromax]",
            ):
                resolved = crosswalk.resolve_dispensable_rxcui(term)
            self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
