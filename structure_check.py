"""
Kontrola HTML struktury.

Vrací List[Issue] — strukturovaná data (viz issues.py).
Pro zpětnou kompatibilitu je tu ještě `check_structure_legacy()` co vrací list stringů.

Prováděné kontroly:
  1.  Existence a duplikáty <h1>
  2.  Pořadí nadpisů (žádné přeskočení)
  3.  Prázdné tagy
  4.  Duplicitní ID
  5.  Meta description (existence + neprázdnost)
  6.  Alt texty u obrázků
  7.  HTTP odkazy (místo HTTPS)
  8.  Externí odkazy bez target="_blank" rel="noopener"
  9.  Testovací / zástupný obsah
  10. Chybějící lang atribut na <html>
  11. Chybějící <meta name="viewport">
  12. <meta name="robots" content="noindex"> mimo dev domény
  13. URL ukazující na staging/dev domény (canonical, og:image, src, href...)
"""
import copy
import re
from typing import List
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from config import (META_TITLE_MIN, META_TITLE_MAX, META_DESC_MIN, META_DESC_MAX,
                    SKIP_NOINDEX_PATTERNS, STAGING_DOMAIN_PATTERNS)
from issues import Issue, IssueType

_EMPTY_TAGS = ["p", "div", "span", "section", "article",
               "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"]

# ── Zakázaná slova / testovací obsah ─────────────────────────────────────────
# Použity jen konkrétní výrazy — "lorem" samotné je příliš obecné (false-positive
# na textech o latině). "lorem ipsum" je specifický a jednoznačný.
_FORBIDDEN_WORDS: list[str] = [
    "lorem ipsum",
    "testujeme",
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
    "text zde",
    "nadpis zde",
]

# Parser preference — lxml je 3-5× rychlejší než html.parser
# Fallback na html.parser pokud lxml není nainstalován
try:
    import lxml  # noqa: F401
    _PARSER = "lxml"
except ImportError:
    _PARSER = "html.parser"


# ── Pomocné funkce ───────────────────────────────────────────────────────────

def _netloc_bare(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _is_external(href: str, page_url: str) -> bool:
    if not href.startswith(("http://", "https://")):
        return False
    if not page_url:
        return True
    return _netloc_bare(href) != _netloc_bare(page_url)


def _has_safe_rel(tag) -> bool:
    """True pokud rel obsahuje noopener nebo noreferrer."""
    rel = tag.get("rel", [])
    if isinstance(rel, str):
        rel = rel.split()
    return bool({"noopener", "noreferrer"} & {r.lower() for r in rel})


def _is_dev_noindex_domain(url: str) -> bool:
    """
    True pokud URL je z domény kde je <meta noindex> záměr (dev/staging).
    Tyto domény mají noindex by design — nehlásíme to jako chybu.
    Patří sem domény definované v SKIP_NOINDEX_PATTERNS.
    """
    if not url:
        return False
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
    page_url slouží k rozlišení interních vs. externích odkazů.
    """
    issues: List[Issue] = []
    soup   = BeautifulSoup(html, _PARSER)

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
    http_links = list(dict.fromkeys(
        a["href"].strip() for a in soup.find_all("a", href=True)
        if a["href"].strip().startswith("http://")
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
    for word in _FORBIDDEN_WORDS:
        if word in page_text and word not in found_words:
            found_words.append(word)
    if found_words:
        issues.append(Issue(
            type=IssueType.FORBIDDEN_CONTENT,
            items=[f'"{w}"' for w in found_words],
            count=len(found_words),
        ))

    # 10. lang atribut na <html>
    html_tag = soup.find("html")
    if html_tag is not None and not html_tag.get("lang", "").strip():
        issues.append(Issue(type=IssueType.MISSING_LANG))

    # 11. Meta viewport
    if not soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)}):
        issues.append(Issue(type=IssueType.MISSING_VIEWPORT))

    # 12. Noindex meta tag — kritická chyba (web nebude v Googlu)
    # Skip pro dev/staging domény (poskireal.cz, *.cz.dev.poski.com)
    # kde je noindex záměrný.
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
    # Skip pokud je sama auditovaná stránka na dev doméně (tam je dev URL záměr).
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
            rel = link.get("rel", [])
            if isinstance(rel, str):
                rel = rel.split()
            rel_lower = [r.lower() for r in rel]
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

    return issues


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