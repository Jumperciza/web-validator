"""
Web Validator – hlavní spouštěcí soubor.

Struktura:
  main.py            ← tento soubor (spouštěj tento)
  config.py          ← sdílené konstanty
  ui.py              ← terminál UI (banner, prompt_url, helpers, is_local_url)
  stats.py           ← výpočet statistik a skóre
  issues.py          ← Issue dataclass pro strukturální problémy
  crawler.py         ← crawling stránek
  sitemap.py         ← načtení URL ze sitemap.xml
  validator_w3c.py   ← W3C validace (server mód + subprocess fallback)
  structure_check.py ← kontrola HTML struktury
  robots_check.py    ← kontrola robots.txt + /uzivatel/
  links_check.py     ← dostupnost odkazů a obrázků (404, velikost)
  report_excel.py    ← generování Excel reportu
  report_json.py     ← JSON výstup + porovnání s minulým během
  updater.py         ← kontrola verze vnu.jar z GitHubu
  vnu.jar            ← lokální W3C validátor
"""
import argparse
import codecs
import ipaddress
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from colors          import ok, warn, err, info, gray, blue, pocet_problemu
from config          import (USER_AGENT, ACCEPT_LANGUAGE, FETCH_TIMEOUT,
                             FETCH_WORKERS, LOCAL_WORKERS, FETCH_DELAY,
                             DEFAULT_MAX_PAGES, SITEMAP_MIN_PAGES, IMAGE_MAX_KB,
                             LINK_CHECK_MAX_TARGETS, LINK_CHECK_MAX_SECONDS)
from crawler         import crawl_site
from sitemap         import fetch_sitemap_urls
from robots_check    import (check_robots_js_css, check_user_pages,
                             CRITICAL_PREFIX as ROBOTS_CRITICAL_PREFIX)
import validator_w3c as w3c_mod
from structure_check import (check_structure, check_homepage_meta,
                             extract_title, mark_duplicate_titles)
from links_check     import extract_refs, check_resources
from report_excel    import write_report
from report_json     import (build_json, write_json, build_json_path,
                             find_previous_json, load_previous, compare_runs,
                             format_previous_date)
from validator_w3c   import (find_vnu_jar, start_server, stop_server,
                             check_java_version)
from updater         import check_and_update, download_vnu_jar
from stats           import compute_stats
from ui              import (prompt_url, print_banner, is_valid_url,
                             normalize_url_input, is_local_url,
                             write, write_line)


_META_CHARSET_RE = re.compile(
    rb'<meta[^>]+charset\s*=\s*["\']?\s*([a-zA-Z0-9_.:-]+)', re.I)


def _sniff_meta_charset(html_bytes: bytes) -> str | None:
    """Vytáhne charset z <meta charset> / <meta http-equiv> v hlavičce dokumentu."""
    m = _META_CHARSET_RE.search(html_bytes[:4096])
    if not m:
        return None
    enc = m.group(1).decode("ascii", errors="ignore").strip().lower()
    try:
        codecs.lookup(enc)
        return enc
    except LookupError:
        return None


def fetch_html(session: requests.Session, url: str, timeout: int = FETCH_TIMEOUT):
    """
    Stáhne HTML stránky přes sdílenou Session.
    Vrátí (bytes, text, content_type) nebo (None, None, chyba).

    Kódování textu: pokud HTTP hlavička nenese charset, requests by pro
    text/* podle RFC použil ISO-8859-1 a česká diakritika by se rozbila
    (a struktura-check by pak hledal "vložte text" v mojibake). Proto
    charset bereme z <meta charset> v dokumentu, a když ani ten není,
    z autodetekce (`apparent_encoding`).
    """
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "")
        if "charset=" not in ct.lower():
            resp.encoding = (_sniff_meta_charset(resp.content)
                             or resp.apparent_encoding or "utf-8")
        return resp.content, resp.text, ct or "text/html"
    except Exception as e:
        return None, None, str(e)


def _normalize_for_match(url: str) -> str:
    """
    Normalizuje URL pro porovnání identity stránky:
      - lowercase netloc
      - odstraní www. prefix
      - odstraní trailing slash z path
      - ignoruje scheme, query, fragment
    Vrací řetězec ve tvaru "netloc+path", např. "example.cz/produkty".
    """
    p = urlparse(url)
    netloc = p.netloc.lower().removeprefix("www.")
    path   = p.path.rstrip("/")
    return f"{netloc}{path}"


def _is_audit_root(url: str, base_url: str) -> bool:
    """
    True pokud URL odpovídá startovní URL auditu (po normalizaci).
    Používá se k rozhodnutí kdy spouštět homepage meta kontrolu —
    místo dříve používaného `idx == 1`, který byl křehký
    (sitemap může mít první URL třeba blog post místo homepage).
    """
    if not base_url:
        return False
    return _normalize_for_match(url) == _normalize_for_match(base_url)


def _score_color_fn(score: int):
    if score >= 80: return ok
    if score >= 60: return warn
    return err


def _report_java_problem(status: str, version: str) -> None:
    """
    Vypíše varování pokud Java chybí nebo je zastaralá.
    Tichý při statusu 'ok' nebo 'unknown' – nic se neděje, validace pojede dál.
    Při 'missing'/'too_old' uživatele přátelsky nasměrujeme na adoptium.net.
    """
    if status == "missing":
        err("  [!] Java není nainstalovaná!"); print()
        gray("      W3C validace přes vnu.jar vyžaduje Javu 11 nebo novější."); print()
        gray("      Stáhnout zdarma: "); blue("https://adoptium.net"); print()
        gray("      Bez Javy nebude W3C validace fungovat."); print()
        print()
    elif status == "too_old":
        err(f"  [!] Java je příliš stará (nalezena verze {version})!"); print()
        gray("      W3C validace přes vnu.jar vyžaduje Javu 11 nebo novější."); print()
        gray("      Aktualizovat: "); blue("https://adoptium.net"); print()
        gray("      Se starou Javou se vnu.jar nespustí a W3C validace bude přeskočena."); print()
        print()


def _print_result(idx: int, total: int, url: str, w3c: dict,
                  structure_issues: list) -> None:
    """Vypíše výsledek jedné stránky thread-safe způsobem."""
    lines = []
    lines.append(("gray",  f"[{idx}/{total}]"))
    lines.append(("plain", f" {url}"))
    lines.append(("plain", "\n  -> "))

    cat = w3c["category"]
    if cat == "validator_error":
        # Stránku se nepodařilo stáhnout — nemá smysl tisknout W3C ani strukturu
        # (dřív se tu ukazovalo "[STRUKTURA: OK]", což bylo zavádějící).
        lines.append(("err", "[NEDOSTUPNÁ]"))
        lines.append(("plain", f" {w3c.get('error_msg') or 'stránku se nepodařilo načíst'}\n"))
        _emit_lines(lines)
        return

    if cat == "ok":
        lines.append(("ok", "[W3C: OK]"))
    elif cat == "warning":
        lines.append(("warn", f"[W3C: VAROVÁNÍ {len(w3c['warnings'])}]"))
    elif cat == "error":
        lines.append(("err", f"[W3C: CHYBA {len(w3c['errors'])}]"))
    elif cat == "warning_error":
        lines.append(("err", f"[W3C: VAROVÁNÍ {len(w3c['warnings'])} + CHYBA {len(w3c['errors'])}]"))
    else:
        lines.append(("gray", "[W3C: přeskočeno]"))
        msg = w3c.get("error_msg")
        # Stejný důvod (např. "vnu.jar nenalezen") vypíšeme jen jednou —
        # u 500 stránek by to jinak byl spam.
        if msg and msg not in _reported_skip_msgs:
            _reported_skip_msgs.add(msg)
            lines.append(("plain", "\n  "))
            lines.append(("warn", "[!]"))
            lines.append(("plain", f" {msg}"))

    lines.append(("plain", "  "))
    if structure_issues:
        lines.append(("warn", f"[STRUKTURA: {pocet_problemu(len(structure_issues))}]"))
    else:
        lines.append(("ok", "[STRUKTURA: OK]"))
    lines.append(("plain", "\n"))
    _emit_lines(lines)


_print_lock = threading.Lock()
_reported_skip_msgs: set[str] = set()   # důvody přeskočení W3C už vypsané


def _emit_lines(lines: list) -> None:
    """Vypíše (kind, text) dvojice pod jedním zámkem — výstup z threadů se nemíchá."""
    fn_map = {"ok": ok, "warn": warn, "err": err, "gray": gray, "plain": lambda s: None}
    with _print_lock:
        for kind, text in lines:
            if kind == "plain":
                sys.stdout.write(text); sys.stdout.flush()
            else:
                fn_map[kind](text)
        sys.stdout.flush()


# Doba jednotlivých fází (název → sekundy) – vypisuje se v závěrečném
# souhrnu, aby bylo hned vidět, kde běh trávil čas.
PHASE_TIMES: dict[str, float] = {}


def _fmt_duration(seconds: float) -> str:
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m {secs}s" if mins else f"{secs}s"


def validate_pages(pages: list, jar_path: str = "", start_url: str = "") -> list:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total    = len(pages)
    computed = {}

    # Lokální host = bez throttlingu. Dev server na vlastním stroji
    # nepotřebujeme šetřit, audit běží řádově rychleji.
    is_local = is_local_url(start_url)
    fetch_pause = 0.0 if is_local else FETCH_DELAY

    # Sdílená HTTP Session – keep-alive TCP spojení
    session = requests.Session()
    session.headers.update({
        "User-Agent":      USER_AGENT,
        "Accept-Language": ACCEPT_LANGUAGE,
    })

    # ── Krok 1: Stažení HTML ─────────────────────────────────────────────────
    html_data: dict = {}

    def _do_fetch(url):
        result = fetch_html(session, url)
        # Pauza běží UVNITŘ workeru — každý worker po svém requestu počká
        # FETCH_DELAY, takže na server jde max FETCH_WORKERS requestů za
        # (doba requestu + FETCH_DELAY). Dřív byl sleep v konzumní smyčce
        # as_completed, což workery vůbec nebrzdilo (všechny URL byly
        # submitnuté najednou) a throttling reálně neexistoval.
        if fetch_pause > 0:
            time.sleep(fetch_pause)
        return url, result

    gray("  [1/3]"); print(" Stahuji stránky...")
    t_fetch = time.time()
    ex = ThreadPoolExecutor(max_workers=FETCH_WORKERS)
    try:
        futures = {ex.submit(_do_fetch, url): url for url in pages}
        done = 0
        for future in as_completed(futures):
            url, result = future.result()
            html_data[url] = result
            done += 1
            if done % FETCH_WORKERS == 0 or done == total:
                write(f"\r  Staženo: {done}/{total}   ")
    except KeyboardInterrupt:
        # Ctrl+C: zahodíme frontu, jinak by `with`-blok čekal na dokončení
        # všech už submitnutých úloh (u 500 stránek klidně minuty).
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)
    print()

    # ── Krok 2: W3C + Struktura paralelně ────────────────────────────────────
    PHASE_TIMES["stažení"] = time.time() - t_fetch
    gray("  [2/3]"); print(" Validuji a kontroluji strukturu...\n")
    t_valid = time.time()

    def _do_validate(args: tuple) -> tuple:
        idx, url = args
        html_bytes, html_text, content_type = html_data.get(url, (None, None, "chyba"))

        if html_bytes is None:
            return idx, {
                "url": url, "w3c_category": "validator_error",
                "w3c_warnings": [], "w3c_errors": [],
                "w3c_error_msg": content_type,
                "structure_issues": [], "homepage_meta": [], "title": "",
                "refs": {},
            }

        w3c_res    = [None]
        struct_res = [[]]

        def _run_w3c():
            try:
                w3c_res[0] = w3c_mod.validate(html_bytes, jar=jar_path,
                                              content_type=content_type)
            except Exception as e:
                # Validátor spadl, ale stránka je stažená → "skipped", ne "validator_error"
                w3c_res[0] = {"category": "skipped", "warnings": [],
                              "errors": [], "error_msg": str(e)}

        def _run_struct():
            try:
                struct_res[0] = check_structure(html_text, page_url=url)
            except Exception as e:
                struct_res[0] = []
                gray(f"  (chyba struktura: {e})"); print()

        t1 = threading.Thread(target=_run_w3c)
        t2 = threading.Thread(target=_run_struct)
        t1.start(); t2.start()
        t1.join();  t2.join()

        # Homepage meta – kontrola se spouští jen na URL která odpovídá startu auditu.
        # Dříve to bylo `if idx == 1` což selhávalo když sitemap dala homepage
        # někde uprostřed nebo úplně chyběla.
        homepage_meta = []
        try:
            if _is_audit_root(url, start_url):
                homepage_meta = check_homepage_meta(html_text)
        except Exception as e:
            homepage_meta = [f"Chyba při kontrole meta: {e}"]

        # Title si ukládáme kvůli kontrole duplicit napříč webem (běží až
        # po zpracování všech stránek – viz mark_duplicate_titles).
        try:
            title = extract_title(html_text)
        except Exception:
            title = ""

        # Odkazy a obrázky na stránce – ověřuje je až fáze [LINKS]
        # (links_check.check_resources), tady je jen sbíráme.
        try:
            refs = extract_refs(html_text, url)
        except Exception:
            refs = {}

        return idx, {
            "url":              url,
            "w3c_category":     w3c_res[0]["category"],
            "w3c_warnings":     w3c_res[0]["warnings"],
            "w3c_errors":       w3c_res[0]["errors"],
            "w3c_error_msg":    w3c_res[0]["error_msg"],
            "structure_issues": struct_res[0],   # list[Issue]
            "homepage_meta":    homepage_meta,
            "title":            title,
            "refs":             refs,
        }

    ex = ThreadPoolExecutor(max_workers=LOCAL_WORKERS)
    try:
        futures = {ex.submit(_do_validate, (i + 1, url)): i
                   for i, url in enumerate(pages)}
        for future in as_completed(futures):
            try:
                idx, result = future.result()
                computed[idx] = result
            except Exception as e:
                err(f"\n  [!] Neočekávaná chyba při validaci: {e}"); print()
    except KeyboardInterrupt:
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)

    # ── Krok 3: Tisk výsledků v pořadí ───────────────────────────────────────
    PHASE_TIMES["validace"] = time.time() - t_valid
    gray("  [3/3]"); print(" Sestavuji výsledky...\n")
    results = [computed[i] for i in range(1, total + 1) if i in computed]

    # Kontrola napříč webem – musí proběhnout před tiskem, aby
    # [STRUKTURA: N problémů] u každé stránky už duplicitní title zahrnoval.
    n_dup_titles = mark_duplicate_titles(results)
    if n_dup_titles:
        noun = ("titulek" if n_dup_titles == 1
                else "titulky" if n_dup_titles < 5 else "titulků")
        warn(f"  [!] Duplicitní <title>: {n_dup_titles} {noun} "
             f"se opakuje na více stránkách"); print("\n")

    for i, r in enumerate(results, 1):
        _print_result(i, total, r["url"],
                      {"category":  r["w3c_category"],
                       "warnings":  r["w3c_warnings"],
                       "errors":    r["w3c_errors"],
                       "error_msg": r["w3c_error_msg"]},
                      r["structure_issues"])

    return results


def run_link_checks(results: list, url: str, check_external: bool = False) -> dict:
    """
    Fáze [LINKS]: ověří dostupnost interních odkazů a obrázků (HEAD),
    postiženým stránkám přidá Issue (viz links_check) a vypíše souhrn.
    Vrací link_report pro Excel a JSON.
    """
    info("  [LINKS]")
    print(" Ověřuji odkazy a obrázky"
          + (" (včetně externích)..." if check_external else "..."))

    def _progress(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            write(f"\r  Ověřeno: {done}/{total}   ")

    report = check_resources(results, url, check_external=check_external,
                             on_progress=_progress)
    PHASE_TIMES["odkazy"] = report.get("elapsed", 0.0)
    if report["checked_links"] + report["checked_images"]:
        print()

    if report.get("aborted"):
        warn(f"  [!] Kontrola odkazů přerušena: {report['aborted']}."); print()
        gray("      Neověřené cíle se nehlásí jako chyba – skóre není ovlivněné."); print()

    broken = report["broken_links"]
    images = report["images"]
    n_checked = report["checked_links"] + report["known_ok"]
    if broken:
        n_pages = len({src for b in broken for src in b["sources"]})
        warn(f"  [!] Nefunkční odkazy: {len(broken)} "
             f"(na {n_pages} {'stránce' if n_pages == 1 else 'stránkách'})"); print()
        for b in broken[:5]:
            st = f"HTTP {b['status']}" if b["status"] else "nedostupné"
            gray(f"      {st}  {b['url']}"); print()
        if len(broken) > 5:
            gray(f"      … a dalších {len(broken) - 5} (viz report)"); print()
    else:
        ok("  [✓]"); print(f" Odkazy fungují ({n_checked} ověřeno)")

    n_img_broken = sum(1 for im in images if im["problem"] == "broken")
    n_img_large  = len(images) - n_img_broken
    if images:
        parts = []
        if n_img_broken: parts.append(f"{n_img_broken} nedostupných")
        if n_img_large:  parts.append(f"{n_img_large} nad {IMAGE_MAX_KB} kB")
        warn(f"  [!] Obrázky: {', '.join(parts)}"); print()
    else:
        ok("  [✓]"); print(f" Obrázky v pořádku ({report['checked_images']} ověřeno)")

    if report["skipped_external"]:
        gray(f"  ({report['skipped_external']} externích cílů neověřeno – "
             f"zapni --check-external)"); print()
    if report.get("collapsed_query"):
        gray(f"  ({report['collapsed_query']} URL s parametry (filtry, ?v=…) "
             f"sloučeno na základní stránku)"); print()
    if report.get("skipped_limit"):
        gray(f"  ({report['skipped_limit']} cílů nad limit {LINK_CHECK_MAX_TARGETS} "
             f"neověřeno – ověřeny ty s nejvíc výskyty)"); print()
    if report.get("skipped_time"):
        gray(f"  ({report['skipped_time']} cílů neověřeno – vyčerpán časový limit "
             f"{_fmt_duration(LINK_CHECK_MAX_SECONDS)})"); print()
    if report.get("unverified"):
        gray(f"  ({report['unverified']} cílů neověřeno kvůli síťové chybě – "
             f"nehlásí se jako chyba)"); print()
    print()
    return report


def run_domain_checks(url: str) -> dict:
    """Spustí paralelně robots.txt + /uzivatel/ check."""
    robots_result = [[], False]
    user_result   = [[]]

    def _do_robots():
        try:
            issues, skipped = check_robots_js_css(url)
            robots_result[0] = issues
            robots_result[1] = skipped
        except Exception as e:
            robots_result[0] = [f"Chyba při kontrole robots.txt: {e}"]

    def _do_users():
        try:
            user_result[0] = check_user_pages(url)
        except Exception:
            user_result[0] = []

    t1 = threading.Thread(target=_do_robots)
    t2 = threading.Thread(target=_do_users)
    t1.start(); t2.start()
    t1.join();  t2.join()

    return {
        "robots_issues":  robots_result[0],
        "robots_skipped": robots_result[1],
        "user_pages":     user_result[0],
    }


def make_filename(url: str) -> str:
    """
    Sestaví jméno reportu z URL.
      - Pro běžné domény vezme první část před tečkou (poski.com → poski)
      - Pro IP adresy zachová celou (192.168.1.10 → 192_168_1_10)
      - localhost zůstane localhost
    """
    parsed = urlparse(url)
    host   = (parsed.hostname or parsed.netloc or "report").removeprefix("www.")

    # IP adresa? Zachováme všechny oktety převedením teček na podtržítka.
    try:
        ipaddress.ip_address(host)
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", host)
    except ValueError:
        # Běžný hostname — vezmeme první část (před první tečkou)
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", host.split(".")[0])

    # Fallback pro prázdný název (např. když host obsahoval jen speciální znaky)
    if not name:
        name = "report"
    return f"{name}_validator.xlsx"


def build_output_path(url: str, output: str | None = None, keep: bool = False,
                      reports_dir: Path | None = None) -> Path:
    """
    Kam uložit report.

    Výchozí: `excel reporty/<host>_validator.xlsx` a soubor se při každém
    běhu PŘEPÍŠE. Report je snímek aktuálního stavu webu – při opakovaném
    spouštění během oprav by se jinak hromadily desítky souborů, ze kterých
    je aktuální vždy jen ten poslední. Kdo chce historii, má `--keep`
    (zamčený soubor navíc řeší _save_workbook fallbackem s časovou značkou).

    --output CESTA  → konkrétní soubor (končí na .xlsx), nebo adresář –
                      v něm se použije výchozí jméno.
    --keep          → do jména se přidá časová značka, starý report zůstane.
    """
    if reports_dir is None:
        reports_dir = Path(__file__).resolve().parent / "excel reporty"
    default_name = make_filename(url)

    if output:
        out = Path(output).expanduser()
        if out.suffix.lower() == ".xlsx" and not out.is_dir():
            path = out
        else:
            path = out / default_name
    else:
        path = reports_dir / default_name

    if keep:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path  = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
    return path


def parse_exclude_patterns(values: list | None) -> list[str]:
    """
    `--exclude` lze zadat opakovaně i s více vzory oddělenými čárkou:
      --exclude "/blog/*" --exclude "/en/*"   ==   --exclude "/blog/*,/en/*"
    """
    patterns: list[str] = []
    for value in values or []:
        for pat in value.split(","):
            pat = pat.strip()
            if pat and pat not in patterns:
                patterns.append(pat)
    return patterns


def _ensure_utf8_stdout() -> None:
    """
    Windows: při přesměrování výstupu (pipe, soubor, některé IDE terminály)
    použije Python kódování konzole (cp1250) a znaky jako ✓ / → / … shodí
    program s UnicodeEncodeError. V interaktivní konzoli je UTF-8 default,
    takže se to projevilo jen "někdy" — typický neviditelný bug.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Web Validator – W3C + HTML struktura")
    parser.add_argument("url", nargs="?", help="URL webu")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--delay",     type=float, default=1.0)
    parser.add_argument("--no-update-check", action="store_true",
                        help="Přeskoč kontrolu verze vnu.jar")
    parser.add_argument("--no-interactive", action="store_true",
                        help="Žádné interaktivní dotazy ani ENTER na konci")
    parser.add_argument("--no-server", action="store_true",
                        help="Nepoužívat vnu.jar server mód (fallback na subprocess)")
    parser.add_argument("--exclude", action="append", metavar="VZOR",
                        help="Vynechat URL odpovídající glob vzoru, např. \"/blog/*\". "
                             "Lze opakovat nebo oddělit čárkou.")
    parser.add_argument("--output", metavar="CESTA",
                        help="Kam uložit report: soubor .xlsx nebo adresář "
                             "(výchozí: excel reporty/<host>_validator.xlsx)")
    parser.add_argument("--keep", action="store_true",
                        help="Nepřepisovat starý report – přidat do jména časovou značku")
    parser.add_argument("--fail-under", type=int, metavar="N",
                        help="Skončit s exit kódem 1, pokud je Web Quality Score < N "
                             "(0–100). Pro CI / kontrolu před nasazením.")
    parser.add_argument("--check-external", action="store_true",
                        help="Ověřit i odkazy a obrázky na cizích doménách "
                             "(výchozí: jen interní – rychlejší)")
    parser.add_argument("--json", metavar="CESTA",
                        help="Kam uložit JSON výsledek: soubor .json nebo adresář "
                             "(výchozí: vedle Excel reportu, <host>_validator.json)")
    args = parser.parse_args()

    if args.fail_under is not None and not 0 <= args.fail_under <= 100:
        parser.error("--fail-under musí být v rozsahu 0–100")
    exclude = parse_exclude_patterns(args.exclude)

    # ── Banner ───────────────────────────────────────────────────────────────
    print_banner()

    # ── Java check ───────────────────────────────────────────────────────────
    # Tichý při OK / unknown — varuje jen pokud Java chybí nebo je < 11.
    # Vyhodnocujeme jednou na startu; podle výsledku pak rozhodneme,
    # jestli má smysl provádět update check vnu.jar (ten Javu potřebuje).
    java_status, java_ver = check_java_version()
    _report_java_problem(java_status, java_ver)
    java_ok = java_status in ("ok", "unknown")

    # ── Detekce vnu.jar ──────────────────────────────────────────────────────
    jar = find_vnu_jar()
    if jar:
        ok("  [LOCAL]"); print(f" Lokální validátor nalezen: {jar}")
        # Update check má smysl jen pokud Java funguje — jinak by updater.py
        # vypsal skoro stejné varování o Javě znovu (duplicita).
        if not args.no_update_check and java_ok:
            jar = check_and_update(jar, non_interactive=args.no_interactive)
        elif not java_ok:
            gray("  Kontrola verze vnu.jar přeskočena (Java nefunguje)."); print()
        w3c_mod.vnu_jar = jar
        ok("  [✓]"); print(f" W3C validátor připraven: {jar}")
    else:
        print()
        warn("  [!] vnu.jar nebyl nalezen!"); print()
        print()

        if args.no_interactive:
            gray("  Non-interactive mód – W3C validace bude přeskočena."); print()
            answer = "n"
        else:
            try:
                answer = input("  Chceš stáhnout vnu.jar automaticky? [a/N]: ").strip().lower()
            except EOFError:
                answer = "n"

        if answer in ("a", "ano", "y", "yes"):
            jar = download_vnu_jar()
            if jar:
                w3c_mod.vnu_jar = jar
                ok("  [✓]"); print(f" W3C validátor připraven: {jar}")
            else:
                warn("  W3C validace bude přeskočena."); print()
        else:
            gray("  Bez vnu.jar bude W3C validace přeskočena."); print()
    print()

    # ── URL ──────────────────────────────────────────────────────────────────
    if args.url:
        url = normalize_url_input(args.url)
        if not is_valid_url(url):
            err(f"  [✗] '{url}' nevypadá jako platná URL."); print()
            sys.exit(2)
    elif args.no_interactive:
        err("  [✗] Non-interactive mód vyžaduje URL jako argument."); print()
        sys.exit(2)
    else:
        url = prompt_url()

    # ── Start měření až když je URL zadaná ───────────────────────────────────
    # (dřív bylo na začátku main() – pak se do časovače započítávalo
    #  i čekání na zadání URL a kontrola/stažení vnu.jar)
    _start_time = time.time()

    # ── Info o lokálním auditu ──────────────────────────────────────────────
    if is_local_url(url):
        info("  [LOCAL]"); print(" Auditujeme lokální / privátní host – některé kontroly")
        gray("          (robots.txt, noindex, staging URL, /uzivatel/) budou přeskočeny."); print()
        gray("          Pauzy mezi requesty jsou vypnuté – audit poběží naplno."); print()
        print()

    # ── Start vnu.jar server (pokud možno) ───────────────────────────────────
    if jar and not args.no_server:
        gray("  Spouštím vnu.jar server..."); print()
        if start_server(jar):
            ok("  [✓]"); print(" W3C server běží – validace bude rychlejší.")
        else:
            warn("  [!]"); print(" Server start selhal – použijeme subprocess (pomalejší).")
    print()

    # ── Sitemap → Crawler fallback ────────────────────────────────────────────
    pages        : list[str] = []
    source_label : str       = ""

    if exclude:
        gray(f"  Vynechávám URL podle vzorů: {', '.join(exclude)}"); print()

    info("  [SITEMAP]"); print(" Hledám sitemap.xml...")
    sm_pages: list[str] = []
    try:
        sm_pages = fetch_sitemap_urls(url, max_urls=args.max_pages, exclude=exclude)
        if sm_pages and len(sm_pages) >= SITEMAP_MIN_PAGES:
            # Sitemap má dostatek URL — použijeme ji a crawler přeskočíme.
            ok("  [SITEMAP]"); print(f" Nalezeno {len(sm_pages)} URL – crawler přeskočen.")
            pages        = sm_pages
            source_label = f"sitemap.xml ({len(sm_pages)} URL)"
        elif sm_pages:
            # Sitemap nalezena, ale obsahuje málo URL (pod prahem).
            # Použijeme ji jako seed a doplníme crawlerem.
            warn(f"  [!] Sitemap má jen {len(sm_pages)} URL "
                 f"(práh: {SITEMAP_MIN_PAGES}) – doplňuji crawlerem."); print()
        else:
            gray("  Sitemap nenalezena nebo prázdná."); print()
    except Exception as e:
        warn(f"  [!] Chyba při čtení sitemap: {e}"); print()

    # Crawler — buď samostatně (sitemap nic nenašla), nebo doplnění málo URL.
    if not pages:
        if sm_pages:
            # Hybrid: sitemap URL jako seedy, crawler hledá zbytek.
            gray(f"  Spouštím crawler s {len(sm_pages)} URL ze sitemapy jako seed..."); print()
            try:
                extra = crawl_site(url, max_pages=args.max_pages,
                                   delay=args.delay, seed_urls=sm_pages,
                                   exclude=exclude)
                pages = sm_pages + extra
                source_label = (f"sitemap+crawler "
                                f"({len(sm_pages)} ze sitemap, "
                                f"{len(extra)} z crawleru)")
            except Exception as e:
                err(f"  [✗] Crawler selhal: {e}"); print()
                # Zachráníme aspoň URL ze sitemap
                pages = sm_pages
                source_label = f"sitemap.xml ({len(sm_pages)} URL, crawler selhal)"
        else:
            # Klasický crawler — sitemap nebyla, jdeme od start_url.
            gray("  Spouštím crawler..."); print()
            try:
                pages        = crawl_site(url, max_pages=args.max_pages,
                                          delay=args.delay, exclude=exclude)
                source_label = f"crawler ({len(pages)} URL)"
            except Exception as e:
                err(f"  [✗] Crawler selhal: {e}"); print()
                pages = []

    # Startovní URL (homepage) musí být v auditu vždy — sitemap ji často
    # neobsahuje a bez ní by se nespustila kontrola meta title/description.
    if pages and not any(_is_audit_root(p, url) for p in pages):
        gray("  Startovní URL nebyla v seznamu stránek – přidávám ji na začátek."); print()
        pages.insert(0, url)

    print()

    if not pages:
        err("  [✗] Žádné stránky k validaci. Zkontroluj URL a připojení."); print()
        stop_server()
        if not args.no_interactive:
            input("Stiskni ENTER pro ukončení...")
        # Audit neproběhl → exit 1, aby to CI (--fail-under) nebralo jako úspěch.
        return 1

    if exclude:
        source_label += f", vynecháno: {', '.join(exclude)}"

    # ── Doménové kontroly ─────────────────────────────────────────────────────
    info("  [DOMAIN]"); print(" Kontroluji robots.txt a uživatelskou sekci...")
    try:
        domain_info = run_domain_checks(url)
    except Exception as e:
        err(f"  [!] Chyba doménových kontrol: {e}"); print()
        domain_info = {"robots_issues": [], "robots_skipped": False, "user_pages": []}

    robots_issues  = domain_info["robots_issues"]
    robots_skipped = domain_info["robots_skipped"]
    user_pages     = domain_info["user_pages"]
    user_found     = [p for p in user_pages if p.get("exists")]

    if robots_skipped:
        gray("  robots.txt check přeskočen (interní/dev/lokální host)"); print()
    elif robots_issues:
        for issue in robots_issues:
            if issue.startswith(ROBOTS_CRITICAL_PREFIX):
                # Kritická chyba (Disallow: / blokuje celý web) — červeně
                clean_msg = issue[len(ROBOTS_CRITICAL_PREFIX):]
                err("  [!!] "); print(clean_msg)
            else:
                warn("  [!] "); print(issue)
    else:
        ok("  [✓]"); print(" robots.txt neblokuje JS/CSS")

    if is_local_url(url) and not user_pages:
        gray("  /uzivatel/ check přeskočen (lokální host)"); print()
    elif user_found:
        warn("  [!] Nalezeny uživatelské stránky: ")
        print(", ".join(p["path"] for p in user_found))
        for p in user_found:
            if p.get("note"):
                gray(f"      ({p['note']})"); print()
    elif any(p.get("status_code", 0) == 0 for p in user_pages):
        # Síťová chyba – nevíme; neukazovat zelenou fajfku
        warn("  [!] /uzivatel/ nelze ověřit: ")
        print("; ".join(p.get("note", "chyba spojení") for p in user_pages))
    else:
        ok("  [✓]"); print(" Žádná uživatelská sekce nenalezena")
        for p in user_pages:
            if p.get("note"):
                gray(f"      ({p['note']})"); print()
    print()

    # ── Validace stránek ─────────────────────────────────────────────────────
    info("--- VALIDACE + KONTROLA HTML START ---")
    ok(f" ({len(pages)} stránek)"); print("\n")

    results = validate_pages(pages, jar_path=jar, start_url=url)

    # Server už není potřeba — ukončíme ho
    stop_server()

    # ── Odkazy a obrázky (404, velikost) ─────────────────────────────────────
    # Přidává Issue do výsledků → musí běžet PŘED výpočtem skóre.
    link_report = None
    try:
        link_report = run_link_checks(results, url, check_external=args.check_external)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        err(f"  [!] Kontrola odkazů selhala: {e}"); print()

    # ── Statistiky ────────────────────────────────────────────────────────────
    stats    = compute_stats(results)
    score_fn = _score_color_fn(stats.score)

    # ── Porovnání s minulým během (JSON vedle reportu) ───────────────────────
    output_path = build_output_path(url, output=args.output, keep=args.keep)
    json_path   = build_json_path(output_path, args.json)
    comparison  = None
    try:
        previous = load_previous(find_previous_json(json_path))
        if previous:
            comparison = compare_runs(previous,
                                      build_json(results, url, link_report=link_report))
    except Exception as e:
        gray(f"  (porovnání s minulým během se nepodařilo: {e})"); print()

    # ── Report ───────────────────────────────────────────────────────────────
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        saved_path = write_report(results, output_path, url,
                                  score=stats.score,
                                  source_label=source_label,
                                  domain_info=domain_info,
                                  link_report=link_report,
                                  comparison=comparison)
        if saved_path != output_path:
            warn(f"  [!] Soubor {output_path.name} je otevřený v jiném programu – "
                 f"report uložen jako {saved_path.name}"); print()
        output_path = saved_path
    except Exception as e:
        err(f"  [✗] Chyba při generování Excel reportu: {e}"); print()
        import traceback; traceback.print_exc()

    # JSON se zapisuje vždy – je to zdroj pro příští porovnání.
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path = write_json(build_json(results, url, source_label=source_label,
                                          domain_info=domain_info,
                                          link_report=link_report,
                                          comparison=comparison), json_path)
    except Exception as e:
        err(f"  [✗] Chyba při ukládání JSON: {e}"); print()
        json_path = None

    # ── Souhrn ───────────────────────────────────────────────────────────────
    print()
    info("=" * 62); print()
    print("  "); ok("HOTOVO!"); print()

    write("  Web Quality Score   : ")
    score_fn(f"{stats.score}/100"); write("\n")

    info("-" * 62); print()
    write_line("W3C – Bez problémů :", ok,
               stats.w3c_ok)
    write_line("W3C – Varování     :", warn if stats.w3c_warn else ok,
               stats.w3c_warn)
    write_line("W3C – Chyby        :", err if stats.w3c_err else ok,
               stats.w3c_err)
    write_line("Struktura – OK     :", ok,
               stats.struct_ok)
    write_line("Struktura – Chyby  :", warn if stats.struct_bad else ok,
               stats.struct_bad)
    if stats.w3c_skipped:
        write_line("W3C – Přeskočeno   :", gray, stats.w3c_skipped)
    if stats.w3c_failed:
        write_line("Nepodařilo načíst  :", err, stats.w3c_failed)

    if comparison:
        d = comparison["delta"]
        arrow = f"+{d}" if d > 0 else str(d) if d < 0 else "±0"
        write("  Změna od minula    : ")
        (_score_color_fn(100 if d > 0 else 0 if d < 0 else 70))(
            f"{comparison['previous_score']} → {comparison['score']} ({arrow})")
        write(f"  |  opraveno {comparison['fixed_count']}, "
              f"nové {comparison['new_count']}  "
              f"(minulý běh {format_previous_date(comparison['previous_date'])})\n")

    write(f"  Zdroj URL          : {source_label}\n")
    write("  Uloženo do         : ")
    blue(str(output_path)); write("\n")
    if json_path:
        write("  JSON               : ")
        blue(str(json_path)); write("\n")

    elapsed = time.time() - _start_time
    write("  Celková doba       : ")
    gray(_fmt_duration(elapsed)); write("\n")
    if PHASE_TIMES:
        write("  Doba fází          : ")
        gray("  |  ".join(f"{name} {_fmt_duration(t)}" for name, t in PHASE_TIMES.items()))
        write("\n")

    # ── Práh pro CI (--fail-under) ────────────────────────────────────────────
    exit_code = 0
    if args.fail_under is not None:
        info("-" * 62); print()
        if stats.score < args.fail_under:
            exit_code = 1
            err(f"  [✗] Skóre {stats.score} je pod prahem {args.fail_under} "
                f"→ exit kód 1"); print()
        else:
            ok(f"  [✓] Skóre {stats.score} splňuje práh {args.fail_under}"); print()

    info("=" * 62); print("\n")

    if not args.no_interactive:
        input("Stiskni ENTER pro ukončení...")
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Ctrl+C kdekoliv v průběhu: ukliď vnu.jar server a skonči tiše
        # (bez tracebacku). Exit kód 130 = konvence "ukončeno SIGINT".
        stop_server()
        print()
        warn("  [!] Přerušeno uživatelem (Ctrl+C)."); print()
        sys.exit(130)