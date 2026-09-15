"""
Crawler – prochází web a vrací seznam HTML stránek na stejné doméně.

Rychlost: paralelní stahování (ThreadPoolExecutor) s omezeným počtem
          současných požadavků – rychlejší ale pořád šetrný k webu.

Pro lokální host (localhost, 127.0.0.1, *.local…) se vypnou všechny pauzy
mezi dávkami i `MIN_CRAWL_DELAY` floor. Lokální dev server stejně neexistuje
důvod ho šetřit a uživatelé chtějí audit co nejrychleji.
"""
import fnmatch
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

from colors import ok, gray
from config import (USER_AGENT, ACCEPT_LANGUAGE, CRAWL_TIMEOUT,
                    CRAWL_WORKERS, MIN_CRAWL_DELAY)
from ui import is_local_url

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


def is_excluded(url: str, patterns: list | None) -> bool:
    """
    True pokud URL odpovídá některému uživatelskému vzoru z `--exclude`.

    Vzory jsou glob (`*` = cokoliv včetně lomítek, `?` = jeden znak) a
    porovnávají se case-insensitive proti cestě URL (`/blog/clanek`) i proti
    celé URL – takže funguje `/blog/*`, `*.pdf` i `https://ex.cz/blog/*`.
    Koncové lomítko cesty se ignoruje: `/blog` vynechá `/blog` i `/blog/`.
    Používá fnmatchcase – obyčejný fnmatch je na Windows case-insensitive
    a na Linuxu ne, chování by se lišilo podle OS.
    """
    if not patterns:
        return False
    full = url.lower()
    path = urlparse(full).path.rstrip("/") or "/"
    for pat in patterns:
        p = pat.lower().strip()
        if not p:
            continue
        if fnmatch.fnmatchcase(path, p.rstrip("/") or "/") or fnmatch.fnmatchcase(full, p):
            return True
    return False


def _fetch(session: requests.Session, url: str,
           timeout: float = CRAWL_TIMEOUT) -> tuple:
    """Stáhne URL a vrátí (url, html_text) nebo (url, None)."""
    try:
        resp = session.get(url, timeout=timeout)
        ct   = resp.headers.get("Content-Type", "").lower()
        if resp.status_code == 200 and "text/html" in ct:
            return url, resp.text
        return url, None
    except Exception:
        return url, None


def crawl_site(start_url: str, max_pages: int = 500,
               delay: float = 1.0, timeout: float = CRAWL_TIMEOUT,
               seed_urls: list | None = None,
               exclude: list | None = None) -> list:
    """
    Crawluje web paralelně.
    delay   = minimální pauza mezi dávkami (přepíše MIN_DELAY pokud je vyšší).
    timeout = timeout jednoho requestu v sekundách (výchozí CRAWL_TIMEOUT).
    exclude = glob vzory z `--exclude` (viz is_excluded); odpovídající URL se
              nestahují ani neprocházejí kvůli odkazům.

    Pro lokální host (localhost, 127.0.0.1, *.local…) se delay nastaví na 0
    a `MIN_CRAWL_DELAY` floor i `crawl-delay` z robots.txt se ignorují —
    není koho šetřit, dev server má průchodnost vysokou.

    seed_urls = volitelný seznam URL které jsou už známé (např. z sitemap).
    Crawler tyto URL projde aby z nich vytáhl odkazy a objevil další stránky,
    ale nepřidá je do návratové hodnoty (volající kód je už má).
    Návratová hodnota tedy obsahuje JEN nově nalezené stránky.
    """
    parsed = urlparse(start_url)
    if not parsed.scheme:
        start_url = "https://" + start_url
        parsed    = urlparse(start_url)

    is_local = is_local_url(start_url)

    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept-Language": ACCEPT_LANGUAGE,
    })

    # Zjisti finální URL po přesměrování. Jde přes stejnou Session jako
    # crawl (stejné hlavičky vč. Accept-Language – web s jazykovou
    # negociací by jinak mohl probe přesměrovat jinam než zbytek crawlu).
    try:
        probe       = session.get(start_url, timeout=timeout, allow_redirects=True)
        base_netloc = urlparse(probe.url).netloc
        start_url   = probe.url.split("#")[0].rstrip("/")
    except Exception:
        base_netloc = parsed.netloc

    # Robots.txt + crawl-delay
    if is_local:
        # Lokální host — robots.txt typicky neexistuje a i kdyby ho někdo měl,
        # crawl-delay nás na vlastním stroji nezajímá.
        rp = None
        effective_delay = 0.0
    else:
        rp = RobotFileParser()
        rp_delay = 0.0
        try:
            rp.set_url(f"{parsed.scheme}://{base_netloc}/robots.txt")
            rp.read()
            # Respektuj Crawl-delay z robots.txt pokud je nastavený
            rp_delay = float(rp.crawl_delay(UA) or 0)
        except Exception:
            pass
        effective_delay = max(delay, rp_delay, MIN_DELAY)

        # Hlášku o robots.txt jen když crawl-delay skutečně rozhodl –
        # dřív se vypisovala i když pauzu zvedl jen náš MIN_DELAY floor.
        if rp_delay > delay and rp_delay >= MIN_DELAY:
            gray(f"  (robots.txt nastavuje crawl-delay: {rp_delay:g}s)"); print()

    # Připrav seed_set — URL z parametru seed_urls, které jsou už nalezené
    # jinou cestou (typicky sitemap). Tyto URL crawler stáhne a vytáhne z nich
    # odkazy, ale nepřidá je do `found` aby se neduplikovaly.
    seed_set: set = set()
    if seed_urls:
        for seed in seed_urls:
            seed_set.add(_url_key(_normalize(seed)))

    # Inicializace fronty: start_url je VŽDY první (i v seed režimu — sitemap
    # homepage často neobsahuje a bez toho by se homepage vůbec neauditovala),
    # pak seedy. Deduplikace přes url-key.
    initial: list = []
    seen_init: set = set()
    for s in [start_url] + list(seed_urls or []):
        n = _normalize(s)
        k = _url_key(n)
        if k not in seen_init:
            seen_init.add(k)
            initial.append(n)
    queue = deque(initial)

    seen     = set()
    seen_lock = threading.Lock()
    found    = []

    def _process_batch(batch: list) -> list:
        """Stáhne dávku URL paralelně a vrátí nové linky."""
        new_links = []
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(batch))) as ex:
            futures = {ex.submit(_fetch, session, url, timeout): url for url in batch}
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

            if (_ignore(url) or not _same_domain(base_netloc, url)
                    or is_excluded(url, exclude)):
                continue
            # robots.txt check — jen pro veřejné weby; lokální host vynecháváme
            if rp is not None:
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

        # Krátká pauza mezi dávkami – šetrné k serveru.
        # Pro lokální host effective_delay == 0, takže usínání úplně přeskakujeme.
        if effective_delay > 0:
            time.sleep(effective_delay)

    return found