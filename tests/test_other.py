"""Testy pro stats, ui, robots_check, sitemap parser."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stats        import compute_stats
from ui           import is_valid_url, normalize_url_input
from robots_check import (_parse_robots, _get_relevant_disallows,
                          CRITICAL_PREFIX)
from sitemap      import _parse_sitemap_xml
from issues       import Issue, IssueType


# ── Stats ────────────────────────────────────────────────────────────────────

class TestStats(unittest.TestCase):
    def test_empty_results(self):
        s = compute_stats([])
        self.assertEqual(s.total, 0)
        self.assertEqual(s.score, 0)

    def test_all_ok(self):
        results = [
            {"w3c_category": "ok", "structure_issues": []},
            {"w3c_category": "ok", "structure_issues": []},
        ]
        s = compute_stats(results)
        self.assertEqual(s.score, 100)
        self.assertEqual(s.w3c_ok, 2)

    def test_all_bad(self):
        """Když má každá stránka mnoho kritických problémů, skóre je 0."""
        # Penalizace: -25 -20 -15 -15 -15 -10 = -100 → clamp na 0
        critical_issues = [
            Issue(type=IssueType.NOINDEX),                                    # -25
            Issue(type=IssueType.FORBIDDEN_CONTENT, items=['"lorem ipsum"']), # -20
            Issue(type=IssueType.MISSING_H1),                                 # -15
            Issue(type=IssueType.MISSING_META_DESC),                          # -15
            Issue(type=IssueType.MISSING_VIEWPORT),                           # -15
            Issue(type=IssueType.MISSING_LANG),                               # -10
        ]
        results = [
            {"w3c_category": "error", "w3c_errors": [],
             "structure_issues": critical_issues},
            {"w3c_category": "error", "w3c_errors": [],
             "structure_issues": critical_issues},
        ]
        s = compute_stats(results)
        self.assertEqual(s.score, 0)
        self.assertEqual(s.w3c_err, 2)

    def test_half_bad(self):
        """Jedna OK stránka + jedna na 0 = průměr 50."""
        critical_issues = [
            Issue(type=IssueType.NOINDEX),                                    # -25
            Issue(type=IssueType.FORBIDDEN_CONTENT, items=['"lorem ipsum"']), # -20
            Issue(type=IssueType.MISSING_H1),                                 # -15
            Issue(type=IssueType.MISSING_META_DESC),                          # -15
            Issue(type=IssueType.MISSING_VIEWPORT),                           # -15
            Issue(type=IssueType.MISSING_LANG),                               # -10
        ]
        results = [
            {"w3c_category": "ok", "structure_issues": []},
            {"w3c_category": "error", "w3c_errors": [],
             "structure_issues": critical_issues},
        ]
        s = compute_stats(results)
        self.assertEqual(s.score, 50)

    def test_warnings_dont_reduce_score(self):
        results = [
            {"w3c_category": "ok",      "structure_issues": []},
            {"w3c_category": "warning", "structure_issues": []},
        ]
        s = compute_stats(results)
        self.assertEqual(s.score, 100)
        self.assertEqual(s.w3c_warn, 1)

    def test_validator_error_reduces_score(self):
        """Nedostupná stránka je 'špatná'."""
        results = [
            {"w3c_category": "ok",              "structure_issues": []},
            {"w3c_category": "validator_error", "structure_issues": [],
             "w3c_error_msg": "timeout"},
        ]
        s = compute_stats(results)
        self.assertEqual(s.score, 50)
        self.assertEqual(s.w3c_failed, 1)


# ── UI (URL validace) ────────────────────────────────────────────────────────

class TestUrlValidation(unittest.TestCase):
    def test_valid_urls(self):
        for url in [
            "https://example.cz/",
            "http://example.com",
            "https://www.google.com/path/to/page",
            "https://sub.domain.co.uk/",
            "https://example.cz/page?query=1",
        ]:
            self.assertTrue(is_valid_url(url), f"měla by být platná: {url}")

    def test_invalid_urls(self):
        for url in [
            "neni-to-url",
            "example",
            "https://",
            "http://nodomain",
            "ftp://example.cz/",
        ]:
            self.assertFalse(is_valid_url(url), f"měla by být neplatná: {url}")

    def test_normalize_adds_https(self):
        self.assertEqual(normalize_url_input("example.cz"), "https://example.cz")
        self.assertEqual(normalize_url_input("www.example.cz"), "https://www.example.cz")

    def test_normalize_preserves_http(self):
        self.assertEqual(normalize_url_input("http://example.cz"), "http://example.cz")
        self.assertEqual(normalize_url_input("https://example.cz"), "https://example.cz")

    def test_normalize_strips_whitespace(self):
        self.assertEqual(normalize_url_input("  https://example.cz  "), "https://example.cz")


# ── robots.txt parser ────────────────────────────────────────────────────────

class TestRobotsParser(unittest.TestCase):
    def test_simple_disallow(self):
        content = """
        User-agent: *
        Disallow: /admin/
        """
        parsed = _parse_robots(content)
        self.assertIn("/admin/", parsed.get("*", []))

    def test_multiple_user_agents_one_block(self):
        """Several User-agent over one rules block — všechny dostanou stejná pravidla."""
        content = """
        User-agent: Googlebot
        User-agent: Bingbot
        Disallow: /private/
        """
        parsed = _parse_robots(content)
        self.assertIn("/private/", parsed.get("googlebot", []))
        self.assertIn("/private/", parsed.get("bingbot", []))

    def test_comments_ignored(self):
        content = """
        # Komentář
        User-agent: *   # inline komentář
        Disallow: /hidden/
        """
        parsed = _parse_robots(content)
        self.assertIn("/hidden/", parsed.get("*", []))

    def test_relevant_disallows_includes_star(self):
        parsed = {
            "googlebot": ["/gb/"],
            "*":         ["/all/"],
            "bingbot":   ["/bg/"],   # nesmí se dostat do výsledku
        }
        d = _get_relevant_disallows(parsed)
        self.assertIn("/gb/", d)
        self.assertIn("/all/", d)
        self.assertNotIn("/bg/", d)


# ── Disallow: / detekce (kritická chyba) ─────────────────────────────────────

class TestDisallowAll(unittest.TestCase):
    """Detekce 'Disallow: /' která blokuje celý web pro Googlebota."""

    def test_disallow_root_for_star(self):
        """Disallow: / pro * = celý web zakázán pro všechny boty."""
        content = """
        User-agent: *
        Disallow: /
        """
        parsed = _parse_robots(content)
        disallows = _get_relevant_disallows(parsed)
        self.assertIn("/", disallows)

    def test_disallow_root_for_googlebot(self):
        """Disallow: / specificky pro Googlebot."""
        content = """
        User-agent: Googlebot
        Disallow: /
        """
        parsed = _parse_robots(content)
        disallows = _get_relevant_disallows(parsed)
        self.assertIn("/", disallows)

    def test_disallow_subpath_not_root(self):
        """Disallow: /admin/ nesmí být zaměněno s Disallow: / – jen subdir."""
        content = """
        User-agent: *
        Disallow: /admin/
        """
        parsed = _parse_robots(content)
        disallows = _get_relevant_disallows(parsed)
        # Subdir je v listu, ale samotné lomítko ne
        self.assertIn("/admin/", disallows)
        self.assertNotIn("/", disallows)

    def test_critical_prefix_constant(self):
        """CRITICAL_PREFIX je definovaný a začíná hranatou závorkou."""
        self.assertTrue(CRITICAL_PREFIX.startswith("["))
        self.assertIn("KRITICK", CRITICAL_PREFIX)


# ── Sitemap parser ───────────────────────────────────────────────────────────

class TestSitemapParser(unittest.TestCase):
    def test_urlset(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.cz/a</loc></url>
            <url><loc>https://example.cz/b</loc></url>
        </urlset>"""
        pages, sitemaps = _parse_sitemap_xml(xml)
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(sitemaps), 0)
        self.assertIn("https://example.cz/a", pages)

    def test_sitemapindex(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.cz/sitemap1.xml</loc></sitemap>
            <sitemap><loc>https://example.cz/sitemap2.xml</loc></sitemap>
        </sitemapindex>"""
        pages, sitemaps = _parse_sitemap_xml(xml)
        self.assertEqual(len(pages), 0)
        self.assertEqual(len(sitemaps), 2)

    def test_malformed_xml_fallback(self):
        """Porušený XML nesmí vyhodit vyjímku."""
        xml = "<urlset><url><loc>https://example.cz/broken"
        pages, sitemaps = _parse_sitemap_xml(xml)
        # Jen že neselže; obsah je whatever
        self.assertIsInstance(pages, list)
        self.assertIsInstance(sitemaps, list)


if __name__ == "__main__":
    unittest.main()