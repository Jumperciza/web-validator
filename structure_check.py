"""
Kontrola HTML struktury.

Vrací List[Issue] — strukturovaná data (viz issues.py).

Prováděné kontroly:
  1.  Existence a duplikáty <h1>
  2.  Pořadí nadpisů (žádné přeskočení)
  3.  Prázdné tagy
  4.  Duplicitní ID
  5.  Meta description (existence + neprázdnost)
  6.  Alt texty u obrázků
  7.  HTTP odkazy (místo HTTPS)
  8.  Externí odkazy bez target="_blank" rel="noopener"
  9.  Testovací / zástupný obsah (lorem ipsum…) + rozšířená detekce
      v content_check.py (výchozí title/alt, placeholder obrázky, {{ }},
      undefined, PHP výpisy, výchozí texty CMS)
  10. Chybějící lang atribut na <html>
  11. Chybějící <meta name="viewport">
  12. <meta name="robots" content="noindex"> mimo dev domény
  13. URL ukazující na staging/dev domény (canonical, og:image, src, href...)
  14. <title> existuje a není prázdný (na každé stránce, ne jen na homepage)
  15. <link rel="canonical"> – existuje, míří sám na sebe, není http:// na https
  16. Open Graph meta (og:title, og:description, og:image) pro sdílení na sítích
  17. <img> bez width/height (prohlížeč nezná rozměry → posun layoutu, CLS)
  18. Soft 404 – <title>/<h1> hlásí „Stránka nenalezena“, ale HTTP je 200
      (detekci má availability_check.py)
  19. Prázdné odkazy s textem (href="#", href="", javascript:void(0)) bez
      JS ovladače – nedodělané odkazy

Napříč webem (po zpracování všech stránek, viz `mark_duplicate_titles`):
  20. Duplicitní <title> na více stránkách

Dostupnost odkazů a obrázků (404, velikost) řeší links_check.py – potřebuje
síťové requesty, proto neběží tady.

Pro lokální / privátní hosty (localhost, 127.0.0.1, *.local…) se přeskočí
kontroly které pro lokální vývoj nedávají smysl: HTTP odkazy, noindex,
staging URL detekce, canonical.
"""
import copy
import importlib.util
import re
from collections import defaultdict
from typing import List
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from config import (META_TITLE_MIN, META_TITLE_MAX, META_DESC_MIN, META_DESC_MAX,
                    SKIP_NOINDEX_PATTERNS, STAGING_DOMAIN_PATTERNS)
from availability_check import detect_soft_404
from content_check import check_test_content
from issues import Issue, IssueType
from ui import is_local_url

_EMPTY_TAGS = ["p", "div", "span", "section", "article",
               "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"]

# ── Zakázaná slova / testovací obsah ─────────────────────────────────────────
# Použity jen konkrétní, jednoznačné výrazy. Hledá se jako CELÉ SLOVO / FRÁZE
# (hranice slov), ne jako podřetězec — "asdf" tak nechytne "basdfoo".
#
# Záměrně tu NEJSOU běžná česká slova, která dřív dělala falešné poplachy
# (a každý = −20 bodů): "testujeme" (= "testujeme každý vůz"), "text zde"
# (= "text zde najdete"), "nadpis zde". Samotné "lorem" je také příliš obecné.
_FORBIDDEN_WORDS: list[str] = [
    "lorem ipsum",
    "testovaci text",
    "testovací text",
    "testovaci obsah",
    "testovací obsah",
    "asdf",
    "qwerty",
    "přidat text",
    "pridat text",
    "vložte text",
    "vložit text",
    "dummy text",
    "placeholder text",
    "sample text",
    "změňte tento text",
    "zmente tento text",
]
# Předkompilované regexy: \b = hranice slova (Unicode-aware, funguje i s diakritikou),
# mezery ve frázi tolerují libovolné bílé znaky (text z HTML může mít víc mezer).
_FORBIDDEN_RE: list[tuple[str, re.Pattern]] = [
    (w, re.compile(r"\b" + r"\s+".join(map(re.escape, w.split())) + r"\b", re.I))
    for w in _FORBIDDEN_WORDS
]

# Parser preference — lxml je 3-5× rychlejší než html.parser
# Fallback na html.parser pokud lxml není nainstalován
_PARSER = "lxml" if importlib.util.find_spec("lxml") else "html.parser"


# ── Pomocné funkce ───────────────────────────────────────────────────────────

def _netloc_bare(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _is_http_scheme(href: str) -> bool:
    """True pro odkaz s http:// schématem (case-insensitive – HTML schéma
    nerozlišuje velikost písmen, `HTTP://` je stejně nezabezpečený)."""
    return href.lower().startswith("http://")


def _is_external(href: str, page_url: str) -> bool:
    """
    True pokud href míří na jinou doménu než auditovaná stránka.
    Bere absolutní URL (http/https, bez ohledu na velikost písmen schématu)
    i protokol-relativní URL (`//cdn.example.com/…`), které prohlížeč
    rozvine na aktuální schéma – jsou to tedy plnohodnotné externí odkazy.
    """
    h = href.lower()
    if h.startswith("//"):
        href = "https:" + href
    elif not h.startswith(("http://", "https://")):
        return False
    if not page_url:
        return True
    return _netloc_bare(href) != _netloc_bare(page_url)


def _rel_values(tag) -> list[str]:
    """Hodnoty atributu rel jako lowercase seznam (BS4 vrací list i str)."""
    rel = tag.get("rel", [])
    if isinstance(rel, str):
        rel = rel.split()
    return [r.lower() for r in rel]


def _has_safe_rel(tag) -> bool:
    """True pokud rel obsahuje noopener nebo noreferrer."""
    return bool({"noopener", "noreferrer"} & set(_rel_values(tag)))


def _is_dev_noindex_domain(url: str) -> bool:
    """
    True pokud URL je z domény kde je <meta noindex> záměr (dev/staging/lokální).
    Tyto domény mají noindex by design — nehlásíme to jako chybu.
    Patří sem domény definované v SKIP_NOINDEX_PATTERNS + všechny lokální hosty.
    """
    if not url:
        return False
    if is_local_url(url):
        return True
    netloc = urlparse(url).netloc.lower()
    return any(p in netloc for p in SKIP_NOINDEX_PATTERNS)


def _is_staging_url(url: str) -> bool:
    """
    True pokud URL ukazuje na známou staging/dev doménu.
    Pracuje s plnými URL i s relativními/protokol-relativními URL —
    pokud doménu nelze určit, vrátí False (nebudeme hlásit relativní cesty).
    """
    if not url:
        return False
    url = url.strip()
    # Relativní URL (/path, ./path, ../path) nebo fragmenty (#anchor) — neřešíme
    if not url.startswith(("http://", "https://", "//")):
        return False
    # Protokol-relativní URL (//example.com/...) — doplníme https
    if url.startswith("//"):
        url = "https:" + url
    netloc = urlparse(url).netloc.lower()
    if not netloc:
        return False
    return any(p in netloc for p in STAGING_DOMAIN_PATTERNS)


def _page_title(soup: BeautifulSoup) -> str:
    """
    Text <title> z hlavičky dokumentu (whitespace sbalený), "" pokud chybí.
    Hledáme přednostně v <head> – `<title>` je i platný element uvnitř
    inline <svg> v těle stránky a `soup.find("title")` by ho na stránce
    bez skutečného titulku chytil místo něj.
    """
    scope = soup.head if soup.head is not None else soup
    title = scope.find("title")
    if title is None:
        return ""
    return " ".join(title.get_text().split())


def _canonical_key(url: str) -> str:
    """
    Klíč pro porovnání canonical ↔ URL stránky: bez schématu (http vs. https
    řeší samostatná kontrola), bez www., bez koncového lomítka a fragmentu.
    Query zůstává – canonical na `?page=2` míří skutečně na jinou stránku.
    """
    p = urlparse(url)
    key = p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")
    if p.query:
        key += "?" + p.query
    return key


def _extract_urls_from_srcset(srcset: str) -> list[str]:
    """
    Z srcset hodnoty vytáhne všechny URL.
    Formát: "url1 1x, url2 2x" nebo "url1 100w, url2 200w" nebo jen "url1, url2"
    """
    urls = []
    for entry in srcset.split(","):
        entry = entry.strip()
        if not entry:
            continue
        # První token před mezerou = URL, ostatní = descriptor (1x, 100w, ...)
        url = entry.split()[0] if entry.split() else ""
        if url:
            urls.append(url)
    return urls


# ── Hlavní funkce ────────────────────────────────────────────────────────────

def check_structure(html: str, page_url: str = "") -> List[Issue]:
    """
    Vrátí seznam Issue objektů.
    page_url slouží k rozlišení interních vs. externích odkazů a k detekci
    lokálního prostředí (kde se některé kontroly přeskočí).
    """
    issues: List[Issue] = []
    soup   = BeautifulSoup(html, _PARSER)

    # Lokální prostředí = některé kontroly nedávají smysl (http odkazy očekávané,
    # noindex je záměr, staging URL je nesmysl)
    page_is_local = is_local_url(page_url)

    # 1. H1
    h1s = soup.find_all("h1")
    if not h1s:
        issues.append(Issue(type=IssueType.MISSING_H1))
    elif len(h1s) > 1:
        issues.append(Issue(
            type=IssueType.MULTIPLE_H1,
            count=len(h1s),
            detail=f"Nalezeno {len(h1s)}x, měl by být pouze jeden"
        ))

    # 2. Pořadí nadpisů
    prev, skips = 0, set()
    skip_items: list[str] = []
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        lvl = int(h.name[1])
        if prev > 0 and lvl > prev + 1:
            key = (prev, lvl)
            if key not in skips:
                skips.add(key)
                skip_items.append(f"<h{prev}> → <h{lvl}> (chybí <h{prev+1}>)")
        prev = lvl
    if skip_items:
        issues.append(Issue(
            type=IssueType.HEADING_SKIP,
            items=skip_items,
            count=len(skip_items),
        ))

    # 3. Prázdné tagy — jeden průchod, separátní Issue pro každý typ
    empty_counts: dict[str, int] = {}
    for t in soup.find_all(_EMPTY_TAGS):
        if not t.get_text(strip=True) and not t.find():
            empty_counts[t.name] = empty_counts.get(t.name, 0) + 1
    for tag in _EMPTY_TAGS:   # zachovat pořadí
        n = empty_counts.get(tag, 0)
        if n:
            issues.append(Issue(type=IssueType.EMPTY_TAG, tag=tag, count=n))

    # 4. Duplicitní ID
    ids: dict[str, int] = {}
    for t in soup.find_all(id=True):
        v = t.get("id", "").strip()
        if v:
            ids[v] = ids.get(v, 0) + 1
    dup_ids = [f"#{v} ({n}x)" for v, n in ids.items() if n > 1]
    if dup_ids:
        issues.append(Issue(
            type=IssueType.DUPLICATE_ID,
            items=dup_ids,
            count=len(dup_ids),
        ))

    # 5. Meta description
    md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if not md:
        issues.append(Issue(type=IssueType.MISSING_META_DESC))
    elif not md.get("content", "").strip():
        issues.append(Issue(type=IssueType.EMPTY_META_DESC))

    # 6. Alt texty
    missing_alt = []
    for img in soup.find_all("img"):
        if img.get("alt") is None:
            src = img.get("src", "").strip()
            display = src[:80] + "…" if len(src) > 80 else src or "(bez src)"
            missing_alt.append(display)
    if missing_alt:
        issues.append(Issue(
            type=IssueType.MISSING_ALT,
            items=missing_alt[:50],   # limit v items, count je totální
            count=len(missing_alt),
        ))

    # 7. HTTP odkazy
    # Na lokálním webu jsou http:// odkazy očekávané (lokální dev běží na http) —
    # kontrolu úplně přeskočíme. Na produkci: ignorujeme odkazy mířící
    # na lokální host (např. http://localhost:3000) jako neškodné.
    if not page_is_local:
        http_links = list(dict.fromkeys(
            a["href"].strip() for a in soup.find_all("a", href=True)
            if _is_http_scheme(a["href"].strip())
            and not is_local_url(a["href"].strip())
        ))
        if http_links:
            issues.append(Issue(
                type=IssueType.HTTP_LINK,
                items=http_links[:50],
                count=len(http_links),
            ))

    # 8. Externí odkazy bez target="_blank" rel="noopener"
    bad_ext: list[str] = []
    seen_ext: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not _is_external(href, page_url):
            continue
        missing_parts = []
        if a.get("target", "").lower() != "_blank":
            missing_parts.append('target="_blank"')
        if not _has_safe_rel(a):
            missing_parts.append('rel="noopener"')
        if missing_parts:
            label = href[:80] + "…" if len(href) > 80 else href
            entry = f"{label}  [chybí: {', '.join(missing_parts)}]"
            if entry not in seen_ext:
                seen_ext.add(entry)
                bad_ext.append(entry)
    if bad_ext:
        issues.append(Issue(
            type=IssueType.EXTERNAL_LINK,
            items=bad_ext[:50],
            count=len(bad_ext),
        ))

    # 9. Testovací / zástupný obsah
    # Odstraníme <script>/<style> na KOPII soup — abychom neznehodnotili
    # původní DOM pro další kontroly (kdyby se přidaly níže)
    soup_text = copy.copy(soup)
    for unwanted in soup_text(["script", "style"]):
        unwanted.decompose()
    page_text = soup_text.get_text(" ", strip=True).lower()

    found_words: list[str] = []
    for word, pattern in _FORBIDDEN_RE:
        if pattern.search(page_text) and word not in found_words:
            found_words.append(word)
    if found_words:
        issues.append(Issue(
            type=IssueType.FORBIDDEN_CONTENT,
            items=[f'"{w}"' for w in found_words],
            count=len(found_words),
        ))

    # 9b. Rozšířená detekce testovacího obsahu (content_check.py): výchozí
    # title/alt/og, placeholder obrázky, {{ šablonové }} proměnné, undefined/
    # null v textu, PHP výpisy chyb, výchozí texty CMS. Pracuje s kopií soup.
    issues.extend(check_test_content(html, soup))

    # 10. lang atribut na <html>
    html_tag = soup.find("html")
    if html_tag is not None and not html_tag.get("lang", "").strip():
        issues.append(Issue(type=IssueType.MISSING_LANG))

    # 11. Meta viewport
    if not soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)}):
        issues.append(Issue(type=IssueType.MISSING_VIEWPORT))

    # 12. Noindex meta tag — kritická chyba (web nebude v Googlu)
    # Skip pro dev/staging domény (poskireal.cz, *.cz.dev.poski.com)
    # a všechny lokální hosty kde je noindex záměrný.
    # Detekuje:
    #   <meta name="robots" content="noindex">
    #   <meta name="robots" content="noindex, nofollow">
    #   <meta name="robots" content="none">     (none = noindex, nofollow)
    #   <meta name="googlebot" content="noindex">
    if not _is_dev_noindex_domain(page_url):
        for robots_meta in soup.find_all(
            "meta",
            attrs={"name": re.compile(r"^(robots|googlebot)$", re.I)},
        ):
            content = robots_meta.get("content", "").lower()
            directives = [d.strip() for d in content.split(",")]
            if "noindex" in directives or "none" in directives:
                meta_name = robots_meta.get("name", "robots")
                issues.append(Issue(
                    type=IssueType.NOINDEX,
                    detail=f'<meta name="{meta_name}" content="{content}">',
                ))
                break   # stačí najít jeden - není třeba duplicitně hlásit

    # 13. Staging/dev URL v HTML — leftover ze stagingu po nasazení na produkci.
    # Příklady: canonical pointující na staging, og:image z dev serveru,
    # odkaz vedoucí na dev verzi webu, lazy obrázek z dev URL.
    # Skip pokud je sama auditovaná stránka na dev/lokální doméně
    # (tam je dev URL záměr).
    if not _is_dev_noindex_domain(page_url):
        staging_findings: list[str] = []      # ["[kontext] url", …]
        seen_findings:    set[str]  = set()   # deduplikace

        def _record(context: str, url: str) -> None:
            """Zaznamená nález pokud je to staging URL a ještě jsme ho neviděli."""
            if not _is_staging_url(url):
                return
            entry = f"[{context}] {url.strip()}"
            if entry not in seen_findings:
                seen_findings.add(entry)
                staging_findings.append(entry)

        # ── <a href>, <link href>, <iframe src>, <script src>, <img src>, ...
        # Páry (selektor, atribut, kontextový popisek)
        # Jeden tag může mít víc atributů (např. <video src + poster>)
        _STAGING_TARGETS = [
            ("a",      "href",   "<a href>"),
            ("img",    "src",    "<img src>"),
            ("img",    "data-src", "<img data-src>"),     # lazy loading
            ("script", "src",    "<script src>"),
            ("iframe", "src",    "<iframe src>"),
            ("video",  "src",    "<video src>"),
            ("video",  "poster", "<video poster>"),
            ("audio",  "src",    "<audio src>"),
            ("source", "src",    "<source src>"),
            ("form",   "action", "<form action>"),
            ("embed",  "src",    "<embed src>"),
            ("object", "data",   "<object data>"),
        ]
        for tag_name, attr, ctx in _STAGING_TARGETS:
            for el in soup.find_all(tag_name):
                val = el.get(attr)
                if val:
                    _record(ctx, val)

        # ── srcset atribut (může obsahovat víc URL oddělených čárkou)
        for tag_name in ("img", "source"):
            for el in soup.find_all(tag_name, attrs={"srcset": True}):
                for url in _extract_urls_from_srcset(el["srcset"]):
                    _record(f"<{tag_name} srcset>", url)

        # ── <link href> — speciální zacházení kvůli rel atributu
        # canonical má nejvyšší prioritu (zničí SEO když ukazuje na staging)
        for link in soup.find_all("link", href=True):
            rel_lower = _rel_values(link)
            if "canonical" in rel_lower:
                _record("canonical", link["href"])
            elif "alternate" in rel_lower:
                _record("alternate", link["href"])
            else:
                _record("<link href>", link["href"])

        # ── Open Graph + Twitter Card meta tagy
        # <meta property="og:image" content="https://...">
        # <meta name="twitter:image" content="https://...">
        for meta in soup.find_all("meta"):
            content = meta.get("content", "")
            if not content:
                continue
            prop = (meta.get("property") or "").lower()
            name = (meta.get("name") or "").lower()
            if prop.startswith("og:") and ("image" in prop or "url" in prop or "video" in prop):
                _record(prop, content)
            elif name.startswith("twitter:") and ("image" in name or "url" in name):
                _record(name, content)

        if staging_findings:
            issues.append(Issue(
                type=IssueType.STAGING_URL,
                items=staging_findings[:50],   # limit v reportu (count je celkem)
                count=len(staging_findings),
            ))

    # 14. <title> — na každé stránce. Délku hlídá jen homepage
    # (check_homepage_meta), ale chybějící/prázdný title je chyba všude:
    # Google pak vymýšlí vlastní titulek a v záložce je holá URL.
    if not _page_title(soup):
        issues.append(Issue(type=IssueType.MISSING_TITLE))

    # 15. Canonical — přeskočeno na dev/lokálních doménách: tam canonical
    # běžně (a správně) ukazuje na produkci, hlásili bychom falešný nesoulad.
    if not _is_dev_noindex_domain(page_url):
        canonicals = [link for link in soup.find_all("link", href=True)
                      if "canonical" in _rel_values(link)]
        hrefs = [c["href"].strip() for c in canonicals if c["href"].strip()]
        if not hrefs:
            issues.append(Issue(
                type=IssueType.MISSING_CANONICAL,
                detail="prázdný href" if canonicals else "",
            ))
        else:
            href = hrefs[0]
            if len(hrefs) > 1:
                # Víc canonicalů = Google je ignoruje všechny. Hlásíme jako
                # nesoulad, konkrétní hodnoty v items.
                issues.append(Issue(
                    type=IssueType.CANONICAL_MISMATCH,
                    detail=f"Nalezeno {len(hrefs)}x <link rel=\"canonical\">",
                    items=hrefs[:10], count=len(hrefs),
                ))
            elif page_url and not _is_staging_url(href):
                # Staging canonical už hlásí kontrola 13 – nepenalizovat dvakrát.
                resolved = urljoin(page_url, href)
                if _canonical_key(resolved) != _canonical_key(page_url):
                    issues.append(Issue(
                        type=IssueType.CANONICAL_MISMATCH,
                        items=[resolved], count=1,
                        detail=f"stránka {page_url} → canonical {resolved}",
                    ))
            if page_url and urlparse(page_url).scheme.lower() == "https" \
                    and _is_http_scheme(href):
                issues.append(Issue(
                    type=IssueType.CANONICAL_HTTP,
                    items=[href], count=1,
                ))

    # 16. Open Graph — bez og:title/og:description/og:image ukáže Facebook,
    # LinkedIn i Messenger při sdílení jen holou URL bez náhledu. Kontrolujeme
    # existenci a neprázdnost; og:image navíc musí být absolutní URL
    # (relativní cestu sítě nerozvinou). Dostupnost obrázku ověřuje links_check.
    og_missing = [name for name in _OG_REQUIRED if not _og_content(soup, name)]
    og_detail = ""
    og_image = _og_content(soup, "og:image")
    if og_image and not og_image.lower().startswith(("http://", "https://", "//")):
        og_detail = f"og:image není absolutní URL: {og_image[:80]}"
    if og_missing or og_detail:
        issues.append(Issue(
            type=IssueType.MISSING_OG,
            items=[f"chybí {name}" for name in og_missing]
                  + ([og_detail] if og_detail else []),
            count=len(og_missing) + (1 if og_detail else 0),
            detail=og_detail,
        ))

    # 17. <img> bez width/height — prohlížeč nezná poměr stran, dokud obrázek
    # nestáhne, a obsah pod ním "poskočí" (Cumulative Layout Shift).
    # Stačí atributy width+height, nebo obojí v inline style.
    no_dims: list[str] = []
    for img in soup.find_all("img"):
        if _img_has_dimensions(img):
            continue
        src = (img.get("src") or img.get("data-src") or "").strip()
        if src.lower().startswith("data:"):
            src = "data:… (inline obrázek)"
        display = src[:80] + "…" if len(src) > 80 else src or "(bez src)"
        no_dims.append(display)
    if no_dims:
        issues.append(Issue(
            type=IssueType.IMG_NO_DIMENSIONS,
            items=no_dims[:50],
            count=len(no_dims),
        ))

    # 18. Soft 404 — stránka se načetla (HTTP 200), ale <title>/<h1> říká
    # „Stránka nenalezena“. Skutečné 404 sem nedojdou (validator_error).
    soft_404 = detect_soft_404(soup)
    if soft_404:
        issues.append(Issue(
            type=IssueType.SOFT_404,
            items=[soft_404],
            detail=soft_404,
        ))

    # 19. Prázdné odkazy — <a href="#">Text</a> bez jakéhokoli JS „háčku“
    # (class, id, data-*, role, aria-*, onclick…) je nedodělaný odkaz.
    empty_links = _find_empty_links(soup)
    if empty_links:
        issues.append(Issue(
            type=IssueType.EMPTY_HREF,
            items=empty_links[:50],
            count=len(empty_links),
        ))

    return issues


# href hodnoty, které nikam nevedou. „#neco“ je kotva – ta je v pořádku.
_EMPTY_HREFS = {"", "#", "#!", "javascript:", "javascript:;", "javascript:void(0)",
                "javascript:void(0);", "javascript:void 0", "javascript:void 0;",
                "javascript://", "javascript:false"}

# Atributy, které naznačují, že odkaz ovládá JavaScript (dropdown, modal,
# tab, slider…) – takový <a href="#"> je legitimní ovladač, ne díra.
_JS_HOOK_ATTR_PREFIXES = ("data-", "aria-", "on", "v-", "x-", "ng-", "@", ":", "hx-")
_JS_HOOK_ATTRS = {"role", "class", "id", "tabindex"}


def _find_empty_links(soup: BeautifulSoup) -> list[str]:
    """
    Vrátí popisky odkazů s textem, které nikam nevedou (href="#", "",
    javascript:void(0)) a nemají nic, čím by je mohl chytit JavaScript.
    Odkazy jen s obrázkem (galerie, lightbox) a odkazy, které rozbalují
    podmenu (mají sourozence <ul>), se nehlásí.
    """
    found: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a"):
        href = a.get("href")
        if href is None:
            continue                      # <a name="…"> / kotva bez href
        href_norm = " ".join(href.split()).lower()
        if href_norm not in _EMPTY_HREFS:
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        if not text:
            continue                      # jen ikona / obrázek → typicky JS ovladač
        if any(k in _JS_HOOK_ATTRS or k.lower().startswith(_JS_HOOK_ATTR_PREFIXES)
               for k in a.attrs if k != "href"):
            continue
        if a.find_next_sibling("ul") is not None or a.find_next_sibling("ol") is not None:
            continue                      # rodič rozbalovacího menu
        parent = a.parent
        if parent is not None and parent.name == "li" and parent.find(["ul", "ol"]) is not None:
            continue
        shown = text[:60] + "…" if len(text) > 60 else text
        entry = f'„{shown}“  (href="{href.strip()}")'
        if entry not in seen:
            seen.add(entry)
            found.append(entry)
    return found


_OG_REQUIRED = ("og:title", "og:description", "og:image")
_STYLE_WIDTH_RE  = re.compile(r"(^|[;\s])width\s*:", re.I)
_STYLE_HEIGHT_RE = re.compile(r"(^|[;\s])height\s*:", re.I)


def _og_content(soup: BeautifulSoup, name: str) -> str:
    """
    Obsah <meta property="og:…"> (správně) nebo <meta name="og:…"> (častá
    chyba, sítě ji většinou tolerují). Vrací "" pokud tag chybí nebo je prázdný.
    """
    pattern = re.compile(rf"^{re.escape(name)}$", re.I)
    for attr in ("property", "name"):
        meta = soup.find("meta", attrs={attr: pattern})
        if meta is not None and (meta.get("content") or "").strip():
            return meta["content"].strip()
    return ""


def _img_has_dimensions(img) -> bool:
    """True pokud má <img> width i height (atributy, nebo obojí v inline style)."""
    if (img.get("width") or "").strip() and (img.get("height") or "").strip():
        return True
    style = img.get("style") or ""
    return bool(_STYLE_WIDTH_RE.search(style) and _STYLE_HEIGHT_RE.search(style))


def mark_duplicate_titles(results: list) -> int:
    """
    Kontrola napříč webem: stejný <title> na více stránkách.

    Volá se jednou po zpracování všech stránek. Každé postižené stránce
    přidá do `structure_issues` Issue DUPLICATE_TITLE s textem titulku
    v `detail` a ostatními URL se stejným titulkem v `items`.
    Porovnává se bez ohledu na velikost písmen a nadbytečné mezery;
    prázdné tituly se přeskakují (ty hlásí MISSING_TITLE per stránka).

    Vrátí počet různých duplicitních titulků.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        title = " ".join((r.get("title") or "").split())
        if title and r.get("w3c_category") != "validator_error":
            groups[title.lower()].append(r)

    n_dupes = 0
    for pages in groups.values():
        if len(pages) < 2:
            continue
        n_dupes += 1
        # Stejný detail (= label) pro celou skupinu, i když se tituly liší
        # jen velikostí písmen – Excel je pak seskupí do jednoho řádku.
        shown_title = " ".join(pages[0]["title"].split())[:80]
        for r in pages:
            others = [o["url"] for o in pages if o is not r]
            r.setdefault("structure_issues", []).append(Issue(
                type=IssueType.DUPLICATE_TITLE,
                detail=shown_title,
                items=others[:50],
                count=len(pages),
            ))
    return n_dupes


_HEAD_END_RE = re.compile(r"</head\s*>", re.I)


def extract_title(html: str) -> str:
    """
    Text <title> stránky pro kontrolu duplicit napříč webem (main.py si ho
    ukládá k výsledku). Parsuje jen část dokumentu po </head> – u velkých
    stránek je to výrazně levnější než druhý plný průchod parserem.
    """
    m = _HEAD_END_RE.search(html)
    fragment = html[:m.end()] if m else html
    return _page_title(BeautifulSoup(fragment, _PARSER))


def check_homepage_meta(html: str) -> list:
    """Kontrola délky meta title + description – jen na homepage."""
    issues = []
    soup   = BeautifulSoup(html, _PARSER)

    title = soup.find("title")
    if not title or not title.get_text(strip=True):
        issues.append("Chybí <title> tag")
    else:
        txt = title.get_text(strip=True)
        n   = len(txt)
        if n < META_TITLE_MIN:
            issues.append(f"<title> příliš krátký ({n} znaků, min {META_TITLE_MIN}): \"{txt}\"")
        elif n > META_TITLE_MAX:
            issues.append(f"<title> příliš dlouhý ({n} znaků, max {META_TITLE_MAX}): \"{txt[:60]}…\"")
        else:
            issues.append(f"<title> v pořádku ({n} znaků): \"{txt}\"")

    md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if md:
        content = md.get("content", "").strip()
        if content:
            n = len(content)
            if n < META_DESC_MIN:
                issues.append(f"<meta description> příliš krátká ({n} znaků, min {META_DESC_MIN})")
            elif n > META_DESC_MAX:
                issues.append(f"<meta description> příliš dlouhá ({n} znaků, max {META_DESC_MAX})")
            else:
                issues.append(f"<meta description> v pořádku ({n} znaků)")

    return issues