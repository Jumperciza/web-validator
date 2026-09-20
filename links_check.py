"""
Kontrola dostupnosti odkazů a obrázků napříč webem.

Běží až po stažení všech stránek (main.py, fáze [LINKS]):
  1. `extract_refs()` vytáhne z HTML každé stránky absolutní URL všech
     <a href> a obrázků (<img src / data-src>, og:image).
  2. `check_resources()` cíle deduplikuje napříč webem, ty, které už známe
     (auditované stránky, co se načetly), neověřuje znovu, zbytek ověří
     paralelně HEAD requestem a každé postižené stránce přidá Issue:
       • BROKEN_LINK   – interní odkaz vrací 404 / 5xx / je nedostupný
       • IMG_BROKEN    – obrázek vrací 404 / je nedostupný
       • IMG_TOO_LARGE – obrázek má Content-Length nad IMAGE_MAX_KB
                         (SEO modul – jen s `--seo`)
     Interní odkazy, které vedou přes přesměrování (301/302), se jen
     zaznamenají do reportu (`redirects`) – nepenalizují se, ale odkaz má
     správně mířit rovnou na cílovou URL.

Externí cíle (jiná doména než auditovaný web) se ověřují jen s
`--check-external` – u velkého webu jde o stovky cizích serverů a řada
z nich HEAD od botů blokuje. I se zapnutou kontrolou proto u externích
cílů nebereme 401/403/405/429/999 jako "nefunkční" (nevíme).

Ochrana proti obřím webům (poučení z e-shopu: 423 stránek → 13 700 cílů,
3 hodiny, a k tomu výpadek DNS = 5 000 falešných "404"):
  • interní URL s query stringem (filtry `?p13[0]=…`, `?r=cs`, cache-bustery
    `?v=3`) se sloučí na URL bez parametrů – filtr na funkční kategorii je
    funkční; ověřuje se jen základní stránka. Výjimka: skriptové cesty
    (`index.php?id=5`), kde query nese obsah.
  • max LINK_CHECK_MAX_TARGETS cílů (přednost mají ty s nejvíc výskyty)
    a LINK_CHECK_MAX_SECONDS času; zbytek je "neověřeno", ne "nefunkční".
  • LINK_CHECK_ABORT_AFTER síťových chyb za sebou = výpadek sítě → fáze se
    přeruší a žádný síťově neověřený cíl se nehlásí jako chyba (skóre
    zůstane nezkreslené). DNS / connection chyba u vlastní domény webu
    (kterou jsme právě stáhli) se nikdy nebere jako nefunkční odkaz.
"""
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, urldefrag, urlunparse

import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

from config import (USER_AGENT, ACCEPT_LANGUAGE, DEFAULT_TIMEOUT, CRAWL_WORKERS,
                    IMAGE_MAX_KB, LINK_CHECK_DELAY, LINK_CHECK_MAX_TARGETS,
                    LINK_CHECK_MAX_SECONDS, LINK_CHECK_ABORT_AFTER)
from issues import Issue, IssueType
from ui import is_local_url

# Schémata, která nejsou HTTP cíl – nemá smysl je ověřovat
_SKIP_PREFIXES = ("mailto:", "tel:", "sms:", "callto:", "javascript:", "data:", "#")

# U externích serverů tyhle statusy nic neříkají o existenci stránky
# (blokování botů, HEAD nepovolen, rate limit, LinkedIn 999).
_EXTERNAL_UNKNOWN_STATUSES = {401, 403, 405, 429, 999}

# Po HEAD s těmito statusy zkusíme GET – server může HEAD prostě nepodporovat.
_RETRY_WITH_GET = {403, 405, 501}

# Cesty, kde query string nese obsah (index.php?id=5) – ty se neslučují.
_SCRIPT_SUFFIXES = (".php", ".asp", ".aspx", ".jsp", ".cgi", ".pl", ".do", ".cfm")

# Chyby, které znamenají "nedostali jsme se k serveru" (DNS, spojení) –
# na rozdíl od timeoutu / 404, kde server existuje a odpověděl špatně.
_NETWORK_ERROR_MARKERS = ("NameResolutionError", "getaddrinfo failed",
                          "name resolution", "NewConnectionError",
                          "Network is unreachable", "ConnectionResetError",
                          "RemoteDisconnected", "ProxyError", "SSLError")


# ── Pomocné funkce ───────────────────────────────────────────────────────────

def _netloc_bare(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _same_site(url: str, base_url: str) -> bool:
    return _netloc_bare(url) == _netloc_bare(base_url)


def url_key(url: str) -> str:
    """
    Klíč pro deduplikaci cílů: bez schématu (http/https míří na totéž, server
    přesměruje), bez www., bez koncového lomítka a fragmentu. Query zůstává.
    """
    p = urlparse(url)
    key = p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")
    if p.query:
        key += "?" + p.query
    return key


def collapse_query(url: str) -> str:
    """
    Vrátí URL bez query stringu (a fragmentu), pokud parametry nejspíš
    nenesou obsah – tedy u "hezkých" cest (`/produkty/krémy/?p13[0]=5`,
    `/kontakt?r=cs`, `/logo.png?v=3`). U skriptových cest (`index.php?id=5`)
    zůstane URL beze změny.
    """
    p = urlparse(url)
    if not p.query:
        return url
    last = p.path.rsplit("/", 1)[-1].lower()
    if last.endswith(_SCRIPT_SUFFIXES):
        return url
    return urlunparse((p.scheme, p.netloc, p.path, p.params, "", ""))


def is_trivial_redirect(url: str, target: str) -> bool:
    """
    True, když se URL a cíl přesměrování liší jen schématem (http→https),
    `www.` nebo koncovým lomítkem – technicky správně, jen „kosmetika“.
    """
    return url_key(url) == url_key(target)


def is_network_error(info: dict) -> bool:
    """True, když se k serveru vůbec nedalo dostat (DNS, odmítnuté spojení…)."""
    if info.get("status"):
        return False
    err = info.get("error") or ""
    return any(m in err for m in _NETWORK_ERROR_MARKERS)


def _absolute(page_url: str, ref: str) -> str:
    """Absolutní http(s) URL bez fragmentu, nebo "" pokud to není HTTP cíl."""
    ref = (ref or "").strip()
    if not ref or ref.lower().startswith(_SKIP_PREFIXES):
        return ""
    absolute, _ = urldefrag(urljoin(page_url, ref))
    if not absolute.lower().startswith(("http://", "https://")):
        return ""
    return absolute


def extract_refs(html: str, page_url: str) -> dict:
    """
    Vrátí {"links": [...], "images": [...]} – absolutní URL v pořadí výskytu,
    bez duplicit. Obrázky = <img src>, <img data-src> (lazy loading) a og:image.
    """
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    images: list[str] = []
    seen_l: set[str] = set()
    seen_i: set[str] = set()

    for a in soup.find_all("a", href=True):
        u = _absolute(page_url, a["href"])
        if u and u not in seen_l:
            seen_l.add(u); links.append(u)

    for img in soup.find_all("img"):
        for attr in ("src", "data-src"):
            u = _absolute(page_url, img.get(attr) or "")
            if u and u not in seen_i:
                seen_i.add(u); images.append(u)

    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop == "og:image":
            u = _absolute(page_url, meta.get("content") or "")
            if u and u not in seen_i:
                seen_i.add(u); images.append(u)

    return {"links": links, "images": images}


def make_session(workers: int = CRAWL_WORKERS) -> requests.Session:
    """Session s keep-alive poolem dimenzovaným na počet workerů."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT,
                            "Accept-Language": ACCEPT_LANGUAGE})
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=max(10, workers))
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def probe_url(session: requests.Session, url: str,
              timeout: float = DEFAULT_TIMEOUT) -> dict:
    """
    Ověří jednu URL. Vrátí {"status": int, "size": int|None, "error": str,
    "redirect": str, "redirect_status": int}.
    status 0 = síťová chyba (timeout, DNS…). HEAD, při 403/405/501 GET
    (stream – tělo se nestahuje), protože některé servery HEAD nepodporují.

    Přesměrování: první HEAD jde bez follow. Když server vrátí 3xx, zapíše se
    kam (`redirect` = cílová URL, `redirect_status` = 301/302…) a cíl se
    dojde druhým requestem – `status` je pak status KONCOVÉ stránky.
    Cíl bez přesměrování má "redirect": "" a stojí jeden request jako dřív.

    Spojení se po HEAD NEzavírá (resp.close() by ho vyhodilo z poolu a každý
    další request by znovu dělal DNS + TCP + TLS) – HEAD nemá tělo, stačí
    ho "přečíst", tím se spojení vrátí do keep-alive poolu.
    """
    redirect_to, redirect_status = "", 0
    try:
        resp = session.head(url, timeout=timeout, allow_redirects=False)
        location = resp.headers.get("Location") if resp.headers else None
        if 300 <= resp.status_code < 400 and location:
            redirect_status = resp.status_code
            resp.content
            resp = session.head(url, timeout=timeout, allow_redirects=True)
            # resp.url = kde jsme po všech skocích skončili; Location jen záloha
            final_url = getattr(resp, "url", None)
            if isinstance(final_url, str) and final_url and final_url != url:
                redirect_to = final_url
            else:
                redirect_to = urljoin(url, str(location))
        if resp.status_code in _RETRY_WITH_GET:
            resp.content            # prázdné tělo HEAD → spojení zpět do poolu
            resp = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
            streamed = True
        else:
            streamed = False
        size = None
        cl = resp.headers.get("Content-Length")
        if cl and str(cl).strip().isdigit():
            size = int(cl)
        status = resp.status_code
        if streamed:
            resp.close()            # tělo nechceme stahovat
        else:
            resp.content
        return {"status": status, "size": size, "error": "",
                "redirect": redirect_to, "redirect_status": redirect_status}
    except Exception as e:
        return {"status": 0, "size": None, "error": str(e)[:200],
                "redirect": redirect_to, "redirect_status": redirect_status}


def _status_label(info: dict) -> str:
    if info["status"]:
        return f"HTTP {info['status']}"
    return f"nedostupné ({info['error'] or 'chyba spojení'})"


# ── Hlavní funkce ────────────────────────────────────────────────────────────

def check_resources(results: list, base_url: str, check_external: bool = False,
                    session: requests.Session | None = None,
                    workers: int = CRAWL_WORKERS, timeout: float = DEFAULT_TIMEOUT,
                    on_progress=None,
                    max_targets: int | None = None,
                    max_seconds: float | None = None,
                    seo: bool = False) -> dict:
    """
    Ověří odkazy a obrázky ze všech stránek v `results` (klíč "refs" z
    extract_refs) a postiženým stránkám přidá Issue do "structure_issues".
    seo=False (výchozí) = příliš velké obrázky (IMG_TOO_LARGE) se nehlásí
    ani v "images" – patří do SEO modulu (`--seo`); nedostupné obrázky vždy.

    Vrací report pro Excel / JSON:
      {
        "broken_links": [{"url", "status", "error", "external", "sources": [...]}],
        "images":       [{"url", "status", "error", "size_kb", "problem", "sources": [...]}],
        "redirects":    [{"url", "status", "to", "final_status", "trivial", "sources": [...]}],
                        # interní odkazy vedoucí přes 301/302; trivial = liší se
                        # jen http→https / www / koncovým lomítkem
        "checked_links": int, "checked_images": int,   # kolik cílů se ověřovalo
        "known_ok": int,           # cílů = auditované stránky, neověřovaly se znovu
        "skipped_external": int,   # externích cílů vynecháno (bez --check-external)
        "collapsed_query": int,    # URL s parametry sloučených na základní URL
        "skipped_limit": int,      # cílů nad LINK_CHECK_MAX_TARGETS (neověřeno)
        "skipped_time": int,       # cílů neověřeno kvůli časovému limitu
        "unverified": int,         # cílů se síťovou chybou (nehlásí se jako chyba)
        "aborted": str,            # důvod přerušení fáze ("" = doběhla celá)
        "elapsed": float,          # doba ověřování (s)
        "check_external": bool,
      }
    """
    if max_targets is None:
        max_targets = LINK_CHECK_MAX_TARGETS
    if max_seconds is None:
        max_seconds = LINK_CHECK_MAX_SECONDS

    link_sources: dict[str, list[str]] = defaultdict(list)   # key → stránky
    image_sources: dict[str, list[str]] = defaultdict(list)
    target_url: dict[str, str] = {}                           # key → první viděná URL
    collapsed: set[str] = set()                               # sloučené původní URL

    # Auditované stránky, které se načetly – jejich URL už známe jako OK.
    known_ok = {url_key(r["url"]) for r in results
                if r.get("w3c_category") != "validator_error"}

    def _target(u: str) -> str:
        """Interní URL s parametry → základní URL (viz collapse_query)."""
        if _same_site(u, base_url):
            c = collapse_query(u)
            if c != u:
                collapsed.add(u)
                return c
        return u

    for r in results:
        refs = r.get("refs") or {}
        for u in refs.get("links") or []:
            u = _target(u)
            k = url_key(u)
            target_url.setdefault(k, u)
            if r["url"] not in link_sources[k]:
                link_sources[k].append(r["url"])
        for u in refs.get("images") or []:
            u = _target(u)
            k = url_key(u)
            target_url.setdefault(k, u)
            if r["url"] not in image_sources[k]:
                image_sources[k].append(r["url"])

    # Co ověřovat: interní vždy, externí jen na přání; auditované OK stránky ne.
    to_probe: list[str] = []
    skipped_external = 0
    known_hits = 0
    for k in list(link_sources) + [k for k in image_sources if k not in link_sources]:
        u = target_url[k]
        if k in known_ok:
            known_hits += 1
            continue
        if not _same_site(u, base_url):
            if not check_external:
                skipped_external += 1
                continue
        to_probe.append(k)

    # Strop: přednost mají cíle s nejvíc výskyty (největší dopad), zbytek
    # se neověřuje a nehlásí – jen se to napíše do reportu.
    skipped_limit = 0
    if max_targets and len(to_probe) > max_targets:
        to_probe.sort(key=lambda k: -(len(link_sources.get(k, ())) + len(image_sources.get(k, ()))))
        skipped_limit = len(to_probe) - max_targets
        to_probe = to_probe[:max_targets]

    if session is None:
        session = make_session(workers)

    pause = 0.0 if is_local_url(base_url) else LINK_CHECK_DELAY
    probed: dict[str, dict] = {}
    started = time.time()
    deadline = started + max_seconds if max_seconds else None
    aborted = ""
    attempted: set[str] = set()     # cíle, které se skutečně ověřovaly
    stop = threading.Event()        # přerušení fáze (výpadek sítě)

    def _do_probe(k: str) -> tuple:
        if stop.is_set() or (deadline and time.time() > deadline):
            return k, None          # přerušeno / časový rozpočet vyčerpán
        info = probe_url(session, target_url[k], timeout=timeout)
        if pause > 0:
            time.sleep(pause)
        return k, info

    if to_probe:
        consecutive_errors = 0
        ex = ThreadPoolExecutor(max_workers=max(1, min(workers, len(to_probe))))
        try:
            futures = [ex.submit(_do_probe, k) for k in to_probe]
            for i, fut in enumerate(as_completed(futures), 1):
                k, info = fut.result()
                if info is None:
                    continue        # neověřeno (časový limit)
                attempted.add(k)
                probed[k] = info
                if on_progress:
                    on_progress(i, len(to_probe))
                # Pojistka proti výpadku sítě: řada síťových chyb za sebou
                # = nemá smysl pokračovat (a nic z toho není chyba webu).
                if info["status"] == 0:
                    consecutive_errors += 1
                    if LINK_CHECK_ABORT_AFTER and consecutive_errors >= LINK_CHECK_ABORT_AFTER:
                        aborted = (f"{consecutive_errors} síťových chyb za sebou – "
                                   f"výpadek připojení nebo server blokuje requesty")
                        stop.set()
                        break
                else:
                    consecutive_errors = 0
        except KeyboardInterrupt:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
        ex.shutdown(wait=True, cancel_futures=True)
        if aborted:
            # Všechny síťové chyby z tohoto běhu zahodíme – při výpadku sítě
            # nevíme o webu nic. Co se nestihlo, je "neověřeno".
            for k in list(probed):
                if probed[k]["status"] == 0:
                    del probed[k]

    elapsed = time.time() - started
    skipped_time = 0 if aborted else len(to_probe) - len(attempted)

    # DNS / connection chyba u vlastní domény webu (tu jsme právě stáhli)
    # není nefunkční odkaz, ale výpadek na naší straně → neověřeno.
    unverified = 0
    for k in list(probed):
        info = probed[k]
        if is_network_error(info) and _same_site(target_url[k], base_url):
            del probed[k]
            unverified += 1
    if aborted:
        unverified += len(to_probe) - len(probed) - unverified

    # ── Vyhodnocení ─────────────────────────────────────────────────────────
    def _is_broken(info: dict, external: bool) -> bool:
        st = info["status"]
        if st == 0:
            return True
        if external and st in _EXTERNAL_UNKNOWN_STATUSES:
            return False
        return st >= 400

    broken_links: list[dict] = []
    page_broken: dict[str, list[str]] = defaultdict(list)
    for k, sources in link_sources.items():
        info = probed.get(k)
        if info is None:
            continue
        external = not _same_site(target_url[k], base_url)
        if _is_broken(info, external):
            broken_links.append({"url": target_url[k], "status": info["status"],
                                 "error": info["error"], "external": external,
                                 "sources": list(sources)})
            label = f"{target_url[k]} ({_status_label(info)})"
            for src in sources:
                page_broken[src].append(label)

    image_problems: list[dict] = []
    page_img_broken: dict[str, list[str]] = defaultdict(list)
    page_img_large: dict[str, list[str]] = defaultdict(list)
    max_bytes = IMAGE_MAX_KB * 1024
    for k, sources in image_sources.items():
        info = probed.get(k)
        if info is None:
            continue
        external = not _same_site(target_url[k], base_url)
        size_kb = round(info["size"] / 1024) if info["size"] is not None else None
        if _is_broken(info, external):
            image_problems.append({"url": target_url[k], "status": info["status"],
                                   "error": info["error"], "size_kb": size_kb,
                                   "problem": "broken", "sources": list(sources)})
            label = f"{target_url[k]} ({_status_label(info)})"
            for src in sources:
                page_img_broken[src].append(label)
        elif seo and info["size"] is not None and info["size"] > max_bytes:
            image_problems.append({"url": target_url[k], "status": info["status"],
                                   "error": "", "size_kb": size_kb,
                                   "problem": "large", "sources": list(sources)})
            label = f"{target_url[k]} ({size_kb} kB)"
            for src in sources:
                page_img_large[src].append(label)

    # Issue na každou postiženou stránku (skóre + sekce HTML struktura)
    for r in results:
        url = r["url"]
        for mapping, itype in ((page_broken, IssueType.BROKEN_LINK),
                               (page_img_broken, IssueType.IMG_BROKEN),
                               (page_img_large, IssueType.IMG_TOO_LARGE)):
            items = mapping.get(url)
            if items:
                r.setdefault("structure_issues", []).append(Issue(
                    type=itype, items=items[:50], count=len(items),
                ))

    # Interní odkazy přes přesměrování – informativně (bez Issue, bez penalizace)
    redirects: list[dict] = []
    for k, sources in link_sources.items():
        info = probed.get(k)
        if not info or not info.get("redirect"):
            continue
        if not _same_site(target_url[k], base_url):
            continue
        if not info["status"] or info["status"] >= 400:
            continue                # cíl je nefunkční – už je v broken_links
        redirects.append({"url": target_url[k], "status": info.get("redirect_status", 0),
                          "to": info["redirect"], "final_status": info["status"],
                          "trivial": is_trivial_redirect(target_url[k], info["redirect"]),
                          "sources": list(sources)})
    redirects.sort(key=lambda r: (r["trivial"], -len(r["sources"]), r["url"]))

    # Seřadit: nejdřív cíle s nejvíc zdroji (největší dopad)
    broken_links.sort(key=lambda b: (-len(b["sources"]), b["url"]))
    image_problems.sort(key=lambda b: (b["problem"] != "broken", -len(b["sources"]), b["url"]))

    return {
        "broken_links":     broken_links,
        "images":           image_problems,
        "redirects":        redirects,
        "checked_links":    sum(1 for k in attempted if k in link_sources),
        "checked_images":   sum(1 for k in attempted if k in image_sources and k not in link_sources),
        "known_ok":         known_hits,
        "skipped_external": skipped_external,
        "collapsed_query":  len(collapsed),
        "skipped_limit":    skipped_limit,
        "skipped_time":     skipped_time,
        "unverified":       unverified,
        "aborted":          aborted,
        "elapsed":          round(elapsed, 1),
        "check_external":   check_external,
    }
