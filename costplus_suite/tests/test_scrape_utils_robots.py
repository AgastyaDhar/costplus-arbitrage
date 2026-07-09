"""
Unit tests for shared.scrape_utils.RobotsRules -- a from-scratch robots.txt
checker written because urllib.robotparser gets both these real sites wrong.
Pinned against the ACTUAL robots.txt content of both sites this suite
scrapes (fetched during development), not hypothetical examples.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import scrape_utils  # noqa: E402

TRUMPRX_ROBOTS_TXT = """User-Agent: *
Allow: /
Disallow: /api/

Sitemap: https://trumprx.gov/sitemap.xml
"""

COSTPLUS_ROBOTS_TXT = """User-Agent: *
Allow: /
Allow: /medications/*
Allow: /providers/
Allow: /contact-your-doctor/
Allow: /faq/
Allow: /mission/
Allow: /contact/support/
Allow: /privacy/*
Allow: /terms/
Disallow: /not-found/*
Disallow: /account/*
Disallow: /cart/*
Disallow: /health-profile/*
Disallow: /prescription-confirmation/*
Disallow: /prescription-manager/*
Disallow: /callback/*
Disallow: /optin/
Disallow: /chat/*

Host: https://www.costplusdrugs.com
Sitemap: https://www.costplusdrugs.com/sitemap.xml
"""


class TestRobotsRulesTrumprx(unittest.TestCase):
    def setUp(self):
        self.rules = scrape_utils.RobotsRules()
        self.rules.parse(TRUMPRX_ROBOTS_TXT)

    def test_allows_browse_and_product_pages(self):
        self.assertTrue(self.rules.can_fetch("https://trumprx.gov/browse"))
        self.assertTrue(self.rules.can_fetch("https://trumprx.gov/p/lantus"))

    def test_disallows_api_despite_allow_slash_appearing_first_in_file(self):
        # The real bug: urllib.robotparser applies rules in file order and
        # stops at the first prefix match, so "Allow: /" (first in the file)
        # would win over the later, more specific "Disallow: /api/".
        self.assertFalse(self.rules.can_fetch("https://trumprx.gov/api/anything"))


class TestRobotsRulesCostplus(unittest.TestCase):
    def setUp(self):
        self.rules = scrape_utils.RobotsRules()
        self.rules.parse(COSTPLUS_ROBOTS_TXT)

    def test_allows_medications_and_sitemap(self):
        self.assertTrue(self.rules.can_fetch("https://www.costplusdrugs.com/medications/atorvastatin-20mg-tablet/"))
        self.assertTrue(self.rules.can_fetch("https://www.costplusdrugs.com/sitemap.xml"))

    def test_disallows_wildcard_paths_despite_bare_allow_slash(self):
        # A plain str.startswith("/account/*") would never match a real path
        # (no real path contains a literal asterisk) -- must translate the
        # wildcard to a regex, not treat it as a literal prefix.
        self.assertFalse(self.rules.can_fetch("https://www.costplusdrugs.com/account/settings"))
        self.assertFalse(self.rules.can_fetch("https://www.costplusdrugs.com/cart/checkout"))
        self.assertFalse(self.rules.can_fetch("https://www.costplusdrugs.com/health-profile/edit"))


class TestRobotsRulesEdgeCases(unittest.TestCase):
    def test_no_rules_allows_everything(self):
        rules = scrape_utils.RobotsRules()
        self.assertTrue(rules.can_fetch("https://example.com/anything"))

    def test_disallow_all_blocks_everything(self):
        rules = scrape_utils.RobotsRules()
        rules.disallow_all = True
        self.assertFalse(rules.can_fetch("https://example.com/anything"))

    def test_dollar_anchor_matches_exact_end(self):
        rules = scrape_utils.RobotsRules()
        rules.parse("User-Agent: *\nAllow: /\nDisallow: /page$")
        self.assertFalse(rules.can_fetch("https://example.com/page"))
        self.assertTrue(rules.can_fetch("https://example.com/page/more"))

    def test_other_user_agent_groups_are_ignored(self):
        rules = scrape_utils.RobotsRules()
        rules.parse("User-Agent: Googlebot\nDisallow: /\n\nUser-Agent: *\nAllow: /\n")
        self.assertTrue(rules.can_fetch("https://example.com/anything"))


if __name__ == "__main__":
    unittest.main()
