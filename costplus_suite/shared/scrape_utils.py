"""
Shared infrastructure for this suite's polite live-site scrapers
(fetch/costplus_html_scraper.py, fetch/trumprx.py). Both target sites happen to
be Next.js App Router apps that stream page data as `self.__next_f.push([1,
"<escaped JSON string>"])` calls rather than the classic `__NEXT_DATA__`
blob -- the JSON payloads of interest sit *inside* that decoded text, still
escaped one level further (they were JSON.stringify'd again before being
embedded). The scanning helpers below do this generically via character-
level scanning rather than regex, so they don't break on brackets/quotes
that happen to appear in surrounding content, and PoliteFetcher gives every
scraper the same robots.txt + rate-limit + disk-cache behavior instead of
each reimplementing it slightly differently.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import requests


def scan_quoted_string(text: str, start: int) -> tuple[str, int]:
    """text[start] must be an opening `"`. Returns (decoded value, index just
    past the matching unescaped closing quote), using json.loads to unescape."""
    if text[start] != '"':
        raise ValueError(f"expected '\"' at index {start}")
    i = start + 1
    escaped = False
    while i < len(text):
        c = text[i]
        if escaped:
            escaped = False
        elif c == "\\":
            escaped = True
        elif c == '"':
            return json.loads(text[start : i + 1]), i + 1
        i += 1
    raise ValueError("unterminated quoted string")


def scan_balanced(text: str, start: int) -> tuple[str, int]:
    """text[start] must be `{` or `[`. Returns (raw substring, index just past
    the matching close bracket), respecting string literals so brackets
    inside quoted text don't confuse the depth count."""
    open_ch = text[start]
    close_ch = {"{": "}", "[": "]"}[open_ch]
    depth = 0
    i = start
    in_string = False
    escaped = False
    while i < len(text):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1], i + 1
        i += 1
    raise ValueError(f"unbalanced '{open_ch}...{close_ch}'")


def find_next_f_payloads(html: str) -> list[str]:
    """Every `self.__next_f.push([1, "..."])` call's decoded (one level
    unescaped) string argument, in document order. A single large JSON value
    (e.g. a big catalog array) can be split across several push calls by
    Next.js's streaming -- concatenate all of them (`"".join(...)`) before
    searching for a specific key if a per-payload search comes up empty."""
    marker = "self.__next_f.push([1,"
    payloads = []
    idx = 0
    while True:
        idx = html.find(marker, idx)
        if idx == -1:
            break
        qstart = html.find('"', idx + len(marker))
        if qstart == -1:
            break
        try:
            decoded, end = scan_quoted_string(html, qstart)
        except ValueError:
            break
        payloads.append(decoded)
        idx = end
    return payloads


class RobotsRules:
    """Minimal, CORRECT (longest-matching-prefix-wins, per the de facto
    robots.txt standard -- RFC 9309 section 2.2.2) robots.txt checker for a
    single `User-agent: *` group -- the only group this suite's scrapers
    ever need, since each identifies as one descriptive browser UA.

    Deliberately NOT urllib.robotparser: that implementation applies rules
    in file order and stops at the FIRST prefix match rather than the
    longest one. trumprx.gov's real robots.txt is `Allow: /` followed by
    `Disallow: /api/` -- urllib.robotparser reads that as "Allow: / matches
    first, so allow everything," silently ignoring the Disallow entirely.
    Confirmed directly against trumprx.gov's live robots.txt during
    development. Longest-prefix-match reads it correctly: /api/* is more
    specific than /, so Disallow wins there and Allow wins everywhere else.
    """

    def __init__(self):
        self.rules: list[tuple[str, bool]] = []  # (path_prefix, is_allow)
        self.disallow_all = False
        self.allow_all = False

    def parse(self, text: str) -> None:
        applies = False
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                applies = value == "*"
            elif applies and field == "allow" and value:
                self.rules.append((value, True))
            elif applies and field == "disallow" and value:
                self.rules.append((value, False))

    @staticmethod
    def _pattern_matches(path: str, pattern: str) -> bool:
        """robots.txt patterns support `*` (any sequence) and a trailing `$`
        (end-of-string anchor) -- e.g. costplusdrugs.com's own robots.txt
        uses `/account/*`, which a plain `str.startswith("/account/*")`
        would never match (no real path contains a literal `*`). Translate to
        a regex rather than assuming a bare prefix."""
        ends_anchor = pattern.endswith("$")
        body = pattern[:-1] if ends_anchor else pattern
        regex = "^" + ".*".join(re.escape(part) for part in body.split("*"))
        if ends_anchor:
            regex += "$"
        return re.match(regex, path) is not None

    def can_fetch(self, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all or not self.rules:
            return True
        path = urlsplit(url).path or "/"
        best_len, best_allow = -1, True
        for prefix, is_allow in self.rules:
            if self._pattern_matches(path, prefix) and len(prefix) > best_len:
                best_len, best_allow = len(prefix), is_allow
        return best_allow


class ChallengeDetectedError(Exception):
    """Raised when a response looks like an explicit anti-automation
    challenge (e.g. Cloudflare's interactive "Just a moment..." Turnstile
    page), never on a plain HTTP error or rate-limit response. Callers should
    stop the run and report this rather than retry or work around it."""


def _looks_like_challenge_page(status_code: int, text: str) -> bool:
    if status_code != 403:
        return False
    return "Just a moment" in text and "challenges.cloudflare.com" in text


class PoliteFetcher:
    """Disk-cached, robots.txt-gated, rate-limited GET for one site. Never
    raises on disallow/404/network failure -- returns None so a caller can
    skip and keep a coverage tally honest rather than aborting a whole run.
    The one exception is ChallengeDetectedError (see above): that's a signal
    to stop, not a fetch failure to shrug off."""

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        cache_dir: Path,
        min_delay_seconds: float = 2.0,
        max_delay_seconds: float = 3.0,
        max_429_retries: int = 5,
    ):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.cache_dir = cache_dir
        self.min_delay = min_delay_seconds
        self.max_delay = max_delay_seconds
        self.max_429_retries = max_429_retries
        self._session: Optional[requests.Session] = None
        self._robots: Optional[RobotsRules] = None
        self._last_request_ts = 0.0

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": self.user_agent, "Accept-Language": "en-US,en;q=0.9"})
        return self._session

    def _get_robots(self) -> RobotsRules:
        if self._robots is None:
            rules = RobotsRules()
            # Fetched through our own browser-UA session, not a bare urllib
            # default opener: the latter gets a 403 from the same CDN bot-
            # defenses regular page fetches do (confirmed live on both
            # costplusdrugs.com and trumprx.gov), which would be
            # misread as "robots.txt says disallow everything" for a site
            # whose real robots.txt explicitly allows these paths.
            try:
                resp = self._get_session().get(f"{self.base_url}/robots.txt", timeout=30)
                if resp.status_code == 200:
                    rules.parse(resp.text)
                elif resp.status_code in (401, 403):
                    rules.disallow_all = True
                else:
                    rules.allow_all = True  # no robots.txt (e.g. 404) conventionally means no restrictions
            except requests.RequestException as exc:  # pragma: no cover - network failure path
                print(f"[scrape_utils] WARNING: could not read {self.base_url}/robots.txt ({exc}); refusing to scrape")
                rules.disallow_all = True
            self._robots = rules
        return self._robots

    def cache_path(self, url: str) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{digest}.html"

    def get(self, path_or_url: str, force_refresh: bool = False) -> Optional[str]:
        """Returns page text, or None on a skippable failure (robots
        disallow, 404, network error, or 429s exhausted). Raises
        ChallengeDetectedError if a response looks like an explicit
        anti-automation challenge -- that's a stop-and-report signal, not
        something to skip past."""
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        cache_path = self.cache_path(url)
        if cache_path.exists() and not force_refresh:
            return cache_path.read_text(encoding="utf-8")

        robots = self._get_robots()
        if not robots.can_fetch(url):
            print(f"[scrape_utils] robots.txt disallows {url}, skipping")
            return None

        attempt = 0
        while True:
            elapsed = time.monotonic() - self._last_request_ts
            delay = random.uniform(self.min_delay, self.max_delay)
            if elapsed < delay:
                time.sleep(delay - elapsed)

            try:
                resp = self._get_session().get(url, timeout=30)
                self._last_request_ts = time.monotonic()
            except requests.RequestException as exc:  # pragma: no cover - network failure path
                self._last_request_ts = time.monotonic()
                print(f"[scrape_utils] {url} -> request failed ({exc}), skipping")
                return None

            if _looks_like_challenge_page(resp.status_code, resp.text):
                raise ChallengeDetectedError(
                    f"{url} -> explicit anti-automation challenge detected (Cloudflare Turnstile); stopping."
                )

            if resp.status_code == 429 and attempt < self.max_429_retries:
                attempt += 1
                retry_after = resp.headers.get("Retry-After")
                try:
                    backoff = float(retry_after) if retry_after else 0.0
                except ValueError:
                    backoff = 0.0
                backoff = max(backoff, min(60.0, 2.0 ** attempt))  # exponential, capped at 60s, floor'd by Retry-After
                print(f"[scrape_utils] {url} -> HTTP 429, backing off {backoff:.0f}s (attempt {attempt}/{self.max_429_retries})")
                time.sleep(backoff)
                continue

            if resp.status_code != 200:
                print(f"[scrape_utils] {url} -> HTTP {resp.status_code}, skipping")
                return None

            cache_path.write_text(resp.text, encoding="utf-8")
            return resp.text
