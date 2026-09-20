"""
Unit testy pro síťové kontroly a ukládání reportu (bez reálné sítě – vše mockované):
  - robots_check.check_user_pages  (soft 404, přesměrování, chyba spojení, 401/403)
  - sitemap: gzip komprimované sitemapy
  - crawler: crawl-delay hláška, timeout, probe přes Session
  - report_excel: fallback při zamčeném souboru
  - main.fetch_html: detekce kódování
  - validator_w3c: Content-Type pro vnu server

Spuštění:  python -m unittest discover tests/
"""
import gzip
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _resp(status=200, content=b"", url="", headers=None):
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.url = url
    r.headers = headers or {}
    return r


# ── /uzivatel/ detekce ───────────────────────────────────────────────────────

class TestUserPages(unittest.TestCase):
    BASE = "https://example.cz/"

    def _run(self, responses):
        """responses = list odpovědí v pořadí volání requests.get (nebo výjimka)."""
        from robots_check import check_user_pages
        with patch("robots_check.requests.get", side_effect=responses):
            return check_user_pages(self.BASE)[0]

    def test_404_not_exists(self):
        r = self._run([_resp(404, url=self.BASE + "uzivatel/")])
        self.assertFalse(r["exists"])
        self.assertEqual(r["status_code"], 404)

    def test_200_with_probe_404_exists(self):
        """Web vrací 200 pro /uzivatel/ a 404 pro nesmysl → sekce existuje."""
        r = self._run([
            _resp(200, b"<html>user area</html>", url=self.BASE + "uzivatel/"),
            _resp(404, b"not found", url=self.BASE + "wv-probe-x/"),
        ])
        self.assertTrue(r["exists"])

    def test_catch_all_same_body_is_soft_404(self):
        """Web vrací 200 se stejným HTML pro cokoliv → soft 404, neexistuje."""
        body = b"<html>homepage</html>"
        r = self._run([
            _resp(200, body, url=self.BASE + "uzivatel/"),
            _resp(200, body, url=self.BASE + "wv-probe-x/"),
        ])
        self.assertFalse(r["exists"])
        self.assertIn("soft 404", r["note"])

    def test_catch_all_different_body_exists_with_note(self):
        r = self._run([
            _resp(200, b"<html>" + b"user " * 500 + b"</html>", url=self.BASE + "uzivatel/"),
            _resp(200, b"<html>nope</html>", url=self.BASE + "wv-probe-x/"),
        ])
        self.assertTrue(r["exists"])
        self.assertIn("ověř ručně", r["note"])

    def test_redirect_to_homepage_not_exists(self):
        r = self._run([_resp(200, b"<html>home</html>", url=self.BASE)])
        self.assertFalse(r["exists"])
        self.assertIn("přesměrováno", r["note"])

    def test_redirect_to_login_exists(self):
        r = self._run([
            _resp(200, b"<html>login</html>", url=self.BASE + "prihlaseni"),
            _resp(404, b"", url=self.BASE + "wv-probe-x/"),
        ])
        self.assertTrue(r["exists"])

    def test_401_403_exists_protected(self):
        for code in (401, 403):
            r = self._run([_resp(code, url=self.BASE + "uzivatel/")])
            self.assertTrue(r["exists"], code)
            self.assertIn(str(code), r["note"])

    def test_connection_error_status_zero(self):
        import requests
        r = self._run([requests.ConnectionError("boom")])
        self.assertFalse(r["exists"])
        self.assertEqual(r["status_code"], 0)
        self.assertIn("chyba spojení", r["note"])

    def test_probe_failure_trusts_200(self):
        import requests
        r = self._run([
            _resp(200, b"<html>user</html>", url=self.BASE + "uzivatel/"),
            requests.ConnectionError("probe boom"),
        ])
        self.assertTrue(r["exists"])

    def test_local_host_skipped(self):
        from robots_check import check_user_pages
        self.assertEqual(check_user_pages("http://localhost:8000/"), [])


# ── Sitemap gzip ─────────────────────────────────────────────────────────────

class TestSitemapGzip(unittest.TestCase):
    XML = (b'<?xml version="1.0" encoding="UTF-8"?>'
           b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           b'<url><loc>https://example.cz/a</loc></url>'
           b'<url><loc>https://example.cz/b</loc></url></urlset>')

    def test_gzip_body_decoded(self):
        from sitemap import _decode_sitemap_body
        text = _decode_sitemap_body(gzip.compress(self.XML),
                                    "application/octet-stream",
                                    "https://example.cz/sitemap.xml.gz")
        self.assertIn("https://example.cz/a", text)

    def test_plain_xml_unchanged(self):
        from sitemap import _decode_sitemap_body
        text = _decode_sitemap_body(self.XML, "application/xml",
                                    "https://example.cz/sitemap.xml")
        self.assertIn("<urlset", text)

    def test_binary_non_sitemap_rejected(self):
        from sitemap import _decode_sitemap_body
        self.assertIsNone(_decode_sitemap_body(b"\x89PNG....", "image/png",
                                               "https://example.cz/logo.png"))

    def test_corrupt_gzip_returns_none(self):
        from sitemap import _decode_sitemap_body
        with patch("sys.stdout", new_callable=StringIO):
            out = _decode_sitemap_body(b"\x1f\x8bgarbage", "application/gzip",
                                       "https://example.cz/sitemap.xml.gz")
        self.assertIsNone(out)

    def test_fetch_sitemap_urls_via_gz_candidate(self):
        """Celý průchod: /sitemap.xml nic, /sitemap.xml.gz vrátí gzip → URL."""
        import sitemap

        def _fake_get(url, **kw):
            if url.endswith("/sitemap.xml.gz"):
                return _resp(200, gzip.compress(self.XML), url,
                             {"Content-Type": "application/gzip"})
            return _resp(404, b"", url)

        with patch("sitemap._sitemap_candidates",
                   return_value=["https://example.cz/sitemap.xml",
                                 "https://example.cz/sitemap.xml.gz"]), \
             patch("sitemap.requests.Session") as sess_cls:
            sess_cls.return_value.get.side_effect = _fake_get
            urls = sitemap.fetch_sitemap_urls("https://example.cz/")

        self.assertEqual(urls, ["https://example.cz/a", "https://example.cz/b"])

    def test_gz_in_default_candidates(self):
        import sitemap
        sess = MagicMock()
        sess.get.return_value = _resp(404)
        cands = sitemap._sitemap_candidates("https://example.cz/", sess)
        self.assertIn("https://example.cz/sitemap.xml.gz", cands)

    def test_index_with_gz_sub_sitemaps(self):
        from sitemap import _parse_sitemap_xml
        # Neznámý root → heuristika podle koncovky
        xml = ("<root><loc>https://example.cz/sitemap-posts.xml.gz</loc>"
               "<loc>https://example.cz/page</loc></root>")
        pages, subs = _parse_sitemap_xml(xml)
        self.assertEqual(subs, ["https://example.cz/sitemap-posts.xml.gz"])
        self.assertEqual(pages, ["https://example.cz/page"])


# ── Crawler ──────────────────────────────────────────────────────────────────

class TestCrawlerDelayAndTimeout(unittest.TestCase):
    def _crawl(self, rp_delay, delay, timeout=None):
        import crawler
        seen_timeouts = []

        def _fake_fetch(session, url, timeout=None):
            seen_timeouts.append(timeout)
            return url, "<html><body><h1>x</h1></body></html>"

        class _Probe:
            url = "https://example.cz/"

        kwargs = {"max_pages": 3, "delay": delay}
        if timeout is not None:
            kwargs["timeout"] = timeout

        with patch("crawler.requests.Session") as sess_cls, \
             patch("crawler._fetch", side_effect=_fake_fetch), \
             patch("crawler.RobotFileParser") as rp_cls, \
             patch("crawler.time.sleep"), \
             patch("sys.stdout", new_callable=StringIO) as out:
            sess_cls.return_value.get.return_value = _Probe()
            rp_cls.return_value.crawl_delay.return_value = rp_delay
            rp_cls.return_value.can_fetch.return_value = True
            crawler.crawl_site("https://example.cz/", **kwargs)
        return out.getvalue(), seen_timeouts, sess_cls

    def test_no_robots_message_when_only_min_delay_floor(self):
        """robots.txt nemá crawl-delay, pauzu zvedl jen MIN_DELAY → žádná hláška."""
        out, _, _ = self._crawl(rp_delay=None, delay=0)
        self.assertNotIn("robots.txt nastavuje crawl-delay", out)

    def test_robots_message_when_crawl_delay_wins(self):
        out, _, _ = self._crawl(rp_delay=5, delay=1.0)
        self.assertIn("robots.txt nastavuje crawl-delay: 5s", out)

    def test_no_robots_message_when_user_delay_higher(self):
        out, _, _ = self._crawl(rp_delay=1, delay=3.0)
        self.assertNotIn("robots.txt nastavuje", out)

    def test_timeout_passed_to_fetch(self):
        _, timeouts, sess_cls = self._crawl(rp_delay=None, delay=0, timeout=7)
        self.assertTrue(timeouts)
        self.assertTrue(all(t == 7 for t in timeouts))
        # probe jde přes Session se stejným timeoutem
        _, kw = sess_cls.return_value.get.call_args
        self.assertEqual(kw.get("timeout"), 7)

    def test_default_timeout_is_crawl_timeout(self):
        from config import CRAWL_TIMEOUT
        _, timeouts, _ = self._crawl(rp_delay=None, delay=0)
        self.assertTrue(all(t == CRAWL_TIMEOUT for t in timeouts))


# ── Excel: zamčený soubor ────────────────────────────────────────────────────

class TestExcelSaveFallback(unittest.TestCase):
    def test_permission_error_saves_with_timestamp(self):
        from report_excel import _save_workbook
        target = Path(tempfile.gettempdir()) / "web_validator.xlsx"
        saved_paths = []

        def _save(path):
            saved_paths.append(Path(path))
            if len(saved_paths) == 1:
                raise PermissionError("locked")

        wb = MagicMock(); wb.save.side_effect = _save
        result = _save_workbook(wb, target)

        self.assertEqual(len(saved_paths), 2)
        self.assertNotEqual(result, target)
        self.assertEqual(result.parent, target.parent)
        self.assertTrue(result.name.startswith("web_validator_"))
        self.assertTrue(result.name.endswith(".xlsx"))

    def test_normal_save_returns_original_path(self):
        from report_excel import _save_workbook
        target = Path(tempfile.gettempdir()) / "web_validator.xlsx"
        wb = MagicMock()
        self.assertEqual(_save_workbook(wb, target), target)
        wb.save.assert_called_once_with(target)

    def test_write_report_returns_path(self):
        from report_excel import write_report
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.xlsx"
            result = write_report([], out, "https://example.cz/", score=100)
            self.assertEqual(result, out)
            self.assertTrue(out.exists())


# ── Kódování stažené stránky ─────────────────────────────────────────────────

class TestFetchEncoding(unittest.TestCase):
    def _fetch(self, body: bytes, content_type: str, apparent="utf-8"):
        import requests
        from main import fetch_html
        resp = requests.Response()
        resp.status_code = 200
        resp._content = body
        resp.headers["Content-Type"] = content_type
        resp.url = "https://example.cz/"
        # to samé, co dělá HTTPAdapter.build_response
        resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)
        with patch.object(requests.Response, "apparent_encoding", apparent):
            session = MagicMock(); session.get.return_value = resp
            return fetch_html(session, "https://example.cz/")

    def test_header_charset_respected(self):
        body = "<p>Příliš žluťoučký</p>".encode("windows-1250")
        _, text, ct, final_url = self._fetch(body, "text/html; charset=windows-1250")
        self.assertEqual(final_url, "")     # bez přesměrování
        self.assertIn("žluťoučký", text)
        self.assertEqual(ct, "text/html; charset=windows-1250")

    def test_meta_charset_used_when_header_missing(self):
        body = ('<html><head><meta charset="windows-1250"></head>'
                '<body><p>Příliš žluťoučký</p></body></html>').encode("windows-1250")
        _, text, _, _ = self._fetch(body, "text/html", apparent="ascii")
        self.assertIn("žluťoučký", text)

    def test_meta_http_equiv_charset(self):
        body = ('<html><head><meta http-equiv="Content-Type" '
                'content="text/html; charset=iso-8859-2"></head>'
                '<body><p>Příliš žluťoučký</p></body></html>').encode("iso-8859-2")
        _, text, _, _ = self._fetch(body, "text/html", apparent="ascii")
        self.assertIn("žluťoučký", text)

    def test_apparent_encoding_fallback(self):
        body = "<p>Příliš žluťoučký kůň</p>".encode("utf-8")
        _, text, _, _ = self._fetch(body, "text/html", apparent="utf-8")
        self.assertIn("žluťoučký", text)

    def test_unknown_meta_charset_ignored(self):
        from main import _sniff_meta_charset
        self.assertIsNone(_sniff_meta_charset(b'<meta charset="no-such-enc">'))
        self.assertEqual(_sniff_meta_charset(b'<META CHARSET=UTF-8>'), "utf-8")


# ── vnu server Content-Type ──────────────────────────────────────────────────

class TestServerContentType(unittest.TestCase):
    def test_charset_forwarded(self):
        from validator_w3c import _server_content_type
        self.assertEqual(_server_content_type("text/html; charset=windows-1250"),
                         "text/html; charset=windows-1250")
        self.assertEqual(_server_content_type('text/html; charset="UTF-8"'),
                         "text/html; charset=UTF-8")

    def test_no_charset_lets_vnu_sniff(self):
        from validator_w3c import _server_content_type
        self.assertEqual(_server_content_type("text/html"), "text/html")
        self.assertEqual(_server_content_type(""), "text/html")

    def test_validate_passes_content_type_to_server(self):
        import validator_w3c as w
        with patch.object(w, "_server_port", 12345), \
             patch.object(w, "_check_server_alive", return_value=True), \
             patch("validator_w3c.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"messages": []}
            res = w.validate(b"<html></html>", jar="x.jar",
                             content_type="text/html; charset=iso-8859-2")
        self.assertEqual(res["category"], "ok")
        _, kw = post.call_args
        self.assertEqual(kw["headers"]["Content-Type"], "text/html; charset=iso-8859-2")


# ── vnu.jar server – životní cyklus ──────────────────────────────────────────

class TestServerLifecycle(unittest.TestCase):
    """start_server drží ne-reentrantní _server_lock – úklid po neúspěšném
    startu nesmí volat stop_server() (dřív → deadlock při pomalém startu Javy)."""

    def setUp(self):
        import validator_w3c as v
        self.v = v
        self.proc = MagicMock(); self.proc.poll.return_value = None
        v._server_proc = None; v._server_port = 0; v._atexit_registered = False

    def _start(self, server_up: bool):
        with patch.object(self.v.subprocess, "Popen", return_value=self.proc),              patch.object(self.v, "_wait_for_server", return_value=server_up),              patch.object(self.v, "_find_free_port", return_value=8899):
            return self.v.start_server("vnu.jar")

    def test_failed_start_does_not_deadlock(self):
        import threading
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("r", self._start(False)),
                             daemon=True)
        t.start(); t.join(5)
        self.assertFalse(t.is_alive(), "start_server() se zasekl (deadlock)")
        self.assertFalse(result["r"])
        self.proc.terminate.assert_called_once()
        self.assertIsNone(self.v._server_proc)
        self.assertEqual(self.v._server_port, 0)

    def test_atexit_registered_once_across_restarts(self):
        regs = []
        with patch.object(self.v.atexit, "register", side_effect=regs.append):
            self.assertTrue(self._start(True)); self.v.stop_server()
            self.assertTrue(self._start(True)); self.v.stop_server()
        self.assertEqual(regs, [self.v.stop_server])

    def test_start_is_idempotent_while_running(self):
        with patch.object(self.v.atexit, "register"):
            self.assertTrue(self._start(True))
            with patch.object(self.v.subprocess, "Popen") as popen:
                self.assertTrue(self.v.start_server("vnu.jar"))
                popen.assert_not_called()
        self.v.stop_server()


class TestMakeFilename(unittest.TestCase):
    def test_www_prefix_stripped_only_at_start(self):
        from main import make_filename
        self.assertEqual(make_filename("https://www.poski.com/"), "poski_validator.xlsx")
        self.assertEqual(make_filename("https://mywww.site.cz/"), "mywww_validator.xlsx")
        self.assertEqual(make_filename("https://www.www-shop.cz/"), "www-shop_validator.xlsx")

    def test_ip_and_localhost(self):
        from main import make_filename
        self.assertEqual(make_filename("http://192.168.1.10:8000/"), "192_168_1_10_validator.xlsx")
        self.assertEqual(make_filename("http://localhost:8000/"), "localhost_validator.xlsx")


# ── --output / --keep ────────────────────────────────────────────────────────

class TestBuildOutputPath(unittest.TestCase):
    URL = "https://www.poski.com/"

    def test_default_overwrites_in_reports_dir(self):
        from main import build_output_path
        d = Path(tempfile.gettempdir())
        self.assertEqual(build_output_path(self.URL, reports_dir=d),
                         d / "poski_validator.xlsx")

    def test_keep_adds_timestamp(self):
        from main import build_output_path
        d = Path(tempfile.gettempdir())
        p = build_output_path(self.URL, keep=True, reports_dir=d)
        self.assertEqual(p.parent, d)
        self.assertRegex(p.name, r"^poski_validator_\d{8}_\d{6}\.xlsx$")

    def test_output_file(self):
        from main import build_output_path
        p = build_output_path(self.URL, output="C:/tmp/muj_report.xlsx")
        self.assertEqual(p, Path("C:/tmp/muj_report.xlsx"))

    def test_output_directory_uses_default_name(self):
        from main import build_output_path
        with tempfile.TemporaryDirectory() as d:
            p = build_output_path(self.URL, output=d)
            self.assertEqual(p, Path(d) / "poski_validator.xlsx")
        # Neexistující cesta bez .xlsx přípony se bere jako adresář
        p = build_output_path(self.URL, output="C:/tmp/reporty")
        self.assertEqual(p, Path("C:/tmp/reporty") / "poski_validator.xlsx")

    def test_output_file_with_keep(self):
        from main import build_output_path
        p = build_output_path(self.URL, output="C:/tmp/r.xlsx", keep=True)
        self.assertEqual(p.parent, Path("C:/tmp"))
        self.assertRegex(p.name, r"^r_\d{8}_\d{6}\.xlsx$")


class TestParseExcludePatterns(unittest.TestCase):
    def test_repeat_and_comma(self):
        from main import parse_exclude_patterns
        self.assertEqual(parse_exclude_patterns(["/blog/*", "/en/*, /de/*"]),
                         ["/blog/*", "/en/*", "/de/*"])

    def test_none_and_dedup(self):
        from main import parse_exclude_patterns
        self.assertEqual(parse_exclude_patterns(None), [])
        self.assertEqual(parse_exclude_patterns(["/a", "/a", " "]), ["/a"])


# ── --fail-under / exit kód ──────────────────────────────────────────────────

class TestExitCode(unittest.TestCase):
    """
    Průchod main() s odpojenou sítí: sitemap/crawler/validace/report jsou
    mockované, testuje se jen návratový (exit) kód podle skóre a prahu.
    """

    def _run(self, argv, score_results, pages=("https://example.cz",)):
        import main as m
        with tempfile.TemporaryDirectory() as d, \
             patch.object(sys, "argv", ["main.py", *argv,
                                        "--no-interactive", "--no-update-check",
                                        "--output", d]), \
             patch.object(m, "print_banner"), \
             patch.object(m, "check_java_version", return_value=("ok", "21")), \
             patch.object(m, "find_vnu_jar", return_value=""), \
             patch.object(m, "fetch_sitemap_urls", return_value=[]), \
             patch.object(m, "crawl_site", return_value=list(pages)), \
             patch.object(m, "run_domain_checks",
                          return_value={"robots_issues": [], "robots_skipped": False,
                                        "user_pages": []}), \
             patch.object(m, "validate_pages", return_value=score_results), \
             patch.object(m, "run_link_checks", return_value=None), \
             patch.object(m, "stop_server"), \
             patch("sys.stdout", new_callable=StringIO) as out:
            code = m.main()
            self._json_files = sorted(Path(d).glob("*.json"))
            self._xlsx_files = sorted(Path(d).glob("*.xlsx"))
        return code, out.getvalue()

    @staticmethod
    def _page(category="ok", issues=None):
        return {"url": "https://example.cz", "w3c_category": category,
                "w3c_warnings": [], "w3c_errors": [], "w3c_error_msg": "",
                "structure_issues": issues or [], "homepage_meta": [], "title": "T"}

    def test_no_threshold_returns_zero(self):
        code, _ = self._run(["https://example.cz"], [self._page()])
        self.assertEqual(code, 0)

    def test_score_above_threshold(self):
        code, out = self._run(["https://example.cz", "--fail-under", "80"], [self._page()])
        self.assertEqual(code, 0)
        self.assertIn("splňuje práh 80", out)

    def test_score_below_threshold(self):
        # validator_error → skóre 0
        code, out = self._run(["https://example.cz", "--fail-under", "80"],
                              [self._page(category="validator_error")])
        self.assertEqual(code, 1)
        self.assertIn("pod prahem 80", out)

    def test_no_pages_returns_one(self):
        """Sitemap ani crawler nic nenašly → audit neproběhl → exit 1."""
        code, out = self._run(["https://example.cz"], [], pages=())
        self.assertEqual(code, 1)
        self.assertIn("Žádné stránky", out)

    def test_json_written_next_to_excel(self):
        """JSON se zapisuje vždy (zdroj pro příští porovnání), bez přepínače."""
        _, out = self._run(["https://example.cz"], [self._page()])
        self.assertEqual([p.name for p in self._json_files], ["example_validator.json"])
        self.assertEqual([p.name for p in self._xlsx_files], ["example_validator.xlsx"])
        self.assertIn("JSON               :", out)
        self.assertNotIn("Změna od minula", out)

    def test_invalid_threshold_is_usage_error(self):
        import main as m
        with patch.object(sys, "argv", ["main.py", "https://example.cz",
                                        "--fail-under", "150"]), \
             patch("sys.stderr", new_callable=StringIO):
            with self.assertRaises(SystemExit) as cm:
                m.main()
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
