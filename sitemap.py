"""
Sitemap ingestion – načte URL ze sitemap.xml (včetně sitemap index souborů).

Postup:
  1. Zkontroluje robots.txt pro Sitemap: direktivu
  2. Zkusí /sitemap.xml, /sitemap_index.xml, /sitemap-index.xml
  3. Rekurzivně rozbalí sitemap index → dílčí sitemaps
  4. Filtruje URL přes stejné filtry jako crawler (stejná doména, ignorované přípony…)

Pokud sitemap neexistuje nebo nastane chyba, vrátí prázdný seznam
a volající kód přepne na klasický crawler.
"""
import re
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

from colors import ok, warn, gray, info
from config import USER_AGENT, ACCEPT_LANGUAGE, DEFAULT_TIMEOUT
# Přesunuto z pozdního importu – pomocné funkce ze crawleru
from crawler import _same_domain, _ignore, _url_key

UA      = USER_AGENT
TIMEOUT = DEFAULT_TIMEOUT

# Namespace, který W3C sitemap protokol používá
_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


# ── Interní pomocné funkce ────────────────────────────────────────────────────

def _fetch_text(url: str, session: requests.Session) -> str | None:
    """Stáhne URL a vrátí text, nebo None při jakékoliv chybě."""
    try:
        resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "")
            if any(k in ct for k in ("xml", "text", "html")):
                return resp.text
        return None
    except Exception as e:
        gray(f"  (sitemap fetch chyba pro {url}: {e})"); print()
        return None


def _parse_sitemap_xml(xml_text: str) -> tuple[list, list]:
    """
    Parsuje sitemap XML.
    Vrátí (page_urls, sub_sitemap_urls).
    Funguje pro <urlset> i <sitemapindex>.
    """
    page_urls    = []
    sitemap_urls = []

    try:
        # Odstraníme namespace pro jednodušší parsing (ET ho vyžaduje v {})
        # Varianta A: ET s namespace-aware cestami
        root = ET.fromstring(xml_text)

        def _find_all(tag: str) -> list:
            """Hledá tag s i bez namespace."""
            results = root.findall(f".//{{{_NS}}}{tag}")
            if not results:
                results = root.findall(f".//{tag}")
            return results

        for loc in _find_all("loc"):
            text = (loc.text or "").strip()
            if not text:
                continue
            parent_tag = loc.find("..") if hasattr(loc, "find") else None
            # Rozlišení: jsme v <sitemap> (index) nebo <url> (stránka)?
            # Spolehlivější je projít přes parent tag
            sitemap_urls.append(text) if text.endswith(".xml") and (
                "sitemap" in text.lower() or _in_sitemap_element(root, text)
            ) else page_urls.append(text)

    except ET.ParseError as e:
        # Pokus o záchranný parsing – odstraníme namespace a zkusíme znovu
        try:
            clean = re.sub(r'\s*xmlns(?::[^=]+)?="[^"]+"', "", xml_text)
            root  = ET.fromstring(clean)
            for sm in root.findall(".//sitemap/loc"):
                t = (sm.text or "").strip()
                if t: sitemap_urls.append(t)
            for url in root.findall(".//url/loc"):
                t = (url.text or "").strip()
                if t: page_urls.append(t)
            # Fallback – nic nenašlo, vezmi vše z <loc>
            if not page_urls and not sitemap_urls:
                for loc in root.findall(".//loc"):
                    t = (loc.text or "").strip()
                    if t:
                        if t.endswith(".xml") and "sitemap" in t.lower():
                            sitemap_urls.append(t)
                        else:
                            page_urls.append(t)
        except ET.ParseError:
            gray(f"  (XML parse chyba, sitemap přeskočena)"); print()

    return page_urls, sitemap_urls


def _in_sitemap_element(root: ET.Element, url_text: str) -> bool:
    """Vrátí True pokud se daná URL nachází uvnitř <sitemap> elementu (ne <url>)."""
    for sm_elem in root.iter():
        if sm_elem.tag in (f"{{{_NS}}}sitemap", "sitemap"):
            for loc in sm_elem:
                if loc.tag in (f"{{{_NS}}}loc", "loc"):
                    if (loc.text or "").strip() == url_text:
                        return True
    return False


def _sitemap_candidates(base_url: str, session: requests.Session) -> list[str]:
    """
    Sestaví seznam kandidátních URL pro sitemap.
    Přednost má Sitemap: direktiva z robots.txt, pak standardní cesty.
    """
    parsed = urlparse(base_url)
    root   = f"{parsed.scheme}://{parsed.netloc}"
    candidates: list[str] = []

    # robots.txt → Sitemap: direktiva
    try:
        robots_resp = session.get(f"{root}/robots.txt", timeout=TIMEOUT)
        if robots_resp.status_code == 200:
            for line in robots_resp.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    if sm and sm not in candidates:
                        candidates.append(sm)
    except Exception:
        pass

    # Standardní cesty jako fallback
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
        url = root + path
        if url not in candidates:
            candidates.append(url)

    return candidates


# ── Veřejné API ───────────────────────────────────────────────────────────────

def fetch_sitemap_urls(base_url: str, max_urls: int = 500) -> list[str]:
    """
    Pokusí se načíst sitemap.xml a vrátit seznam stránek webu.

    Vrátí:
      - Seznam URL (list[str]) pokud sitemap existuje a obsahuje URL.
      - Prázdný seznam [] pokud sitemap neexistuje nebo nastane chyba –
        volající kód pak přepne na crawler.
    """
    parsed      = urlparse(base_url)
    base_netloc = parsed.netloc.lower().removeprefix("www.")

    session = requests.Session()
    session.headers.update({
        "User-Agent":      UA,
        "Accept-Language": ACCEPT_LANGUAGE,
        "Accept":          "application/xml,text/xml,*/*",
    })

    candidates       = _sitemap_candidates(base_url, session)
    visited_sitemaps : set[str] = set()
    all_page_urls    : list[str] = []
    found_sitemap    = False

    def _process_sitemap(sm_url: str, depth: int = 0) -> None:
        """Rekurzivně zpracuje sitemap (max hloubka 3 pro sitemap index)."""
        nonlocal found_sitemap
        if sm_url in visited_sitemaps or depth > 3:
            return
        visited_sitemaps.add(sm_url)

        xml_text = _fetch_text(sm_url, session)
        if xml_text is None:
            return

        found_sitemap = True
        page_urls, sub_sitemaps = _parse_sitemap_xml(xml_text)

        # Přidej stránky
        all_page_urls.extend(page_urls)

        # Rekurzivně zpracuj dílčí sitemaps (sitemap index)
        for sub_url in sub_sitemaps:
            if len(all_page_urls) >= max_urls:
                break
            _process_sitemap(sub_url, depth + 1)

    # Zkus kandidáty jeden po druhém; přestaň při prvním úspěchu
    for candidate in candidates:
        _process_sitemap(candidate)
        if found_sitemap:
            break   # Máme sitemap, nepokoušejme se o další kandidáty

    if not found_sitemap or not all_page_urls:
        return []

    # ── Filtrace – používáme _same_domain, _ignore, _url_key importované nahoře ──
    seen_keys : set[str] = set()
    filtered  : list[str] = []

    for url in all_page_urls:
        try:
            if not url.startswith(("http://", "https://")):
                continue
            # Porovnání domény
            url_netloc = urlparse(url).netloc.lower().removeprefix("www.")
            if url_netloc != base_netloc:
                continue
            if _ignore(url):
                continue
            key = _url_key(url)
            if key not in seen_keys:
                seen_keys.add(key)
                filtered.append(url)
            if len(filtered) >= max_urls:
                break
        except Exception:
            continue   # Přeskočíme problematickou URL

    return filtered