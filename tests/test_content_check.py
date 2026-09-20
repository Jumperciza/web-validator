"""
Testy pro content_check.py – rozšířená detekce testovacího obsahu.

Každá skupina má testy pozitivní (musí chytit) i negativní (běžný český
web NESMÍ hlásit nic – falešné poplachy jsou horší než nechycený případ).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from content_check import check_test_content, _is_placeholder_url
from structure_check import check_structure
from issues import IssueType
from stats import page_score


def _types(html: str) -> set:
    return {i.type for i in check_test_content(html)}


def _issue(html: str, t: IssueType):
    return next((i for i in check_test_content(html) if i.type == t), None)


# Realistická „čistá“ stránka – nic z ní se nesmí hlásit.
CLEAN_PAGE = """<!DOCTYPE html><html lang="cs"><head>
<meta charset="utf-8"><title>Prodej bytu 3+kk, Brno-Židenice | Reality Novák</title>
<meta name="description" content="Nabízíme k prodeji světlý byt 3+kk o výměře 78 m² v klidné části Židenic. Cena 6 490 000 Kč.">
<meta property="og:title" content="Prodej bytu 3+kk, Brno-Židenice">
<meta property="og:description" content="Světlý byt 3+kk, 78 m², balkon, sklep.">
<meta property="og:image" content="https://www.reality-novak.cz/media/byty/zidenice-01.jpg">
</head><body>
<header><a href="/"><img src="/img/logo.svg" alt="Reality Novák – logo"></a></header>
<h1>Prodej bytu 3+kk, 78 m², Brno-Židenice</h1>
<p>Testujeme každý vůz před prodejem. Sleva 20 % platí do 30. 9. Cena je 100 % konečná.</p>
<p>Byt je v přípravě k nastěhování, stav domu je po rekonstrukci. Nadpis 2. kapitoly zní jinak.</p>
<p>Náš tým: Jan Novák, tel. 777 123 456, e-mail info@reality-novak.cz.</p>
<img src="/media/byty/zidenice-01.jpg" alt="Obývací pokoj s kuchyňským koutem" width="800" height="600">
<img src="/img/placeholder.png" data-src="/media/byty/zidenice-02.jpg" alt="Ložnice">
<img src="/media/testimonial-jana.jpg" alt="Jana Dvořáková, spokojená klientka">
<img src="/media/test-drive-bmw.jpg" alt="Testovací jízda BMW">
<a href="/kontakt">Kontakt</a> <a href="https://www.facebook.com/realitynovak">Facebook</a>
<p>Ceny uvádíme v Kč; nulová provize. Naše nabídka null bodů. Pole Array se zde nepoužívá.</p>
<p>Programátoři hledají chyby, warning svítí na palubní desce, a fatální error se stal jinde.</p>
<code>{{ user.name }} – ukázka Twig syntaxe v článku</code>
<pre>Warning: include(): failed in /var/www/x.php on line 3</pre>
<footer><p>&copy; 2026 Reality Novák. Hello, world of homes. Read more →</p></footer>
</body></html>"""


class TestCleanPage(unittest.TestCase):
    def test_nothing_reported(self):
        self.assertEqual(_types(CLEAN_PAGE), set())

    def test_structure_check_integration_clean(self):
        new_types = {IssueType.DEFAULT_META_TEXT, IssueType.PLACEHOLDER_IMAGE,
                     IssueType.TEMPLATE_VARIABLE, IssueType.JS_VALUE_IN_TEXT,
                     IssueType.DEV_ERROR_OUTPUT, IssueType.DEFAULT_CMS_TEXT}
        types = {i.type for i in check_structure(CLEAN_PAGE, "https://www.reality-novak.cz/byt")}
        self.assertEqual(types & new_types, set())

    def test_original_soup_not_modified(self):
        # check_structure po content_check pořád vidí <script>/<code> (pracuje s kopií)
        html = "<html><body><h1>A</h1><p>x</p><code>{{ a }}</code></body></html>"
        issues = check_structure(html)
        self.assertFalse(any(i.type == IssueType.TEMPLATE_VARIABLE for i in issues))
        self.assertFalse(any(i.type == IssueType.EMPTY_TAG for i in issues))


class TestDefaultValues(unittest.TestCase):
    def test_default_title(self):
        for t in ("Document", "Untitled", " Nadpis stránky ", "Nová stránka", "Home"):
            html = f"<html><head><title>{t}</title></head><body><h1>x</h1></body></html>"
            issue = _issue(html, IssueType.DEFAULT_META_TEXT)
            self.assertIsNotNone(issue, t)
            self.assertTrue(issue.items[0].startswith("<title>"), issue.items)

    def test_title_containing_default_word_ok(self):
        html = "<html><head><title>Home Credit – půjčky</title></head><body></body></html>"
        self.assertNotIn(IssueType.DEFAULT_META_TEXT, _types(html))
        html = "<html><head><title>Dokumenty ke stažení</title></head><body></body></html>"
        self.assertNotIn(IssueType.DEFAULT_META_TEXT, _types(html))

    def test_default_description_and_og(self):
        html = ('<html><head><meta name="description" content="Popis stránky">'
                '<meta property="og:title" content="Untitled">'
                '<meta property="og:description" content="Description"></head><body></body></html>')
        issue = _issue(html, IssueType.DEFAULT_META_TEXT)
        self.assertEqual(issue.count, 3)
        self.assertTrue(any(i.startswith("meta description") for i in issue.items))
        self.assertTrue(any(i.startswith("og:title") for i in issue.items))

    def test_default_alt_counted(self):
        html = ('<html><body><img src="a.jpg" alt="image"><img src="b.jpg" alt="Image">'
                '<img src="c.jpg" alt="obrázek"><img src="d.jpg" alt="Logo firmy">'
                '<img src="e.jpg" alt=""><img src="f.jpg"></body></html>')
        issue = _issue(html, IssueType.DEFAULT_META_TEXT)
        self.assertEqual(issue.count, 3)          # "image", "Image", "obrázek"
        self.assertIn('alt="obrázek"', issue.items)
        # prázdný alt (dekorativní) a chybějící alt sem nepatří
        self.assertFalse(any('alt=""' in i for i in issue.items))

    def test_foto_alt_not_reported(self):
        html = '<html><body><img src="a.jpg" alt="foto"><img src="b.jpg" alt="Fotografie domu"></body></html>'
        self.assertNotIn(IssueType.DEFAULT_META_TEXT, _types(html))


class TestPlaceholderImages(unittest.TestCase):
    def test_placeholder_hosts(self):
        for url in ("https://via.placeholder.com/300x200", "//placehold.co/600x400",
                    "https://picsum.photos/200/300", "http://dummyimage.com/600x400/000/fff",
                    "https://placekitten.com/200/300", "https://source.unsplash.com/random/800x600",
                    "https://unsplash.com/random/800x600", "https://loremflickr.com/320/240"):
            self.assertTrue(_is_placeholder_url(url), url)

    def test_placeholder_filenames(self):
        for url in ("/img/dummy.jpg", "/img/dummy-1.png", "/img/sample_image.webp",
                    "/img/lorem.jpg", "/img/placeholder.png", "/uploads/test.jpg",
                    "/uploads/test1.jpg", "/uploads/test-02.png", "/uploads/test_3.webp",
                    "https://www.example.cz/media/Sample.JPG"):
            self.assertTrue(_is_placeholder_url(url), url)

    def test_legit_filenames(self):
        for url in ("/img/testimonial.jpg", "/img/test-drive.jpg", "/img/testovani-aut.jpg",
                    "/img/samples-of-work.jpg", "/img/dummyhead-band.jpg", "/img/logo.svg",
                    "/img/no-image.png", "/img/unsplash-photo.jpg", "data:image/png;base64,AAAA",
                    "https://images.unsplash.com/photo-123", "/img/placeholders-guide.pdf", ""):
            self.assertFalse(_is_placeholder_url(url), url)

    def test_img_and_og_image(self):
        html = ('<html><head><meta property="og:image" content="https://via.placeholder.com/1200x630"></head>'
                '<body><img src="https://picsum.photos/300"><img src="/img/dummy.jpg">'
                '<img src="/img/real.jpg" srcset="/img/real.jpg 1x, https://placehold.co/600 2x"></body></html>')
        issue = _issue(html, IssueType.PLACEHOLDER_IMAGE)
        self.assertEqual(issue.count, 4)
        self.assertTrue(any(i.startswith("og:image") for i in issue.items))

    def test_lazy_placeholder_src_ok_but_data_src_checked(self):
        html = '<html><body><img src="/img/placeholder.png" data-src="/media/real.jpg"></body></html>'
        self.assertNotIn(IssueType.PLACEHOLDER_IMAGE, _types(html))
        html = '<html><body><img src="/img/placeholder.png" data-src="https://picsum.photos/400"></body></html>'
        self.assertIn(IssueType.PLACEHOLDER_IMAGE, _types(html))
        # placeholder.png bez lazy-loadu = opravdový placeholder
        html = '<html><body><img src="/img/placeholder.png" alt="Tým"></body></html>'
        self.assertIn(IssueType.PLACEHOLDER_IMAGE, _types(html))


class TestTemplateVariables(unittest.TestCase):
    def test_detects_various_syntaxes(self):
        cases = {
            "<p>Vítejte, {{ user.name }}!</p>":          "{{ … }}",
            "<p>{% if logged %}Ahoj{% endif %}</p>":     "{% … %}",
            "<p>Cena: [[ price ]] Kč</p>":               "[[ … ]]",
            "<p>Dobrý den, %JMENO%!</p>":                "%NAME%",
            "<p>Celkem ${total} Kč</p>":                 "${ … }",
        }
        for body, kind in cases.items():
            issue = _issue(f"<html><body>{body}</body></html>", IssueType.TEMPLATE_VARIABLE)
            self.assertIsNotNone(issue, body)
            self.assertTrue(issue.items[0].startswith(kind), (body, issue.items))

    def test_in_title_and_meta(self):
        html = ('<html><head><title>{{ page.title }}</title>'
                '<meta name="description" content="%DESCRIPTION%"></head><body></body></html>')
        issue = _issue(html, IssueType.TEMPLATE_VARIABLE)
        self.assertEqual(issue.count, 2)

    def test_php_tag_in_html(self):
        html = "<html><body><h1>Nabídka</h1><?php echo $title; ?><p>text</p></body></html>"
        issue = _issue(html, IssueType.TEMPLATE_VARIABLE)
        self.assertIsNotNone(issue)
        self.assertTrue(issue.items[0].startswith("<?php"))
        html = "<html><body><p>Cena: <?= $price ?> Kč</p></body></html>"
        self.assertIn(IssueType.TEMPLATE_VARIABLE, _types(html))

    def test_php_in_code_block_ok(self):
        html = "<html><body><pre>&lt;?php echo 1; ?&gt;</pre><code><?php echo 2; ?></code></body></html>"
        self.assertNotIn(IssueType.TEMPLATE_VARIABLE, _types(html))

    def test_percent_in_normal_text_ok(self):
        html = ("<html><body><p>Sleva 20% na vše, 100%DPH% se nepočítá, 5 % úrok. "
                "Rozměry 50%x30%. Kód %20 v URL.</p></body></html>")
        self.assertNotIn(IssueType.TEMPLATE_VARIABLE, _types(html))

    def test_vue_template_ok(self):
        html = '<html><body><div id="app" v-cloak><p>{{ message }}</p></div></body></html>'
        self.assertNotIn(IssueType.TEMPLATE_VARIABLE, _types(html))
        html = '<html><body><div ng-app="x"><p ng-if="ok">{{ item.name }}</p></div></body></html>'
        self.assertNotIn(IssueType.TEMPLATE_VARIABLE, _types(html))

    def test_template_tag_and_script_ignored(self):
        html = ('<html><body><template id="row"><p>{{ name }}</p></template>'
                '<script type="text/x-template">{{ a }}</script><p>ok</p></body></html>')
        self.assertNotIn(IssueType.TEMPLATE_VARIABLE, _types(html))


class TestJsValues(unittest.TestCase):
    def test_whole_node_values(self):
        for txt in ("undefined", "null", "NaN", "[object Object]", "Array", "undefined undefined"):
            html = f"<html><body><h2>Cena</h2><span class='price'>{txt}</span></body></html>"
            issue = _issue(html, IssueType.JS_VALUE_IN_TEXT)
            self.assertIsNotNone(issue, txt)
            self.assertIn("<span>", issue.items[0])

    def test_value_with_unit(self):
        for txt in ("undefined Kč", "NaN m²", "null %", "undefined ks", "NaN Kč."):
            html = f"<html><body><p>{txt}</p></body></html>"
            self.assertIn(IssueType.JS_VALUE_IN_TEXT, _types(html), txt)

    def test_object_object_anywhere(self):
        html = "<html><body><p>Vybráno: [object Object], [object Object]</p></body></html>"
        self.assertIn(IssueType.JS_VALUE_IN_TEXT, _types(html))

    def test_in_url_and_alt(self):
        html = ('<html><body><a href="/detail/undefined">Detail</a>'
                '<img src="/img/null.jpg" alt="undefined"></body></html>')
        issue = _issue(html, IssueType.JS_VALUE_IN_TEXT)
        self.assertEqual(issue.count, 3)

    def test_words_in_sentences_ok(self):
        html = ("<html><body><p>Naše nabídka null bodů, undefined behaviour je pojem z C, "
                "pole Array v JavaScriptu. Nan Goldin fotografka.</p>"
                "<a href='/nullova-provize'>Nulová provize</a><a href='/annulled'>x</a>"
                "<code>undefined</code></body></html>")
        self.assertNotIn(IssueType.JS_VALUE_IN_TEXT, _types(html))


class TestDevErrors(unittest.TestCase):
    def test_php_warning_default_format(self):
        html = ('<html><body><br />\n<b>Warning</b>:  Undefined variable $x in '
                '<b>/var/www/html/index.php</b> on line <b>12</b><br />\n<h1>Web</h1></body></html>')
        issue = _issue(html, IssueType.DEV_ERROR_OUTPUT)
        self.assertIsNotNone(issue)
        self.assertTrue(any(i.startswith("PHP chyba") for i in issue.items), issue.items)

    def test_fatal_error_and_stack_trace(self):
        html = ("<html><body>Fatal error: Uncaught Error: Call to undefined function foo() in "
                "/app/x.php:5 Stack trace: #0 {main} thrown in /app/x.php on line 5</body></html>")
        issue = _issue(html, IssueType.DEV_ERROR_OUTPUT)
        kinds = {i.split(":")[0] for i in issue.items}
        self.assertTrue({"PHP chyba", "Uncaught", "Stack trace"} <= kinds, kinds)

    def test_var_dump_print_r_sql_tracy(self):
        self.assertIn(IssueType.DEV_ERROR_OUTPUT,
                      _types('<html><body>array(2) { ["a"]=> string(1) "b" }</body></html>'))
        self.assertIn(IssueType.DEV_ERROR_OUTPUT,
                      _types("<html><body>Array ( [id] => 5 [name] => x )</body></html>"))
        self.assertIn(IssueType.DEV_ERROR_OUTPUT,
                      _types("<html><body>You have an error in your SQL syntax; check the manual</body></html>"))
        self.assertIn(IssueType.DEV_ERROR_OUTPUT,
                      _types('<html><body><div id="tracy-bs"></div></body></html>'))
        self.assertIn(IssueType.DEV_ERROR_OUTPUT,
                      _types("<html><body>Whoops, looks like something went wrong.</body></html>"))

    def test_error_text_in_code_blocks_ok(self):
        html = ("<html><body><h1>Jak opravit PHP chyby</h1>"
                "<pre>Warning: include(): failed in /var/www/x.php on line 3</pre>"
                "<code>Fatal error: Uncaught Exception in a.php on line 1</code>"
                "<script>console.log('x'); var a = undefined;</script></body></html>")
        self.assertNotIn(IssueType.DEV_ERROR_OUTPUT, _types(html))

    def test_normal_text_with_keywords_ok(self):
        html = ("<html><body><p>Warning: this product contains nuts. Notice board. "
                "Deprecated models are on sale. The array of colours is stunning. "
                "Undefined future awaits. Call stack of cards.</p></body></html>")
        self.assertNotIn(IssueType.DEV_ERROR_OUTPUT, _types(html))


class TestCmsDefaults(unittest.TestCase):
    def test_wordpress_defaults(self):
        html = ('<html><head><title>Můj web – Just another WordPress site</title></head><body>'
                '<h2><a href="/hello-world/">Hello world!</a></h2>'
                '<p>Welcome to WordPress. This is your first post. Edit or delete it, then start writing!</p>'
                '<li><a href="/sample-page/">Sample Page</a></li></body></html>')
        issue = _issue(html, IssueType.DEFAULT_CMS_TEXT)
        self.assertIsNotNone(issue)
        joined = " | ".join(issue.items)
        self.assertIn("Hello world!", joined)
        self.assertIn("tagline", joined)
        self.assertIn("Sample Page", joined)

    def test_template_placeholders_czech(self):
        html = ("<html><body><h2>Nadpis stránky</h2><p>Text odstavce</p>"
                "<h3>Název sekce</h3><p>Web je ve výstavbě.</p></body></html>")
        issue = _issue(html, IssueType.DEFAULT_CMS_TEXT)
        self.assertGreaterEqual(issue.count, 3)

    def test_under_construction_english(self):
        html = "<html><body><h1>Site under construction</h1></body></html>"
        self.assertIn(IssueType.DEFAULT_CMS_TEXT, _types(html))

    def test_normal_text_ok(self):
        html = ("<html><body><h1>Nadpis stránky o nás</h1><p>Hello world of coffee.</p>"
                "<p>Coming soon: nová kolekce.</p><p>Sample pages from our catalogue.</p>"
                "<a href='/vice'>Read more</a><button>Tlačítko</button>"
                "<p>Heading 2 is used in Word documents.</p></body></html>")
        self.assertNotIn(IssueType.DEFAULT_CMS_TEXT, _types(html))


class TestScoring(unittest.TestCase):
    def test_penalties_applied(self):
        html = ('<html lang="cs"><head><title>Document</title><meta name="viewport" content="x">'
                '<meta name="description" content="Dobrý popis stránky o nás a našich službách."></head>'
                '<body><h1>Vítejte</h1><p>Cena: undefined Kč</p>'
                '<img src="https://picsum.photos/200" alt="Byt" width="1" height="1"></body></html>')
        issues = check_structure(html, "http://localhost/")
        types = {i.type for i in issues}
        self.assertTrue({IssueType.DEFAULT_META_TEXT, IssueType.JS_VALUE_IN_TEXT,
                         IssueType.PLACEHOLDER_IMAGE} <= types)
        clean = page_score({"w3c_category": "ok", "w3c_errors": [],
                            "structure_issues": [i for i in issues if i.type not in
                                                 (IssueType.DEFAULT_META_TEXT, IssueType.JS_VALUE_IN_TEXT,
                                                  IssueType.PLACEHOLDER_IMAGE)]})
        dirty = page_score({"w3c_category": "ok", "w3c_errors": [], "structure_issues": issues})
        self.assertEqual(clean - dirty, 10 + 10 + 5)


if __name__ == "__main__":
    unittest.main()
