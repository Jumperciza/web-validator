"""
Robots.txt kontrola + detekce uživatelské sekce.

robots.txt check:
  - Přeskočí se pro domény obsahující poskireal.cz nebo poski.com
    (interní/dev prostředí kde robots.txt nemá produkční hodnotu).
  - Přeskočí se i pro lokální hosty (localhost, 127.0.0.1, *.local…) —
    lokální dev servery zřídka mají smysluplný robots.txt a kontrola
    by jen vracela falešné poplachy.
  - KRITICKÉ: detekuje `Disallow: /` pro Googlebot nebo * — to znamená
    že celý web je zablokován pro vyhledávače (klasický staging artefakt
    který se zapomene odstranit při nasazení na produkci).
  - Hledá pravidla pro Googlebot nebo * (all) která blokují .js/.css soubory
    nebo WordPress asset složky (/wp-content/, /wp-includes/).

Uživatelská sekce:
  - Testuje jednu cestu: /uzivatel/
  - HTTP 200 = existuje, ale jen pokud web nevrací 200 i pro náhodnou
    neexistující cestu (catch-all / soft 404) a nepřesměroval nás jinam.
  - HTTP 401/403 = existuje (chráněná sekce).
  - Síťová chyba = status 0, "nedostupné" (ne "neexistuje").
"""
import re
import uuid
from urllib.parse import urlparse

import requests

from config import (USER_AGENT, ACCEPT_LANGUAGE, DEFAULT_TIMEOUT,
                    SKIP_ROBOTS_PATTERNS)
from ui import is_local_url

UA      = USER_AGENT
TIMEOUT = DEFAULT_TIMEOUT

# ── Konstanty ─────────────────────────────────────────────────────────────────

# _SKIP_ROBOTS_PATTERNS importujeme z config.py
_SKIP_ROBOTS_PATTERNS = SKIP_ROBOTS_PATTERNS

# Cesta ke kontrole existence uživatelské sekce
_USER_PATH = "/uzivatel/"

# Prefix pro označení kritických chyb v issues listu.
# Volající kód (terminál, Excel) může podle prefixu rozpoznat závažnost
# a zvýraznit kritické chyby silněji než ostatní robots.txt problémy.
CRITICAL_PREFIX = "[KRITICKÉ] "

# Regex vzory pro detekci blokování JS/CSS v Disallow hodnotě
_JS_BLOCK_RE  = re.compile(r"(\.js[\$\?\*]?$|/\*\.js|\*\.js|\bjs\b/)", re.I)
_CSS_BLOCK_RE = re.compile(r"(\.css[\$\?\*]?$|/\*\.css|\*\.css|\bcss\b/)", re.I)
_WP_BLOCK_RE  = re.compile(r"/wp-(content|includes)/", re.I)


# ── Interní pomocné funkce ────────────────────────────────────────────────────

def _should_skip(url: str) -> bool:
    """
    True pokud doménu vůbec nebudeme kontrolovat — interní/dev prostředí
    nebo lokální host (localhost, 127.0.0.1, *.local atd.).
    """
    netloc = urlparse(url).netloc.lower()
    if any(p in netloc for p in _SKIP_ROBOTS_PATTERNS):
        return True
    return is_local_url(url)


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
      skipped  – True pokud se kontrola přeskočila (interní/dev/lokální host)
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

    # ── KRITICKÁ kontrola: Disallow: / blokuje celý web ──────────────────────
    # Pokud robots.txt obsahuje pro Googlebot nebo * pravidlo "Disallow: /",
    # znamená to že celý web je zakázán pro vyhledávače = web zmizí z Googlu.
    # Klasický staging artefakt (na dev má být schválně, na produkci je to chyba).
    # Hlásíme tu samou chybu jen jednou — netřeba ji duplikovat
    # pro Googlebot i * pokud je v obou.
    if any(p.strip() == "/" for p in disallows):
        issues.append(
            CRITICAL_PREFIX +
            "Disallow: / blokuje celý web pro vyhledávače "
            "(Googlebot nebo *) – web nebude indexován"
        )

    # ── JS/CSS blokování (méně závažné, ale stále problém pro SEO) ───────────
    for path in disallows:
        if not path:
            continue   # Prázdné Disallow = nic neblokuje
        if path.strip() == "/":
            continue   # Už zachyceno výše jako kritická chyba
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
      [{path, url, status_code, exists, note}]
    `note` = lidsky čitelné zdůvodnění (soft 404, přesměrování, chyba spojení…),
    prázdný string když není co dodat.

    Pro lokální hosty (localhost, 127.0.0.1, *.local…) vrací prázdný list —
    detekce uživatelské sekce na lokálním vývojovém serveru nemá smysl.
    """
    if is_local_url(base_url):
        return []

    parsed   = urlparse(base_url)
    root     = f"{parsed.scheme}://{parsed.netloc}"
    full_url = root + _USER_PATH
    headers  = {"User-Agent": UA, "Accept-Language": ACCEPT_LANGUAGE}

    try:
        resp        = requests.get(full_url, timeout=TIMEOUT,
                                   allow_redirects=True, headers=headers)
        status_code = resp.status_code
    except Exception as e:
        # Síťová chyba ≠ "neexistuje" – to nevíme. status 0 + note, aby
        # report ukázal "nedostupné" místo zeleného "Neexistuje".
        return [{
            "path": _USER_PATH, "url": full_url, "status_code": 0,
            "exists": False, "note": f"chyba spojení: {e.__class__.__name__}",
        }]

    exists = False
    note   = ""
    if status_code == 200:
        final_path = (urlparse(resp.url).path or "/").lower()
        if final_path.startswith(_USER_PATH.rstrip("/")):
            in_section = True
        elif any(k in final_path for k in ("prihlas", "login", "uzivatel")):
            # Přesměrování na přihlášení = sekce existuje, jen chce login
            in_section = True
            note = f"přesměrováno na přihlášení: {resp.url}"
        else:
            in_section = False
            # Přesměrováno pryč (typicky na homepage) – sekce reálně není.
            note = f"přesměrováno na {resp.url}"
        if in_section:
            # HTTP 200 samo o sobě nestačí: "catch-all" weby vrací 200 s
            # homepage/soft-404 pro libovolnou cestu. Sondou na nesmyslnou
            # cestu zjistíme, jestli je 200 vůbec vypovídající.
            exists, confirm_note = _confirm_exists(root, resp, headers)
            note = confirm_note or note
    elif status_code in (401, 403):
        # Chráněná sekce – existuje, jen do ní nesmíme.
        exists = True
        note   = f"HTTP {status_code} – chráněný přístup"

    return [{
        "path":        _USER_PATH,
        "url":         full_url,
        "status_code": status_code,
        "exists":      exists,
        "note":        note,
    }]


def _confirm_exists(root: str, resp, headers: dict) -> tuple[bool, str]:
    """
    Ověří, že HTTP 200 pro /uzivatel/ není jen catch-all odpověď webu.
    Stáhne náhodnou neexistující cestu; pokud i ta vrátí 200 s (téměř)
    stejným obsahem, je /uzivatel/ soft-404 → neexistuje.
    Když se sonda nepovede (síť), věříme původní 200.
    """
    probe_url = f"{root}/wv-probe-{uuid.uuid4().hex[:12]}/"
    try:
        probe = requests.get(probe_url, timeout=TIMEOUT,
                             allow_redirects=True, headers=headers)
    except Exception:
        return True, ""

    if probe.status_code != 200:
        return True, ""

    # Web vrací 200 i pro nesmysl → porovnáme obsah
    a, b = resp.content, probe.content
    if a == b:
        return False, "soft 404 – web vrací 200 pro libovolnou cestu"
    ratio = len(a) / len(b) if b else 0
    if 0.9 <= ratio <= 1.1:
        return False, "soft 404 – web vrací 200 pro libovolnou cestu"
    return True, "web vrací 200 i pro neexistující cesty – ověř ručně"