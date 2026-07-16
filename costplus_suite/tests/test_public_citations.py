"""
Unit tests for modules.g_public_citations -- the reproducible replacement
for the hand-joined citation columns that used to live only in an
uncommitted leaderboard.csv. Runs against the real, committed
data/public_spreads_matched.csv (no network calls, no fixtures needed: this
IS the source-of-truth file the module reads in production).
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from modules import g_public_citations  # noqa: E402


class TestLoadCitations(unittest.TestCase):
    def test_51_distinct_rxcuis_55_leaderboard_rows_after_join(self):
        # data/public_spreads_matched.csv has 51 distinct RxCUIs (34 from the
        # original public-report citations, +11 from the first litigation
        # pass, +6 from the follow-up strength-disambiguation pass over the
        # previously-unmatched litigation rows -- see the "litigation and
        # 46brooklyn citation extraction" commits) -- one row per RxCUI here.
        # A handful of those RxCUIs (Tadalafil 20mg/(PAH) 20mg, 4 Clobetasol
        # Propionate 0.05% Ointment package variants) map to more than one
        # leaderboard row each, which is why the join in
        # TestRunAgainstRealLeaderboard below produces 55 populated rows
        # from these 51 distinct citations.
        citations = g_public_citations.load_citations()
        self.assertEqual(len(citations), 51)
        self.assertEqual(citations["rxcui"].duplicated().sum(), 0)

    def test_metoprolol_tartrate_25mg_matches_known_value(self):
        citations = g_public_citations.load_citations()
        row = citations[citations["rxcui"] == 866924]
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(row.iloc[0]["best_confirmed_spread"], 10850.00, places=2)
        self.assertIn("Maine MHDO", row.iloc[0]["best_confirmed_source"])
        self.assertIn("p.25", row.iloc[0]["best_confirmed_source"])

    def test_highest_value_wins_when_rxcui_has_multiple_markup_rows(self):
        # RxCUI 1100075 (Abiraterone Acetate 250mg) has 4 markup_pct rows
        # from Maine MHDO manufacturer data (1727.73, 1558.05, 1321.51,
        # 1018.72) plus two litigation-sourced rows added later (6391.86
        # from Lewandowski v. J&J, 2171.74 from Navarro v. Wells Fargo) --
        # the highest of all six must be the one kept.
        citations = g_public_citations.load_citations()
        row = citations[citations["rxcui"] == 1100075]
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(row.iloc[0]["best_confirmed_spread"], 6391.86, places=2)

    def test_source_type_is_one_of_four_known_values(self):
        citations = g_public_citations.load_citations()
        self.assertEqual(
            set(citations["source_type"].unique()),
            {"litigation", "state_disclosure", "peer_reviewed"},
        )

    def test_abiraterone_all_confirmed_sources_shows_both_source_types(self):
        # RxCUI 1100075 (Abiraterone Acetate 250mg): best_confirmed_spread is
        # litigation's 6391.86% (Lewandowski v. J&J), but Maine MHDO's
        # state_disclosure figure (1727.73%) is real, sourced, and must not
        # be hidden just because it lost the max-value selection -- Task 4's
        # whole point is making that selection visible.
        citations = g_public_citations.load_citations()
        row = citations[citations["rxcui"] == 1100075].iloc[0]
        self.assertEqual(row["source_type"], "litigation")
        self.assertIn("6,391.86%", row["all_confirmed_sources"])
        self.assertIn("litigation", row["all_confirmed_sources"])
        self.assertIn("1,727.73%", row["all_confirmed_sources"])
        self.assertIn("state_disclosure", row["all_confirmed_sources"])

    def test_non_markup_type_wins_on_value_but_gets_no_price_formula(self):
        # RxCUI 309362 (Clopidogrel bisulfate) has only spread_dollars (8.59)
        # and spread_pct (70.2) rows -- the higher raw value (spread_pct)
        # wins the max-value selection like any other row, but since it
        # isn't markup_pct, run() must NOT compute a price for it (no
        # defensible nadac-based formula for spread_pct/spread_dollars).
        citations = g_public_citations.load_citations()
        row = citations[citations["rxcui"] == 309362]
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(row.iloc[0]["best_confirmed_spread"], 70.2, places=2)
        self.assertEqual(row.iloc[0]["best_confirmed_type"], "spread_pct")

        leaderboard = pd.DataFrame([
            {"rxcui": 309362, "drug_term": "Clopidogrel 75mg Tablet", "nadac_per_unit": 0.04768},
        ])
        out = g_public_citations.run(leaderboard)
        out_row = out.iloc[0]
        self.assertAlmostEqual(out_row["best_confirmed_spread"], 70.2, places=2)
        self.assertTrue(pd.isna(out_row["estimated_pbm_price_per_unit"]))
        self.assertTrue(pd.isna(out_row["estimated_pbm_price_basis"]))


class TestRun(unittest.TestCase):
    def _leaderboard_fixture(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"rxcui": 866924, "drug_term": "Metoprolol Tartrate 25mg Tablet", "nadac_per_unit": 0.01704},
            {"rxcui": 999999999, "drug_term": "Untracked Drug 1mg Tablet", "nadac_per_unit": 1.0},
        ])

    def test_populates_five_columns_and_computes_estimated_price(self):
        out = g_public_citations.run(self._leaderboard_fixture())
        row = out[out["rxcui"] == 866924].iloc[0]
        self.assertAlmostEqual(row["best_confirmed_spread"], 10850.00, places=2)
        self.assertEqual(row["best_confirmed_type"], "markup_pct")
        self.assertIn("Maine MHDO", row["best_confirmed_source"])
        # estimated_pbm_price_per_unit = nadac_per_unit * (1 + spread/100)
        # = 0.01704 * (1 + 10850/100) = 1.86588
        self.assertAlmostEqual(row["estimated_pbm_price_per_unit"], 1.8659, places=4)
        self.assertEqual(row["estimated_pbm_price_basis"], g_public_citations.ESTIMATED_PBM_PRICE_BASIS)
        self.assertEqual(row["source_type"], "state_disclosure")
        self.assertIn("10,850.00%", row["all_confirmed_sources"])

    def test_unmatched_rxcui_gets_null_citation_columns(self):
        out = g_public_citations.run(self._leaderboard_fixture())
        row = out[out["rxcui"] == 999999999].iloc[0]
        self.assertTrue(pd.isna(row["best_confirmed_spread"]))
        self.assertTrue(pd.isna(row["estimated_pbm_price_per_unit"]))
        self.assertTrue(pd.isna(row["estimated_pbm_price_basis"]))

    def test_string_rxcui_matches_despite_int_dtype_in_citations_file(self):
        # Regression test: the real pipeline's leaderboard carries rxcui as
        # str (crosswalk.py stores RxNav's own string ids), but pandas
        # infers int64 for the plain-digit rxcui column in
        # public_spreads_matched.csv -- merging on "rxcui" directly raised
        # ValueError: "trying to merge on str and int64 columns" the first
        # time this was run against the live (non-CSV-round-tripped)
        # pipeline. Must work regardless of which dtype the caller passes.
        leaderboard = pd.DataFrame([
            {"rxcui": "866924", "drug_term": "Metoprolol Tartrate 25mg Tablet", "nadac_per_unit": 0.01704},
        ])
        out = g_public_citations.run(leaderboard)
        self.assertAlmostEqual(out.iloc[0]["best_confirmed_spread"], 10850.00, places=2)
        self.assertEqual(out.iloc[0]["rxcui"], "866924")  # untouched, still str

    def test_row_count_unchanged_by_join(self):
        # A citations file with multiple rows per RxCUI must never fan out
        # the leaderboard -- load_citations() already dedupes to one row
        # per RxCUI before the merge.
        leaderboard = self._leaderboard_fixture()
        out = g_public_citations.run(leaderboard)
        self.assertEqual(len(out), len(leaderboard))


class TestRunAgainstRealLeaderboard(unittest.TestCase):
    """The exact reproducibility check Task 2 asks for, as a standing test:
    joining the real, committed citations file against the real, committed
    leaderboard.csv must produce exactly 55 populated markup rows (54 of
    which also get an estimated price -- the Clopidogrel spread_pct row
    gets a spread but no price, see above). Was 38/37 before the first
    litigation and 46brooklyn citation extraction pass (-> 49/48), then
    49/48 before the follow-up strength-disambiguation pass over the
    previously-unmatched litigation rows added 6 more confirmed drugs."""

    def test_exactly_55_populated_markup_rows(self):
        leaderboard_path = config.OUTPUT_DIR / "leaderboard.csv"
        if not leaderboard_path.exists():
            self.skipTest("output/leaderboard.csv not present -- run the pipeline first")
        leaderboard = pd.read_csv(leaderboard_path)
        leaderboard = leaderboard.drop(
            columns=["best_confirmed_spread", "best_confirmed_type", "best_confirmed_source",
                     "source_type", "all_confirmed_sources",
                     "estimated_pbm_price_per_unit", "estimated_pbm_price_basis"],
            errors="ignore",
        )
        out = g_public_citations.run(leaderboard)
        self.assertEqual(out["best_confirmed_spread"].notna().sum(), 55)
        self.assertEqual(out["estimated_pbm_price_per_unit"].notna().sum(), 54)


if __name__ == "__main__":
    unittest.main()
