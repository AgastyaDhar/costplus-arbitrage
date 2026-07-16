# Cost Plus Arbitrage Dataset

A cross-referenced dataset comparing Mark Cuban Cost Plus Drugs' generic
drug prices against what U.S. Medicare Part D and state Medicaid programs
paid for the same drugs, enriched with independently-sourced,
publicly-documented pharmacy benefit manager (PBM) markup figures drawn
from federal studies, a state-mandated price-transparency report, a
peer-reviewed academic study, and federal court litigation exhibits.

This folder is the deposit package: **data only**, no pipeline code. The
code that produced these files lives in the parent repository's
`costplus_suite/` (MIT licensed; see `../LICENSE` there) and is not part
of this deposit. See `REPRODUCE.md` in this folder for the exact commands
to regenerate every file here from that code.

## Files

| File | Rows | What it is |
|---|---|---|
| `leaderboard.csv` | 2,062 | One row per Cost Plus generic drug (by strength/form), ranked by estimated annual government overpayment. The primary analytic table. |
| `leaderboard_by_state.csv` | 70,253 | `leaderboard.csv`'s Medicaid comparison broken out per (state, drug) pair. |
| `state_summary.csv` | 52 | One row per US state/territory: total estimated Medicaid overpayment and top drug. |
| `catalog_gaps.csv` | 7 | Drugs with a confirmed public PBM markup citation that Cost Plus's catalog does not carry under any name or brand -- a genuine catalog absence, not a pipeline artifact. |
| `unpriced_drugs.csv` | 204 | Drugs Cost Plus carries and that resolve to a real generic drug identifier with real NDCs, but for which the NADAC acquisition-cost survey currently has no price for any of them -- an external data-coverage gap, distinct from `catalog_gaps.csv`. |
| `public_spreads_matched.csv` | 123 | The underlying confirmed-markup citations `leaderboard.csv`'s `best_confirmed_*`/`all_confirmed_sources` columns are built from -- one row per (drug, source) pair, with the source's own quote, page, and type. |

**`data_dictionary.csv`** (in this folder) documents every column in every
file above: name, data type, unit, a plain-language description, its
provenance (which upstream source it comes from or how it was derived),
whether it can be null, and a real example value. Start there.

## Headline figures (as of this deposit)

- 2,062 Cost Plus generic drugs matched against Medicare Part D and/or
  Medicaid pricing.
- Estimated combined annual overpayment: **~$13.3 billion** (~$8.0B
  Medicare Part D + ~$5.3B Medicaid), generics only.
- 56 of those 2,062 drugs additionally carry an independently-sourced,
  publicly-documented PBM markup percentage (`leaderboard.csv`'s
  `best_confirmed_spread` column) -- these are real government-study,
  litigation, or academic figures, not modeled from the overpayment
  numbers above. The two are kept in clearly separate columns and are
  never combined into a single number.

These figures will drift slightly on any re-run: NADAC (the acquisition-cost
benchmark) refreshes weekly, and Medicare/Medicaid spending files refresh
on their own federal release schedules. See `REPRODUCE.md` for the exact
snapshot this deposit was built from.

## Selection-bias caveat (read before citing the markup figures)

The confirmed-markup citations in `public_spreads_matched.csv` and
`leaderboard.csv` come from four source types with **different and
sometimes opposite selection properties**:

- `federal_study` (FTC interim reports) samples across a PBM's entire
  book of business for the drugs it covers.
- `state_disclosure` (Maine MHDO) is mandated reporting, not a
  discretionary sample.
- `peer_reviewed` (JAMA, Mattingly et al.) is an academic study with its
  own published inclusion criteria.
- `litigation` (two ERISA class-action complaints) is evidence selected
  by plaintiffs' counsel to make a case -- it systematically skews toward
  the largest markups and is **not a representative sample**.

Where more than one source cites the same drug, `best_confirmed_spread`
reports the single highest value, which mechanically favors `litigation`
sources when one is present. `all_confirmed_sources` lists every citation
for that drug (not just the winner) so this selection is visible rather
than hidden -- e.g. Abiraterone Acetate 250mg shows both Maine MHDO's
1,727.73% (`state_disclosure`) and a litigation complaint's 6,391.86%.
Any onward use of `best_confirmed_spread` as a summary statistic should
account for this rather than treating all 56 rows as equivalently sourced.

See `METHODOLOGY.md` (also in this folder) for the full data-source,
computation, and limitation writeup, and `PROVENANCE.md` for exactly
where every upstream source came from and when it was accessed.

## License

CC BY 4.0 -- see `LICENSE` in this folder. This license covers the data
files listed above and the data dictionary; it does not relicense the
underlying third-party sources (CMS, Cost Plus Drugs, RxNorm, government
reports, court filings) -- see `PROVENANCE.md` for each source's own
terms.

## Citation

See `../CITATION.cff` at the parent repository root.
