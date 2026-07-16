"""
Unit tests for modules.j_workbook -- the script that replaced the
hand-maintained PBM_Markup_Analysis.xlsx (see dataset/REPRODUCE.md,
section 6, "there is currently no committed script"). Uses small
synthetic leaderboard/state_summary CSVs, no network calls.
"""
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules import j_workbook  # noqa: E402


def _leaderboard(rows):
    """rows: list of dicts with at least the columns j_workbook reads."""
    base = {
        "rank": None, "drug_term": None, "costplus_per_unit": None, "nadac_per_unit": None,
        "partd_per_unit": None, "gap_partd": None, "total_overpayment": None,
        "best_confirmed_spread": None, "estimated_pbm_price_per_unit": None,
        "best_confirmed_source": None, "source_type": None, "canonical_unit": None,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def _state_summary(rows):
    return pd.DataFrame(rows)


def _write_tmp(df: pd.DataFrame) -> Path:
    fd, path = tempfile.mkstemp(suffix=".csv")
    df.to_csv(path, index=False)
    return Path(path)


class TestWorkbookStructure(unittest.TestCase):
    def test_sheet_names(self):
        leaderboard = _leaderboard([
            {"rank": 1, "drug_term": "Testdrug 10mg Tablet", "costplus_per_unit": 0.10,
             "nadac_per_unit": 0.05, "partd_per_unit": 0.20, "gap_partd": 0.10,
             "total_overpayment": 1000.0, "canonical_unit": "EA"},
        ])
        state_summary = _state_summary([
            {"state": "CA", "total_medicaid_overpayment": 500.0, "top_drug": "Testdrug",
             "top_drug_overpayment": 500.0, "drugs_analyzed": 1},
        ])
        wb = j_workbook.run(
            leaderboard_path=_write_tmp(leaderboard),
            state_summary_path=_write_tmp(state_summary),
        )
        self.assertEqual(wb.sheetnames, ["PBM Markup", "States", "Methodology", "Sources"])

    def test_pbm_markup_row_count_matches_leaderboard(self):
        leaderboard = _leaderboard([
            {"rank": i, "drug_term": f"Drug {i}", "costplus_per_unit": 0.1, "nadac_per_unit": 0.2,
             "partd_per_unit": 0.3, "gap_partd": 0.1, "total_overpayment": 100.0 * i,
             "canonical_unit": "EA"}
            for i in range(1, 6)
        ])
        state_summary = _state_summary([
            {"state": "CA", "total_medicaid_overpayment": 500.0, "top_drug": "Drug 1",
             "top_drug_overpayment": 500.0, "drugs_analyzed": 5},
        ])
        wb = j_workbook.run(
            leaderboard_path=_write_tmp(leaderboard),
            state_summary_path=_write_tmp(state_summary),
        )
        ws = wb["PBM Markup"]
        # header at row 10, 5 data rows below it
        self.assertEqual(ws.max_row, 15)
        self.assertEqual(ws.cell(row=10, column=2).value, "Drug")

    def test_headline_total_equals_leaderboard_sum(self):
        overpayments = [746482855.4952761, 359531869.5645537, 100.0]
        leaderboard = _leaderboard([
            {"rank": i + 1, "drug_term": f"Drug {i}", "costplus_per_unit": 0.1,
             "nadac_per_unit": 0.2, "partd_per_unit": 0.3, "gap_partd": 0.1,
             "total_overpayment": op, "canonical_unit": "EA"}
            for i, op in enumerate(overpayments)
        ])
        state_summary = _state_summary([
            {"state": "CA", "total_medicaid_overpayment": 500.0, "top_drug": "Drug 0",
             "top_drug_overpayment": 500.0, "drugs_analyzed": 3},
        ])
        leaderboard_path = _write_tmp(leaderboard)
        wb = j_workbook.run(
            leaderboard_path=leaderboard_path,
            state_summary_path=_write_tmp(state_summary),
        )
        ws = wb["PBM Markup"]
        total_overpayment_col = 7  # "Total Overpayment", see _PBM_MARKUP_COLUMNS
        xlsx_total = sum(
            ws.cell(row=r, column=total_overpayment_col).value
            for r in range(10 + 1, ws.max_row + 1)
        )
        expected_total = pd.read_csv(leaderboard_path)["total_overpayment"].sum()
        self.assertAlmostEqual(xlsx_total, expected_total, places=4)


if __name__ == "__main__":
    unittest.main()
