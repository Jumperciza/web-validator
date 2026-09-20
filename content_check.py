"""
Detekce testovacího / zástupného obsahu, který nemá být na produkčním webu.

Doplňuje kontrolu 9 v structure_check.py (fráze typu "lorem ipsum" ve
viditelném textu). Každá skupina má vlastní IssueType, aby se v reportu dalo
hned poznat, o jaký druh problému jde, a aby měla vlastní váhu ve skóre.

Skupiny:
  A. Výchozí / nevyplněné hodnoty v <title>, meta description, og:title,
     og:description a alt u obrázků ("Document", "Untitled", alt="image"…).
  B. Zástupné obrázky – placeholder služby (via.placeholder.com, picsum.photos…)
     a soubory pojmenované dummy*/sample*/lorem*/test.jpg/placeholder*.
  C. Nevyrenderované šablonové proměnné ve viditelném textu:
     {{ name }}, {% if %}, [[ name ]], %NAME%, ${name}, <?php v HTML.
  D. JavaScriptové hodnoty v textu: undefined / null / NaN / [object Object]
     jako celý text prvku (nebo s jednotkou: "undefined Kč"), a `undefined`
     v href/src.
  E. Vývojářské výpisy chyb v celém HTML: PHP Fatal error / Warning … on line,
     Stack trace, Uncaught …, var_dump / print_r, Smarty, Tracy, Whoops.
  F. Výchozí texty CMS: WordPress ("Hello world!", "Just another WordPress
     site", "Sample Page"…), Joomla, Drupal.

Zásada: **žádné falešné poplachy** – raději něco nechytit než hlásit běžný
český text. Proto se hledá jen v konkrétních místech (celý text uzlu, celý
title, konkrétní atribut) a obsah <code>/<pre>/<script>/<style>/<textarea>/
<template> se přeskakuje (tam jsou takové věci legitimní).
"""
import copy
import re
from typing import List
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment

from issues import Issue, IssueType

# Prvky, jejichž text se pro detekci nepoužívá (ukázky kódu, skripty, šablony)
_SKIP_TAGS = ["script", "style", "noscript", "template", "code", "pre",
              "textarea", "kbd", "samp"]


def _norm(text: str) -> str:
    """Ořezané, lowercase, s jednou mezerou mezi slovy."""
    return " ".join((text or "").split()).lower()


# ── A. Výchozí hodnoty title / description / alt / og ────────────────────────

# Celá hodnota (po normalizaci) se musí rovnat některé z těchto.
_DEFAULT_TITLES = {
    "document", "untitled", "untitled document", "untitled page", "title",
    "page title", "site title", "nadpis", "nadpis stránky", "nadpis stranky",
    "titulek", "titulek stránky", "titulek stranky", "název stránky",
    "nazev stranky", "nová stránka", "nova stranka", "new page", "home",
    "homepage", "index", "test", "testovací stránka", "testovaci stranka",
    "my website", "my site", "můj web", "muj web", "web", "website",
}
_DEFAULT_DESCRIPTIONS = {
    "description", "meta description", "popis", "popis stránky",
    "popis stranky", "site description", "page description", "test",
    "zde bude popis", "sem napište popis", "sem napiste popis",
}
_DEFAULT_ALTS = {
    "image", "img", "picture", "photo", "obrázek", "obrazek", "alt", "alt text",
    "alt-text", "alternative text", "alternativní text", "alternativni text",
    "popis obrázku", "popis obrazku", "image description", "img alt",
    "image alt", "untitled", "bez názvu", "bez nazvu", "test", "placeholder",
}


def _meta_content(soup: BeautifulSoup, attr: str, name: str) -> str:
    pattern = re.compile(rf"^{re.escape(name)}$", re.I)
    meta = soup.find("meta", attrs={attr: pattern})
    return (meta.get("content") or "") if meta is not None else ""


def _check_default_values(soup: BeautifulSoup) -> Issue | None:
    findings: list[str] = []

    title = soup.find("title")
    if title is not None:
        t = _norm(title.get_text())
        if t in _DEFAULT_TITLES:
            findings.append(f'<title>: "{title.get_text().strip()}"')

    desc = _meta_content(soup, "name", "description")
    if _norm(desc) in _DEFAULT_DESCRIPTIONS:
        findings.append(f'meta description: "{desc.strip()}"')

    for og in ("og:title", "og:description"):
        val = _meta_content(soup, "property", og) or _meta_content(soup, "name", og)
        pool = _DEFAULT_TITLES if og == "og:title" else _DEFAULT_DESCRIPTIONS
        if _norm(val) in pool:
            findings.append(f'{og}: "{val.strip()}"')

    bad_alts: dict[str, int] = {}
    for img in soup.find_all("img"):
        alt = img.get("alt")
        if alt is None:
            continue                      # chybějící alt hlásí structure_check
        if _norm(alt) in _DEFAULT_ALTS:
            bad_alts[alt.strip()] = bad_alts.get(alt.strip(), 0) + 1
    for alt, n in bad_alts.items():
        findings.append(f'alt="{alt}"' + (f" ({n}×)" if n > 1 else ""))

    if not findings:
        return None
    return Issue(type=IssueType.DEFAULT_META_TEXT, items=findings[:50],
                 count=len(findings))


# ── B. Zástupné obrázky ──────────────────────────────────────────────────────

_PLACEHOLDER_HOSTS = (
    "via.placeholder.com", "placeholder.com", "placehold.it", "placehold.co",
    "dummyimage.com", "picsum.photos", "placekitten.com", "lorempixel.com",
    "loremflickr.com", "fakeimg.pl", "placeimg.com", "source.unsplash.com",
    "baconmockup.com", "fillmurray.com", "placebear.com", "placebeard.it",
    "placecage.com", "stevensegallery.com", "loremipsum.io",
)
# Název souboru (bez cesty): dummy*, sample*, lorem*, placeholder*,
# test.jpg / test1.jpg / test-02.png / test_3.webp.
# "test" jen s číslem/příponou, aby nechytilo testimonial.jpg, test-drive.jpg.
_PLACEHOLDER_FILE_RE = re.compile(
    r"^(?:(?:dummy|sample|lorem|placeholder)(?:[-_.\d]|$)|test(?:[-_]?\d+)?\.)",
    re.I,
)
_IMG_SRC_ATTRS = ("src", "data-src", "data-lazy-src", "data-original")


def _is_placeholder_url(url: str, allow_placeholder_name: bool = True) -> bool:
    u = (url or "").strip()
    if not u or u.lower().startswith("data:"):
        return False
    parsed = urlparse(u)
    host = parsed.netloc.lower().removeprefix("www.")
    if host and (host in _PLACEHOLDER_HOSTS
                 or any(host.endswith("." + h) for h in _PLACEHOLDER_HOSTS)):
        return True
    if host == "unsplash.com" and "/random" in parsed.path.lower():
        return True
    name = parsed.path.rsplit("/", 1)[-1]
    if not name:
        return False
    if not allow_placeholder_name and name.lower().startswith("placeholder"):
        return False
    return bool(_PLACEHOLDER_FILE_RE.match(name))


def _check_placeholder_images(soup: BeautifulSoup) -> Issue | None:
    found: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        short = url if len(url) <= 100 else url[:100] + "…"
        if short not in seen:
            seen.add(short)
            found.append(short)

    for img in soup.find_all("img"):
        lazy = any(img.get(a) for a in _IMG_SRC_ATTRS[1:]) or img.get("srcset")
        for attr in _IMG_SRC_ATTRS:
            val = (img.get(attr) or "").strip()
            if not val:
                continue
            # Lazy-load: src="placeholder.png" data-src="real.jpg" je legitimní
            # technika – jméno "placeholder" u src tehdy neřešíme.
            allow_name = not (attr == "src" and lazy)
            if _is_placeholder_url(val, allow_placeholder_name=allow_name):
                _add(val)
        for part in (img.get("srcset") or "").split(","):
            url = part.strip().split(" ")[0]
            if url and _is_placeholder_url(url):
                _add(url)

    og_image = _meta_content(soup, "property", "og:image") or _meta_content(soup, "name", "og:image")
    if og_image and _is_placeholder_url(og_image.strip()):
        _add("og:image → " + og_image.strip())

    if not found:
        return None
    return Issue(type=IssueType.PLACEHOLDER_IMAGE, items=found[:50], count=len(found))


# ── C. Nevyrenderované šablonové proměnné ────────────────────────────────────

_TEMPLATE_RES: list[tuple[str, re.Pattern]] = [
    ("{{ … }}",  re.compile(r"\{\{\s*[\w.\-\[\]'\"|() ]{1,80}?\s*\}\}")),
    ("{% … %}",  re.compile(r"\{%\s*[\w.\-\[\]'\"|() =]{1,80}?\s*%\}")),
    ("[[ … ]]",  re.compile(r"\[\[\s*[\w.\-|]{1,60}\s*\]\]")),
    ("%NAME%",   re.compile(r"(?<![\w%])%[A-Z][A-Z0-9_]{1,40}%(?![\w%])")),
    ("${ … }",   re.compile(r"\$\{\s*[\w.\-\[\]]{1,60}\s*\}")),
]
_PHP_TAG_RE = re.compile(r"<\?(?:php\b|=)", re.I)
# Atributy, které prozradí klientský framework – uvnitř takového prvku jsou
# {{ }} legitimní (Vue / Angular / Alpine je vyrenderují až v prohlížeči).
_CLIENT_TEMPLATE_ATTRS = ("v-cloak", "v-if", "v-for", "v-show", "v-else",
                          "ng-app", "ng-controller", "ng-repeat", "ng-if",
                          "x-data", "x-for", "x-if")


def _inside_client_template(node) -> bool:
    for parent in node.parents:
        if parent is None or getattr(parent, "attrs", None) is None:
            continue
        for attr in parent.attrs:
            if attr in _CLIENT_TEMPLATE_ATTRS or attr.startswith(("v-", "ng-", "x-")):
                return True
    return False


def _visible_text_nodes(soup: BeautifulSoup):
    """
    Textové uzly viditelného obsahu. Prvky z _SKIP_TAGS už jsou z kopie
    dokumentu odstraněné (viz check_test_content), tady stačí vynechat
    komentáře a prázdné uzly.
    """
    for node in soup.find_all(string=True):
        if isinstance(node, Comment):
            continue
        text = str(node)
        if text.strip():
            yield node, text


def _check_template_vars(soup: BeautifulSoup, html: str) -> Issue | None:
    found: list[str] = []
    seen: set[str] = set()

    def _add(kind: str, sample: str) -> None:
        key = f"{kind}: {sample}"
        if key not in seen:
            seen.add(key)
            found.append(key)

    for node, text in _visible_text_nodes(soup):
        checked_client = None
        for kind, pattern in _TEMPLATE_RES:
            m = pattern.search(text)
            if not m:
                continue
            if kind in ("{{ … }}", "[[ … ]]"):
                if checked_client is None:
                    checked_client = _inside_client_template(node)
                if checked_client:
                    continue
            _add(kind, " ".join(m.group(0).split())[:80])

    # <title> a meta description / og: – hodnoty v atributech get_text nevidí
    for val in (_meta_content(soup, "name", "description"),
                _meta_content(soup, "property", "og:title"),
                _meta_content(soup, "property", "og:description")):
        for kind, pattern in _TEMPLATE_RES:
            m = pattern.search(val or "")
            if m:
                _add(kind, " ".join(m.group(0).split())[:80])

    # Nezpracované PHP – v prohlížeči se <?php … ?> nezobrazí (bere se jako
    # komentář), ale znamená to, že server soubor nespustil. Ukázky kódu
    # v <code>/<pre>/<script>/<textarea> se vynechají.
    stripped = _strip_tags_content(html, ("script", "style", "code", "pre", "textarea"))
    m = _PHP_TAG_RE.search(stripped)
    if m:
        snippet = " ".join(stripped[m.start():m.start() + 60].split())
        _add("<?php", snippet)

    if not found:
        return None
    return Issue(type=IssueType.TEMPLATE_VARIABLE, items=found[:50], count=len(found))


def _strip_tags_content(html: str, tags: tuple) -> str:
    """Odstraní z HTML celé bloky <tag>…</tag> pro zadané tagy."""
    for tag in tags:
        html = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}\s*>", " ", html, flags=re.I | re.S)
    return html


# ── D. JavaScriptové hodnoty v textu ─────────────────────────────────────────

_JS_WHOLE_NODE = {"undefined", "null", "nan", "[object object]", "array",
                  "undefined undefined", "null null"}
# "undefined Kč", "NaN m²", "null %", "undefined ks" – hodnota + jednotka
_JS_WITH_UNIT_RE = re.compile(
    r"^(undefined|null|NaN)\s*(?i:kč|czk|eur|€|\$|%|m²|m2|ks|km|kg|g|l|min|h|hod|dní|dny|den|x)\.?$",
)
# "[object Object]" kdekoli; "undefined Kč" / "NaN m²" / "null %" i uvnitř věty
_JS_ANYWHERE_RE = re.compile(
    r"\[object Object\]"
    r"|(?<![\w\-/.])(?:undefined|NaN|null) ?(?:Kč|CZK|€|EUR|\$|%|m²|m2|ks)(?!\w)"
)
_JS_URL_RE = re.compile(r"(?:^|[/=])(?:undefined|null)(?:$|[/?&#.])")


def _check_js_values(soup: BeautifulSoup) -> Issue | None:
    found: list[str] = []
    seen: set[str] = set()

    def _add(sample: str) -> None:
        if sample not in seen:
            seen.add(sample)
            found.append(sample)

    for node, text in _visible_text_nodes(soup):
        t = " ".join(text.split())
        low = t.lower()
        if low in _JS_WHOLE_NODE or _JS_WITH_UNIT_RE.match(t):
            parent = node.parent.name if node.parent is not None else "?"
            _add(f'<{parent}>: "{t}"')
        elif _JS_ANYWHERE_RE.search(t):
            _add(f'text: "{t[:80]}"')

    for tag in soup.find_all(["a", "img", "source", "iframe", "link", "script"]):
        for attr in ("href", "src", "data-src", "srcset"):
            val = (tag.get(attr) or "").strip()
            if val and _JS_URL_RE.search(val):
                _add(f'{attr}="{val[:80]}"')
        alt = tag.get("alt")
        if alt is not None and _norm(alt) in _JS_WHOLE_NODE:
            _add(f'alt="{alt.strip()}"')

    if not found:
        return None
    return Issue(type=IssueType.JS_VALUE_IN_TEXT, items=found[:50], count=len(found))


# ── E. Vývojářské výpisy chyb ────────────────────────────────────────────────

# Hledá se v textu celého HTML (bez tagů, bez <code>/<pre>/<script>/<style>/
# <textarea>). PHP vypisuje "<b>Warning</b>:  … in <b>/path</b> on line <b>12</b>",
# po odstranění tagů z toho je "Warning: … in /path on line 12".
_DEV_ERROR_RES: list[tuple[str, re.Pattern]] = [
    ("PHP chyba",       re.compile(r"\b(Fatal error|Parse error|Warning|Notice|Deprecated|Strict Standards)\s*:\s.{0,400}?\bon line\s+\d+", re.S)),
    ("Uncaught",        re.compile(r"\bUncaught\s+(?:\w+\\)*\w*(?:Exception|Error)\b")),
    ("Stack trace",     re.compile(r"\bStack trace:\s*#0\s")),
    ("Call Stack",      re.compile(r"\bCall Stack\b.{0,200}?\bTime\b.{0,200}?\bMemory\b", re.S)),
    ("Undefined",       re.compile(r"\bUndefined (variable|index|offset|array key|property)\b[:\s]")),
    ("Traceback",       re.compile(r"Traceback \(most recent call last\)")),
    ("var_dump",        re.compile(r"\b(?:array|string|int|float|bool)\((\d+)\)\s*(?:\{|\")")),
    ("print_r",         re.compile(r"\bArray\s*\(\s*\[\w+\]\s*=>")),
    ("SQL chyba",       re.compile(r"You have an error in your SQL syntax|SQLSTATE\[\w+\]|mysqli?_(?:connect|query|fetch)\w*\(\)")),
    ("Smarty",          re.compile(r"\bSmarty(?:Compiler)?(?:Exception| error| Compiler:)")),
    ("Tracy / Nette",   re.compile(r"\btracy-bs\b|Tracy\\\w*Debugger")),
    ("Whoops / Laravel", re.compile(r"Whoops, looks like something went wrong|Whoops\\Run")),
    ("Symfony",         re.compile(r"Symfony\\Component\\\w+\\Exception")),
    ("ASP.NET",         re.compile(r"Server Error in '/' Application|Runtime Error\s+Description: An (?:application|exception) error")),
    ("Debug výpis",     re.compile(r"\bconsole\.log\(|\bdd\(\$|\bvar_dump\(\$|\bprint_r\(\$")),
]
_TAG_RE = re.compile(r"<[^>]+>")


def _check_dev_errors(html: str) -> Issue | None:
    # Tracy debugger se pozná podle id ještě před odstraněním tagů
    findings: list[str] = []
    if re.search(r'id=["\']tracy-bs["\']', html):
        findings.append("Tracy / Nette: laděnka (id=tracy-bs) v HTML")

    stripped = _strip_tags_content(html, ("script", "style", "code", "pre", "textarea", "noscript"))
    flat = " ".join(_TAG_RE.sub(" ", stripped).split())
    flat = (flat.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
                .replace("&#039;", "'").replace("&amp;", "&"))

    for kind, pattern in _DEV_ERROR_RES:
        m = pattern.search(flat)
        if m:
            snippet = flat[m.start():m.start() + 120]
            findings.append(f"{kind}: {snippet}")

    if not findings:
        return None
    return Issue(type=IssueType.DEV_ERROR_OUTPUT, items=findings[:50], count=len(findings))


# ── F. Výchozí texty CMS ─────────────────────────────────────────────────────

# (popis, regex nad viditelným textem stránky). Fráze jsou dost specifické,
# aby se v běžném textu neobjevily.
_CMS_DEFAULT_RES: list[tuple[str, re.Pattern]] = [
    ("WordPress – Hello world!",      re.compile(r"\bHello world!")),
    ("WordPress – tagline",           re.compile(r"Just another WordPress(?: site)?|Další web (?:používající|na) WordPress|Jen další web WordPress", re.I)),
    ("WordPress – první příspěvek",   re.compile(r"This is your first post\. Edit or delete it|Toto je (?:váš|tvůj) první příspěvek", re.I)),
    ("WordPress – Sample Page",       re.compile(r"This is an example page\. It's different from a blog post|Toto je ukázková stránka", re.I)),
    ("WordPress – komentář",          re.compile(r"\bA WordPress Commenter\b|Komentátor WordPressu")),
    ("WordPress – Mr WordPress",      re.compile(r"\bMr WordPress\b")),
    ("Joomla – ukázka",               re.compile(r"Welcome to your (?:new )?Joomla|Getting Started with Joomla", re.I)),
    ("Drupal – ukázka",               re.compile(r"Welcome to Drupal|No front page content has been created yet", re.I)),
    ("Šablona – Under construction",  re.compile(r"\b(?:Site |Page |Website )?under construction\b", re.I)),
    ("Šablona – Web v přípravě",      re.compile(r"\b(?:Web|Stránky?|Stránka) (?:je|jsou) (?:ve výstavbě|v přípravě|v rekonstrukci)\b", re.I)),
    ("Šablona – Text odstavce",       re.compile(r"\b(?:Text odstavce|Sem vložte text|Zde bude text|Zde vložte text|Text (?:bloku|sekce) zde)\b", re.I)),
    ("Šablona – Your content here",   re.compile(r"\bYour (?:content|text) (?:goes )?here\b|\bInsert (?:your )?text here\b", re.I)),
]
# Texty, které se hlásí jen jako CELÝ text prvku (odkaz / nadpis)
_CMS_WHOLE_NODE = {
    "hello world!", "hello world", "sample page", "ukázková stránka",
    "ukazkova stranka", "sample post", "nadpis stránky", "nadpis stranky",
    "název sekce", "nazev sekce", "text odstavce", "sem napište text",
    "sem napiste text", "your title here", "heading goes here",
    "podnadpis zde", "lorem", "ipsum", "heading 1", "heading 2", "heading 3",
    "nadpis 1", "nadpis 2", "nadpis 3", "nadpis zde", "heading", "subheading",
}


def _check_cms_defaults(soup: BeautifulSoup) -> Issue | None:
    found: list[str] = []
    seen: set[str] = set()

    def _add(sample: str) -> None:
        if sample not in seen:
            seen.add(sample)
            found.append(sample)

    parts: list[str] = []
    for node, text in _visible_text_nodes(soup):
        t = " ".join(text.split())
        parts.append(t)
        if t.lower() in _CMS_WHOLE_NODE:
            parent = node.parent.name if node.parent is not None else "?"
            _add(f'<{parent}>: "{t}"')

    page_text = " ".join(parts)
    for kind, pattern in _CMS_DEFAULT_RES:
        m = pattern.search(page_text)
        if m:
            _add(f'{kind}: "{page_text[m.start():m.start() + 80].strip()}"')

    if not found:
        return None
    return Issue(type=IssueType.DEFAULT_CMS_TEXT, items=found[:50], count=len(found))


# ── Veřejné API ──────────────────────────────────────────────────────────────

def check_test_content(html: str, soup: BeautifulSoup | None = None) -> List[Issue]:
    """
    Spustí všechny skupiny detekce testovacího obsahu. `soup` je volitelný
    už naparsovaný dokument (structure_check ho předá, aby se HTML
    neparsovalo dvakrát); pracuje se s kopií, původní DOM se nemění.
    """
    if soup is None:
        from structure_check import _PARSER
        soup = BeautifulSoup(html, _PARSER)
    else:
        soup = copy.copy(soup)
    for unwanted in soup(_SKIP_TAGS):
        unwanted.decompose()

    issues: List[Issue] = []
    for check in (_check_default_values, _check_placeholder_images,
                  _check_js_values, _check_cms_defaults):
        issue = check(soup)
        if issue:
            issues.append(issue)
    issue = _check_template_vars(soup, html)
    if issue:
        issues.append(issue)
    issue = _check_dev_errors(html)
    if issue:
        issues.append(issue)
    return issues
