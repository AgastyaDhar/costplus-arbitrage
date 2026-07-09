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
