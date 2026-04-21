"""
Sdílené konstanty napříč projektem.

Všechny "magic numbers" jsou zde — snadné úpravy a konzistentní chování.
"""

# ── HTTP identifikace ────────────────────────────────────────────────────────
USER_AGENT      = "Mozilla/5.0 (compatible; WebValidator/1.0)"
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

# ── Prahy pro meta tagy ──────────────────────────────────────────────────────
META_TITLE_MIN = 30
META_TITLE_MAX = 60
META_DESC_MIN  = 70
META_DESC_MAX  = 160

# ── Skip patterns pro robots.txt kontrolu ────────────────────────────────────
# Domény pro které se přeskočí robots.txt kontrola (interní/dev prostředí)
SKIP_ROBOTS_PATTERNS = [
    "poskireal.cz",
    "poski.com",
    "poski.",
    ".cz.dev.",
]
