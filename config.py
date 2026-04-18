"""
Sdílené konstanty napříč projektem.

Dřív byl User-Agent a další hodnoty duplikované v crawler.py, sitemap.py,
robots_check.py a main.py. Centralizujeme na jedno místo – snadnější úpravy
a konzistentní chování.
"""

# HTTP identifikace – co se servery uvidí v access logu
USER_AGENT = "Mozilla/5.0 (compatible; WebValidator/1.0)"

# HTTP accept-language – preferované jazyky
ACCEPT_LANGUAGE = "cs-CZ,cs;q=0.9,en;q=0.8"

# Defaultní timeouty (sekundy)
DEFAULT_TIMEOUT = 10   # pro krátké requesty (HEAD, robots.txt, sitemap)
FETCH_TIMEOUT   = 60   # pro stažení HTML stránky
CRAWL_TIMEOUT   = 12   # pro crawling (potřebuje být rychlejší)
