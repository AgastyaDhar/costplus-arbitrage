"""
Central configuration for the Cost Plus arbitrage suite.

Resource IDs for CMS/Medicaid datasets are NEVER hardcoded here. They rotate
(NADAC publishes a new distribution every week; Part D and SDUD roll to new
identifiers every release). Instead, each fetch/*.py module discovers the
current distribution at runtime via the hosting platform's metadata API and
writes what it resolved into cache/resolved_ids.json, which this module reads
back so a run's provenance is inspectable after the fact.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
CACHE_DIR = ROOT_DIR / "cache"
DATA_DIR = ROOT_DIR.parent / "data"
OUTPUT_DIR = ROOT_DIR / "output"
RESOLVED_IDS_PATH = CACHE_DIR / "resolved_ids.json"

for _d in (CACHE_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# API endpoints (fixed NIH/CMS REST APIs -- these are stable service URLs,
# not rotating dataset resource IDs, so they are fine to hardcode).
# ---------------------------------------------------------------------------
RXNAV_BASE = "https://rxnav.nlm.nih.gov/REST"
MEDICAID_METASTORE_BASE = "https://data.medicaid.gov/api/1/metastore/schemas/dataset/items"
CMS_DATA_METASTORE_BASE = "https://data.cms.gov/data.json"

# ---------------------------------------------------------------------------
# Methodology switches
# ---------------------------------------------------------------------------
GENERICS_ONLY = True  # headline overpayment numbers are restricted to generics
                       # (Part D spend is gross of rebates; brand gap would be
                       # overstated). See METHODOLOGY.md.

COSTPLUS_MARKUP = 1.15  # Cost Plus's published 15% markup on acquisition cost

# ---------------------------------------------------------------------------
# Module toggles (Phase 2 scaffolding)
# ---------------------------------------------------------------------------
MODULES_ENABLED = {
    "a_arbitrage": True,
    "b_intelligence": False,
    "c_list_vs_net": False,
    "d_employer_calculator": False,
    "e_brand_trumprx": False,
    "f_oncology": False,
}

# NDC canonical form: CMS/NADAC and RxNav both use 11-digit, no-dash NDCs.
NDC_LENGTH = 11
