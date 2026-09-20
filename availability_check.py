"""
Dostupnost a „falešně fungující“ stránky.

Tři věci, které HTTP status sám neodhalí:

  1. **Soft 404** – stránka vrací HTTP 200, ale v <title> nebo <h1> hlásí
     „Stránka nenalezena“ / „404“ / „Page not found“. Vyhledávače ji
     indexují jako běžnou stránku, uživatel vidí chybu. Hledá se JEN
     v <title> a <h1> (rozdělených na části podle „|“, „–“, „:“), a jen
     když ta část JE chybová hláška – článek „Jak nastavit stránku 404“
     se nehlásí (viz `detect_soft_404`).

  2. **Test vlastní 404 stránky** – jeden GET na náhodnou neexistující
     cestu. Správně má web vrátit 404 (nebo 410). HTTP 200 znamená, že
     každý překlep v URL vypadá pro Google jako platná stránka; přesměrování
     na homepage totéž, jen skrytěji (viz `check_not_found_page`).

  3. **Bot ochrana** – Anubis (PoskiREAL weby), Cloudflare, DDoS-Guard…
     vrací místo obsahu ověřovací stránku, často s HTTP 200. Kdyby se
     validovala ona, celý audit by byl nesmysl: stejné W3C chyby na všech
     stránkách, žádný <h1>, „chybí meta description“… Detekce
     (`detect_bot_challenge`) běží před W3C i strukturou; postižená
     stránka se hlásí jako nenačtená a main.py varuje, že audit není platný.
"""
import re
import uuid
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from config import USER_AGENT, ACCEPT_LANGUAGE, DEFAULT_TIMEOUT

# Stejný parser jako structure_check (lxml není nutné)
_PARSER = "html.parser"


# ── 1. Soft 404 ──────────────────────────────────────────────────────────────

# <title> bývá „Stránka nenalezena | Firma s.r.o.“ – rozdělíme na části,
# každou posuzujeme zvlášť. Oddělovače: |, –, —, -, ·, •, :, ::
_TITLE_SPLIT_RE = re.compile(r"\s+[|–—\-·•]+\s+|\s*::\s*|\s*[|]\s*|:\s+")

# Část, která ZAČÍNÁ kódem 404 („404“, „Chyba 404“, „Error 404 – Not Found“,
# „HTTP 404“). „Prodej bytu 404 m²“ nezačíná na 404 → nehlásí se; „404 m²“
# jako samostatná část titulku je číslo s jednotkou, ne kód chyby.
_CODE_404_RE = re.compile(
    r"^(?:(?:chyba|error|fehler|http|kód|code|stav|status)\s*(?:č\.?\s*)?)?404"
    r"(?![\d,.])(?!\s*(?:m²|m2|m\b|mm\b|cm\b|km\b|kč|czk|eur|€|\$|ks\b|kg\b|%|,-))", re.I)

# Fráze „něco nebylo nalezeno“ – musí být spolu s podmětem (stránka/page…),
# aby „Nenalezeny žádné nemovitosti“ (prázdný výsledek filtru) neprošlo.
_NOT_FOUND_RE = re.compile(
    r"\b(?:nenalezena|nenalezeno|nebyla\s+nalezena|nebyla\s+najdena|nenašli|nenašla|"
    r"neexistuje|už\s+neexistuje|již\s+neexistuje|nelze\s+(?:najít|nalézt|zobrazit|načíst)|"
    r"nepodařilo\s+(?:se\s+)?(?:najít|nalézt|načíst)|nebyla\s+nalezená|"
    r"not\s+found|could\s+not\s+be\s+found|cannot\s+be\s+found|can't\s+be\s+found|"
    r"doesn'?t\s+exist|does\s+not\s+exist|no\s+longer\s+exists|"
    r"nicht\s+gefunden|existiert\s+nicht)\b", re.I)
_SUBJECT_RE = re.compile(
    r"\b(?:stránk[auy]|stranu|strana|str\.|page|seite|url|adresa|adresu|address|"
    r"obsah|content|dokument|document|soubor|file)\b", re.I)

# Články o 404 („Jak nastavit vlastní 404 stránku“) – ne soft 404.
_HOWTO_RE = re.compile(
    r"^(?:jak|proč|co\s+(?:je|dělat|znamená)|kdy|how|why|what|tipy|návod|guide|tutorial)\b", re.I)

# Stránka o 404 s dlouhým textem (blog) – soft 404 stránky jsou krátké.
SOFT_404_MAX_TEXT_LEN = 8000


def _segments(text: str) -> list[str]:
    text = " ".join((text or "").split())
    return [s.strip(" .,!?()[]\"'„“") for s in _TITLE_SPLIT_RE.split(text) if s.strip()]


def _is_not_found_text(segment: str) -> bool:
    """True, když je celá část titulku / nadpisu hláškou o nenalezené stránce."""
    if not segment or len(segment) > 90:
        return False
    if _HOWTO_RE.search(segment):
        return False
    if _CODE_404_RE.search(segment):
        return True
    return bool(_NOT_FOUND_RE.search(segment) and _SUBJECT_RE.search(segment))


def detect_soft_404(soup: BeautifulSoup) -> str:
    """
    Vrátí text (<title> nebo <h1>), podle kterého stránka vypadá jako
    404 hláška, nebo "" když nic. Volá se jen pro stránky s HTTP 200
    (skutečné 404 se do kontroly struktury vůbec nedostanou).
    """
    candidates: list[str] = []
    # <title> přednostně z <head> – inline <svg> v těle má vlastní <title>
    scope = soup.head if soup.head is not None else soup
    title = scope.find("title")
    if title:
        candidates.append(title.get_text(" ", strip=True))
    for h1 in soup.find_all("h1"):
        candidates.append(h1.get_text(" ", strip=True))

    hit = ""
    for text in candidates:
        if any(_is_not_found_text(seg) for seg in _segments(text)):
            hit = " ".join(text.split())[:120]
            break
    if not hit:
        return ""

    # Dlouhý článek, který má v titulku „404“ → to není chybová stránka.
    body = soup.find("body") or soup
    if len(body.get_text(" ", strip=True)) > SOFT_404_MAX_TEXT_LEN:
        return ""
    return hit


def detect_soft_404_html(html: str) -> str:
    """Varianta pro syrové HTML (test 404 stránky)."""
    if not html:
        return ""
    return detect_soft_404(BeautifulSoup(html, _PARSER))


# ── HTTP status z chybové hlášky requests ───────────────────────────────────

# requests: "404 Client Error: Not Found for url: …", "503 Server Error: …"
_HTTP_ERROR_RE = re.compile(r"^\s*(\d{3}) (?:Client|Server) Error\b")


def http_status_from_error(message: str) -> int:
    """HTTP status z hlášky `raise_for_status()`; 0 = síťová chyba / neznámé."""
    m = _HTTP_ERROR_RE.match(message or "")
    return int(m.group(1)) if m else 0


# ── 2. Test vlastní 404 stránky ─────────────────────────────────────────────

def _root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _is_root_path(url: str) -> bool:
    p = urlparse(url)
    return (p.path or "/") in ("/", "") and not p.query


def check_not_found_page(base_url: str, timeout: float = DEFAULT_TIMEOUT,
                         session: requests.Session | None = None) -> dict:
    """
    GET na náhodnou neexistující cestu; podle odpovědi řekne, jak se web
    chová k neexistujícím stránkám.

    Vrací:
      {
        "url":       testovaná URL,
        "status":    HTTP status prvního kroku (0 = chyba spojení),
        "final_status": status po přesměrování (== status, když nebylo),
        "final_url": kam jsme skončili ("" = bez přesměrování),
        "verdict":   "ok" | "soft_404" | "redirect_home" | "redirect_200" |
                     "server_error" | "forbidden" | "unknown" | "unreachable",
        "message":   česká věta pro terminál / Excel,
        "soft_404_text": text hlášky, pokud 200 stránka vypadá jako 404,
      }
    """
    root = _root(base_url)
    test_url = f"{root}/wv-neexistujici-stranka-{uuid.uuid4().hex[:10]}/"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": ACCEPT_LANGUAGE}
    get = (session.get if session is not None else requests.get)

    result = {"url": test_url, "status": 0, "final_status": 0, "final_url": "",
              "verdict": "unreachable", "message": "", "soft_404_text": ""}
    try:
        first = get(test_url, timeout=timeout, allow_redirects=False, headers=headers)
    except Exception as e:
        result["message"] = f"Test 404 stránky se nepodařil (chyba spojení: {e.__class__.__name__})"
        return result

    status = first.status_code
    result["status"] = status
    result["final_status"] = status
    final = first

    if 300 <= status < 400 and first.headers.get("Location"):
        # Přesměrování – dojdeme na konec a podíváme se, kde jsme skončili.
        try:
            final = get(test_url, timeout=timeout, allow_redirects=True, headers=headers)
            result["final_status"] = final.status_code
            result["final_url"] = final.url
        except Exception as e:
            result["verdict"] = "unknown"
            result["message"] = (f"Neexistující stránka se přesměrovává (HTTP {status}), "
                                 f"cíl se nepodařilo načíst ({e.__class__.__name__})")
            return result

    fs = result["final_status"]
    redirected = bool(result["final_url"])

    if fs in (404, 410):
        result["verdict"] = "ok"
        if redirected:
            result["message"] = (f"Neexistující stránka: přesměrování (HTTP {status}) "
                                 f"na {result['final_url']}, ta správně vrací HTTP {fs}")
        else:
            result["message"] = f"Web správně vrací HTTP {fs} pro neexistující stránku"
    elif fs == 200:
        soft = ""
        try:
            soft = detect_soft_404_html(final.text)
        except Exception:
            pass
        result["soft_404_text"] = soft
        if redirected and _is_root_path(result["final_url"]):
            result["verdict"] = "redirect_home"
            result["message"] = (f"Neexistující stránka se přesměrovává na homepage "
                                 f"(HTTP {status} → {result['final_url']}) – správně má "
                                 f"vrátit HTTP 404")
        elif redirected:
            result["verdict"] = "redirect_200"
            result["message"] = (f"Neexistující stránka se přesměrovává (HTTP {status}) na "
                                 f"{result['final_url']}, která vrací HTTP 200"
                                 + (f" (obsah: „{soft}“) – správně má vrátit HTTP 404" if soft
                                    else " – správně má vrátit HTTP 404"))
        else:
            result["verdict"] = "soft_404"
            if soft:
                result["message"] = (f"Web vrací HTTP 200 pro neexistující stránku – "
                                     f"zobrazuje chybovou stránku („{soft}“), ale se špatným "
                                     f"stavovým kódem (soft 404); správně má být HTTP 404")
            else:
                result["message"] = ("Web vrací HTTP 200 pro neexistující stránku (soft 404) – "
                                     "každá překlepnutá URL vypadá pro Google jako platná "
                                     "stránka; správně má být HTTP 404")
    elif fs >= 500:
        result["verdict"] = "server_error"
        result["message"] = f"Server vrací HTTP {fs} (chyba serveru) pro neexistující stránku"
    elif fs in (401, 403):
        result["verdict"] = "forbidden"
        result["message"] = (f"Neexistující stránka vrací HTTP {fs} – přístup odepřen "
                             f"(pravděpodobně firewall / bot ochrana), 404 nelze ověřit")
    else:
        result["verdict"] = "unknown"
        result["message"] = f"Neexistující stránka vrací HTTP {fs} – očekáváno 404"
    return result


# ── 3. Bot ochrana / challenge stránky ──────────────────────────────────────

# Každá položka: (název ochrany, regex nad prvními ~60 kB HTML).
# Vzory jsou konkrétní identifikátory těch služeb, ne obecná slova –
# „Just a moment“ v běžném textu stránky by samo o sobě stačit nemělo.
_BOT_CHALLENGE_RES: list[tuple[str, re.Pattern]] = [
    ("Anubis", re.compile(
        r'id="anubis_challenge"|techaro\.lol-anubis|/\.within\.website/x/cmd/anubis/|'
        r"<title>[^<]*Making sure you(?:&#39;|'|’)re not a bot", re.I)),
    ("Cloudflare", re.compile(
        r'<title>Just a moment\.{0,3}</title>|<title>Attention Required! \| Cloudflare</title>|'
        r'cf-browser-verification|id="cf-challenge-running"|/cdn-cgi/challenge-platform/|'
        r'id="challenge-form"[^>]*action="[^"]*__cf_chl|Checking your browser before accessing|'
        r'class="cf-error-details"|<span class="cf-code-label">Cloudflare Ray ID', re.I)),
    ("DDoS-Guard", re.compile(r"ddos-guard\.net|<title>DDo?S-Guard</title>", re.I)),
    ("Imperva / Incapsula", re.compile(r"_Incapsula_Resource|incapsula incident id", re.I)),
    ("Sucuri WAF", re.compile(r"Sucuri WebSite Firewall|sucuri_cloudproxy", re.I)),
    ("Akamai Bot Manager", re.compile(r"ak-challenge|_abck=|akamai\.net/.*/bm-verify", re.I)),
    ("hCaptcha / reCAPTCHA stránka", re.compile(
        r"<title>[^<]*(?:Verify you are human|Ověřte, že jste člověk|Are you a robot)", re.I)),
]

_CHALLENGE_SCAN_LIMIT = 60_000


def detect_bot_challenge(html: str) -> str:
    """
    Vrátí název bot ochrany („Anubis“, „Cloudflare“…), pokud HTML není
    obsah webu, ale ověřovací / blokovací stránka; jinak "".
    """
    if not html:
        return ""
    head = html[:_CHALLENGE_SCAN_LIMIT]
    for name, rx in _BOT_CHALLENGE_RES:
        if rx.search(head):
            return name
    return ""


def bot_challenge_message(name: str) -> str:
    """Text chyby pro výsledek stránky (Excel „Nedostupné stránky“, terminál)."""
    return (f"Blokováno bot ochranou ({name}) – server vrátil ověřovací stránku "
            f"místo obsahu; výsledek pro tuto stránku není platný")
