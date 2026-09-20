"""
Testy pro crawler.py a report_excel.py.

Crawler: URL normalizace/filtry, _fetch a chování crawl_site (mockované sítě).
Report:  pomocné funkce + end-to-end generování Excelu – workbook se po
         uložení znovu načte přes openpyxl a kontroluje se obsah buněk.
"""
import io
import sys
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook

from issues import Issue, IssueType


# ── Pomocné funkce ───────────────────────────────────────────────────────────

def _resp(status=200, text="", content_type="text/html; charset=utf-8", url=""):
    r = MagicMock()
    r.status_code = status
    r.text        = text
    r.headers     = {"Content-Type": content_type}
    r.url         = url
    return r


def _page(url, category="ok", warnings=None, errors=None, issues=None,
          error_msg="", homepage_meta=None):
    return {
        "url":              url,
        "w3c_category":     category,
        "w3c_warnings":     warnings or [],
        "w3c_errors":       errors or [],
        "w3c_error_msg":    error_msg,
        "structure_issues": issues or [],
        "homepage_meta":    homepage_meta or [],
    }


def _sheet_values(path: Path) -> list[str]:
    """Všechny neprázdné textové hodnoty buněk z prvního listu (pro assertIn)."""
    wb = load_workbook(path)
    ws = wb.worksheets[0]
    vals = []
    for row in ws.iter_rows(values_only=True):
        for v in row:
            if v is not None:
                vals.append(str(v))
    return vals


def _run_crawl(pages: dict, start="https://example.cz/", **kwargs):
    """
    Spustí crawl_site nad falešným webem.
    pages = {url: html}; URL mimo slovník vrací None (jako 404 / non-html).
    Vrátí (found, fetched_urls).
    """
    import crawler

    fetched: list[str] = []

    def _fake_fetch(session, url, timeout=None):
        fetched.append(url)
        return url, pages.get(url)

    # requests vrací finální URL vždy se schématem
    default_probe = start if "://" in start else "https://" + start
    probe = MagicMock(); probe.url = kwargs.pop("probe_url", default_probe)

    with patch("crawler.requests.Session") as sess_cls, \
         patch("crawler._fetch", side_effect=_fake_fetch), \
         patch("crawler.RobotFileParser") as rp_cls, \
         patch("crawler.time.sleep"), \
         redirect_stdout(io.StringIO()):
        sess_cls.return_value.get.return_value = probe
        rp = rp_cls.return_value
        rp.crawl_delay.return_value = 0
        rp.can_fetch.side_effect = kwargs.pop("can_fetch", lambda ua, u: True)
        found = crawler.crawl_site(start, delay=0, **kwargs)

    return found, fetched, rp


# ── Crawler: URL utility ─────────────────────────────────────────────────────

class TestCrawlerUrlUtils(unittest.TestCase):
    def test_normalize_strips_fragment_and_trailing_slash(self):
        from crawler import _normalize
        self.assertEqual(_normalize("https://a.cz/x/#sekce"), "https://a.cz/x")
        self.assertEqual(_normalize("https://a.cz/"), "https://a.cz")
        self.assertEqual(_normalize("https://a.cz/x"), "https://a.cz/x")

    def test_url_key_ignores_www_case_and_slash(self):
        from crawler import _url_key
        self.assertEqual(_url_key("https://WWW.Example.cz/o-nas/"),
                         _url_key("http://example.cz/o-nas"))
        self.assertNotEqual(_url_key("https://example.cz/a"),
                            _url_key("https://example.cz/b"))

    def test_same_domain_www_variants(self):
        from crawler import _same_domain
        self.assertTrue(_same_domain("www.example.cz", "https://example.cz/x"))
        self.assertTrue(_same_domain("example.cz", "https://WWW.example.cz/"))
        self.assertFalse(_same_domain("example.cz", "https://blog.example.cz/"))
        self.assertFalse(_same_domain("example.cz", "https://example.com/"))

    def test_ignore_patterns(self):
        from crawler import _ignore
        for u in ["https://a.cz/soubor.PDF", "https://a.cz/img.png",
                  "https://a.cz/x?page=2", "mailto:info@a.cz", "tel:+420",
                  "javascript:void(0)", "https://a.cz/vse/2",
                  "https://a.cz/inzerat-1684", "https://a.cz/sitemap.xml"]:
            self.assertTrue(_ignore(u), u)
        for u in ["https://a.cz/o-nas", "https://a.cz/", "https://a.cz/rok-2024/",
                  "https://a.cz/produkt-12"]:
            self.assertFalse(_ignore(u), u)


# ── Crawler: _fetch ──────────────────────────────────────────────────────────

class TestCrawlerFetch(unittest.TestCase):
    def test_html_200_returns_text(self):
        from crawler import _fetch
        s = MagicMock(); s.get.return_value = _resp(200, "<html>x</html>")
        self.assertEqual(_fetch(s, "https://a.cz/", timeout=3),
                         ("https://a.cz/", "<html>x</html>"))
        s.get.assert_called_once_with("https://a.cz/", timeout=3)

    def test_non_html_content_type_returns_none(self):
        from crawler import _fetch
        s = MagicMock(); s.get.return_value = _resp(200, "{}", "application/json")
        self.assertEqual(_fetch(s, "https://a.cz/api")[1], None)

    def test_non_200_returns_none(self):
        from crawler import _fetch
        s = MagicMock(); s.get.return_value = _resp(404, "<html>404</html>")
        self.assertIsNone(_fetch(s, "https://a.cz/neni")[1])

    def test_exception_returns_none(self):
        from crawler import _fetch
        s = MagicMock(); s.get.side_effect = ConnectionError("boom")
        self.assertEqual(_fetch(s, "https://a.cz/"), ("https://a.cz/", None))


# ── Crawler: crawl_site ──────────────────────────────────────────────────────

class TestCrawlSite(unittest.TestCase):
    SITE = {
        "https://example.cz": (
            '<a href="/o-nas">O nás</a>'
            '<a href="/kontakt/">Kontakt</a>'
            '<a href="https://www.example.cz/o-nas#tym">Tým</a>'     # duplicita
            '<a href="https://jiny.cz/">Cizí</a>'
            '<a href="/cenik.pdf">PDF</a>'
            '<a href="/vyhledat?q=x">Query</a>'
            '<a href="mailto:a@b.cz">Mail</a>'
        ),
        "https://example.cz/o-nas":   '<a href="/">Domů</a><a href="/historie">Historie</a>',
        "https://example.cz/kontakt": '<a href="/o-nas">O nás</a>',
        "https://example.cz/historie": "<p>konec</p>",
    }

    def test_discovers_internal_pages_only(self):
        found, fetched, _ = _run_crawl(self.SITE)
        self.assertEqual(set(found), set(self.SITE))
        # Cizí doména, PDF, query a mailto se nestahují
        for u in fetched:
            self.assertTrue(u.startswith("https://example.cz"), u)
            self.assertNotIn("?", u); self.assertNotIn(".pdf", u)

    def test_no_duplicate_fetch_for_url_variants(self):
        found, fetched, _ = _run_crawl(self.SITE)
        self.assertEqual(len(fetched), len(set(fetched)))
        self.assertEqual(fetched.count("https://example.cz/o-nas"), 1)
        self.assertEqual(len(found), 4)

    def test_max_pages_limits_total(self):
        found, fetched, _ = _run_crawl(self.SITE, max_pages=2)
        self.assertEqual(len(found), 2)
        self.assertLessEqual(len(fetched), 2)

    def test_robots_disallow_skips_page(self):
        found, fetched, _ = _run_crawl(
            self.SITE, can_fetch=lambda ua, u: "kontakt" not in u)
        self.assertNotIn("https://example.cz/kontakt", fetched)
        self.assertNotIn("https://example.cz/kontakt", found)
        self.assertIn("https://example.cz/o-nas", found)

    def test_schemeless_start_url_gets_https(self):
        found, fetched, _ = _run_crawl(self.SITE, start="example.cz")
        self.assertIn("https://example.cz", fetched)

    def test_redirected_probe_changes_base_domain(self):
        """Web přesměruje example.cz → www.example.com: crawluje se cílová doména."""
        site = {"https://www.example.com": '<a href="/x">x</a>',
                "https://www.example.com/x": ""}
        found, fetched, _ = _run_crawl(site, start="https://example.cz/",
                                       probe_url="https://www.example.com/")
        self.assertEqual(set(found), set(site))

    def test_local_host_skips_robots(self):
        site = {"http://localhost:8000": '<a href="/a">a</a>',
                "http://localhost:8000/a": ""}
        found, fetched, rp = _run_crawl(site, start="http://localhost:8000/")
        self.assertEqual(set(found), set(site))
        rp.read.assert_not_called()
        rp.can_fetch.assert_not_called()

    def test_unreachable_pages_not_in_found(self):
        site = {"https://example.cz": '<a href="/mrtva">x</a>'}   # /mrtva → None
        found, fetched, _ = _run_crawl(site)
        self.assertIn("https://example.cz/mrtva", fetched)
        self.assertEqual(found, ["https://example.cz"])

    def test_exclude_patterns_skip_fetch_and_discovery(self):
        """--exclude: vynechaná stránka se nestahuje, ani se z ní neberou odkazy."""
        site = {
            "https://example.cz":            '<a href="/blog">Blog</a><a href="/kontakt">K</a>',
            "https://example.cz/blog":       '<a href="/blog/clanek">Č</a><a href="/skryta">S</a>',
            "https://example.cz/blog/clanek": "",
            "https://example.cz/skryta":     "",
            "https://example.cz/kontakt":    "",
        }
        found, fetched, _ = _run_crawl(site, exclude=["/blog*"])
        self.assertEqual(set(found), {"https://example.cz", "https://example.cz/kontakt"})
        self.assertNotIn("https://example.cz/blog", fetched)
        self.assertNotIn("https://example.cz/skryta", fetched)   # dostupná jen přes /blog


class TestIsExcluded(unittest.TestCase):
    def test_path_glob(self):
        from crawler import is_excluded
        self.assertTrue(is_excluded("https://e.cz/blog/clanek-1", ["/blog/*"]))
        self.assertTrue(is_excluded("https://e.cz/blog/2024/x", ["/blog/*"]))   # * bere i lomítka
        self.assertFalse(is_excluded("https://e.cz/blog", ["/blog/*"]))
        self.assertTrue(is_excluded("https://e.cz/blog", ["/blog*"]))

    def test_trailing_slash_and_case_insensitive(self):
        from crawler import is_excluded
        self.assertTrue(is_excluded("https://e.cz/Blog/", ["/blog"]))
        self.assertTrue(is_excluded("https://e.cz/blog", ["/BLOG/"]))

    def test_full_url_and_extension_patterns(self):
        from crawler import is_excluded
        self.assertTrue(is_excluded("https://e.cz/en/about", ["https://e.cz/en/*"]))
        self.assertFalse(is_excluded("https://e.cz/en/about", ["https://jiny.cz/en/*"]))
        self.assertTrue(is_excluded("https://e.cz/soubor.pdf", ["*.pdf"]))

    def test_empty_patterns(self):
        from crawler import is_excluded
        self.assertFalse(is_excluded("https://e.cz/a", None))
        self.assertFalse(is_excluded("https://e.cz/a", []))
        self.assertFalse(is_excluded("https://e.cz/a", ["", "  "]))


class TestSitemapExclude(unittest.TestCase):
    def test_excluded_urls_do_not_consume_max_urls(self):
        import sitemap
        xml = ("<urlset>"
               + "".join(f"<url><loc>https://example.cz/blog/clanek{i}</loc></url>"
                         for i in range(20))
               + "<url><loc>https://example.cz/kontakt</loc></url>"
               "<url><loc>https://example.cz/o-nas</loc></url>"
               "</urlset>")
        with patch("sitemap._sitemap_candidates", return_value=["https://example.cz/sitemap.xml"]), \
             patch("sitemap._fetch_text", return_value=xml):
            urls = sitemap.fetch_sitemap_urls("https://example.cz/", max_urls=5,
                                              exclude=["/blog/*"])
        self.assertEqual(urls, ["https://example.cz/kontakt", "https://example.cz/o-nas"])


# ── Report: pomocné funkce ───────────────────────────────────────────────────

class TestReportHelpers(unittest.TestCase):
    def test_as_https(self):
        from report_excel import _as_https
        self.assertEqual(_as_https("http://example.cz/a"), "https://example.cz/a")
        self.assertEqual(_as_https("https://example.cz/a"), "https://example.cz/a")
        self.assertEqual(_as_https("http://localhost:8000/a"), "http://localhost:8000/a")
        self.assertEqual(_as_https("http://127.0.0.1/"), "http://127.0.0.1/")
        self.assertIsNone(_as_https(None))

    def test_w3c_link_encodes_url(self):
        from report_excel import _w3c_link
        link = _w3c_link("http://example.cz/o nás?x=1&y=2")
        self.assertTrue(link.startswith("https://validator.w3.org/nu/?doc="))
        self.assertIn("https%3A%2F%2Fexample.cz%2Fo%20n", link)
        self.assertNotIn("&y", link)

    def test_score_palette_thresholds(self):
        from report_excel import _score_palette, SCORE_COLORS
        self.assertEqual(_score_palette(100), SCORE_COLORS["good"])
        self.assertEqual(_score_palette(80),  SCORE_COLORS["good"])
        self.assertEqual(_score_palette(79),  SCORE_COLORS["warn"])
        self.assertEqual(_score_palette(60),  SCORE_COLORS["warn"])
        self.assertEqual(_score_palette(59),  SCORE_COLORS["bad"])
        self.assertEqual(_score_palette(0),   SCORE_COLORS["bad"])

    def test_format_w3c_messages_errors_first_with_lines(self):
        from report_excel import _format_w3c_messages
        txt = _format_w3c_messages(
            warnings=[{"message": "Section lacks heading.", "line": 41}],
            errors=[{"message": "Bad img.", "line": 23},
                    {"message": "No line", "line": None}],
        )
        lines = txt.splitlines()
        self.assertEqual(lines[0], "[CHYBA @ ř.23] Bad img.")
        self.assertEqual(lines[1], "[CHYBA] No line")
        self.assertEqual(lines[2], "[VAROVÁNÍ @ ř.41] Section lacks heading.")

    def test_format_w3c_messages_truncates(self):
        from report_excel import _format_w3c_messages
        errs = [{"message": f"e{i}", "line": i} for i in range(40)]
        lines = _format_w3c_messages([], errs, max_items=30).splitlines()
        self.assertEqual(len(lines), 31)
        self.assertIn("dalších 10", lines[-1])

    def test_format_w3c_messages_accepts_plain_strings(self):
        from report_excel import _format_w3c_messages
        self.assertEqual(_format_w3c_messages(["w"], ["e"]),
                         "[CHYBA] e\n[VAROVÁNÍ] w")
        self.assertEqual(_format_w3c_messages([], []), "")


# ── Report: end-to-end generování Excelu ─────────────────────────────────────

class TestWriteReportContent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out  = Path(self._tmp.name) / "report.xlsx"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, results, start="https://example.cz/", **kw):
        from report_excel import write_report
        saved = write_report(results, self.out, start, **kw)
        self.assertEqual(saved, self.out)
        return _sheet_values(saved)

    def test_header_and_score(self):
        vals = self._write([_page("https://example.cz/")], score=85,
                           source_label="sitemap")
        self.assertIn("WEB VALIDATOR – REPORT", vals)
        self.assertTrue(any("Zdroj: sitemap" in v for v in vals))
        self.assertIn("85/100  –  Výborný", vals)

    def test_score_hidden_when_negative(self):
        vals = self._write([_page("https://example.cz/")], score=-1)
        self.assertNotIn("Web Quality Score", vals)

    def test_all_ok_messages(self):
        vals = self._write([_page("https://example.cz/"),
                            _page("https://example.cz/o-nas")])
        self.assertIn("✓ Žádné W3C problémy nalezeny", vals)
        self.assertIn("✓ Žádné strukturální problémy nalezeny", vals)
        self.assertIn("Kontrola neproběhla nebo nedostupná", vals)   # user_pages
        self.assertTrue(any(v.startswith("✓ Robots.txt neblokuje") for v in vals))

    def test_all_skipped_does_not_claim_ok(self):
        vals = self._write([_page("https://example.cz/", "skipped")])
        self.assertNotIn("✓ Žádné W3C problémy nalezeny", vals)
        self.assertTrue(any("W3C validace neproběhla" in v for v in vals))
        self.assertIn("W3C – Přeskočeno (validátor nedostupný)", vals)

    def test_w3c_problem_pages_public_link(self):
        results = [
            _page("https://example.cz/", "ok"),
            _page("https://example.cz/a", "warning",
                  warnings=[{"message": "w", "line": 1}]),
            _page("https://example.cz/b", "warning_error",
                  warnings=[{"message": "w", "line": 1}],
                  errors=[{"message": "e", "line": 2}, {"message": "e2", "line": 3}]),
        ]
        vals = self._write(results)
        self.assertIn("VAROVÁNÍ", vals)
        self.assertIn("VAR+CHYBA", vals)
        links = [v for v in vals if v.startswith("https://validator.w3.org/nu/?doc=")]
        # 2 stránky s problémy + 2 ukázky v tabulce nejčastějších chyb (e, e2)
        self.assertEqual(len(links), 4)
        self.assertTrue(any("example.cz%2Fb" in l for l in links))
        # Detailní výpis chyb se u veřejného webu nezobrazuje
        self.assertFalse(any(v.startswith("[CHYBA") for v in vals))

    def test_w3c_top_errors_table_public(self):
        results = [
            _page("https://example.cz/", "error",
                  errors=[{"message": "Duplicate ID \"menu\".", "line": 1},
                          {"message": "Duplicate ID \"menu\".", "line": 2}]),
            _page("https://example.cz/a", "error",
                  errors=[{"message": "Duplicate ID \"menu\".", "line": 5},
                          {"message": "Stray end tag \"div\".", "line": 9}]),
            _page("https://example.cz/b", "warning",
                  warnings=[{"message": "Section lacks heading.", "line": 1}]),
        ]
        vals = self._write(results)
        self.assertIn("W3C VALIDACE – NEJČASTĚJŠÍ CHYBY (napříč webem)", vals)
        self.assertIn("Duplicate ID \"menu\".", vals)
        self.assertIn("Stray end tag \"div\".", vals)
        # Varování se neagregují
        self.assertNotIn("Section lacks heading.", vals)
        # Ukázka je W3C odkaz na první stránku s chybou
        self.assertIn("https://validator.w3.org/nu/?doc=https%3A%2F%2Fexample.cz%2F", vals)
        # Agregace (2 řádky) + stránky s problémy (3 řádky) = 5 W3C odkazů
        links = [v for v in vals if v.startswith("https://validator.w3.org/nu/?doc=")]
        self.assertEqual(len(links), 5)
        # Pořadí buněk: text chyby → počet stránek → počet výskytů
        i = vals.index("Duplicate ID \"menu\".")
        self.assertEqual(vals[i + 1:i + 3], ["2", "3"])

    def test_w3c_top_errors_absent_without_errors(self):
        vals = self._write([_page("https://example.cz/", "warning",
                                  warnings=[{"message": "w", "line": 1}])])
        self.assertNotIn("W3C VALIDACE – NEJČASTĚJŠÍ CHYBY (napříč webem)", vals)

    def test_w3c_top_errors_local_audit_uses_page_url(self):
        results = [_page("http://localhost:8000/x", "error",
                         errors=[{"message": "Bad thing.", "line": 7}])]
        vals = self._write(results, start="http://localhost:8000/")
        self.assertIn("W3C VALIDACE – NEJČASTĚJŠÍ CHYBY (napříč webem)", vals)
        self.assertIn("Ukázková stránka", vals)
        self.assertFalse(any("validator.w3.org" in v for v in vals))
        self.assertEqual(vals.count("http://localhost:8000/x"), 2)   # ukázka + sekce stránek

    def test_w3c_top_errors_truncated_to_limit(self):
        from report_excel import W3C_TOP_ERRORS_LIMIT
        errors = [{"message": f"Error {i:02d}", "line": i}
                  for i in range(W3C_TOP_ERRORS_LIMIT + 5)]
        vals = self._write([_page("https://example.cz/", "error", errors=errors)])
        self.assertIn("Error 00", vals)
        self.assertIn(f"Error {W3C_TOP_ERRORS_LIMIT - 1:02d}", vals)
        self.assertNotIn(f"Error {W3C_TOP_ERRORS_LIMIT:02d}", vals)
        self.assertTrue(any(v.startswith("… a dalších 5 různých chyb") for v in vals))

    def test_w3c_local_audit_shows_messages_instead_of_link(self):
        results = [_page("http://localhost:8000/a", "error",
                         errors=[{"message": "Bad thing.", "line": 7}])]
        vals = self._write(results, start="http://localhost:8000/")
        self.assertFalse(any("validator.w3.org" in v for v in vals))
        self.assertIn("http://localhost:8000/a", vals)
        self.assertIn("[CHYBA @ ř.7] Bad thing.", vals)
        self.assertIn("ℹ Kontrola přeskočena – lokální / privátní host", vals)

    def test_structure_issues_grouped_by_label(self):
        results = [
            _page("https://example.cz/", issues=[Issue(IssueType.MISSING_H1),
                                                Issue(IssueType.EMPTY_TAG, tag="div", count=2)]),
            _page("https://example.cz/a", issues=[Issue(IssueType.MISSING_H1)]),
            _page("https://example.cz/b", issues=["nejsem Issue"]),   # ignorováno
        ]
        vals = self._write(results)
        self.assertIn("Chybí <h1> tag", vals)
        self.assertIn("Prázdné tagy <div>", vals)
        self.assertIn("https://example.cz/\nhttps://example.cz/a", vals)
        self.assertIn("2", vals)      # počet URL u Chybí <h1>
        self.assertIn("Struktura – Problémy", vals)

    def test_failed_pages_section(self):
        results = [_page("https://example.cz/", "ok"),
                   _page("http://example.cz/mrtva", "validator_error",
                         error_msg="HTTP 500 " + "x" * 300)]
        vals = self._write(results)
        self.assertIn("NEDOSTUPNÉ STRÁNKY – NEPODAŘILO SE NAČÍST", vals)
        self.assertIn("https://example.cz/mrtva", vals)          # http → https
        msg = next(v for v in vals if v.startswith("HTTP 500"))
        self.assertEqual(len(msg), 200)                            # oříznuto
        self.assertIn("Nepodařilo načíst stránek", vals)
        # http:// URL ve výsledcích → informační řádek o převodu
        self.assertTrue(any("zobrazeny jako https://" in v for v in vals))

    def test_no_http_conversion_note_for_local(self):
        vals = self._write([_page("http://localhost:8000/")],
                           start="http://localhost:8000/")
        self.assertFalse(any("zobrazeny jako https://" in v for v in vals))

    def test_robots_section_critical_and_normal(self):
        from robots_check import CRITICAL_PREFIX
        vals = self._write([_page("https://example.cz/")], domain_info={
            "robots_issues": [CRITICAL_PREFIX + "Disallow: / blokuje celý web",
                              "Blokuje /assets/*.css pro Googlebot"],
        })
        self.assertIn("⛔ KRITICKÉ: Disallow: / blokuje celý web", vals)
        self.assertIn("Blokuje /assets/*.css pro Googlebot", vals)

    def test_robots_skipped(self):
        vals = self._write([_page("https://example.cz/")],
                           domain_info={"robots_skipped": True})
        self.assertTrue(any("Kontrola přeskočena – interní" in v for v in vals))

    def test_user_pages_badges_and_notes(self):
        pages = [
            {"path": "/uzivatel/", "url": "https://example.cz/uzivatel/",
             "status_code": 200, "exists": True, "note": ""},
            {"path": "/uzivatel/login/", "url": "https://example.cz/uzivatel/login/",
             "status_code": 200, "exists": False,
             "note": "soft 404 – web vrací 200 pro libovolnou cestu"},
            {"path": "/uzivatel/x/", "url": "https://example.cz/uzivatel/x/",
             "status_code": 0, "exists": False, "note": "chyba spojení: Timeout"},
            {"path": "/uzivatel/y/", "url": "https://example.cz/uzivatel/y/",
             "status_code": 503, "exists": False, "note": ""},
        ]
        vals = self._write([_page("https://example.cz/")],
                           domain_info={"user_pages": pages})
        self.assertIn("EXISTUJE ⚠", vals)
        self.assertIn("Neexistuje", vals)
        self.assertIn("Nedostupné (chyba spojení)", vals)
        self.assertIn("HTTP 503", vals)
        self.assertIn("   ↳ soft 404 – web vrací 200 pro libovolnou cestu", vals)
        self.assertIn("   ↳ chyba spojení: Timeout", vals)
        self.assertIn("–", vals)    # status 0 → pomlčka místo čísla

    def test_homepage_meta_rows(self):
        results = [_page("https://example.cz/", homepage_meta=[
            "Title: 45 znaků – v pořádku",
            "Description: Chybí",
            "",
        ])]
        vals = self._write(results)
        self.assertIn("Title", vals); self.assertIn("45 znaků – v pořádku", vals)
        self.assertIn("OK", vals);    self.assertIn("PROBLÉM", vals)

    def test_homepage_meta_missing(self):
        vals = self._write([_page("https://example.cz/")])
        self.assertIn("Žádná data", vals)


if __name__ == "__main__":
    unittest.main()
