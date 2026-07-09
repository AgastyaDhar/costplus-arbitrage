"""
Phase 0 gate: prove the drug-name -> RxCUI -> NDC -> NADAC crosswalk works
before any other module gets built.

Run: python spike.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import crosswalk  # noqa: E402

# 20 common generic drugs: name, strength, form. Deliberately plain oral
# solids so the expected NADAC Pricing Unit is unambiguous (EA = each tablet
# or capsule) and a human can sanity-check the match by eye.
DRUGS = [
    "atorvastatin 20 mg tablet",
    "metformin 500 mg tablet",
    "lisinopril 10 mg tablet",
    "amlodipine 5 mg tablet",
    "levothyroxine 50 mcg tablet",
    "omeprazole 20 mg capsule",
    "simvastatin 20 mg tablet",
    "losartan potassium 50 mg tablet",
    "metoprolol tartrate 50 mg tablet",
    "gabapentin 300 mg capsule",
    "hydrochlorothiazide 25 mg tablet",
    "sertraline 50 mg tablet",
    "furosemide 40 mg tablet",
    "citalopram 20 mg tablet",
    "montelukast 10 mg tablet",
    "pantoprazole sodium 40 mg tablet",
    "amoxicillin 500 mg capsule",
    "azithromycin 250 mg tablet",
    "prednisone 10 mg tablet",
    "warfarin sodium 5 mg tablet",
]


def main() -> None:
    print(f"[spike] Resolving crosswalk for {len(DRUGS)} drugs...\n")
    nadac_df = crosswalk.load_nadac()
    print()

    rows = []
    for term in DRUGS:
        r = crosswalk.crosswalk_drug(term, nadac_df)
        rows.append(
            {
                "drug": term,
                "rxcui": r.rxcui or "-",
                "tty": r.tty or "-",
                "ndc_count": r.ndc_count,
                "matched_ndcs": r.matched_ndc_count,
                "nadac_per_unit": f"{r.nadac_per_unit:.5f}" if r.nadac_per_unit is not None else "-",
                "pricing_unit": r.pricing_unit or "-",
                "matched": "YES" if r.matched else "NO",
                "note": r.note,
            }
        )
        status = "OK " if r.matched else "FAIL"
        print(f"  [{status}] {term:45s} -> rxcui={r.rxcui} matched_ndcs={r.matched_ndc_count}")

    out = pd.DataFrame(rows)
    print("\n" + "=" * 140)
    print(out.to_string(index=False))
    print("=" * 140)

    match_count = (out["matched"] == "YES").sum()
    total = len(out)
    print(f"\nMatch rate: {match_count}/{total} ({match_count / total:.0%})")

    failed = out[out["matched"] == "NO"]
    if not failed.empty:
        print("\nFailed drugs:")
        for _, row in failed.iterrows():
            print(f"  - {row['drug']}: {row['note']}")

    out_path = Path(__file__).resolve().parent / "cache" / "spike_results.csv"
    out.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
