"""
Unit tests for modules.e_brand_trumprx's TrumpRx-to-Cost-Plus-generic join
(the headline exhibit). Pure function over small synthetic, already-
ingredient-normalized DataFrames -- no crosswalk/network calls.
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules import e_brand_trumprx  # noqa: E402


def _trumprx_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"brand_name": "Lipitor", "trumprx_price": 60.00, "ingredient_norm": "ATORVASTATIN"},
            {"brand_name": "Glucophage", "trumprx_price": 45.00, "ingredient_norm": "METFORMIN"},
            # No Cost Plus generic in the fixture below -- must be dropped by the inner join, not error.
            {"brand_name": "Novolog", "trumprx_price": 300.00, "ingredient_norm": "INSULIN ASPART"},
        ]
    )


def _costplus_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # acquisition_cost*markup + pharmacy_fee = 3*1.15+5 = 8.45 package price
            {"costplus_per_unit": 8.45 / 90, "package_quantity": 90, "ingredient_norm": "ATORVASTATIN"},
            # 2*1.15+5 = 7.30 package price
            {"costplus_per_unit": 7.30 / 90, "package_quantity": 90, "ingredient_norm": "METFORMIN"},
            # a second atorvastatin strength -- median of [8.45, 9.60] = 9.025
            {"costplus_per_unit": 9.60 / 90, "package_quantity": 90, "ingredient_norm": "ATORVASTATIN"},
        ]
    )


class TestJoinTrumprxToCostplus(unittest.TestCase):
    def test_drops_brands_with_no_costplus_generic(self):
        out = e_brand_trumprx._join_trumprx_to_costplus(_trumprx_fixture(), _costplus_fixture())
        self.assertNotIn("Novolog", out["brand_name"].values)
        self.assertEqual(len(out), 2)

    def test_uses_median_package_price_across_strengths(self):
        out = e_brand_trumprx._join_trumprx_to_costplus(_trumprx_fixture(), _costplus_fixture())
        row = out[out["brand_name"] == "Lipitor"].iloc[0]
        # median of the two atorvastatin package prices (8.45, 9.60) = 9.025
        self.assertAlmostEqual(row["costplus_generic_price"], 9.025, places=4)

    def test_gap_and_gap_pct_math(self):
        out = e_brand_trumprx._join_trumprx_to_costplus(_trumprx_fixture(), _costplus_fixture())
        row = out[out["brand_name"] == "Glucophage"].iloc[0]
        # gap = trumprx_price - costplus_generic_price = 45.00 - 7.30 = 37.70
        self.assertAlmostEqual(row["gap"], 37.70, places=4)
        self.assertAlmostEqual(row["gap_pct"], 37.70 / 45.00 * 100, places=4)

    def test_sorted_by_gap_descending(self):
        out = e_brand_trumprx._join_trumprx_to_costplus(_trumprx_fixture(), _costplus_fixture())
        # Lipitor gap = 60.00 - 9.025 = 50.975 ; Glucophage gap = 45.00 - 7.30 = 37.70
        self.assertEqual(list(out["brand_name"]), ["Lipitor", "Glucophage"])
        self.assertTrue((out["gap"].diff().dropna() <= 0).all())

    def test_output_columns_match_headline_exhibit_spec(self):
        out = e_brand_trumprx._join_trumprx_to_costplus(_trumprx_fixture(), _costplus_fixture())
        self.assertEqual(list(out.columns), ["brand_name", "trumprx_price", "costplus_generic_price", "gap", "gap_pct"])


if __name__ == "__main__":
    unittest.main()
