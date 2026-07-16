"""
Unit tests for modules.h_catalog_gaps -- the regenerated, non-static
replacement for the hand-built catalog_gaps.csv that turned out to be
wrong for Glatiramer, Mycophenolic Acid, and Ribavirin (all present in
the raw Cost Plus catalog, but the file was never re-derived after later
crosswalk fixes changed what could resolve).
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules import h_catalog_gaps  # noqa: E402


def _catalog(rows):
    """rows: list of (drug, brand_name) tuples."""
    return pd.DataFrame({"drug": [r[0] for r in rows], "brand_name": [r[1] for r in rows]})


def _spreads(rows):
    """rows: list of (drug_name, metric_type, value, source_name, source_page)."""
    return pd.DataFrame([
        {"drug_name": r[0], "metric_type": r[1], "value": r[2], "source_name": r[3], "source_page": r[4]}
        for r in rows
    ])


class TestSearchKeyAndBrand(unittest.TestCase):
    def test_extracts_first_word_and_parenthetical_as_brand(self):
        key, brand = h_catalog_gaps._search_key_and_brand("Abiraterone (Zytiga) Pill")
        self.assertEqual(key, "abiraterone")
        self.assertEqual(brand, "Zytiga")

    def test_no_parenthetical_gives_none_brand(self):
        key, brand = h_catalog_gaps._search_key_and_brand("Mycophenolate Sodium Tablet")
        self.assertEqual(key, "mycophenolate")
        self.assertIsNone(brand)

    def test_multi_word_brand_captured_whole(self):
        key, brand = h_catalog_gaps._search_key_and_brand("Ribavirin (Moderiba/Rebetol) Pill")
        self.assertEqual(key, "ribavirin")
        self.assertEqual(brand, "Moderiba/Rebetol")


class TestInRawCatalog(unittest.TestCase):
    def test_finds_ingredient_substring_in_drug_column(self):
        catalog = _catalog([("Glatopa (Glatiramer Acetate)", "Copaxone")])
        self.assertTrue(h_catalog_gaps._in_raw_catalog("glatiramer", None, catalog))

    def test_finds_brand_in_brand_name_column_when_drug_column_misses(self):
        catalog = _catalog([("Some Generic Name", "Sandostatin")])
        self.assertTrue(h_catalog_gaps._in_raw_catalog("octreotide", "Sandostatin", catalog))

    def test_false_when_neither_ingredient_nor_brand_present(self):
        catalog = _catalog([("Atorvastatin", "Lipitor")])
        self.assertFalse(h_catalog_gaps._in_raw_catalog("octreotide", "Sandostatin", catalog))

    def test_empty_search_key_is_never_a_match(self):
        catalog = _catalog([("Atorvastatin", "Lipitor")])
        self.assertFalse(h_catalog_gaps._in_raw_catalog("", None, catalog))


class TestRun(unittest.TestCase):
    def test_glatiramer_style_drug_is_not_a_gap(self):
        # The actual regression this module exists to fix: a drug present
        # in the raw catalog under a brand-first naming convention
        # ("Glatopa (Glatiramer Acetate)") must never be listed, even
        # though the citation's own drug_name ("Glatiramer") shares no
        # exact substring with "Glatopa".
        catalog = _catalog([("Glatopa (Glatiramer Acetate)", "Copaxone")])
        spreads = _spreads([
            ("Glatiramer", "markup_pct", 190.77, "Lewandowski v. Johnson & Johnson", 44),
        ])
        out = h_catalog_gaps.run(
            public_spreads_path=_write_tmp(spreads),
            costplus_catalog_path=_write_tmp(catalog),
        )
        self.assertTrue(out.empty)

    def test_genuinely_absent_drug_is_listed_with_source_columns(self):
        catalog = _catalog([("Atorvastatin", "Lipitor")])
        spreads = _spreads([
            ("Octreotide Acetate", "markup_pct", 29.14, "Lewandowski v. Johnson & Johnson", 44),
            ("Octreotide Acetate", "markup_pct", 14.98, "Navarro v. Wells Fargo & Co.", 55),
        ])
        out = h_catalog_gaps.run(
            public_spreads_path=_write_tmp(spreads),
            costplus_catalog_path=_write_tmp(catalog),
        )
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertEqual(row["drug_name"], "Octreotide Acetate")

    def test_two_source_namings_of_the_same_molecule_consolidate_to_one_row(self):
        # FTC ("Octreotide (Sandostatin) Injectable") and litigation
        # ("Octreotide Acetate") name the same real gap drug differently --
        # must produce ONE row, not two.
        catalog = _catalog([("Atorvastatin", "Lipitor")])
        spreads = _spreads([
            ("Octreotide (Sandostatin) Injectable", "markup_pct", 56.0, "FTC Second Interim Staff Report (Jan 2025)", 36),
            ("Octreotide Acetate", "markup_pct", 29.14, "Lewandowski v. Johnson & Johnson, ERISA Class Action Complaint (D.N.J. 1:24-cv-00671, filed Feb. 2024)", 44),
        ])
        out = h_catalog_gaps.run(
            public_spreads_path=_write_tmp(spreads),
            costplus_catalog_path=_write_tmp(catalog),
        )
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertEqual(row["ftc_markup_pct"], 56.0)
        self.assertEqual(row["jj_markup_pct"], 29.14)

    def test_non_markup_pct_rows_excluded(self):
        catalog = _catalog([("Atorvastatin", "Lipitor")])
        spreads = _spreads([
            ("Octreotide Acetate", "spread_dollars", 500.0, "Some Source", 1),
        ])
        out = h_catalog_gaps.run(
            public_spreads_path=_write_tmp(spreads),
            costplus_catalog_path=_write_tmp(catalog),
        )
        self.assertTrue(out.empty)


def _write_tmp(df: pd.DataFrame) -> Path:
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".csv")
    df.to_csv(path, index=False)
    return Path(path)


if __name__ == "__main__":
    unittest.main()
