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
               delay: float = 1.0, timeout: int = 15,
               seed_urls: list | None = None) -> list:
    """
    Crawluje web paralelně.
    delay = minimální pauza mezi dávkami (přepíše MIN_DELAY pokud je vyšší).

    seed_urls = volitelný seznam URL které jsou už známé (např. z sitemap).
    Crawler tyto URL projde aby z nich vytáhl odkazy a objevil další stránky,
    ale nepřidá je do návratové hodnoty (volající kód je už má).
    Návratová hodnota tedy obsahuje JEN nově nalezené stránky.
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

    # Připrav seed_set — URL z parametru seed_urls, které jsou už nalezené
    # jinou cestou (typicky sitemap). Tyto URL crawler stáhne a vytáhne z nich
    # odkazy, ale nepřidá je do `found` aby se neduplikovaly.
    seed_set: set = set()
    if seed_urls:
        for seed in seed_urls:
            seed_set.add(_url_key(_normalize(seed)))

    # Inicializace fronty: pokud máme seedy, použij je. Jinak start_url.
    if seed_urls:
        # Normalizujeme každý seed; deduplikujeme přes seen-key
        initial = []
        seen_init: set = set()
        for s in seed_urls:
            n = _normalize(s)
            k = _url_key(n)
            if k not in seen_init:
                seen_init.add(k)
                initial.append(n)
        queue = deque(initial)
    else:
        queue = deque([_normalize(start_url)])

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

                # Pokud URL byla seed (už ji volající má), do found ji nedáváme,
                # jen z ní vytáhneme odkazy.
                is_seed = _url_key(url) in seed_set
                if not is_seed:
                    with seen_lock:
                        found.append(url)

                    sys.stdout.write("  "); sys.stdout.flush()
                    ok("[OK]")
                    sys.stdout.write(f" {url}\n"); sys.stdout.flush()

                # Extrahuj linky (i ze seed URL — to je celý smysl seed režimu)
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

    # Hlavní smyčka – zpracovává dávky.
    # Limit max_pages se vztahuje na CELKOVÝ počet stránek (seed + nově nalezené).
    # Volající si seed URL drží, takže "nové" URL můžeme přidávat jen do
    # rozdílu max_pages - len(seed_set).
    seed_count = len(seed_set)
    while queue and (len(found) + seed_count) < max_pages:
        # Připrav dávku URL ke stažení
        batch = []
        while (queue and len(batch) < WORKERS
               and (len(found) + seed_count + len(batch)) < max_pages):
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