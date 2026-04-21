"""
Crawler – prochází web a vrací seznam HTML stránek na stejné doméně.

Rychlost: paralelní stahování (ThreadPoolExecutor) s omezeným počtem
          současných požadavků – rychlejší ale pořád šetrný k webu.
"""
import re
import sys
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from colors import ok, warn, err, gray
from config import (USER_AGENT, ACCEPT_LANGUAGE, CRAWL_TIMEOUT,
                    CRAWL_WORKERS, MIN_CRAWL_DELAY)

# ── Konfigurace ──────────────────────────────────────────────────────────────
WORKERS   = CRAWL_WORKERS
MIN_DELAY = MIN_CRAWL_DELAY
UA        = USER_AGENT

# ── Filtry – precompilované regex vzory (rychlejší než str→re.search každé volání) ──
_IGNORE_PATTERNS = [
    r"\.pdf$", r"\.jpg$", r"\.jpeg$", r"\.png$", r"\.gif$", r"\.svg$",
    r"\.css$", r"\.js$",  r"\.ico$",  r"\.xml$", r"\.zip$", r"\.mp4$",
    r"\.mp3$", r"\.woff$",r"\.woff2$",r"\.ttf$",
    r"mailto:", r"tel:",  r"javascript:",
    r"\?",       # query parametry
    r"/\d+$",    # stránkování /vse/2
    r"-\d{3,}$", # detaily inzerátů -1684
]
_IGNORE_RE = [re.compile(p, re.IGNORECASE) for p in _IGNORE_PATTERNS]


def _normalize(url: str) -> str:
    url, _ = urldefrag(url)
    return url.rstrip("/")

def _strip_www(netloc: str) -> str:
    return netloc.lower().removeprefix("www.")

def _url_key(url: str) -> str:
    p = urlparse(url)
    return _strip_www(p.netloc) + p.path.rstrip("/")

def _same_domain(base: str, url: str) -> bool:
    return _strip_www(urlparse(url).netloc) == _strip_www(base)

def _ignore(url: str) -> bool:
    u = url.lower()
    return any(p.search(u) for p in _IGNORE_RE)


def _fetch(session: requests.Session, url: str) -> tuple:
    """Stáhne URL a vrátí (url, html_text) nebo (url, None)."""
    try:
        resp = session.get(url, timeout=CRAWL_TIMEOUT)
        ct   = resp.headers.get("Content-Type", "").lower()
        if resp.status_code == 200 and "text/html" in ct:
            return url, resp.text
        return url, None
    except Exception:
        return url, None


def crawl_site(start_url: str, max_pages: int = 500,
               delay: float = 1.0, timeout: int = 15) -> list:
    """
    Crawluje web paralelně.
    delay = minimální pauza mezi dávkami (přepíše MIN_DELAY pokud je vyšší).
    """
    parsed = urlparse(start_url)
    if not parsed.scheme:
        start_url = "https://" + start_url
        parsed    = urlparse(start_url)

    # Zjisti finální URL po přesměrování
    try:
        probe       = requests.get(start_url, timeout=10, allow_redirects=True,
                                   headers={"User-Agent": UA})
        base_netloc = urlparse(probe.url).netloc
        start_url   = probe.url.split("#")[0].rstrip("/")
    except Exception:
        base_netloc = parsed.netloc

    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept-Language": ACCEPT_LANGUAGE,
    })

    # Robots.txt
    rp = RobotFileParser()
    try:
        rp.set_url(f"{parsed.scheme}://{base_netloc}/robots.txt")
        rp.read()
        # Respektuj Crawl-delay z robots.txt pokud je nastavený
        rp_delay = rp.crawl_delay(UA) or 0
        effective_delay = max(delay, rp_delay, MIN_DELAY)
    except Exception:
        effective_delay = max(delay, MIN_DELAY)

    if effective_delay != delay:
        gray(f"  (robots.txt nastavuje crawl-delay: {effective_delay}s)"); print()

    queue    = deque([_normalize(start_url)])
    seen     = set()
    seen_lock = threading.Lock()
    found    = []

    def _process_batch(batch: list) -> list:
        """Stáhne dávku URL paralelně a vrátí nové linky."""
        new_links = []
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(batch))) as ex:
            futures = {ex.submit(_fetch, session, url): url for url in batch}
            for future in as_completed(futures):
                url, html = future.result()
                if html is None:
                    continue

                with seen_lock:
                    found.append(url)

                sys.stdout.write("  "); sys.stdout.flush()
                ok("[OK]")
                sys.stdout.write(f" {url}\n"); sys.stdout.flush()

                # Extrahuj linky
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    nxt = _normalize(urljoin(url, a["href"].strip()))
                    key = _url_key(nxt)
                    if (nxt.startswith(("http://", "https://"))
                            and _same_domain(base_netloc, nxt)
                            and not _ignore(nxt)):
                        with seen_lock:
                            if key not in seen:
                                new_links.append(nxt)
        return new_links

    # Hlavní smyčka – zpracovává dávky
    while queue and len(found) < max_pages:
        # Připrav dávku URL ke stažení
        batch = []
        while queue and len(batch) < WORKERS and len(found) + len(batch) < max_pages:
            url = queue.popleft()
            key = _url_key(url)

            with seen_lock:
                if key in seen:
                    continue
                seen.add(key)

            if _ignore(url) or not _same_domain(base_netloc, url):
                continue
            try:
                if not rp.can_fetch(UA, url):
                    continue
            except Exception:
                pass

            batch.append(url)

        if not batch:
            continue

        # Zpracuj dávku
        new_links = _process_batch(batch)

        # Přidej nové linky do fronty (deduplikace)
        with seen_lock:
            for lnk in new_links:
                key = _url_key(lnk)
                if key not in seen:
                    queue.append(lnk)

        # Krátká pauza mezi dávkami – šetrné k serveru
        time.sleep(effective_delay)

    return found