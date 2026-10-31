"""
Sdílené konstanty napříč projektem.

Všechny "magic numbers" jsou zde — snadné úpravy a konzistentní chování.
"""
from pathlib import Path

# ── HTTP identifikace ────────────────────────────────────────────────────────
# Bez "Mozilla": ochrana Anubis pustí UA s "Mozilla" a bez Accept-Language
# jen na stránku "Making sure you're not a bot!". Poctivý bot UA projde,
# Accept-Language posíláme jako druhou pojistku. Kdyby se ověřovací stránka
# přece vrátila, pozná ji availability_check.detect_bot_challenge.
USER_AGENT      = "WebValidator/1.0 (+https://github.com/Jumperciza/web-validator)"
ACCEPT_LANGUAGE = "cs-CZ,cs;q=0.9,en;q=0.8"

# ── Timeouty (sekundy) ───────────────────────────────────────────────────────
DEFAULT_TIMEOUT = 10   # krátké requesty (HEAD, robots.txt, sitemap)
FETCH_TIMEOUT   = 60   # stažení HTML stránky
CRAWL_TIMEOUT   = 12   # crawling (potřebuje být rychlejší)
W3C_TIMEOUT     = 60   # vnu.jar subprocess

# ── Paralelizace ─────────────────────────────────────────────────────────────
FETCH_WORKERS   = 3    # souběžná stažení HTML
LOCAL_WORKERS   = 4    # souběžné vnu.jar procesy
CRAWL_WORKERS   = 5    # souběžné requesty v crawleru
FETCH_DELAY     = 0.5  # pauza mezi dávkami stahování (s)
MIN_CRAWL_DELAY = 0.2  # minimální pauza v crawleru

# ── Limity ───────────────────────────────────────────────────────────────────
DEFAULT_MAX_PAGES = 500
SITEMAP_MAX_DEPTH = 3    # max hloubka sitemap index rekurze
SITEMAP_MIN_PAGES = 10   # pokud sitemap najde méně URL než tohle, doplní se crawlerem

# ── Odkazy a obrázky (links_check.py) ────────────────────────────────────────
IMAGE_MAX_KB    = 500  # obrázek větší než tohle se hlásí jako "příliš velký"
LINK_CHECK_DELAY = 0.2 # pauza po každém HEAD requestu v jednom workeru (s)
# Ochrana proti obřím webům (e-shopy s filtry = tisíce URL s parametry):
LINK_CHECK_MAX_TARGETS = 1500  # max ověřovaných cílů (přednost mají ty s nejvíc výskyty)
LINK_CHECK_MAX_SECONDS = 600   # časový rozpočet fáze [LINKS]; zbytek se označí jako neověřený
LINK_CHECK_ABORT_AFTER = 15    # tolik síťových chyb za sebou = výpadek sítě → fáze se přeruší
# Druhý pokus: cíl se síťovou chybou (status 0) se po pauze ověří ještě jednou,
# sériově – server může padat jen kvůli paralelní zátěži (viz links_check).
LINK_CHECK_RETRY_DELAY = 2.0   # pauza před druhým kolem (s)
LINK_CHECK_MAX_RETRIES = 50    # max cílů zkoušených podruhé (víc = spíš výpadek sítě)

# ── Prahy pro meta tagy ──────────────────────────────────────────────────────
META_TITLE_MIN = 30
META_TITLE_MAX = 60
META_DESC_MIN  = 70
META_DESC_MAX  = 160

# ── Firemní dev/staging domény ───────────────────────────────────────────────
# Seznam je v souboru firemni_domeny.txt vedle tohoto souboru. Ten se na GitHub
# nenahrává (.gitignore), vzor je ve firemni_domeny.example.txt.
# Bez souboru program funguje, jen nezná žádné dev/staging domény.
DOMAINS_FILE = Path(__file__).with_name("firemni_domeny.txt")


def load_domains(path: Path = DOMAINS_FILE) -> dict[str, list[str]]:
    """Načte sekce [dev] a [robots] ze souboru s doménami. Chybí-li, vrátí prázdné seznamy."""
    sections: dict[str, list[str]] = {"dev": [], "robots": []}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return sections
    current = None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    return sections


_DOMAINS = load_domains()

# [dev] – domény, kde je noindex záměr. Odkaz na ně z produkčního webu
# je zapomenutý odkaz na staging.
SKIP_NOINDEX_PATTERNS   = _DOMAINS["dev"]
STAGING_DOMAIN_PATTERNS = SKIP_NOINDEX_PATTERNS

# Kontrola robots.txt se přeskočí pro dev domény a pro [robots]
# (interní weby, kde robots.txt nemá produkční hodnotu).
SKIP_ROBOTS_PATTERNS = _DOMAINS["dev"] + _DOMAINS["robots"]
