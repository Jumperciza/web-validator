"""
Robots.txt kontrola + detekce uživatelské sekce.

robots.txt check:
  - Přeskočí se pro domény obsahující poskireal.cz nebo poski.com
    (interní/dev prostředí kde robots.txt nemá produkční hodnotu).
  - Hledá pravidla pro Googlebot nebo * (all) která blokují .js/.css soubory
    nebo WordPress asset složky (/wp-content/, /wp-includes/).

Uživatelská sekce:
  - Testuje jednu cestu: /uzivatel/
  - HTTP 200 = existuje, jiný kód = neexistuje.
"""
import re
from urllib.parse import urlparse

import requests

from config import USER_AGENT, ACCEPT_LANGUAGE, DEFAULT_TIMEOUT

UA      = USER_AGENT
TIMEOUT = DEFAULT_TIMEOUT

# ── Konstanty ─────────────────────────────────────────────────────────────────

# Domény pro které se přeskočí robots.txt kontrola
_SKIP_ROBOTS_PATTERNS = [
    "poskireal.cz",
    "poski.com",
    "poski.",           # catch-all pro subdomény
    ".cz.dev.",         # dev prostředí
]

# Cesta ke kontrole existence uživatelské sekce
_USER_PATH = "/uzivatel/"

# Regex vzory pro detekci blokování JS/CSS v Disallow hodnotě
_JS_BLOCK_RE  = re.compile(r"(\.js[\$\?\*]?$|/\*\.js|\*\.js|\bjs\b/)", re.I)
_CSS_BLOCK_RE = re.compile(r"(\.css[\$\?\*]?$|/\*\.css|\*\.css|\bcss\b/)", re.I)
_WP_BLOCK_RE  = re.compile(r"/wp-(content|includes)/", re.I)


# ── Interní pomocné funkce ────────────────────────────────────────────────────

def _should_skip(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return any(p in netloc for p in _SKIP_ROBOTS_PATTERNS)


def _parse_robots(content: str) -> dict[str, list[str]]:
    """
    Parsuje robots.txt dle RFC.
    Vrátí dict {agent_lower: [disallow_path, …]}.

    Správně zvládá:
      - více User-agent nad jedním blokem pravidel
      - komentáře (#)
      - prázdné řádky jako oddělovač záznamů
    """
    result      : dict[str, list[str]] = {}
    cur_agents  : list[str]            = []
    in_rules    : bool                 = False  # už jsme viděli alespoň jedno pravidlo

    def _flush():
        nonlocal cur_agents, in_rules
        cur_agents = []
        in_rules   = False

    for raw in content.splitlines():
        line = raw.split("#")[0].strip()   # Odstraň komentáře

        if not line:
            _flush()
            continue

        if ":" not in line:
            continue

        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            if in_rules:
                # Nový User-agent po pravidlech = nový záznam
                _flush()
            cur_agents.append(value.lower())

        elif field == "disallow":
            in_rules = True
            for agent in cur_agents:
                result.setdefault(agent, []).append(value)

        elif field in ("allow", "crawl-delay", "sitemap"):
            in_rules = True   # Taky je to pravidlo – signalizuje konec UA bloku

    return result


def _get_relevant_disallows(parsed: dict[str, list[str]]) -> list[str]:
    """Vrátí Disallow hodnoty pro Googlebot a * (all)."""
    disallows: list[str] = []
    for agent in ("googlebot", "*"):
        disallows.extend(parsed.get(agent, []))
    return disallows


# ── Veřejné API ───────────────────────────────────────────────────────────────

def check_robots_js_css(base_url: str) -> tuple[list[str], bool]:
    """
    Zkontroluje robots.txt zda neblokuje Googlebot od CSS/JS souborů.

    Vrátí (issues: list[str], skipped: bool).
      issues   – seznam textových problémů; prázdný = vše OK
      skipped  – True pokud doména je v _SKIP_ROBOTS_PATTERNS
    """
    if _should_skip(base_url):
        return [], True

    parsed_url = urlparse(base_url)
    robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"

    try:
        resp = requests.get(
            robots_url, timeout=TIMEOUT,
            headers={"User-Agent": UA},
            allow_redirects=True,
        )
    except Exception as e:
        return [f"Chyba při načítání robots.txt: {e}"], False

    if resp.status_code == 404:
        return [], False   # Žádný robots.txt = žádné blokování
    if resp.status_code != 200:
        return [f"robots.txt vrátil HTTP {resp.status_code}"], False

    parsed = _parse_robots(resp.text)
    disallows = _get_relevant_disallows(parsed)

    issues: list[str] = []
    for path in disallows:
        if not path:
            continue   # Prázdné Disallow = nic neblokuje
        if _JS_BLOCK_RE.search(path):
            issues.append(f"Blokování JavaScriptu (Googlebot): Disallow: {path}")
        if _CSS_BLOCK_RE.search(path):
            issues.append(f"Blokování CSS (Googlebot): Disallow: {path}")
        if _WP_BLOCK_RE.search(path):
            issues.append(
                f"Blokování WordPress assets – JS+CSS nedostupné (Googlebot): Disallow: {path}"
            )

    # Deduplikace při zachování pořadí
    seen   : set[str]  = set()
    unique : list[str] = []
    for i in issues:
        if i not in seen:
            seen.add(i)
            unique.append(i)

    return unique, False


def check_user_pages(base_url: str) -> list[dict]:
    """
    Otestuje zda existuje /uzivatel/ sekce.

    Vrátí seznam s jedním dict (list pro kompatibilitu s report_excel.py):
      [{path, url, status_code, exists}]
    """
    parsed   = urlparse(base_url)
    full_url = f"{parsed.scheme}://{parsed.netloc}{_USER_PATH}"

    try:
        resp        = requests.get(
            full_url,
            timeout=TIMEOUT,
            allow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": ACCEPT_LANGUAGE},
        )
        status_code = resp.status_code
        exists      = (status_code == 200)
    except Exception:
        status_code = 0
        exists      = False

    return [{
        "path":        _USER_PATH,
        "url":         full_url,
        "status_code": status_code,
        "exists":      exists,
    }]