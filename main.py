"""
Web Validator – hlavní spouštěcí soubor.

Struktura:
  main.py            ← tento soubor (spouštěj tento)
  config.py          ← sdílené konstanty
  ui.py              ← terminál UI (banner, prompt_url, helpers)
  stats.py           ← výpočet statistik a skóre
  issues.py          ← Issue dataclass pro strukturální problémy
  crawler.py         ← crawling stránek
  sitemap.py         ← načtení URL ze sitemap.xml
  validator_w3c.py   ← W3C validace (server mód + subprocess fallback)
  structure_check.py ← kontrola HTML struktury
  robots_check.py    ← kontrola robots.txt + /uzivatel/
  report_excel.py    ← generování Excel reportu
  updater.py         ← kontrola verze vnu.jar z GitHubu
  vnu.jar            ← lokální W3C validátor
"""
import argparse
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from colors          import ok, warn, err, info, gray, blue, pocet_problemu
from config          import (USER_AGENT, ACCEPT_LANGUAGE, FETCH_TIMEOUT,
                             FETCH_WORKERS, LOCAL_WORKERS, FETCH_DELAY,
                             DEFAULT_MAX_PAGES, SITEMAP_MIN_PAGES)
from crawler         import crawl_site
from sitemap         import fetch_sitemap_urls
from robots_check    import (check_robots_js_css, check_user_pages,
                             CRITICAL_PREFIX as ROBOTS_CRITICAL_PREFIX)
import validator_w3c as w3c_mod
from structure_check import check_structure, check_homepage_meta
from report_excel    import write_report
from validator_w3c   import find_vnu_jar, start_server, stop_server
from updater         import check_and_update, download_vnu_jar
from stats           import compute_stats
from ui              import (prompt_url, print_banner, is_valid_url,
                             normalize_url_input, write, write_line)


def fetch_html(session: requests.Session, url: str, timeout: int = FETCH_TIMEOUT):
    """Stáhne HTML stránky přes sdílenou Session. Vrátí (bytes, text, ct) nebo (None, None, chyba)."""
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content, resp.text, resp.headers.get("Content-Type", "text/html; charset=utf-8")
    except Exception as e:
        return None, None, str(e)


def _score_color_fn(score: int):
    if score >= 80: return ok
    if score >= 60: return warn
    return err


def _print_result(idx: int, total: int, url: str, w3c: dict,
                  structure_issues: list) -> None:
    """Vypíše výsledek jedné stránky thread-safe způsobem."""
    lines = []
    lines.append(("gray",  f"[{idx}/{total}]"))
    lines.append(("plain", f" {url}"))
    lines.append(("plain", "\n  -> "))

    cat = w3c["category"]
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
        if w3c.get("error_msg"):
            lines.append(("plain", "\n  "))
            lines.append(("warn", "[!]"))
            lines.append(("plain", f" {w3c['error_msg']}"))

    lines.append(("plain", "  "))
    if structure_issues:
        lines.append(("warn", f"[STRUKTURA: {pocet_problemu(len(structure_issues))}]"))
    else:
        lines.append(("ok", "[STRUKTURA: OK]"))
    lines.append(("plain", "\n"))

    _lock = getattr(_print_result, "_lock", None)
    if _lock is None:
        _print_result._lock = threading.Lock()
        _lock = _print_result._lock

    fn_map = {"ok": ok, "warn": warn, "err": err, "gray": gray, "plain": lambda s: None}
    with _lock:
        for kind, text in lines:
            if kind == "plain":
                sys.stdout.write(text); sys.stdout.flush()
            else:
                fn_map[kind](text)
        sys.stdout.flush()


def validate_pages(pages: list, jar_path: str = "") -> list:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total    = len(pages)
    computed = {}

    # Sdílená HTTP Session – keep-alive TCP spojení
    session = requests.Session()
    session.headers.update({
        "User-Agent":      USER_AGENT,
        "Accept-Language": ACCEPT_LANGUAGE,
    })

    # ── Krok 1: Stažení HTML ─────────────────────────────────────────────────
    html_data: dict = {}

    def _do_fetch(url):
        return url, fetch_html(session, url)

    gray("  [1/3]"); print(" Stahuji stránky...")
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        futures = {ex.submit(_do_fetch, url): url for url in pages}
        done = 0
        for future in as_completed(futures):
            url, result = future.result()
            html_data[url] = result
            done += 1
            if done % FETCH_WORKERS == 0 or done == total:
                write(f"\r  Staženo: {done}/{total}   ")
            time.sleep(FETCH_DELAY / FETCH_WORKERS)
    print()

    # ── Krok 2: W3C + Struktura paralelně ────────────────────────────────────
    gray("  [2/3]"); print(" Validuji a kontroluji strukturu...\n")

    def _do_validate(args: tuple) -> tuple:
        idx, url = args
        html_bytes, html_text, content_type = html_data.get(url, (None, None, "chyba"))

        if html_bytes is None:
            return idx, {
                "url": url, "w3c_category": "validator_error",
                "w3c_warnings": [], "w3c_errors": [],
                "w3c_error_msg": content_type,
                "structure_issues": [], "homepage_meta": [],
            }

        w3c_res    = [None]
        struct_res = [[]]

        def _run_w3c():
            try:
                w3c_res[0] = w3c_mod.validate(html_bytes, jar=jar_path)
            except Exception as e:
                w3c_res[0] = {"category": "validator_error", "warnings": [],
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

        homepage_meta = []
        try:
            if idx == 1:
                homepage_meta = check_homepage_meta(html_text)
        except Exception as e:
            homepage_meta = [f"Chyba při kontrole meta: {e}"]

        return idx, {
            "url":              url,
            "w3c_category":     w3c_res[0]["category"],
            "w3c_warnings":     w3c_res[0]["warnings"],
            "w3c_errors":       w3c_res[0]["errors"],
            "w3c_error_msg":    w3c_res[0]["error_msg"],
            "structure_issues": struct_res[0],   # list[Issue]
            "homepage_meta":    homepage_meta,
        }

    with ThreadPoolExecutor(max_workers=LOCAL_WORKERS) as ex:
        futures = {ex.submit(_do_validate, (i + 1, url)): i
                   for i, url in enumerate(pages)}
        for future in as_completed(futures):
            try:
                idx, result = future.result()
                computed[idx] = result
            except Exception as e:
                err(f"\n  [!] Neočekávaná chyba při validaci: {e}"); print()

    # ── Krok 3: Tisk výsledků v pořadí ───────────────────────────────────────
    gray("  [3/3]"); print(" Sestavuji výsledky...\n")
    results = []
    for i in range(1, total + 1):
        if i not in computed:
            continue
        r = computed[i]
        results.append(r)
        _print_result(i, total, r["url"],
                      {"category":  r["w3c_category"],
                       "warnings":  r["w3c_warnings"],
                       "errors":    r["w3c_errors"],
                       "error_msg": r["w3c_error_msg"]},
                      r["structure_issues"])

    return results


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
    parsed = urlparse(url)
    host   = (parsed.hostname or parsed.netloc or "report").replace("www.", "")
    name   = re.sub(r"[^a-zA-Z0-9_-]", "_", host.split(".")[0])
    return f"{name}_validator.xlsx"


def main():
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
    args = parser.parse_args()

    # ── Banner ───────────────────────────────────────────────────────────────
    print_banner()

    # ── Detekce vnu.jar ──────────────────────────────────────────────────────
    jar = find_vnu_jar()
    if jar:
        ok("  [LOCAL]"); print(f" Lokální validátor nalezen: {jar}")
        if not args.no_update_check:
            jar = check_and_update(jar, non_interactive=args.no_interactive)
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

    info("  [SITEMAP]"); print(" Hledám sitemap.xml...")
    sm_pages: list[str] = []
    try:
        sm_pages = fetch_sitemap_urls(url, max_urls=args.max_pages)
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
                                   delay=args.delay, seed_urls=sm_pages)
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
                pages        = crawl_site(url, max_pages=args.max_pages, delay=args.delay)
                source_label = f"crawler ({len(pages)} URL)"
            except Exception as e:
                err(f"  [✗] Crawler selhal: {e}"); print()
                pages = []

    print()

    if not pages:
        err("  [✗] Žádné stránky k validaci. Zkontroluj URL a připojení."); print()
        stop_server()
        if not args.no_interactive:
            input("Stiskni ENTER pro ukončení...")
        return

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
        gray("  robots.txt check přeskočen (interní/dev doména)"); print()
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

    if user_found:
        warn("  [!] Nalezeny uživatelské stránky: ")
        print(", ".join(p["path"] for p in user_found))
    else:
        ok("  [✓]"); print(" Žádná uživatelská sekce nenalezena")
    print()

    # ── Validace stránek ─────────────────────────────────────────────────────
    info("--- VALIDACE + KONTROLA HTML START ---")
    ok(f" ({len(pages)} stránek)"); print("\n")

    results = validate_pages(pages, jar_path=jar)

    # Server už není potřeba — ukončíme ho
    stop_server()

    # ── Statistiky ────────────────────────────────────────────────────────────
    stats    = compute_stats(results)
    score_fn = _score_color_fn(stats.score)

    # ── Report ───────────────────────────────────────────────────────────────
    reports_dir = Path(__file__).resolve().parent / "excel reporty"
    reports_dir.mkdir(exist_ok=True)
    output_path = reports_dir / make_filename(url)
    try:
        write_report(results, output_path, url,
                     score=stats.score,
                     source_label=source_label,
                     domain_info=domain_info)
    except Exception as e:
        err(f"  [✗] Chyba při generování Excel reportu: {e}"); print()
        import traceback; traceback.print_exc()

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
    if stats.w3c_failed:
        write_line("Nepodařilo načíst  :", err, stats.w3c_failed)

    write(f"  Zdroj URL          : {source_label}\n")
    write(f"  Uloženo do         : ")
    blue(str(output_path)); write("\n")

    elapsed = time.time() - _start_time
    mins, secs = divmod(int(elapsed), 60)
    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    write(f"  Celková doba       : ")
    gray(time_str); write("\n")

    info("=" * 62); print("\n")

    if not args.no_interactive:
        input("Stiskni ENTER pro ukončení...")


if __name__ == "__main__":
    main()