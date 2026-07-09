"""
Unit tests for shared.costplus's per-unit price formula. Pure arithmetic,
no network -- runs a synthetic CSV through the real loader and checks the
math by hand.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import costplus  # noqa: E402


class TestCostPlusPerUnit(unittest.TestCase):
    def _load(self, csv_text: str):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "costplus_fixture.csv"
            path.write_text(csv_text, encoding="utf-8")
            return costplus.load_costplus(path)

    def test_basic_formula(self):
        # atorvastatin 20mg, 90ct: (3.00*1.15 + 5.00) / 90 = 8.45 / 90 = 0.093888...
        csv_text = (
            "drug,strength,form,package_quantity,acquisition_cost,markup,pharmacy_fee,shipping_fee\n"
            "atorvastatin,20 mg,tablet,90,3.00,1.15,5.00,5.00\n"
        )
        df = self._load(csv_text)
        self.assertAlmostEqual(df.loc[0, "costplus_per_unit"], (3.00 * 1.15 + 5.00) / 90, places=8)

    def test_markup_defaults_when_blank(self):
        csv_text = (
            "drug,strength,form,package_quantity,acquisition_cost,markup,pharmacy_fee,shipping_fee\n"
            "metformin,500 mg,tablet,90,2.00,,5.00,5.00\n"
        )
        df = self._load(csv_text)
        expected = (2.00 * costplus.config.COSTPLUS_MARKUP + 5.00) / 90
        self.assertAlmostEqual(df.loc[0, "costplus_per_unit"], expected, places=8)

    def test_shipping_fee_never_folded_into_per_unit_price(self):
        # HARD CONSTRAINT: shipping_fee must never affect costplus_per_unit.
        csv_text = (
            "drug,strength,form,package_quantity,acquisition_cost,markup,pharmacy_fee,shipping_fee\n"
            "drug_a,10 mg,tablet,30,1.00,1.15,2.00,0.00\n"
            "drug_b,10 mg,tablet,30,1.00,1.15,2.00,99.00\n"
        )
        df = self._load(csv_text)
        self.assertAlmostEqual(df.loc[0, "costplus_per_unit"], df.loc[1, "costplus_per_unit"], places=10)
        self.assertEqual(df.loc[0, "shipping_fee"], 0.00)
        self.assertEqual(df.loc[1, "shipping_fee"], 99.00)

    def test_missing_file_raises_not_silently_falls_back(self):
        with self.assertRaises(FileNotFoundError):
            costplus.load_costplus(Path("/nonexistent/costplus.csv"))

    def test_zero_package_quantity_rejected(self):
        csv_text = (
            "drug,strength,form,package_quantity,acquisition_cost,markup,pharmacy_fee,shipping_fee\n"
            "drug_a,10 mg,tablet,0,1.00,1.15,2.00,5.00\n"
        )
        with self.assertRaises(ValueError):
            self._load(csv_text)

    def test_sample_file_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "costplus.SAMPLE.csv"
            path.write_text(
                "drug,strength,form,package_quantity,acquisition_cost,markup,pharmacy_fee,shipping_fee\n"
                "drug_a,10 mg,tablet,30,1.00,1.15,2.00,5.00\n",
                encoding="utf-8",
            )
            df = costplus.load_costplus(path)
            self.assertTrue(df.attrs["is_sample"])


if __name__ == "__main__":
    unittest.main()
