"""
Unit tests for shared.scrape_utils.PoliteFetcher's 429 backoff and
anti-automation-challenge detection (added for Task 3's resumable fetch of
missing product-page variants). A fake requests.Session stands in so no
network or real sleeping is involved.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import scrape_utils  # noqa: E402


def _fetcher(tmp_path: Path) -> scrape_utils.PoliteFetcher:
    f = scrape_utils.PoliteFetcher(
        base_url="https://example.test", user_agent="test-agent", cache_dir=tmp_path,
        min_delay_seconds=0.0, max_delay_seconds=0.0,
    )
    f._robots = scrape_utils.RobotsRules()  # allow_all=False, no rules -> can_fetch defaults True
    return f


def _resp(status_code, text="", headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    r.headers = headers or {}
    return r


class TestChallengeDetection(unittest.TestCase):
    def test_detects_cloudflare_turnstile_page(self):
        self.assertTrue(scrape_utils._looks_like_challenge_page(403, "Just a moment... challenges.cloudflare.com"))

    def test_plain_403_is_not_a_challenge(self):
        self.assertFalse(scrape_utils._looks_like_challenge_page(403, "<html>Forbidden</html>"))

    def test_non_403_is_never_a_challenge(self):
        self.assertFalse(scrape_utils._looks_like_challenge_page(200, "Just a moment... challenges.cloudflare.com"))


class TestPoliteFetcherGet(unittest.TestCase):
    def test_raises_challenge_detected_and_does_not_cache(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = _fetcher(Path(d))
            session = MagicMock()
            session.get.return_value = _resp(403, "Just a moment... challenges.cloudflare.com")
            with patch.object(f, "_get_session", return_value=session):
                with self.assertRaises(scrape_utils.ChallengeDetectedError):
                    f.get("/p/whatever")
            self.assertFalse(f.cache_path("https://example.test/p/whatever").exists())

    def test_retries_on_429_then_succeeds(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = _fetcher(Path(d))
            f.max_429_retries = 3
            session = MagicMock()
            session.get.side_effect = [_resp(429, headers={"Retry-After": "0"}), _resp(200, "real content")]
            with patch.object(f, "_get_session", return_value=session), patch("time.sleep"):
                result = f.get("/p/whatever")
            self.assertEqual(result, "real content")
            self.assertEqual(session.get.call_count, 2)

    def test_gives_up_after_max_429_retries(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = _fetcher(Path(d))
            f.max_429_retries = 2
            session = MagicMock()
            session.get.return_value = _resp(429, headers={"Retry-After": "0"})
            with patch.object(f, "_get_session", return_value=session), patch("time.sleep"):
                result = f.get("/p/whatever")
            self.assertIsNone(result)
            self.assertEqual(session.get.call_count, 3)  # initial + 2 retries

    def test_plain_404_returns_none_without_retry(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = _fetcher(Path(d))
            session = MagicMock()
            session.get.return_value = _resp(404, "not found")
            with patch.object(f, "_get_session", return_value=session), patch("time.sleep"):
                result = f.get("/p/whatever")
            self.assertIsNone(result)
            self.assertEqual(session.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
