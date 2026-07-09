"""
Disk-cached HTTP helpers shared by every fetch/*.py and shared/crosswalk.py.

Every network pull in this suite goes through here so reruns are offline and
reproducible, and so dataset resolution is auditable: cache_resolved_id()
appends what a discovery call actually found (dataset identifier, title,
modified date, download URL) to cache/resolved_ids.json.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

USER_AGENT = "costplus-arbitrage-suite/0.1 (research use; contact via repo)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

REQUEST_TIMEOUT = 60
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2


def _cache_path_for_url(url: str, subdir: str, suffix: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    d = config.CACHE_DIR / subdir
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}{suffix}"


def _get_with_retry(url: str, **kwargs) -> requests.Response:
    last_exc = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = SESSION.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:  # pragma: no cover
            last_exc = exc
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise last_exc


def cached_get_json(url: str, subdir: str, force_refresh: bool = False) -> dict:
    """GET a JSON endpoint, caching the raw response body to disk."""
    cache_path = _cache_path_for_url(url, subdir, ".json")
    if cache_path.exists() and not force_refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    resp = _get_with_retry(url)
    cache_path.write_text(resp.text, encoding="utf-8")
    return resp.json()


def cached_get_file(url: str, subdir: str, filename: str, force_refresh: bool = False) -> Path:
    """Download a file to a stable, human-readable cache path (used for large
    CSVs, which are too big to key by URL hash alone -- we want the filename
    to say what it is when someone looks in cache/). Streams to disk so large
    files (SDUD is ~500MB) don't have to fit in memory twice."""
    d = config.CACHE_DIR / subdir
    d.mkdir(parents=True, exist_ok=True)
    dest = d / filename
    if dest.exists() and not force_refresh:
        return dest
    with SESSION.get(url, timeout=REQUEST_TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
        tmp.replace(dest)
    return dest


def cache_resolved_id(dataset_key: str, info: dict) -> None:
    """Append/update what we resolved for a dataset into cache/resolved_ids.json
    so a run's provenance (which distribution, which day) is auditable later."""
    existing = {}
    if config.RESOLVED_IDS_PATH.exists():
        existing = json.loads(config.RESOLVED_IDS_PATH.read_text(encoding="utf-8"))
    existing[dataset_key] = info
    config.RESOLVED_IDS_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
