"""
Medicare Part B ASP (Average Sales Price) Pricing Files, CMS, quarterly.
https://www.cms.gov/medicare/payment/part-b-drugs/asp-pricing-files

Unlike NADAC/Part D/SDUD, these files are not in a DCAT/metastore catalog --
they are plain zips linked from a landing page, and even the landing page URL
itself has moved before (the old /medicare/payment/fee-schedules/drugs/... path
404s as of this build; the current one is /medicare/payment/part-b-drugs/...).
So discovery here means: fetch the current landing page HTML, and take the
*first* zip link matching the payment-limit / crosswalk naming pattern (the
page lists newest quarter first) -- never a hardcoded quarter or filename.

METHODOLOGY NOTE (per HARD CONSTRAINT, repeated in METHODOLOGY.md): the
"Payment Limit" column in this file is ASP + 6%, and ASP itself is a
volume-weighted average of manufacturers' quarterly sales net of certain
concessions -- it already has rebate-like averaging baked in by design before
it ever reaches this file. That is different from, and not comparable to, a
single transaction's net price. We surface it as "ASP-based payment limit",
never relabel it "net", and note in the module output that ASP's own
averaging is a limitation, not a net-price estimate we are computing.
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from shared import http_cache  # noqa: E402

LANDING_PAGE = "https://www.cms.gov/medicare/payment/part-b-drugs/asp-pricing-files"


def _find_zip_links(html: str, name_pattern: str) -> list[str]:
    hrefs = re.findall(r'href="([^"]+\.zip)"', html, re.I)
    matches = [h for h in hrefs if re.search(name_pattern, h, re.I)]
    # dedupe, keep page order (page lists newest quarter first)
    seen, out = set(), []
    for h in matches:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def discover_latest_asp(force_refresh: bool = False) -> dict:
    resp = http_cache.SESSION.get(LANDING_PAGE, timeout=http_cache.REQUEST_TIMEOUT)
    resp.raise_for_status()
    html = resp.text

    payment_links = _find_zip_links(html, r"payment-limit-files|asp-pricing")
    crosswalk_links = _find_zip_links(html, r"ndc-hcpcs-crosswalk")
    if not payment_links or not crosswalk_links:
        raise RuntimeError(
            f"Could not find ASP payment-limit or NDC-HCPCS crosswalk zip links on {LANDING_PAGE}. "
            "CMS may have restructured the page -- inspect it manually."
        )

    payment_url = payment_links[0]
    crosswalk_url = crosswalk_links[0]
    if payment_url.startswith("/"):
        payment_url = "https://www.cms.gov" + payment_url
    if crosswalk_url.startswith("/"):
        crosswalk_url = "https://www.cms.gov" + crosswalk_url

    info = {"landing_page": LANDING_PAGE, "payment_limit_zip": payment_url, "crosswalk_zip": crosswalk_url}
    print(f"[fetch.asp] Resolved ASP payment limit file: {payment_url}")
    print(f"[fetch.asp] Resolved ASP NDC-HCPCS crosswalk: {crosswalk_url}")
    http_cache.cache_resolved_id("asp", info)
    return info


def _extract_csv_by_name(zip_path: Path, name_pattern: str) -> str:
    with zipfile.ZipFile(zip_path) as zf:
        candidates = [n for n in zf.namelist() if n.lower().endswith(".csv") and re.search(name_pattern, n, re.I)]
        if not candidates:
            raise RuntimeError(f"No CSV matching {name_pattern!r} inside {zip_path} (contents: {zf.namelist()})")
        with zf.open(candidates[0]) as f:
            return f.read().decode("latin1")


def _find_header_row(text: str, must_contain: list[str]) -> int:
    for i, line in enumerate(text.splitlines()):
        upper = line.upper()
        if all(token.upper() in upper for token in must_contain):
            return i
    raise RuntimeError(f"Could not find a header row containing {must_contain}")


def load_asp_payment_limits(force_refresh: bool = False) -> pd.DataFrame:
    """Return a DataFrame indexed by HCPCS code with payment_limit (dollars
    per HCPCS billing unit -- ASP + 6%) and short_description."""
    info = discover_latest_asp(force_refresh=force_refresh)
    zip_path = http_cache.cached_get_file(
        info["payment_limit_zip"], subdir="asp", filename="asp_payment_limit.zip", force_refresh=force_refresh
    )
    text = _extract_csv_by_name(zip_path, r"payment limit file")
    header_row = _find_header_row(text, ["HCPCS Code", "Payment Limit"])
    df = pd.read_csv(io.StringIO(text), skiprows=header_row)
    df = df.rename(
        columns={
            "HCPCS Code": "hcpcs_code",
            "Short Description": "short_description",
            "HCPCS Code Dosage": "hcpcs_dosage",
            "Payment Limit": "payment_limit",
        }
    )
    df["hcpcs_code"] = df["hcpcs_code"].astype(str).str.strip()
    df["payment_limit"] = pd.to_numeric(df["payment_limit"], errors="coerce")
    df = df.dropna(subset=["hcpcs_code", "payment_limit"])
    df = df[df["hcpcs_code"] != "nan"].set_index("hcpcs_code")
    print(f"[fetch.asp] Loaded {len(df):,} HCPCS payment limit rows")
    return df[["short_description", "hcpcs_dosage", "payment_limit"]]


def load_ndc_hcpcs_crosswalk(force_refresh: bool = False) -> pd.DataFrame:
    """Return a DataFrame with ndc (11-digit normalized), hcpcs_code,
    billunits, billunitspkg, drug_name -- the ASP-specific crosswalk (not
    AWP/OPPS/PrEP, which ship in the same zip under different filenames)."""
    info = discover_latest_asp(force_refresh=force_refresh)
    zip_path = http_cache.cached_get_file(
        info["crosswalk_zip"], subdir="asp", filename="asp_ndc_hcpcs_crosswalk.zip", force_refresh=force_refresh
    )
    text = _extract_csv_by_name(zip_path, r"^(?!.*(AWP|OPPS|PrEP)).*ASP NDC-HCPCS Crosswalk")
    header_row = _find_header_row(text, ["NDC", "HCPCS dosage"])
    df = pd.read_csv(io.StringIO(text), skiprows=header_row, dtype=str)

    code_col = next((c for c in df.columns if c.strip().upper().endswith("_CODE")), None)
    if code_col is None:
        raise RuntimeError(f"Could not find a *_CODE column in ASP crosswalk; columns were {list(df.columns)}")

    df = df.rename(
        columns={
            code_col: "hcpcs_code",
            "NDC": "ndc_raw",
            "Drug Name": "drug_name",
            "LABELER NAME": "labeler_name",
            "BILLUNITS": "billunits",
            "BILLUNITSPKG": "billunitspkg",
            "PKG SIZE": "pkg_size",
            "PKG QTY": "pkg_qty",
        }
    )
    df["ndc"] = df["ndc_raw"].str.replace("-", "", regex=False).str.zfill(config.NDC_LENGTH)
    for col in ("billunits", "billunitspkg", "pkg_size", "pkg_qty"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["ndc", "hcpcs_code"])
    print(f"[fetch.asp] Loaded {len(df):,} NDC-HCPCS ASP crosswalk rows")
    return df[["ndc", "hcpcs_code", "drug_name", "labeler_name", "billunits", "billunitspkg", "pkg_size", "pkg_qty"]]


def get_asp_per_billunit(ndcs: list[str], force_refresh: bool = False) -> pd.DataFrame:
    """For a list of NDCs, return their HCPCS code(s) and ASP-based payment
    limit per billing unit, joined via the ASP NDC-HCPCS crosswalk. A given
    NDC's "billing unit" (e.g. per 10mg) is NOT guaranteed to equal NADAC's
    Pricing Unit (e.g. per mL) for the same drug -- callers must reconcile
    units explicitly (billunitspkg gives billing units per package) before
    comparing to any other per-unit price in this suite.
    """
    xwalk = load_ndc_hcpcs_crosswalk(force_refresh=force_refresh)
    limits = load_asp_payment_limits(force_refresh=force_refresh)

    ndc_set = {str(n).zfill(config.NDC_LENGTH) for n in ndcs}
    hits = xwalk[xwalk["ndc"].isin(ndc_set)].merge(limits, on="hcpcs_code", how="left")
    print(f"[fetch.asp] {len(hits)} of {len(ndc_set)} requested NDCs found in ASP crosswalk")
    return hits
