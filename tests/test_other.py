"""Testy pro stats, ui, robots_check, sitemap parser a Java version check."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stats         import compute_stats
from ui            import is_valid_url, normalize_url_input
from robots_check  import (_parse_robots, _get_relevant_disallows,
                           CRITICAL_PREFIX)
from sitemap       import _parse_sitemap_xml
from issues        import Issue, IssueType
from validator_w3c import check_java_version


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
        critical_issues = [
            Issue(type=IssueType.NOINDEX),
            Issue(type=IssueType.FORBIDDEN_CONTENT, items=['"lorem ipsum"']),
            Issue(type=IssueType.MISSING_H1),
            Issue(type=IssueType.MISSING_META_DESC),
            Issue(type=IssueType.MISSING_VIEWPORT),
            Issue(type=IssueType.MISSING_LANG),
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
        for url in ["https://example.cz/", "http://example.com",
                    "https://www.google.com/path/to/page",
                    "https://sub.domain.co.uk/",
                    "https://example.cz/page?query=1"]:
            self.assertTrue(is_valid_url(url), f"měla by být platná: {url}")

    def test_invalid_urls(self):
        for url in ["neni-to-url", "example", "https://",
                    "http://nodomain", "ftp://example.cz/"]:
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
        content = "User-agent: *\nDisallow: /admin/\n"
        parsed = _parse_robots(content)
        self.assertIn("/admin/", parsed.get("*", []))

    def test_multiple_user_agents_one_block(self):
        content = "User-agent: Googlebot\nUser-agent: Bingbot\nDisallow: /private/\n"
        parsed = _parse_robots(content)
        self.assertIn("/private/", parsed.get("googlebot", []))
        self.assertIn("/private/", parsed.get("bingbot", []))

    def test_comments_ignored(self):
        content = "# Komentář\nUser-agent: *   # inline komentář\nDisallow: /hidden/\n"
        parsed = _parse_robots(content)
        self.assertIn("/hidden/", parsed.get("*", []))

    def test_relevant_disallows_includes_star(self):
        parsed = {
            "googlebot": ["/gb/"],
            "*":         ["/all/"],
            "bingbot":   ["/bg/"],
        }
        d = _get_relevant_disallows(parsed)
        self.assertIn("/gb/", d)
        self.assertIn("/all/", d)
        self.assertNotIn("/bg/", d)


# ── Disallow: / detekce (kritická chyba) ─────────────────────────────────────

class TestDisallowAll(unittest.TestCase):
    """Detekce 'Disallow: /' která blokuje celý web pro Googlebota."""

    def test_disallow_root_for_star(self):
        content = "User-agent: *\nDisallow: /\n"
        parsed = _parse_robots(content)
        disallows = _get_relevant_disallows(parsed)
        self.assertIn("/", disallows)

    def test_disallow_root_for_googlebot(self):
        content = "User-agent: Googlebot\nDisallow: /\n"
        parsed = _parse_robots(content)
        disallows = _get_relevant_disallows(parsed)
        self.assertIn("/", disallows)

    def test_disallow_subpath_not_root(self):
        content = "User-agent: *\nDisallow: /admin/\n"
        parsed = _parse_robots(content)
        disallows = _get_relevant_disallows(parsed)
        self.assertIn("/admin/", disallows)
        self.assertNotIn("/", disallows)

    def test_critical_prefix_constant(self):
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
        xml = "<urlset><url><loc>https://example.cz/broken"
        pages, sitemaps = _parse_sitemap_xml(xml)
        self.assertIsInstance(pages, list)
        self.assertIsInstance(sitemaps, list)


# ── Hybrid sitemap → crawler ─────────────────────────────────────────────────

class TestHybridCrawl(unittest.TestCase):
    def test_sitemap_min_pages_constant(self):
        from config import SITEMAP_MIN_PAGES
        self.assertIsInstance(SITEMAP_MIN_PAGES, int)
        self.assertGreater(SITEMAP_MIN_PAGES, 0)
        self.assertLess(SITEMAP_MIN_PAGES, 100)

    def test_crawl_site_accepts_seed_urls(self):
        import inspect
        from crawler import crawl_site
        sig = inspect.signature(crawl_site)
        self.assertIn("seed_urls", sig.parameters)
        self.assertFalse(sig.parameters["seed_urls"].default)


# ── Java version check ──────────────────────────────────────────────────────

class TestJavaVersionCheck(unittest.TestCase):
    """
    Testy pro check_java_version() — mockujeme subprocess.run, takže testy
    běží i na stroji bez Javy a nezávisle na tom, jakou verzi má vývojář
    nainstalovanou.

    Kontrolujeme parsování všech tří formátů výstupu `java -version`:
      - Moderní (Java 9+):     openjdk version "21.0.10"
      - Krátký (jen major):    openjdk version "17"
      - Legacy (Java 8 a níž): java version "1.8.0_281"  → major = 8
    """

    def _make_run_result(self, stderr: bytes = b"", stdout: bytes = b"") -> MagicMock:
        """Pomocná: vyrobí mock objekt vracený subprocess.run."""
        r = MagicMock()
        r.stderr = stderr
        r.stdout = stdout
        return r

    # ── Moderní formát (Java 9+) ─────────────────────────────────────────────

    @patch("validator_w3c.subprocess.run")
    def test_modern_format_java21(self, mock_run):
        mock_run.return_value = self._make_run_result(
            stderr=b'openjdk version "21.0.10" 2026-01-20\n'
                   b'OpenJDK Runtime Environment (build 21.0.10+7)\n'
        )
        status, ver = check_java_version()
        self.assertEqual(status, "ok")
        self.assertEqual(ver, "21.0.10")

    @patch("validator_w3c.subprocess.run")
    def test_modern_format_java17(self, mock_run):
        mock_run.return_value = self._make_run_result(
            stderr=b'openjdk version "17.0.8" 2023-07-18\n'
        )
        status, ver = check_java_version()
        self.assertEqual(status, "ok")
        self.assertEqual(ver, "17.0.8")

    @patch("validator_w3c.subprocess.run")
    def test_short_version_format(self, mock_run):
        """Java někdy vypíše jen major bez teček: openjdk version "17"."""
        mock_run.return_value = self._make_run_result(
            stderr=b'openjdk version "17" 2023-09-19\n'
        )
        status, ver = check_java_version()
        self.assertEqual(status, "ok")
        self.assertEqual(ver, "17")

    # ── Hranice 11 (minimum pro vnu.jar) ─────────────────────────────────────

    @patch("validator_w3c.subprocess.run")
    def test_minimum_supported_java11(self, mock_run):
        """Java 11 přesně na hraně musí projít jako OK."""
        mock_run.return_value = self._make_run_result(
            stderr=b'openjdk version "11.0.19" 2023-04-18 LTS\n'
        )
        status, ver = check_java_version()
        self.assertEqual(status, "ok")
        self.assertEqual(ver, "11.0.19")

    @patch("validator_w3c.subprocess.run")
    def test_just_below_threshold_java10(self, mock_run):
        """Java 10 je o jednu pod prahem — too_old."""
        mock_run.return_value = self._make_run_result(
            stderr=b'openjdk version "10.0.2" 2018-07-17\n'
        )
        status, ver = check_java_version()
        self.assertEqual(status, "too_old")
        self.assertEqual(ver, "10.0.2")

    @patch("validator_w3c.subprocess.run")
    def test_java9_too_old(self, mock_run):
        mock_run.return_value = self._make_run_result(
            stderr=b'openjdk version "9.0.4"\n'
        )
        status, ver = check_java_version()
        self.assertEqual(status, "too_old")
        self.assertEqual(ver, "9.0.4")

    # ── Legacy formát (Java 8 a níž: "1.X.Y") ────────────────────────────────

    @patch("validator_w3c.subprocess.run")
    def test_legacy_format_java8(self, mock_run):
        """1.8.0_281 = Java 8 (legacy formát před Javou 9) → too_old."""
        mock_run.return_value = self._make_run_result(
            stderr=b'java version "1.8.0_281"\n'
                   b'Java(TM) SE Runtime Environment\n'
        )
        status, ver = check_java_version()
        self.assertEqual(status, "too_old")
        self.assertEqual(ver, "1.8.0_281")

    @patch("validator_w3c.subprocess.run")
    def test_legacy_format_java7(self, mock_run):
        mock_run.return_value = self._make_run_result(
            stderr=b'java version "1.7.0_80"\n'
        )
        status, ver = check_java_version()
        self.assertEqual(status, "too_old")

    # ── Chybové scénáře ──────────────────────────────────────────────────────

    @patch("validator_w3c.subprocess.run", side_effect=FileNotFoundError)
    def test_java_not_installed(self, mock_run):
        """`java` není v PATH → FileNotFoundError → status 'missing'."""
        status, ver = check_java_version()
        self.assertEqual(status, "missing")
        self.assertEqual(ver, "")

    @patch("validator_w3c.subprocess.run", side_effect=OSError("permission denied"))
    def test_subprocess_generic_error(self, mock_run):
        """Jakákoli jiná výjimka → 'unknown' (raději mlčet než matoucí hláška)."""
        status, ver = check_java_version()
        self.assertEqual(status, "unknown")
        self.assertEqual(ver, "")

    @patch("validator_w3c.subprocess.run")
    def test_empty_output(self, mock_run):
        """Prázdný stdout i stderr → 'unknown'."""
        mock_run.return_value = self._make_run_result(stderr=b"", stdout=b"")
        status, ver = check_java_version()
        self.assertEqual(status, "unknown")
        self.assertEqual(ver, "")

    @patch("validator_w3c.subprocess.run")
    def test_garbage_output(self, mock_run):
        """Když java vypíše něco nečekaného (chybí 'version "X"'), je to unknown."""
        mock_run.return_value = self._make_run_result(
            stderr=b"completely unrelated garbage output without version string"
        )
        status, ver = check_java_version()
        self.assertEqual(status, "unknown")
        self.assertEqual(ver, "")

    @patch("validator_w3c.subprocess.run")
    def test_output_on_stdout_instead_of_stderr(self, mock_run):
        """
        Kdyby se Java v budoucnu rozhodla psát na stdout (nebo nějaký wrapper
        to přesměroval), funkce to musí stejně rozpoznat.
        """
        mock_run.return_value = self._make_run_result(
            stderr=b"",
            stdout=b'openjdk version "21.0.1" 2023-10-17\n'
        )
        status, ver = check_java_version()
        self.assertEqual(status, "ok")
        self.assertEqual(ver, "21.0.1")


if __name__ == "__main__":
    unittest.main()