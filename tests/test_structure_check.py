"""
Unit testy pro structure_check.py.

Spuštění:  python -m pytest tests/
Nebo:      python -m unittest discover tests/
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from structure_check import (check_structure, check_homepage_meta,
                             extract_title, mark_duplicate_titles)
from issues import IssueType


def _has_issue(issues, issue_type: IssueType) -> bool:
    """True pokud seznam obsahuje issue daného typu."""
    return any(i.type == issue_type for i in issues)


def _get_issue(issues, issue_type: IssueType):
    """Vrátí první issue daného typu, nebo None."""
    return next((i for i in issues if i.type == issue_type), None)


class TestH1(unittest.TestCase):
    def test_missing_h1(self):
        html = "<html><body><p>text</p></body></html>"
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.MISSING_H1))

    def test_single_h1_ok(self):
        html = "<html><body><h1>Titulek</h1></body></html>"
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.MISSING_H1))
        self.assertFalse(_has_issue(issues, IssueType.MULTIPLE_H1))

    def test_multiple_h1(self):
        html = "<html><body><h1>A</h1><h1>B</h1><h1>C</h1></body></html>"
        issues = check_structure(html)
        issue = _get_issue(issues, IssueType.MULTIPLE_H1)
        self.assertIsNotNone(issue)
        self.assertEqual(issue.count, 3)


class TestHeadingOrder(unittest.TestCase):
    def test_correct_order(self):
        html = "<h1>A</h1><h2>B</h2><h3>C</h3>"
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.HEADING_SKIP))

    def test_skip_level(self):
        html = "<h1>A</h1><h3>C</h3>"
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.HEADING_SKIP))

    def test_skip_deduplication(self):
        """Stejný skip se nesmí objevit 2× ve výsledku."""
        html = "<h1>A</h1><h3>C</h3><h1>D</h1><h3>E</h3>"
        issues = check_structure(html)
        skip = _get_issue(issues, IssueType.HEADING_SKIP)
        # Jeden unikátní skip: h1→h3
        self.assertEqual(len(skip.items), 1)


class TestEmptyTags(unittest.TestCase):
    def test_empty_p(self):
        html = "<html><body><h1>A</h1><p></p></body></html>"
        issues = check_structure(html)
        empty = [i for i in issues if i.type == IssueType.EMPTY_TAG]
        self.assertTrue(any(i.tag == "p" for i in empty))

    def test_nonempty_p_not_reported(self):
        html = "<html><body><h1>A</h1><p>text</p></body></html>"
        issues = check_structure(html)
        empty = [i for i in issues if i.type == IssueType.EMPTY_TAG]
        self.assertFalse(any(i.tag == "p" for i in empty))

    def test_p_with_child_not_empty(self):
        """<p> s dítětem (i když bez textu) není "prázdný"."""
        html = "<html><body><h1>A</h1><p><img src='x'></p></body></html>"
        issues = check_structure(html)
        empty = [i for i in issues if i.type == IssueType.EMPTY_TAG]
        self.assertFalse(any(i.tag == "p" for i in empty))


class TestDuplicateID(unittest.TestCase):
    def test_duplicate_id(self):
        html = '<h1>A</h1><div id="x">1</div><div id="x">2</div>'
        issues = check_structure(html)
        issue = _get_issue(issues, IssueType.DUPLICATE_ID)
        self.assertIsNotNone(issue)
        self.assertTrue(any("#x" in item for item in issue.items))

    def test_unique_ids_ok(self):
        html = '<h1>A</h1><div id="a">1</div><div id="b">2</div>'
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.DUPLICATE_ID))


class TestMetaDescription(unittest.TestCase):
    def test_missing(self):
        html = "<html><head></head><body><h1>A</h1></body></html>"
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.MISSING_META_DESC))

    def test_empty(self):
        html = '<html><head><meta name="description" content=""></head><body><h1>A</h1></body></html>'
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.EMPTY_META_DESC))

    def test_present(self):
        html = '<html><head><meta name="description" content="Popis webu"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.MISSING_META_DESC))
        self.assertFalse(_has_issue(issues, IssueType.EMPTY_META_DESC))


class TestAltText(unittest.TestCase):
    def test_missing_alt(self):
        html = "<h1>A</h1><img src='/logo.png'>"
        issues = check_structure(html)
        issue = _get_issue(issues, IssueType.MISSING_ALT)
        self.assertIsNotNone(issue)
        self.assertEqual(issue.count, 1)

    def test_empty_alt_ok(self):
        """alt="" je OK (dekorativní obrázek), chybí jen když alt není vůbec."""
        html = "<h1>A</h1><img src='/logo.png' alt=''>"
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.MISSING_ALT))

    def test_alt_present_ok(self):
        html = "<h1>A</h1><img src='/logo.png' alt='Logo'>"
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.MISSING_ALT))


class TestHttpLinks(unittest.TestCase):
    def test_http_detected(self):
        html = '<h1>A</h1><a href="http://insecure.com">link</a>'
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.HTTP_LINK))

    def test_https_ok(self):
        html = '<h1>A</h1><a href="https://secure.com">link</a>'
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.HTTP_LINK))

    def test_uppercase_scheme_detected(self):
        """Schéma je case-insensitive – HTTP:// je stejně nezabezpečené."""
        html = '<h1>A</h1><a href="HTTP://insecure.com">link</a>'
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.HTTP_LINK))

    def test_protocol_relative_not_http_link(self):
        """//host se rozvine na schéma stránky (https) – není to http odkaz."""
        html = '<h1>A</h1><a href="//cdn.example.com/x">link</a>'
        issues = check_structure(html, page_url="https://myweb.cz/")
        self.assertFalse(_has_issue(issues, IssueType.HTTP_LINK))


class TestExternalLinks(unittest.TestCase):
    def test_external_without_target(self):
        html = '<h1>A</h1><a href="https://other.com">link</a>'
        issues = check_structure(html, page_url="https://myweb.cz/")
        self.assertTrue(_has_issue(issues, IssueType.EXTERNAL_LINK))

    def test_external_with_both_ok(self):
        html = '<h1>A</h1><a href="https://other.com" target="_blank" rel="noopener">link</a>'
        issues = check_structure(html, page_url="https://myweb.cz/")
        self.assertFalse(_has_issue(issues, IssueType.EXTERNAL_LINK))

    def test_noreferrer_also_ok(self):
        """rel="noreferrer" implicitně zahrnuje noopener."""
        html = '<h1>A</h1><a href="https://other.com" target="_blank" rel="noreferrer">link</a>'
        issues = check_structure(html, page_url="https://myweb.cz/")
        self.assertFalse(_has_issue(issues, IssueType.EXTERNAL_LINK))

    def test_internal_link_not_flagged(self):
        """Interní odkaz (stejná doména) nemá povinnost mít target/noopener."""
        html = '<h1>A</h1><a href="https://myweb.cz/jina-stranka">link</a>'
        issues = check_structure(html, page_url="https://myweb.cz/")
        self.assertFalse(_has_issue(issues, IssueType.EXTERNAL_LINK))

    def test_www_variant_still_internal(self):
        """myweb.cz a www.myweb.cz je stejná doména."""
        html = '<h1>A</h1><a href="https://www.myweb.cz/page">link</a>'
        issues = check_structure(html, page_url="https://myweb.cz/")
        self.assertFalse(_has_issue(issues, IssueType.EXTERNAL_LINK))

    def test_protocol_relative_external_detected(self):
        """//jina-domena.cz je externí odkaz – prohlížeč doplní schéma stránky."""
        html = '<h1>A</h1><a href="//other.com/page">link</a>'
        issues = check_structure(html, page_url="https://myweb.cz/")
        self.assertTrue(_has_issue(issues, IssueType.EXTERNAL_LINK))

    def test_protocol_relative_same_domain_internal(self):
        html = '<h1>A</h1><a href="//www.myweb.cz/page">link</a>'
        issues = check_structure(html, page_url="https://myweb.cz/")
        self.assertFalse(_has_issue(issues, IssueType.EXTERNAL_LINK))

    def test_uppercase_scheme_external_detected(self):
        html = '<h1>A</h1><a href="HTTPS://other.com/page">link</a>'
        issues = check_structure(html, page_url="https://myweb.cz/")
        self.assertTrue(_has_issue(issues, IssueType.EXTERNAL_LINK))


class TestForbiddenContent(unittest.TestCase):
    def test_lorem_ipsum(self):
        html = "<h1>Titulek</h1><p>Lorem ipsum dolor sit amet</p>"
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.FORBIDDEN_CONTENT))

    def test_asdf(self):
        html = "<h1>Titulek</h1><p>asdf qwerty</p>"
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.FORBIDDEN_CONTENT))

    def test_clean_content_ok(self):
        html = "<h1>Titulek</h1><p>Legitimní obsah webu o nemovitostech.</p>"
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.FORBIDDEN_CONTENT))

    def test_script_content_ignored(self):
        """Hledání v <script> by dalo false-positive."""
        html = '<h1>A</h1><script>var x = "asdf";</script><p>Čistý text</p>'
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.FORBIDDEN_CONTENT))

    def test_common_czech_words_not_flagged(self):
        """'testujeme' / 'text zde' jsou běžná slova – dřív dávala −20 bodů."""
        html = ("<h1>Naše služby</h1>"
                "<p>Testujeme každý vůz před prodejem. Text zde najdete níže. "
                "Nadpis zde není žádný problém.</p>")
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.FORBIDDEN_CONTENT))

    def test_word_boundary_substring_not_flagged(self):
        """'asdf' uvnitř jiného slova (např. kód produktu) není placeholder."""
        html = "<h1>A</h1><p>Produkt XASDFQ-200, model qwertyx.</p>"
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.FORBIDDEN_CONTENT))

    def test_phrase_with_extra_whitespace(self):
        """Fráze rozdělená víc mezerami / novým řádkem se má najít."""
        html = "<h1>A</h1><p>Lorem\n   ipsum dolor</p>"
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.FORBIDDEN_CONTENT))

    def test_case_insensitive(self):
        html = "<h1>A</h1><p>TESTOVACÍ TEXT</p>"
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.FORBIDDEN_CONTENT))


class TestLangAndViewport(unittest.TestCase):
    def test_missing_lang(self):
        html = "<html><body><h1>A</h1></body></html>"
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.MISSING_LANG))

    def test_lang_present(self):
        html = '<html lang="cs"><body><h1>A</h1></body></html>'
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.MISSING_LANG))

    def test_missing_viewport(self):
        html = "<html><head></head><body><h1>A</h1></body></html>"
        issues = check_structure(html)
        self.assertTrue(_has_issue(issues, IssueType.MISSING_VIEWPORT))

    def test_viewport_present(self):
        html = '<html><head><meta name="viewport" content="width=device-width"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html)
        self.assertFalse(_has_issue(issues, IssueType.MISSING_VIEWPORT))


class TestNoindex(unittest.TestCase):
    def test_noindex_detected(self):
        html = '<html><head><meta name="robots" content="noindex"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.NOINDEX))

    def test_noindex_with_nofollow(self):
        html = '<html><head><meta name="robots" content="noindex, nofollow"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.NOINDEX))

    def test_none_directive_detected(self):
        """'none' = ekvivalent 'noindex, nofollow'."""
        html = '<html><head><meta name="robots" content="none"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.NOINDEX))

    def test_googlebot_noindex_detected(self):
        """meta name=googlebot s noindex se taky detekuje."""
        html = '<html><head><meta name="googlebot" content="noindex"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.NOINDEX))

    def test_nofollow_only_not_flagged(self):
        """Samotné nofollow bez noindex se neflaguje – indexaci to neblokuje."""
        html = '<html><head><meta name="robots" content="nofollow"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://example.cz/")
        self.assertFalse(_has_issue(issues, IssueType.NOINDEX))

    def test_index_follow_ok(self):
        html = '<html><head><meta name="robots" content="index, follow"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://example.cz/")
        self.assertFalse(_has_issue(issues, IssueType.NOINDEX))

    def test_no_robots_meta_ok(self):
        """Žádné robots meta = výchozí chování (indexovat). OK."""
        html = "<html><head></head><body><h1>A</h1></body></html>"
        issues = check_structure(html, page_url="https://example.cz/")
        self.assertFalse(_has_issue(issues, IssueType.NOINDEX))

    def test_dev_domain_poskireal_ignored(self):
        """Na *.poskireal.cz je noindex záměr – nehlásíme."""
        html = '<html><head><meta name="robots" content="noindex"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://www.example.poskireal.cz/")
        self.assertFalse(_has_issue(issues, IssueType.NOINDEX))

    def test_dev_domain_cz_dev_poski_ignored(self):
        """Na *.cz.dev.poski.com je noindex záměr – nehlásíme."""
        html = '<html><head><meta name="robots" content="noindex"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://www.example.cz.dev.poski.com/")
        self.assertFalse(_has_issue(issues, IssueType.NOINDEX))

    def test_other_poski_domain_still_checked(self):
        """Normální poski.com (ne dev/staging) noindex check probíhá – chytne se."""
        html = '<html><head><meta name="robots" content="noindex"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://www.poski.com/")
        self.assertTrue(_has_issue(issues, IssueType.NOINDEX))

    def test_capitalized_noindex_detected(self):
        """Velká písmena ve content – kvůli toho používáme .lower()."""
        html = '<html><head><meta name="ROBOTS" content="NOINDEX"></head><body><h1>A</h1></body></html>'
        issues = check_structure(html, page_url="https://example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.NOINDEX))


class TestStagingUrl(unittest.TestCase):
    """Detekce odkazů/zdrojů ukazujících na dev/staging domény."""

    def test_canonical_to_staging_detected(self):
        """Canonical pointující na dev doménu = SEO katastrofa."""
        html = '''
        <html><head>
            <link rel="canonical" href="https://www.example.cz.dev.poski.com/page/">
        </head><body><h1>A</h1></body></html>
        '''
        issues = check_structure(html, page_url="https://www.example.cz/")
        issue = _get_issue(issues, IssueType.STAGING_URL)
        self.assertIsNotNone(issue)
        self.assertTrue(any("canonical" in item for item in issue.items))

    def test_og_image_to_staging_detected(self):
        html = '''
        <html><head>
            <meta property="og:image" content="https://example.poskireal.cz/banner.jpg">
        </head><body><h1>A</h1></body></html>
        '''
        issues = check_structure(html, page_url="https://www.example.cz/")
        issue = _get_issue(issues, IssueType.STAGING_URL)
        self.assertIsNotNone(issue)
        self.assertTrue(any("og:image" in item for item in issue.items))

    def test_twitter_image_detected(self):
        html = '''
        <html><head>
            <meta name="twitter:image" content="https://example.poskireal.cz/twit.jpg">
        </head><body><h1>A</h1></body></html>
        '''
        issues = check_structure(html, page_url="https://www.example.cz/")
        issue = _get_issue(issues, IssueType.STAGING_URL)
        self.assertIsNotNone(issue)

    def test_a_href_to_staging_detected(self):
        html = '<h1>A</h1><a href="https://example.cz.dev.poski.com/kontakt/">Kontakt</a>'
        issues = check_structure(html, page_url="https://www.example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.STAGING_URL))

    def test_img_src_to_staging_detected(self):
        html = '<h1>A</h1><img src="https://example.poskireal.cz/logo.png" alt="logo">'
        issues = check_structure(html, page_url="https://www.example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.STAGING_URL))

    def test_form_action_to_staging_detected(self):
        """Formulář pošle data na staging — vážná chyba (data se ztratí)."""
        html = '<h1>A</h1><form action="https://example.poskireal.cz/odeslat"><input></form>'
        issues = check_structure(html, page_url="https://www.example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.STAGING_URL))

    def test_iframe_to_staging_detected(self):
        html = '<h1>A</h1><iframe src="https://example.cz.dev.poski.com/embed"></iframe>'
        issues = check_structure(html, page_url="https://www.example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.STAGING_URL))

    def test_srcset_url_to_staging_detected(self):
        """Srcset může obsahovat víc URL oddělených čárkou — chytí se každá."""
        html = '''<h1>A</h1>
        <img srcset="https://example.poskireal.cz/small.jpg 1x,
                     https://example.poskireal.cz/big.jpg 2x"
             alt="responsive">'''
        issues = check_structure(html, page_url="https://www.example.cz/")
        issue = _get_issue(issues, IssueType.STAGING_URL)
        self.assertIsNotNone(issue)
        # Obě URL by měly být zachyceny
        self.assertGreaterEqual(issue.count, 2)

    def test_data_src_lazy_loading_detected(self):
        """Lazy loading používá data-src — taky chytíme."""
        html = '<h1>A</h1><img data-src="https://example.poskireal.cz/lazy.jpg" alt="x">'
        issues = check_structure(html, page_url="https://www.example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.STAGING_URL))

    def test_relative_url_not_flagged(self):
        """Relativní URL nemají doménu — neflagujeme."""
        html = '''
        <h1>A</h1>
        <a href="/jina-stranka">Vnitřní</a>
        <img src="../images/logo.png" alt="logo">
        <link rel="canonical" href="/aktualni-stranka">
        '''
        issues = check_structure(html, page_url="https://www.example.cz/")
        self.assertFalse(_has_issue(issues, IssueType.STAGING_URL))

    def test_production_url_not_flagged(self):
        """URL na produkční doménu nebo libovolnou jinou (ne dev) je OK."""
        html = '''
        <h1>A</h1>
        <a href="https://www.example.cz/jina">Vnitřní</a>
        <img src="https://cdn.example.com/logo.png" alt="logo">
        <link rel="canonical" href="https://www.example.cz/">
        '''
        issues = check_structure(html, page_url="https://www.example.cz/")
        self.assertFalse(_has_issue(issues, IssueType.STAGING_URL))

    def test_dev_domain_audit_skipped(self):
        """Když auditujeme samotnou dev doménu, staging URL check se přeskakuje."""
        html = '''
        <h1>A</h1>
        <link rel="canonical" href="https://example.poskireal.cz/page">
        <img src="https://example.cz.dev.poski.com/img.jpg" alt="x">
        '''
        # Auditovaná stránka je sama na poskireal.cz → není to chyba
        issues = check_structure(html, page_url="https://example.poskireal.cz/")
        self.assertFalse(_has_issue(issues, IssueType.STAGING_URL))

    def test_protocol_relative_url_handled(self):
        """Protokol-relativní URL (//example.com/...) se správně rozpozná."""
        html = '<h1>A</h1><img src="//example.poskireal.cz/img.jpg" alt="x">'
        issues = check_structure(html, page_url="https://www.example.cz/")
        self.assertTrue(_has_issue(issues, IssueType.STAGING_URL))

    def test_multiple_findings_grouped(self):
        """Víc nálezů na jedné stránce = jeden Issue se seznamem."""
        html = '''
        <h1>A</h1>
        <link rel="canonical" href="https://example.poskireal.cz/">
        <img src="https://example.poskireal.cz/img1.jpg" alt="x">
        <a href="https://example.poskireal.cz/page">link</a>
        '''
        issues = check_structure(html, page_url="https://www.example.cz/")
        staging_issues = [i for i in issues if i.type == IssueType.STAGING_URL]
        self.assertEqual(len(staging_issues), 1)
        self.assertEqual(staging_issues[0].count, 3)

    def test_findings_have_context_label(self):
        """Každý nález má v sobě kontext (canonical, og:image, <a href>, ...)."""
        html = '''
        <h1>A</h1>
        <link rel="canonical" href="https://example.poskireal.cz/c">
        <a href="https://example.poskireal.cz/a">link</a>
        '''
        issues = check_structure(html, page_url="https://www.example.cz/")
        issue = _get_issue(issues, IssueType.STAGING_URL)
        self.assertIsNotNone(issue)
        # Položky mají formát "[<kontext>] <url>"
        self.assertTrue(all(item.startswith("[") for item in issue.items))


class TestTitle(unittest.TestCase):
    """Kontrola 14: <title> na každé stránce."""

    def test_missing_title(self):
        issues = check_structure("<html><head></head><body><h1>x</h1></body></html>",
                                 page_url="https://example.cz/a")
        self.assertTrue(_has_issue(issues, IssueType.MISSING_TITLE))

    def test_empty_title(self):
        issues = check_structure("<html><head><title>   </title></head></html>",
                                 page_url="https://example.cz/a")
        self.assertTrue(_has_issue(issues, IssueType.MISSING_TITLE))

    def test_present_title_ok(self):
        issues = check_structure("<html><head><title>Kontakt</title></head></html>",
                                 page_url="https://example.cz/a")
        self.assertFalse(_has_issue(issues, IssueType.MISSING_TITLE))

    def test_svg_title_in_body_does_not_count(self):
        """<title> uvnitř inline <svg> není titulek stránky."""
        html = ("<html><head></head><body>"
                "<svg><title>Ikona</title></svg></body></html>")
        issues = check_structure(html, page_url="https://example.cz/a")
        self.assertTrue(_has_issue(issues, IssueType.MISSING_TITLE))

    def test_extract_title_collapses_whitespace(self):
        html = "<html><head><title>  Naše \n  firma </title></head><body></body></html>"
        self.assertEqual(extract_title(html), "Naše firma")
        self.assertEqual(extract_title("<p>bez head</p>"), "")

    def test_extract_title_ignores_svg_after_head(self):
        html = ("<html><head></head><body>"
                "<svg><title>Ikona</title></svg></body></html>")
        self.assertEqual(extract_title(html), "")


class TestDuplicateTitles(unittest.TestCase):
    """Kontrola 16: stejný <title> napříč webem (mark_duplicate_titles)."""

    @staticmethod
    def _r(url, title, category="ok"):
        return {"url": url, "title": title, "w3c_category": category,
                "structure_issues": []}

    def test_marks_all_pages_in_group(self):
        results = [self._r("https://e.cz/a", "Firma"),
                   self._r("https://e.cz/b", "  firma "),   # case + mezery
                   self._r("https://e.cz/c", "Kontakt")]
        n = mark_duplicate_titles(results)
        self.assertEqual(n, 1)
        a, b, c = (r["structure_issues"] for r in results)
        self.assertTrue(_has_issue(a, IssueType.DUPLICATE_TITLE))
        self.assertTrue(_has_issue(b, IssueType.DUPLICATE_TITLE))
        self.assertEqual(c, [])
        ia = _get_issue(a, IssueType.DUPLICATE_TITLE)
        self.assertEqual(ia.items, ["https://e.cz/b"])   # ostatní URL ve skupině
        self.assertEqual(ia.count, 2)
        # Stejný label pro celou skupinu → v Excelu jeden řádek
        self.assertEqual(ia.label, _get_issue(b, IssueType.DUPLICATE_TITLE).label)
        self.assertIn('"Firma"', ia.label)

    def test_empty_and_unreachable_pages_skipped(self):
        results = [self._r("https://e.cz/a", ""),
                   self._r("https://e.cz/b", ""),
                   self._r("https://e.cz/c", "X", category="validator_error"),
                   self._r("https://e.cz/d", "X")]
        self.assertEqual(mark_duplicate_titles(results), 0)
        for r in results:
            self.assertEqual(r["structure_issues"], [])

    def test_missing_title_key_tolerated(self):
        """Starší výsledky bez klíče 'title' nesmí spadnout."""
        results = [{"url": "u", "w3c_category": "ok", "structure_issues": []}]
        self.assertEqual(mark_duplicate_titles(results), 0)


class TestCanonical(unittest.TestCase):
    """Kontrola 15: <link rel="canonical">."""
    PAGE = "https://www.example.cz/o-nas/"

    def _check(self, head, page_url=PAGE):
        html = f"<html><head><title>T</title>{head}</head><body><h1>x</h1></body></html>"
        return check_structure(html, page_url=page_url)

    def test_missing_canonical(self):
        issues = self._check("")
        self.assertTrue(_has_issue(issues, IssueType.MISSING_CANONICAL))

    def test_empty_href_counts_as_missing(self):
        issues = self._check('<link rel="canonical" href="">')
        issue = _get_issue(issues, IssueType.MISSING_CANONICAL)
        self.assertIsNotNone(issue)
        self.assertIn("prázdný", issue.detail)

    def test_self_canonical_ok_despite_www_and_slash(self):
        issues = self._check('<link rel="canonical" href="https://example.cz/o-nas">')
        for t in (IssueType.MISSING_CANONICAL, IssueType.CANONICAL_MISMATCH,
                  IssueType.CANONICAL_HTTP):
            self.assertFalse(_has_issue(issues, t), t)

    def test_relative_self_canonical_ok(self):
        issues = self._check('<link rel="canonical" href="/o-nas/">')
        self.assertFalse(_has_issue(issues, IssueType.CANONICAL_MISMATCH))

    def test_canonical_to_other_page_is_mismatch(self):
        issues = self._check('<link rel="canonical" href="https://example.cz/">')
        issue = _get_issue(issues, IssueType.CANONICAL_MISMATCH)
        self.assertIsNotNone(issue)
        self.assertEqual(issue.items, ["https://example.cz/"])

    def test_canonical_with_query_is_mismatch(self):
        issues = self._check('<link rel="canonical" href="https://example.cz/o-nas?page=2">')
        self.assertTrue(_has_issue(issues, IssueType.CANONICAL_MISMATCH))

    def test_multiple_canonicals_reported(self):
        issues = self._check('<link rel="canonical" href="https://example.cz/o-nas">'
                             '<link rel="canonical" href="https://example.cz/">')
        issue = _get_issue(issues, IssueType.CANONICAL_MISMATCH)
        self.assertIsNotNone(issue)
        self.assertIn("2x", issue.detail)

    def test_http_canonical_on_https_page(self):
        issues = self._check('<link rel="canonical" href="http://example.cz/o-nas">')
        self.assertTrue(_has_issue(issues, IssueType.CANONICAL_HTTP))
        # Jinak míří správně → žádný mismatch navíc
        self.assertFalse(_has_issue(issues, IssueType.CANONICAL_MISMATCH))

    def test_http_canonical_on_http_page_is_fine(self):
        issues = self._check('<link rel="canonical" href="http://example.cz/o-nas">',
                             page_url="http://example.cz/o-nas")
        self.assertFalse(_has_issue(issues, IssueType.CANONICAL_HTTP))

    def test_staging_canonical_not_double_reported(self):
        """Canonical na staging hlásí kontrola 13 – mismatch se nepřidává."""
        issues = self._check('<link rel="canonical" href="https://web.poskireal.cz/o-nas">')
        self.assertTrue(_has_issue(issues, IssueType.STAGING_URL))
        self.assertFalse(_has_issue(issues, IssueType.CANONICAL_MISMATCH))

    def test_skipped_on_local_and_dev_hosts(self):
        for url in ("http://localhost:8000/o-nas", "https://web.poskireal.cz/o-nas"):
            issues = self._check("", page_url=url)
            self.assertFalse(_has_issue(issues, IssueType.MISSING_CANONICAL), url)
            issues = self._check('<link rel="canonical" href="https://example.cz/o-nas">',
                                 page_url=url)
            self.assertFalse(_has_issue(issues, IssueType.CANONICAL_MISMATCH), url)


class TestOpenGraph(unittest.TestCase):
    PAGE = "https://www.example.cz/o-nas/"

    def _check(self, head_extra: str):
        html = (f"<html lang='cs'><head><title>T</title>{head_extra}</head>"
                "<body><h1>x</h1></body></html>")
        return _get_issue(check_structure(html, page_url=self.PAGE), IssueType.MISSING_OG)

    def test_all_missing(self):
        issue = self._check("")
        self.assertIsNotNone(issue)
        self.assertEqual(issue.items, ["chybí og:title", "chybí og:description", "chybí og:image"])
        self.assertEqual(issue.count, 3)

    def test_complete_is_ok(self):
        issue = self._check('<meta property="og:title" content="A">'
                            '<meta property="og:description" content="B">'
                            '<meta property="og:image" content="https://example.cz/og.png">')
        self.assertIsNone(issue)

    def test_name_attribute_tolerated_and_empty_content_missing(self):
        issue = self._check('<meta name="og:title" content="A">'
                            '<meta property="og:description" content="  ">'
                            '<meta property="og:image" content="//cdn.example.cz/og.png">')
        self.assertEqual(issue.items, ["chybí og:description"])

    def test_relative_og_image_reported(self):
        issue = self._check('<meta property="og:title" content="A">'
                            '<meta property="og:description" content="B">'
                            '<meta property="og:image" content="/img/og.png">')
        self.assertEqual(issue.items, ["og:image není absolutní URL: /img/og.png"])
        self.assertIn("není absolutní", issue.detail)


class TestImageDimensions(unittest.TestCase):
    def _check(self, body: str):
        html = f"<html lang='cs'><head><title>T</title></head><body><h1>x</h1>{body}</body></html>"
        return _get_issue(check_structure(html, page_url="https://example.cz/"),
                          IssueType.IMG_NO_DIMENSIONS)

    def test_missing_dimensions_listed(self):
        issue = self._check('<img src="/a.png" alt=""><img src="/b.png" width="10" alt="">'
                            '<img alt="">')
        self.assertEqual(issue.items, ["/a.png", "/b.png", "(bez src)"])
        self.assertEqual(issue.count, 3)

    def test_attributes_or_inline_style_ok(self):
        issue = self._check('<img src="/a.png" width="10" height="5" alt="">'
                            '<img src="/b.png" style="width: 10px; height:5px" alt="">')
        self.assertIsNone(issue)

    def test_style_with_only_width_is_reported(self):
        issue = self._check('<img src="/a.png" style="width:100%" alt="">'
                            '<img src="data:image/gif;base64,R0lGOD" alt="">')
        self.assertEqual(issue.items, ["/a.png", "data:… (inline obrázek)"])


class TestHomepageMeta(unittest.TestCase):
    def test_title_too_short(self):
        html = "<html><head><title>Krátký</title></head></html>"
        issues = check_homepage_meta(html)
        self.assertTrue(any("krátký" in i for i in issues))

    def test_title_too_long(self):
        html = f"<html><head><title>{'x' * 100}</title></head></html>"
        issues = check_homepage_meta(html)
        self.assertTrue(any("dlouhý" in i for i in issues))

    def test_title_ok(self):
        html = "<html><head><title>Naše webová stránka o nemovitostech v Praze</title></head></html>"
        issues = check_homepage_meta(html)
        self.assertTrue(any("v pořádku" in i for i in issues))


if __name__ == "__main__":
    unittest.main()