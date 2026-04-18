"""
Kontrola HTML struktury.

Prováděné kontroly:
  1.  Existence a duplikáty <h1>
  2.  Pořadí nadpisů (žádné přeskočení)
  3.  Prázdné tagy (jeden průchod DOMem)
  4.  Duplicitní ID
  5.  Meta description (existence + neprázdnost)
  6.  Alt texty u obrázků
  7.  HTTP odkazy (místo HTTPS)
  8.  Externí odkazy bez target="_blank" rel="noopener"  ← nové
  9.  Testovací / zástupný obsah (lorem ipsum, asdf…)    ← nové
  10. Chybějící lang atribut na <html>                   ← bonus
  11. Chybějící <meta name="viewport">                   ← bonus
"""
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup

_EMPTY_TAGS = ["p", "div", "span", "section", "article",
               "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"]

META_TITLE_MIN = 30;  META_TITLE_MAX = 60
META_DESC_MIN  = 70;  META_DESC_MAX  = 160

# ── Zakázaná slova / testovací obsah ─────────────────────────────────────────
_FORBIDDEN_WORDS: list[str] = [
    "lorem ipsum",
    "lorem",
    "testujeme",
    "testovaci text",
    "testovaci obsah",
    "asdf",
    "qwerty",
    "pridat text",
    "vloztte text",
    "vložit text",
    "dummy text",
    "placeholder text",
    "sample text",
    "zmente tento text",
    "text zde",
    "nadpis zde",
]


def _netloc_bare(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _is_external(href: str, page_url: str) -> bool:
    if not href.startswith(("http://", "https://")):
        return False
    if not page_url:
        return True
    return _netloc_bare(href) != _netloc_bare(page_url)


def _has_safe_rel(tag) -> bool:
    """True pokud rel obsahuje noopener nebo noreferrer (noreferrer implkuje noopener)."""
    rel = tag.get("rel", [])
    if isinstance(rel, str):
        rel = rel.split()
    return bool({"noopener", "noreferrer"} & {r.lower() for r in rel})


def check_structure(html: str, page_url: str = "") -> list:
    """
    Vrátí seznam textových problémů v HTML.
    page_url slouží k rozlišení interních vs. externích odkazů.
    """
    issues = []
    soup   = BeautifulSoup(html, "html.parser")

    # 1. H1
    h1s = soup.find_all("h1")
    if not h1s:
        issues.append("Chybí <h1> tag")
    elif len(h1s) > 1:
        issues.append(f"Více <h1> tagů ({len(h1s)}x) – měl by být pouze jeden")

    # 2. Pořadí nadpisů
    prev, skips = 0, set()
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        lvl = int(h.name[1])
        if prev > 0 and lvl > prev + 1:
            key = (prev, lvl)
            if key not in skips:
                issues.append(f"Přeskočení nadpisů: <h{prev}> → <h{lvl}> (chybí <h{prev+1}>)")
                skips.add(key)
        prev = lvl

    # 3. Prázdné tagy – jeden průchod DOMem
    empty_counts: dict[str, int] = {}
    for t in soup.find_all(_EMPTY_TAGS):
        if not t.get_text(strip=True) and not t.find():
            empty_counts[t.name] = empty_counts.get(t.name, 0) + 1
    for tag in _EMPTY_TAGS:
        n = empty_counts.get(tag, 0)
        if n:
            issues.append(f"Prázdný tag <{tag}> ({n}x)")

    # 4. Duplicitní ID
    ids: dict[str, int] = {}
    for t in soup.find_all(id=True):
        v = t.get("id", "").strip()
        if v:
            ids[v] = ids.get(v, 0) + 1
    for v, n in ids.items():
        if n > 1:
            issues.append(f"Duplicitní ID: #{v} ({n}x)")

    # 5. Meta description
    md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if not md:
        issues.append('Chybí <meta name="description">')
    elif not md.get("content", "").strip():
        issues.append('<meta name="description"> je prázdná')

    # 6. Alt texty
    missing_alt = []
    for img in soup.find_all("img"):
        if img.get("alt") is None:
            src = img.get("src", "").strip()
            missing_alt.append(src[:60] + "..." if len(src) > 60 else src or "(bez src)")
    if missing_alt:
        issues.append(f"Obrázky bez alt atributu ({len(missing_alt)}x):")
        for s in missing_alt[:10]:
            issues.append(f"    img: {s}")
        if len(missing_alt) > 10:
            issues.append(f"    … a dalších {len(missing_alt) - 10}")

    # 7. HTTP odkazy
    http_links = list(dict.fromkeys(
        a["href"].strip() for a in soup.find_all("a", href=True)
        if a["href"].strip().startswith("http://")
    ))
    if http_links:
        issues.append(f"HTTP odkazy (nezabezpečené) ({len(http_links)}x):")
        for lnk in http_links[:10]:
            issues.append(f"    {lnk[:80]}")
        if len(http_links) > 10:
            issues.append(f"    … a dalších {len(http_links) - 10}")

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
            label = href[:70] + ("…" if len(href) > 70 else "")
            entry = f"{label}  [chybí: {', '.join(missing_parts)}]"
            if entry not in seen_ext:
                seen_ext.add(entry)
                bad_ext.append(entry)

    if bad_ext:
        issues.append(f"Externí odkazy bez target/noopener ({len(bad_ext)}x):")
        for lnk in bad_ext[:10]:
            issues.append(f"    {lnk}")
        if len(bad_ext) > 10:
            issues.append(f"    … a dalších {len(bad_ext) - 10}")

    # 9. Testovací / zástupný obsah
    # Odstraníme <script> a <style> aby se nehledalo v JS/CSS kódu
    for unwanted in soup(["script", "style"]):
        unwanted.decompose()
    page_text = soup.get_text(" ", strip=True).lower()

    found_words: list[str] = []
    for word in _FORBIDDEN_WORDS:
        if word in page_text and word not in found_words:
            found_words.append(word)
    if found_words:
        issues.append(f"Testovací/zástupný obsah nalezen ({len(found_words)} výraz(ů)):")
        for w in found_words:
            issues.append(f"    \"{w}\"")

    # 10. Chybějící lang atribut na <html> (bonus)
    html_tag = soup.find("html")
    if html_tag is not None and not html_tag.get("lang", "").strip():
        issues.append('Chybějící lang atribut na <html> (např. lang="cs")')

    # 11. Chybějící <meta name="viewport"> (bonus)
    if not soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)}):
        issues.append('Chybějící <meta name="viewport"> – problém na mobilech')

    return issues


def check_homepage_meta(html: str) -> list:
    """Kontrola délky meta title + description – jen na homepage."""
    issues = []
    soup   = BeautifulSoup(html, "html.parser")

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