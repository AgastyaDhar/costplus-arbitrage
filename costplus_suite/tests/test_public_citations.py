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
    def test_52_distinct_rxcuis_56_leaderboard_rows_after_join(self):
        # data/public_spreads_matched.csv has 52 distinct RxCUIs (34 from the
        # original public-report citations, +11 from the first litigation
        # pass, +6 from the litigation strength-disambiguation pass, +1
        # (Teriparatide) from the FTC name-format crosswalk fix -- see the
        # "litigation and 46brooklyn citation extraction" commits and the
        # FTC crosswalk fix commit) -- one row per RxCUI here. A handful of
        # those RxCUIs (Tadalafil 20mg/(PAH) 20mg, 4 Clobetasol Propionate
        # 0.05% Ointment package variants) map to more than one leaderboard
        # row each, which is why the join in TestRunAgainstRealLeaderboard
        # below produces 56 populated rows from these 52 distinct citations.
        citations = g_public_citations.load_citations()
        self.assertEqual(len(citations), 52)
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
            {"litigation", "state_disclosure", "peer_reviewed", "federal_study"},
        )

    def test_teriparatide_is_the_first_federal_study_citation(self):
        # RxCUI 1435115 (Teriparatide 560 mcg/2.24ml pen-injector): the FTC
        # name-format crosswalk fix's one genuinely new drug (sole
        # leaderboard candidate for the ingredient -- Dalfampridine, the
        # other FTC drug the fix rescued, was already matched via
        # litigation, so it's an additional citation, not a new RxCUI).
        # Confirms federal_study data is actually flowing through the
        # pipeline after being stuck at 0 rows since the source-type
        # column was introduced.
        citations = g_public_citations.load_citations()
        row = citations[citations["rxcui"] == 1435115]
        self.assertEqual(len(row), 1)
        self.assertEqual(row.iloc[0]["source_type"], "federal_study")
        self.assertIn("FTC Second Interim Staff Report", row.iloc[0]["best_confirmed_source"])

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

    def test_blank_confirmed_spread_value_stays_blank_not_zero(self):
        # "Never default a missing/non-applicable markup_pct to 0.0 --
        # missing means blank." Simulates a source that yields no
        # extractable percentage at all (confirmed_spread_value left empty
        # in the CSV, as opposed to a real 0.0 -- see the paired test below
        # for that case). Must surface as NaN throughout the pipeline, not
        # get coerced to 0.0 anywhere, and must never render as the literal
        # string "nan" in a human-facing column either.
        import tempfile

        csv_text = (
            "rxcui,drug_name,nadac_per_unit,costplus_per_unit,confirmed_spread_value,"
            "confirmed_spread_type,source_name,source_year,source_page,source_quote,source_type\n"
            "9999999,Synthetic Test Drug,10.0,5.0,,markup_pct,Synthetic Source,2025,1,"
            "no percentage extractable from this source,federal_study\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as tmp:
            tmp.write(csv_text)
            tmp_path = Path(tmp.name)
        try:
            citations = g_public_citations.load_citations(tmp_path)
            self.assertTrue(pd.isna(citations.iloc[0]["best_confirmed_spread"]))
            self.assertNotIn("nan", citations.iloc[0]["all_confirmed_sources"].lower())

            leaderboard = pd.DataFrame([
                {"rxcui": 9999999, "drug_term": "Synthetic Test Drug", "nadac_per_unit": 10.0},
            ])
            out = g_public_citations.run(leaderboard, citations_path=tmp_path)
            out_row = out.iloc[0]
            self.assertTrue(pd.isna(out_row["best_confirmed_spread"]))
            self.assertTrue(pd.isna(out_row["estimated_pbm_price_per_unit"]))
        finally:
            tmp_path.unlink()

    def test_teriparatide_zero_is_genuine_extracted_value_not_a_default(self):
        # The paired positive case for the test above: RxCUI 1435115
        # (Teriparatide)'s best row is a REAL 0.0 -- FTC Second Interim
        # Staff Report, Figure A1 p.36, Medicare Part D affiliated-pharmacy
        # markup over NADAC -- not a placeholder. A genuine 0.0 must
        # survive exactly as 0.0 (not get treated as falsy/missing and
        # dropped to NaN), distinguishing it from the blank case above by
        # carrying a real, well-formed source citation alongside it.
        citations = g_public_citations.load_citations()
        row = citations[citations["rxcui"] == 1435115].iloc[0]
        self.assertEqual(row["best_confirmed_spread"], 0.0)
        self.assertFalse(pd.isna(row["best_confirmed_spread"]))
        self.assertEqual(row["best_confirmed_type"], "markup_pct")
        self.assertIn("FTC Second Interim Staff Report", row["best_confirmed_source"])
        self.assertIn("p.36", row["best_confirmed_source"])

        leaderboard = pd.DataFrame([
            {"rxcui": 1435115, "drug_term": "Teriparatide 560 mcg/2.24ml Solution Pen-injector",
             "nadac_per_unit": 536.54901},
        ])
        out = g_public_citations.run(leaderboard)
        out_row = out.iloc[0]
        # 0% markup -> estimated price legitimately equals NADAC exactly.
        # This is correct arithmetic on real data, not a bug to suppress.
        self.assertAlmostEqual(out_row["estimated_pbm_price_per_unit"], 536.54901, places=4)

    def test_fingolimod_citation_note_surfaces_in_leaderboard_source(self):
        # RxCUI 1012895 (Fingolimod HCl 0.5mg): the winning citation
        # (Lewandowski, p.44, 1,395.60%) has a documented discrepancy with
        # that same complaint's own p.38 narrative (1,420.7%, identical
        # dollar figures). We cite p.44 as printed, but the discrepancy
        # must be visible on the leaderboard row itself, not just in
        # METHODOLOGY.md.
        citations = g_public_citations.load_citations()
        row = citations[citations["rxcui"] == 1012895].iloc[0]
        self.assertAlmostEqual(row["best_confirmed_spread"], 1395.6, places=2)
        self.assertIn("NOTE", row["best_confirmed_source"])
        self.assertIn("1,420.7%", row["best_confirmed_source"])

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
    leaderboard.csv must produce exactly 56 populated markup rows (55 of
    which also get an estimated price -- the Clopidogrel spread_pct row
    gets a spread but no price, see above). Was 38/37 before the first
    litigation and 46brooklyn citation extraction pass (-> 49/48), 49/48
    before the litigation strength-disambiguation pass (-> 55/54), then
    55/54 before the FTC name-format crosswalk fix added Teriparatide
    (federal_study's first-ever confirmed row)."""

    def test_exactly_56_populated_markup_rows(self):
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
        self.assertEqual(out["best_confirmed_spread"].notna().sum(), 56)
        self.assertEqual(out["estimated_pbm_price_per_unit"].notna().sum(), 55)


if __name__ == "__main__":
    unittest.main()
