"""
Unit tests for modules.a_arbitrage's gap/overpayment math, using small
synthetic DataFrames with hand-computed expected values. No network calls --
attach_partd/attach_sdud/attach_nadac_gap/build_leaderboard are pure
functions over DataFrames already in memory.
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules import a_arbitrage  # noqa: E402


def _drug_level_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "drug_term": "testdrug 10 mg tablet",
                "drug": "testdrug",
                "strength": "10 mg",
                "form": "tablet",
                "rxcui": "123456",
                "ingredient_name": "testdrug",
                "ingredient_norm": "TESTDRUG",
                "tty": "SCD",
                "matched_ndcs": ["11111111111"],
                "ndc_count": 1,
                "matched_ndc_count": 1,
                "nadac_per_unit": 0.05,
                "nadac_pricing_unit": "EA",
                "crosswalk_matched": True,
                "crosswalk_note": "",
                "costplus_per_unit": 0.10,
                "pharmacy_fee": 5.0,
                "shipping_fee": 5.0,
                "package_quantity": 90,
            },
            {
                "drug_term": "unmatched drug 5 mg tablet",
                "drug": "unmatched drug",
                "strength": "5 mg",
                "form": "tablet",
                "rxcui": None,
                "ingredient_name": None,
                "ingredient_norm": "",
                "tty": None,
                "matched_ndcs": [],
                "ndc_count": 0,
                "matched_ndc_count": 0,
                "nadac_per_unit": None,
                "nadac_pricing_unit": None,
                "crosswalk_matched": False,
                "crosswalk_note": "no dispensable RxCUI found",
                "costplus_per_unit": 0.20,
                "pharmacy_fee": 5.0,
                "shipping_fee": 5.0,
                "package_quantity": 30,
            },
        ]
    )


def _partd_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Brnd_Name": "Testdrug", "Gnrc_Name": "Testdrug", "Tot_Mftr": 1, "Mftr_Name": "Overall",
                "Tot_Spndng": 2000.0, "Tot_Dsg_Unts": 10000.0, "Tot_Clms": 500,
            },
            # A per-manufacturer breakdown row that must NOT be double-counted
            # (attach_partd should only use Mftr_Name == 'Overall' rows).
            {
                "Brnd_Name": "Testdrug", "Gnrc_Name": "Testdrug", "Tot_Mftr": 1, "Mftr_Name": "Acme Pharma",
                "Tot_Spndng": 2000.0, "Tot_Dsg_Unts": 10000.0, "Tot_Clms": 500,
            },
        ]
    )


def _sdud_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # national rollup row -- the one attach_sdud should use
            {"ndc": "11111111111", "state": "XX", "product_name": "TESTDRUG", "units_reimbursed": 500.0, "medicaid_amount_reimbursed": 100.0},
            # a real state row that sums into XX -- must NOT be double-counted
            {"ndc": "11111111111", "state": "CA", "product_name": "TESTDRUG", "units_reimbursed": 300.0, "medicaid_amount_reimbursed": 60.0},
        ]
    )


def _drug_level_with_brand_fixture() -> pd.DataFrame:
    """Same as _drug_level_fixture's matched generic row, plus a confirmed-
    brand row (RxNorm TTY=SBD) that DOES crosswalk successfully -- the exact
    shape that let Eliquis (apixaban, TTY=SBD) leak $480M+ into a nominally
    generics-only Medicaid total during real-catalog testing, because
    attach_sdud has no brand/generic awareness of its own."""
    generic_row = _drug_level_fixture().iloc[[0]].copy()  # the matched "testdrug" row
    brand_row = generic_row.copy()
    brand_row["drug_term"] = "brandname 10 mg tablet"
    brand_row["drug"] = "brandname"
    brand_row["tty"] = "SBD"
    brand_row["matched_ndcs"] = [["22222222222"]]
    brand_row["costplus_per_unit"] = 5.75
    return pd.concat([generic_row, brand_row], ignore_index=True)


class TestExcludeBrandRows(unittest.TestCase):
    def test_generics_only_true_strips_brand_ndcs_and_flags_unmatched(self):
        out = a_arbitrage._exclude_brand_rows(_drug_level_with_brand_fixture(), generics_only=True)
        brand_row = out[out["tty"] == "SBD"].iloc[0]
        self.assertEqual(brand_row["matched_ndcs"], [])
        self.assertFalse(brand_row["crosswalk_matched"])
        generic_row = out[out["tty"] == "SCD"].iloc[0]
        self.assertTrue(generic_row["crosswalk_matched"])
        self.assertEqual(generic_row["matched_ndcs"], ["11111111111"])

    def test_generics_only_false_leaves_brand_rows_untouched(self):
        fixture = _drug_level_with_brand_fixture()
        out = a_arbitrage._exclude_brand_rows(fixture, generics_only=False)
        pd.testing.assert_frame_equal(out, fixture)

    def test_brand_drug_excluded_from_medicaid_total_end_to_end(self):
        """Regression test for the real bug: a brand drug's Medicaid gap must
        contribute $0 to a generics_only leaderboard, not its real (often
        huge) gap. Runs the same attach_sdud + build_leaderboard pipeline
        modules.a_arbitrage.run() uses, just without the network calls."""
        drug_level = _exclude_and_price(_drug_level_with_brand_fixture(), generics_only=True)
        leaderboard = a_arbitrage.build_leaderboard(drug_level)
        self.assertNotIn("brandname 10 mg tablet", leaderboard["drug_term"].values)
        self.assertIn("testdrug 10 mg tablet", leaderboard["drug_term"].values)

    def test_brand_drug_included_when_generics_only_false(self):
        drug_level = _exclude_and_price(_drug_level_with_brand_fixture(), generics_only=False)
        leaderboard = a_arbitrage.build_leaderboard(drug_level)
        self.assertIn("brandname 10 mg tablet", leaderboard["drug_term"].values)


def _exclude_and_price(drug_level: pd.DataFrame, generics_only: bool) -> pd.DataFrame:
    filtered = a_arbitrage._exclude_brand_rows(drug_level, generics_only)
    sdud = pd.concat(
        [
            _sdud_fixture(),
            pd.DataFrame(
                [{"ndc": "22222222222", "state": "XX", "product_name": "BRANDNAME",
                  "units_reimbursed": 100.0, "medicaid_amount_reimbursed": 1000.0}]
            ),
        ],
        ignore_index=True,
    )
    priced = a_arbitrage.attach_partd(filtered, _partd_fixture(), generics_only=generics_only)
    priced = a_arbitrage.attach_sdud(priced, sdud)
    priced = a_arbitrage.attach_nadac_gap(priced)
    return priced


class TestAttachPartd(unittest.TestCase):
    def test_gap_and_overpayment(self):
        drug_level = _drug_level_fixture()
        priced = a_arbitrage.attach_partd(drug_level, _partd_fixture(), generics_only=True)
        row = priced.iloc[0]
        # partd_per_unit = 2000 / 10000 = 0.20 (Overall row only, not doubled by the per-mftr row)
        self.assertAlmostEqual(row["partd_per_unit"], 0.20, places=6)
        self.assertAlmostEqual(row["Tot_Dsg_Unts"], 10000.0, places=6)
        # gap_partd = 0.20 - 0.10 = 0.10 ; overpayment_partd = 0.10 * 10000 = 1000
        self.assertAlmostEqual(row["gap_partd"], 0.10, places=6)
        self.assertAlmostEqual(row["overpayment_partd"], 1000.0, places=4)

    def test_unmatched_drug_has_no_partd_gap(self):
        priced = a_arbitrage.attach_partd(_drug_level_fixture(), _partd_fixture(), generics_only=True)
        row = priced.iloc[1]
        self.assertTrue(pd.isna(row["partd_per_unit"]))


def _multi_strength_drug_level_fixture() -> pd.DataFrame:
    """Two strengths of the same molecule (shared ingredient_norm), the
    real-world shape that caused the strength-duplication bug: Part D only
    publishes one national Tot_Dsg_Unts per molecule, but both strengths
    used to get the full national figure multiplied onto them independently."""
    base = _drug_level_fixture().iloc[[0]].copy()  # the matched "testdrug" row, costplus_per_unit=0.10
    cheaper = base.copy()
    cheaper["drug_term"] = "testdrug 5 mg tablet"
    cheaper["strength"] = "5 mg"
    cheaper["matched_ndcs"] = [["22222222222"]]
    cheaper["costplus_per_unit"] = 0.05
    pricier = base.copy()
    pricier["drug_term"] = "testdrug 20 mg tablet"
    pricier["strength"] = "20 mg"
    pricier["matched_ndcs"] = [["33333333333"]]
    pricier["costplus_per_unit"] = 0.08  # the higher-priced strength -- must become the sole carrier
    return pd.concat([cheaper, pricier], ignore_index=True)


class TestAttachPartdMoleculeDedup(unittest.TestCase):
    def test_only_highest_price_strength_carries_the_dollar_figure(self):
        priced = a_arbitrage.attach_partd(_multi_strength_drug_level_fixture(), _partd_fixture(), generics_only=True)
        cheaper_row = priced[priced["drug_term"] == "testdrug 5 mg tablet"].iloc[0]
        pricier_row = priced[priced["drug_term"] == "testdrug 20 mg tablet"].iloc[0]

        self.assertFalse(cheaper_row["is_partd_molecule_row"])
        self.assertTrue(pricier_row["is_partd_molecule_row"])
        self.assertEqual(cheaper_row["overpayment_partd"], 0.0)
        # partd_per_unit = 0.20 (both rows share the same molecule-wide figure);
        # representative price = max(0.05, 0.08) = 0.08 -- the conservative
        # (highest-price, smallest-gap) strength, not the cheaper one.
        self.assertAlmostEqual(pricier_row["costplus_per_unit_partd_molecule"], 0.08, places=6)
        self.assertAlmostEqual(pricier_row["overpayment_partd"], (0.20 - 0.08) * 10000.0, places=4)
        self.assertEqual(pricier_row["partd_molecule_n_strengths"], 2)
        self.assertEqual(cheaper_row["partd_molecule_n_strengths"], 2)

    def test_per_strength_gap_partd_stays_real_and_distinct(self):
        # gap_partd (the per-unit, per-strength gap) must NOT be flattened to
        # the molecule-wide value -- it's informational and legitimate at any
        # granularity even though the dollar figure is molecule-level.
        priced = a_arbitrage.attach_partd(_multi_strength_drug_level_fixture(), _partd_fixture(), generics_only=True)
        cheaper_row = priced[priced["drug_term"] == "testdrug 5 mg tablet"].iloc[0]
        pricier_row = priced[priced["drug_term"] == "testdrug 20 mg tablet"].iloc[0]
        self.assertAlmostEqual(cheaper_row["gap_partd"], 0.20 - 0.05, places=6)
        self.assertAlmostEqual(pricier_row["gap_partd"], 0.20 - 0.08, places=6)

    def test_leaderboard_total_not_multiplied_by_strength_count(self):
        # Regression test for the real bug: summing overpayment_partd across
        # every strength row of a multi-strength molecule must equal the
        # SINGLE molecule figure ((0.20-0.08)*10000 = 1200), not that figure
        # doubled by naively multiplying the shared national Tot_Dsg_Unts by
        # each strength's own gap and summing both rows (which would have
        # given (0.20-0.05)*10000 + (0.20-0.08)*10000 = 1500 + 1200 = 2700).
        priced = a_arbitrage.attach_partd(_multi_strength_drug_level_fixture(), _partd_fixture(), generics_only=True)
        priced = a_arbitrage.attach_sdud(priced, pd.DataFrame(columns=["ndc", "state", "product_name", "units_reimbursed", "medicaid_amount_reimbursed"]))
        priced = a_arbitrage.attach_nadac_gap(priced)
        leaderboard = a_arbitrage.build_leaderboard(priced)

        self.assertEqual(len(leaderboard), 2)  # both strengths still visible
        self.assertAlmostEqual(leaderboard["overpayment_partd"].sum(), 1200.0, places=4)


class TestBuildPartdMoleculeTable(unittest.TestCase):
    def test_one_row_per_molecule_using_the_representative_strength(self):
        priced = a_arbitrage.attach_partd(_multi_strength_drug_level_fixture(), _partd_fixture(), generics_only=True)
        table = a_arbitrage.build_partd_molecule_table(priced)
        self.assertEqual(len(table), 1)  # one molecule, not one row per strength
        row = table.iloc[0]
        self.assertEqual(row["representative_drug_term"], "testdrug 20 mg tablet")  # the $0.08 strength
        self.assertAlmostEqual(row["overpayment_partd"], 1200.0, places=4)
        self.assertEqual(row["partd_molecule_n_strengths"], 2)


class TestAttachSdud(unittest.TestCase):
    def test_uses_national_xx_row_not_sum_of_states(self):
        drug_level = _drug_level_fixture()
        priced = a_arbitrage.attach_sdud(drug_level, _sdud_fixture())
        row = priced.iloc[0]
        # Must equal the XX row (500 units / $100), NOT XX + CA (800 units / $160)
        self.assertAlmostEqual(row["medicaid_units"], 500.0, places=4)
        self.assertAlmostEqual(row["medicaid_amount"], 100.0, places=4)
        # medicaid_per_unit = 100 / 500 = 0.20 ; gap = 0.20 - 0.10 = 0.10 ; overpayment = 0.10*500=50
        self.assertAlmostEqual(row["medicaid_per_unit"], 0.20, places=6)
        self.assertAlmostEqual(row["gap_medicaid"], 0.10, places=6)
        self.assertAlmostEqual(row["overpayment_medicaid"], 50.0, places=4)


class TestAttachNadacGap(unittest.TestCase):
    def test_gap_nadac_shows_costplus_margin_over_acquisition_cost(self):
        priced = a_arbitrage.attach_nadac_gap(_drug_level_fixture())
        row = priced.iloc[0]
        # gap_nadac = costplus_per_unit - nadac_per_unit = 0.10 - 0.05 = 0.05
        self.assertAlmostEqual(row["gap_nadac"], 0.05, places=6)


class TestBuildLeaderboard(unittest.TestCase):
    def test_excludes_unmatched_drugs_and_computes_total(self):
        drug_level = _drug_level_fixture()
        priced = a_arbitrage.attach_partd(drug_level, _partd_fixture(), generics_only=True)
        priced = a_arbitrage.attach_sdud(priced, _sdud_fixture())
        priced = a_arbitrage.attach_nadac_gap(priced)
        leaderboard = a_arbitrage.build_leaderboard(priced)

        self.assertEqual(len(leaderboard), 1)  # the unmatched drug is dropped
        row = leaderboard.iloc[0]
        self.assertAlmostEqual(row["total_overpayment"], 1000.0 + 50.0, places=4)
        self.assertEqual(row["net_per_unit"], "not public")  # HARD CONSTRAINT
        self.assertEqual(row["canonical_unit"], "EA")

    def test_negative_gap_not_dropped_from_leaderboard_but_excluded_from_positive_savings_sum(self):
        drug_level = _drug_level_fixture()
        # Cost Plus priced ABOVE what Part D paid -- gap_partd goes negative.
        partd = _partd_fixture()
        partd.loc[partd["Mftr_Name"] == "Overall", "Tot_Spndng"] = 500.0  # per-unit -> 0.05, below costplus 0.10
        priced = a_arbitrage.attach_partd(drug_level, partd, generics_only=True)
        priced = a_arbitrage.attach_sdud(priced, _sdud_fixture())
        priced = a_arbitrage.attach_nadac_gap(priced)
        leaderboard = a_arbitrage.build_leaderboard(priced)

        row = leaderboard.iloc[0]
        self.assertLess(row["gap_partd"], 0)
        # the drug still appears in the leaderboard (transparency), but a
        # correct aggregate-savings sum must floor each drug's contribution
        # at 0, which report.py / a_arbitrage.run() does downstream.
        positive_only_sum = leaderboard.loc[leaderboard["overpayment_partd"] > 0, "overpayment_partd"].sum()
        self.assertEqual(positive_only_sum, 0.0)


if __name__ == "__main__":
    unittest.main()
