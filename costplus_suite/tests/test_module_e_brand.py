"""
Unit tests for modules.e_brand_trumprx's brand price-increase leaderboard
math (a utilization-blended Part D YoY proxy, not WAC -- see METHODOLOGY.md).
Pure function over a small synthetic DataFrame, no network.
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules import e_brand_trumprx  # noqa: E402


def _partd_raw_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # Brand row, Overall -- should appear in the leaderboard.
            {"Brnd_Name": "Humira", "Gnrc_Name": "Adalimumab", "Mftr_Name": "Overall", "Chg_Avg_Spnd_Per_Dsg_Unt_23_24": 0.15},
            # Same brand's per-manufacturer breakdown row -- must NOT double-count / must be excluded.
            {"Brnd_Name": "Humira", "Gnrc_Name": "Adalimumab", "Mftr_Name": "AbbVie", "Chg_Avg_Spnd_Per_Dsg_Unt_23_24": 0.15},
            # A bigger mover, Overall.
            {"Brnd_Name": "Eliquis", "Gnrc_Name": "Apixaban", "Mftr_Name": "Overall", "Chg_Avg_Spnd_Per_Dsg_Unt_23_24": 0.42},
            # A generic row (Brnd_Name == Gnrc_Name) -- must be excluded, brand-only leaderboard.
            {"Brnd_Name": "Atorvastatin Calcium", "Gnrc_Name": "Atorvastatin Calcium", "Mftr_Name": "Overall", "Chg_Avg_Spnd_Per_Dsg_Unt_23_24": 0.90},
            # A row missing the YoY column -- must be dropped, not treated as 0.
            {"Brnd_Name": "Jardiance", "Gnrc_Name": "Empagliflozin", "Mftr_Name": "Overall", "Chg_Avg_Spnd_Per_Dsg_Unt_23_24": None},
        ]
    )


class TestComputeBrandLeaderboard(unittest.TestCase):
    def test_excludes_generic_rows(self):
        out = e_brand_trumprx._compute_brand_leaderboard(_partd_raw_fixture(), "Chg_Avg_Spnd_Per_Dsg_Unt_23_24")
        self.assertNotIn("Atorvastatin Calcium", out["Brnd_Name"].values)

    def test_excludes_per_manufacturer_rows_not_just_overall(self):
        out = e_brand_trumprx._compute_brand_leaderboard(_partd_raw_fixture(), "Chg_Avg_Spnd_Per_Dsg_Unt_23_24")
        # Humira should appear exactly once (the Overall row), not twice.
        self.assertEqual((out["Brnd_Name"] == "Humira").sum(), 1)

    def test_drops_rows_missing_the_yoy_column(self):
        out = e_brand_trumprx._compute_brand_leaderboard(_partd_raw_fixture(), "Chg_Avg_Spnd_Per_Dsg_Unt_23_24")
        self.assertNotIn("Jardiance", out["Brnd_Name"].values)

    def test_sorted_descending_by_yoy_change(self):
        out = e_brand_trumprx._compute_brand_leaderboard(_partd_raw_fixture(), "Chg_Avg_Spnd_Per_Dsg_Unt_23_24")
        self.assertEqual(list(out["Brnd_Name"]), ["Eliquis", "Humira"])
        self.assertAlmostEqual(out.iloc[0]["gross_spend_per_unit_yoy_chg_pct"], 0.42, places=6)

    def test_respects_top_n(self):
        out = e_brand_trumprx._compute_brand_leaderboard(_partd_raw_fixture(), "Chg_Avg_Spnd_Per_Dsg_Unt_23_24", top_n=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["Brnd_Name"], "Eliquis")


if __name__ == "__main__":
    unittest.main()
