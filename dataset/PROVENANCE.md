# Provenance

**Snapshot date**: this deposit's live-fetched data (Section A) was
accessed **2026-07-16**, against the NADAC file with effective date
**2026-07-15** (dataset `fbb83258-11c7-47f5-8b18-5f8e79f7e704`). This is
a dated snapshot, not a live claim -- NADAC republishes weekly and
Part D/SDUD refresh on their own federal schedules, so the same pipeline
run against current CMS data will produce different figures. See each
source's own "Version / access" line below for its individual access
date, and "Change notice" at the end of this document.

This document records, for every upstream source this dataset draws on:
what it is, where it came from, when it was accessed, what its own
license/terms are, and exactly what was extracted from it. It is the
companion to `METHODOLOGY.md` (which explains the computation) and
`data_dictionary.csv` (which explains each output column).

Two source categories feed this dataset:

1. **Live-fetched pipeline data** -- prices and government spending,
   pulled programmatically every time the pipeline runs (Section A).
2. **Static citation sources** -- PDFs, court filings, and one blog post,
   read once by hand/LLM-assisted extraction and hand-curated into
   `public_spreads_matched.csv`; not re-fetched automatically (Section B).

---

## A. Live-fetched pipeline data

### A1. Cost Plus Drugs storefront (drug catalog and prices)

- **What it is**: Mark Cuban Cost Plus Drug Company's public retail
  catalog and pricing.
- **Access mechanism**: `costplus_suite/fetch/costplus_graphql.py` --
  `POST https://www.costplusdrugs.com/graphql/`, the same public Saleor
  storefront GraphQL API the site's own frontend calls (no private/
  authenticated endpoint). `robots.txt` at the site root was checked
  programmatically before use and does not disallow `/graphql/`.
- **Version / access**: Re-fetched on every pipeline run; this deposit's
  snapshot was pulled 2026-07-16 (see `REPRODUCE.md` for the run that
  produced the committed `output/` files).
- **License / terms**: No published API terms of service; this is the
  storefront's own public product API, read the same way a browser
  reading the site would. Used here for factual pricing data only (price,
  package size, drug name/strength) -- no proprietary content, images, or
  copy is redistributed.
- **What was extracted**: drug name, strength/form, package quantity, and
  `priceCalculation` (the real per-package price, read per product
  *variant* -- the product-level field and the `retailPricePerUnit`
  metafield were both tested and found unreliable; see the header comment
  in `costplus_suite/fetch/costplus_graphql.py` for the verification).

### A2. CMS NADAC (National Average Drug Acquisition Cost)

- **What it is**: CMS's weekly survey-based benchmark of what retail
  pharmacies actually pay wholesalers to acquire each NDC -- the
  acquisition-cost baseline this dataset compares Cost Plus prices
  against.
- **Landing page**: https://www.medicaid.gov/medicaid/nadac
- **Access mechanism**: `costplus_suite/fetch/nadac.py` queries the
  `data.medicaid.gov` metastore API (`MEDICAID_METASTORE_BASE` in
  `config.py`) for datasets whose title matches `NADAC_TITLE_RE`
  (`"NADAC (National Average Drug Acquisition Cost) <year>"`), then
  resolves the current weekly file's own `downloadURL`
  (`https://download.medicaid.gov/data/nadac-national-average-drug-acquisition-cost-MM-DD-YYYY.csv`)
  rather than a hardcoded URL, since NADAC republishes weekly.
- **Version / access**: dataset identifier
  `fbb83258-11c7-47f5-8b18-5f8e79f7e704` ("NADAC ... 2026"), snapshot
  dated 2026-07-15, fetched 2026-07-16.
- **License / terms**: U.S. government public data, released under the
  OPEN Government Data Act in an open format; no additional restriction.
- **What was extracted**: `NDC`, `NDC Description`, `NADAC_Per_Unit`,
  `Pricing_Unit`, `Effective_Date` for every active NDC.

### A3. CMS Medicare Part D Spending by Drug

- **What it is**: CMS's annual summary of Medicare Part D claims,
  aggregated to one row per drug (average spending per dosage unit,
  total dosage units, total spending) -- one of the two government-payer
  comparison points in `leaderboard.csv`.
- **Landing page**: https://data.cms.gov/summary-statistics-on-use-and-payments/medicare-medicaid-spending-by-drug/medicare-part-d-spending-by-drug
- **Access mechanism**: `costplus_suite/fetch/partd.py` queries
  `data.cms.gov/data.json` (CMS's DCAT catalog, `CMS_DATA_METASTORE_BASE`
  in `config.py`) for the dataset titled exactly `"Medicare Part D
  Spending by Drug"` (`DATASET_TITLE` constant -- deliberately not the
  quarterly variant, which is a different dataset with different
  columns), then calls that dataset's resource API for the CSV
  `downloadURL`.
- **Version / access**: most recent annual release as of 2026-07-16 (this
  dataset updates roughly once per year; CMS does not version-number it,
  so the metastore's `modified` date is the effective version marker).
- **License / terms**: U.S. government public data, no additional
  restriction (same OPEN Government Data Act framework as A2).
- **What was extracted**: brand name, generic name, total spending,
  total dosage units, and average spending per dosage unit per drug
  (used to compute `partd_per_unit` in `leaderboard.csv`).

### A4. CMS/Medicaid State Drug Utilization Data (SDUD)

- **What it is**: State-reported, drug-level Medicaid utilization and
  reimbursement data (units reimbursed, amount reimbursed) -- the source
  for `leaderboard_by_state.csv` and `state_summary.csv`.
- **Landing page**: https://www.medicaid.gov/medicaid/prescription-drugs/state-drug-utilization-data
- **Access mechanism**: `costplus_suite/fetch/sdud.py` queries the same
  `data.medicaid.gov` metastore as A2 for datasets whose title matches
  `SDUD_TITLE_RE` (`"State Drug Utilization Data <year>"`), takes the
  most recent year(s), and resolves each one's own `downloadURL`.
- **Version / access**: most recent quarter(s) of the current and prior
  year available at `data.medicaid.gov` as of 2026-07-16.
- **License / terms**: U.S. government public data, no additional
  restriction (same framework as A2/A3).
- **What was extracted**: state, NDC, number of prescriptions, units
  reimbursed, and total amount reimbursed, per state per drug per
  quarter.

### A5. RxNav / RxNorm (National Library of Medicine)

- **What it is**: NLM's standard drug-naming and identifier service, used
  throughout this pipeline as the crosswalk backbone -- every drug name
  from every other source (Cost Plus catalog text, NADAC's free-text NDC
  description, PDF citation text) is resolved to a single canonical
  `rxcui` so that records from unrelated sources can be joined.
- **Landing page**: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html
- **Access mechanism**: `costplus_suite/shared/crosswalk.py` and
  `costplus_suite/config.py` (`RXNAV_BASE =
  "https://rxnav.nlm.nih.gov/REST"`) -- the public RxNorm REST API
  (`/rxcui.json`, `/rxcui/{rxcui}/allrelated.json`, etc.).
- **Version / access**: live REST calls, cached locally per run;
  RxNorm itself is republished monthly by NLM. This deposit reflects
  whatever RxNorm content was live on 2026-07-16.
- **License / terms**: per NLM's Terms of Service
  (https://lhncbc.nlm.nih.gov/RxNav/TermsofService.html), the RxNorm API
  requires no license for the endpoints used here (the one exception,
  `/rxcui/{rxcui}/proprietary`, requiring a UMLS license, is not used by
  this pipeline) and is free to use with attribution: *"This product uses
  publicly available data from the U.S. National Library of Medicine
  (NLM), National Institutes of Health, Department of Health and Human
  Services; NLM is not responsible for the product and does not endorse
  or recommend this or any other product."* Rate-limited to 20
  requests/second/IP, respected by this pipeline's fetch layer.
- **What was extracted**: `rxcui` identifiers and term-type (`TTY`:
  SCD/SBD/GPCK/BPCK/etc.) classifications used to resolve free-text drug
  names to a canonical concept and to distinguish generic from branded
  concepts.

---

## B. Static citation sources (PBM markup figures)

These are the sources behind `public_spreads_matched.csv`'s
`best_confirmed_spread` / `all_confirmed_sources` figures in
`leaderboard.csv`, plus the two litigation-only columns in
`catalog_gaps.csv` (`jj_markup_pct`, `wf_markup_pct`) and the
`ftc_markup_pct` column there. Unlike Section A, these are not
re-fetched by the pipeline -- they were read once (PDF text extraction,
government dockets, or web fetch) and the specific figures that met this
project's disambiguation rules (see `METHODOLOGY.md`, "Public citation
enrichment") were hand-curated into `public_spreads_matched.csv`. Local
copies of the primary documents are kept in
`costplus_suite/data/sources/` (not part of this `dataset/` deposit,
which is data-only; see the parent repo for the PDFs themselves).

### B1. Sources that contributed a confirmed drug-level markup figure

| Source | Publisher / venue | Date | URL | Local file | source_type | What was extracted |
|---|---|---|---|---|---|---|
| Specialty Generic Drugs: A Growing Profit Center for Vertically Integrated Pharmacy Benefit Managers, Second Interim Staff Report | U.S. Federal Trade Commission, Office of Policy Planning | Jan. 2025 | https://www.ftc.gov/reports/pharmacy-benefit-managers-report | `ftc_2025_second_interim_staff_report.pdf` | `federal_study` | Per-drug NADAC-vs.-affiliated-pharmacy-reimbursement markup percentages for specialty generics dispensed by the "Big 3" vertically integrated PBMs (2017-2022 claims). |
| Pharmacy Benefit Managers: The Powerful Middlemen Inflating Drug Costs and Squeezing Main Street Pharmacies, Interim Staff Report | U.S. Federal Trade Commission, Office of Policy Planning | Jul. 2024 | https://www.ftc.gov/reports/pharmacy-benefit-managers-report | `ftc_2024_interim_staff_report.pdf` | `federal_study` | Two named cancer drugs' aggregate excess PBM revenue figure. |
| Pharmacy Benefit Manager Pricing and Spread Pricing for High-Utilization Generic Drugs (Research Letter) | JAMA Health Forum (Mattingly, Ben-Umeh, Bai, Anderson) | Oct. 2023 | https://jamanetwork.com/journals/jama-health-forum | `jama_health_forum_spread_pricing.pdf` | `peer_reviewed` | Per-drug PBM gross-profit / spread-pricing figures for 45 high-utilization generics, published inclusion criteria. |
| Maine Health Data Organization, Prescription Drug Pricing and Transparency Report | Maine MHDO | Dec. 2022 (covering CY2021) | https://mhdo.maine.gov | `maine_mhdo_rx_transparency_2022.pdf` | `state_disclosure` | Drug-level WAC vs. PBM-reimbursement figures under Maine's mandatory price-transparency statute. |
| Lewandowski v. Johnson & Johnson, ERISA Class Action Complaint | D.N.J., Civil No. 1:24-cv-00671 (filed Feb. 2024) | Feb. 2024; accessed 2026-07-17 via CourtListener/RECAP (`storage.courtlistener.com/recap/gov.uscourts.njd.539961/gov.uscourts.njd.539961.1.0.pdf`), docket https://www.courtlistener.com/docket/68223269/ | `lewandowski_v_jnj_erisa_complaint_2024.pdf` (reference copy, not relicensed -- see note below) | `litigation` | A 42-drug exhibit table comparing NADAC acquisition cost to the price J&J's health plan paid Express Scripts. |
| Navarro v. Wells Fargo & Co., ERISA Class Action Complaint | D. Minn., Civil No. 0:24-cv-03043 (filed Jul. 2024) | Jul. 2024; accessed 2026-07-17 via CourtListener/RECAP (`storage.courtlistener.com/recap/gov.uscourts.mnd.218287/gov.uscourts.mnd.218287.1.0.pdf`), docket https://www.courtlistener.com/docket/68995654/ | `navarro_v_wellsfargo_erisa_complaint_2024.pdf` (reference copy, not relicensed -- see note below) | `litigation` | A 38-drug exhibit table comparing NADAC acquisition cost to the price Wells Fargo's health plan paid Express Scripts. |
| "Wrecklimid: How Treating Generic Drugs as Something Special Can Wreck Affordability" | 46brooklyn Research, citing Evernorth/Georgia commercial NADAC disclosure filings | 2026 | https://www.46brooklyn.com/research/wrecklimid-how-treating-generic-drugs-as-something-special-can-wreck-affordability-jfmve | *(fetched via web; not stored locally as a standalone PDF)* | `peer_reviewed` | Abiraterone: ESI-affiliate vs. non-affiliate pharmacy reimbursement premium, drawn from Georgia commercial-market NADAC disclosure filings. |

**Note on the two litigation complaints (Lewandowski, Navarro)**: both are
archived locally as reference copies in `costplus_suite/data/sources/`,
fetched from their public CourtListener/RECAP docket on 2026-07-17, the
same treatment given every other source in this table. Archiving closes
the reproducibility gap of depending on an external URL that could move or
be reorganized. This does **not** relicense either document -- like every
other third-party source here, the archived PDF remains under whatever
rights its author holds; this deposit's CC BY 4.0 license covers only the
facts/figures we extracted from it (see "License" in `README.md`), not the
document itself. Court filings are public record and freely available via
PACER/RECAP; verbatim archival of a party's own complaint for research and
citation-verification purposes is standard practice (it's the entire
premise of the CourtListener/RECAP archive these copies were fetched
from). **Both documents state allegations in an active complaint as of the
2026-07-17 access date -- they are not adjudicated facts.** Neither case
had reached judgment, settlement, or dismissal as of that date; the
drug-level figures extracted from their exhibit tables are the plaintiffs'
own claims, not court-established findings. See `METHODOLOGY.md`'s
litigation selection-bias caveat for how this pipeline treats that
distinction.

**Note on `source_type` classification and selection bias**: see
`METHODOLOGY.md`'s "Public citation enrichment" section for the full
caveat. In short -- `federal_study` and `state_disclosure` sample across
a payer's or PBM's full book of business; `litigation` exhibits are
selected by plaintiffs' counsel to make a case and are not a
representative sample; `peer_reviewed` follows the study's own published
inclusion criteria. Where more than one source cites the same drug,
`leaderboard.csv`'s `best_confirmed_spread` reports the single highest
figure (`all_confirmed_sources` lists every citation, not just the
winner).

### B2. Other documents reviewed for citations (data/sources/, no confirmed match)

These 14 files were parsed for drug-level PBM pricing figures using the
same extraction pass as B1, but did not yield a figure that met this
project's disambiguation rules (either the document contains no
drug-specific dollar/percentage figures at all -- e.g. it is
aggregate-only or narrative -- or a figure existed but did not crosswalk
to a specific Cost Plus catalog RxCUI without guessing; see
`METHODOLOGY.md`'s disambiguation rules). They are listed here for
completeness because the user's instruction was to document every file
in `data/sources/`, not only the ones that produced usable data.

| Local file | Title | Publisher | Date | URL |
|---|---|---|---|---|
| `arkansas_pbm_doi_exam.pdf` | Limited Scope Examination of Pharmacy Benefit Managers | Arkansas Insurance Department (examiners: Health Strategies / Regulatory Insurance Advisors) | Jul. 2020 | https://ncpa.org/sites/default/files/2020-10/ark-doi-pbm-mmc-examination.pdf |
| `aspe_pharma_supply_chain_margins_2024.pdf` | An Examination of Pharmaceutical Supply Chain Intermediary Margins in the U.S. Retail Channel | HHS Office of the Assistant Secretary for Planning and Evaluation (ASPE), prepared by Eastern Research Group | Sep. 27, 2024 | https://aspe.hhs.gov/ |
| `cbo_rx_spending_2022.pdf` | Prescription Drugs: Spending, Use, and Prices | Congressional Budget Office | Jan. 2022 | https://www.cbo.gov/publication/57772 |
| `dol_rxdc_2024.pdf` | Prescription Drug Spending, Pricing Trends, and Premiums in Private Health Insurance Plans (Report to Congress, required by the Consolidated Appropriations Act, 2021) | HHS/ASPE (joint report with DOL and Treasury under the RxDC data collection) | Nov. 2024 | https://aspe.hhs.gov/reports/prescription-drug-spending-pricing-trends-premiums |
| `gao_19_498_partd_pbm_2019.pdf` | Medicare Part D: Use of Pharmacy Benefit Managers and Efforts to Manage Drug Expenditures and Utilization | U.S. Government Accountability Office (GAO-19-498) | Jul. 2019 | https://www.gao.gov/products/gao-19-498 |
| `gao_pbm_state_2024.pdf` | Prescription Drugs: Selected States' Regulation of Pharmacy Benefit Managers | U.S. Government Accountability Office (GAO-24-106898) | Mar. 2024 | https://www.gao.gov/products/gao-24-106898 |
| `hhs_oig_dc_medicaid_spread.pdf` | The District of Columbia Has Taken Significant Steps to Ensure Accountability Over Amounts Managed Care Organizations Paid to Pharmacy Benefit Managers | HHS Office of Inspector General (A-03-20-00200) | Mar. 2023 | https://oig.hhs.gov/reports/all/2023/the-district-of-columbia-has-taken-significant-steps-to-ensure-accountability-over-amounts-managed-care-organizations-paid-to-pharmacy-benefit-managers/ |
| `hhs_oig_tmsis_2024.pdf` | Medicaid Managed Care: States Do Not Consistently Define or Validate Paid Amount Data for Drug Claims | HHS Office of Inspector General (OEI-03-20-00560) | May 2024 | https://oig.hhs.gov/reports/all/2024/medicaid-managed-care-states-do-not-consistently-define-or-validate-paid-amount-data-for-drug-claims/ |
| `house_oversight_pbm_2024.pdf` | The Role of Pharmacy Benefit Managers in Prescription Drug Markets | U.S. House Committee on Oversight and Accountability (Comer) | Jul. 23, 2024 | https://oversight.house.gov/wp-content/uploads/2024/07/PBM-Report-FINAL-with-Redactions.pdf |
| `kentucky_chfs_pbm_2019.pdf` | Kentucky Medicaid Releases Report on Pharmacy Benefit Program (press announcement) | Kentucky Cabinet for Health and Family Services | Feb. 19, 2019 | https://www.chfs.ky.gov/News/Documents/pharmacybenefit.pdf |
| `kentucky_full_report_black_box.pdf` | Medicaid Pharmacy Pricing: Opening the Black Box | Kentucky Cabinet for Health and Family Services, Dept. for Medicaid Services / Office of Health Data and Analytics | Feb. 19, 2019 | https://chfs.ky.gov/agencies/ohda/Documents1/CHFS_Medicaid_Pharmacy_Pricing.pdf |
| `ohio_auditor_pbm_2018.pdf` | Ohio's Medicaid Managed Care Pharmacy Services | Ohio Auditor of State (Dave Yost) | Aug. 2018 | https://ohioauditor.gov/auditsearch/Reports/2018/Medicaid_Pharmacy_Services_2018_Franklin.pdf |
| `pa_auditor_general_performrx_2022.pdf` | An Audit of the Pharmacy Benefit Manager Services for the Physical HealthChoices Medicaid Program in Pennsylvania -- PerformRx, LLC (audit period Jan-Dec 2022) | Pennsylvania Department of the Auditor General (DeFoor) | Aug. 21, 2024 | https://www.paauditor.gov/wp-content/uploads/spe133045DHSPerformRx082824.pdf |
| `senate_finance_insulin_report_2021.pdf` | Insulin: Examining the Factors Driving the Rising Cost of a Century Old Drug (Staff Report) | U.S. Senate Committee on Finance (Grassley, Wyden) | Jan. 2021 | https://www.finance.senate.gov/ |
| `wa_hca_dpt_annual_report_2023.pdf` | Drug Price Transparency (DPT) Program Annual Report 2023 | Washington State Health Care Authority | 2023 | https://www.hca.wa.gov/assets/billers-and-providers/drug-price-transparency-annual-report-2023.pdf |
| `wv_medicaid_rx_savings_2019.pdf` | Pharmacy Savings Report: Actuarial Assessment of the SFY18 Impact of Carving Out Prescription Drugs from Managed Care for West Virginia's Medicaid Program | West Virginia Dept. of Health and Human Resources, Bureau for Medical Services (prepared by Navigant Consulting) | Orig. Feb. 25, 2019; amended Apr. 2, 2019 | https://dhhr.wv.gov/bms/News/Documents/WV%20BMS%20Rx%20Savings%20Report%202019-04-02%20-%20FINAL.pdf |

(That is 16 rows above but B1 already separately lists
`ftc_2024_interim_staff_report.pdf`, `ftc_2025_second_interim_staff_report.pdf`,
`jama_health_forum_spread_pricing.pdf`, and
`maine_mhdo_rx_transparency_2022.pdf`, which are 4 of the 21 files
tracked in `data/sources/` -- 16 + 4 = 20. The 21st tracked file,
`drugpatentwatch_costplus_spread_pricing_blog.md`, is listed separately
below since it is not a PDF.)

| Local file | Title | Publisher | Date | URL |
|---|---|---|---|---|
| `drugpatentwatch_costplus_spread_pricing_blog.md` | Mark Cuban Cost Plus Drugs is Killing Spread Pricing (blog post) | DrugPatentWatch | 2026 | https://www.drugpatentwatch.com/ |

**License / terms for all of B1/B2**: every document above is a U.S.
federal or state government report (public domain / no copyright
restriction on government works), a court filing (court-created content
is public domain; PACER access fees do not create a copyright), a
peer-reviewed journal research letter (used here strictly under
fair-use/factual-extraction -- only the specific reported figures were
transcribed, not the article's text or figures reproduced), or a
publicly posted blog article. No paywalled or access-restricted content
was used. This project's own CC BY 4.0 license (`../LICENSE`) covers
only the *extracted figures and this project's citations of them*
(drug name, number, source, page, quote) -- it does not relicense the
underlying documents themselves.

### B3. Files present in the repository but not used in this dataset

Four additional PDFs were fetched into `costplus_suite/data/sources/` in
an earlier phase of this project's research (via the Wayback Machine,
from `mhdo.maine.gov` and `hca.wa.gov`) while checking whether Maine's
and Washington's *other* annual transparency reports (different years
than the ones in B1/B2) contained additional drug-level figures. They did
not, and were left uncommitted/untracked rather than deleted, since they
represent verified negative results, not part of this deposit's
evidentiary basis:

- `maine_mhdo_rx_transparency_2021_covering_cy2020.pdf`
- `maine_mhdo_rx_transparency_2024_covering_cy2022.pdf`
- `maine_mhdo_rx_transparency_2025_covering_cy2023-24.pdf`
- `wa_hca_dpt_annual_report_2022.pdf`

They are not part of this `dataset/` deposit and contribute nothing to
any figure in it.

---

## Change notice

Sections A1-A5 describe *live* data. NADAC refreshes weekly; Part D and
SDUD refresh on their own federal release schedules. Re-running this
pipeline at a later date will pull newer snapshots and will not
reproduce the exact dollar figures in this deposit bit-for-bit -- see
`REPRODUCE.md` for the snapshot date this deposit corresponds to.
Section B is static: the citation figures in `public_spreads_matched.csv`
do not change on re-run and are versioned by this deposit itself, not by
an upstream refresh schedule.
