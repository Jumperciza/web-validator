"""
Web Validator – hlavní spouštěcí soubor.

Struktura:
  main.py            ← tento soubor (spouštěj tento)
  config.py          ← sdílené konstanty (User-Agent, timeouty)
  crawler.py         ← crawling stránek
  sitemap.py         ← načtení URL ze sitemap.xml
  validator_w3c.py   ← W3C validace přes vnu.jar
  structure_check.py ← kontrola HTML struktury
  robots_check.py    ← kontrola robots.txt (JS/CSS) + uživatelská sekce
  report_excel.py    ← generování Excel reportu
  updater.py         ← kontrola verze vnu.jar z GitHubu
  vnu.jar            ← lokální W3C validátor (stáhni z github.com/validator/validator)
"""
import argparse
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
import re

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from colors           import ok, warn, err, info, bold, gray, blue, pocet_problemu
from config           import USER_AGENT, ACCEPT_LANGUAGE, FETCH_TIMEOUT
from crawler          import crawl_site
from sitemap          import fetch_sitemap_urls
from robots_check     import check_robots_js_css, check_user_pages
import validator_w3c as w3c_mod
from structure_check  import check_structure, check_homepage_meta
from report_excel     import write_report
from validator_w3c    import find_vnu_jar
from updater          import check_and_update, download_vnu_jar

_vnu_jar_path: str = ""


def fetch_html(session: requests.Session, url: str, timeout: int = FETCH_TIMEOUT):
    """
    Stáhne HTML stránky přes sdílenou Session (opakované TCP spojení).
    Vrátí (bytes, text, content_type) nebo (None, None, chyba).
    """
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content, resp.text, resp.headers.get("Content-Type", "text/html; charset=utf-8")
    except Exception as e:
        return None, None, str(e)


# ── Paralelní konfigurace ────────────────────────────────────────────────────
FETCH_WORKERS = 3
LOCAL_WORKERS = 4
FETCH_DELAY   = 0.5
# ─────────────────────────────────────────────────────────────────────────────


def calculate_score(results: list) -> int:
    """
    Web Quality Score (0–100).

    Stránka je "špatná" pokud má:
      - W3C chyby (error, warning_error)       NEBO
      - Nepodařilo se ji načíst (validator_error) NEBO
      - Strukturální problémy v HTML

    Varování skóre nesnižují – jsou doporučení, ne blokátory.
    """
    if not results:
        return 0
    total = len(results)
    bad   = sum(
        1 for r in results
        if r["w3c_category"] in ("error", "warning_error", "validator_error")
        or bool(r["structure_issues"])
    )
    return max(0, min(100, round((total - bad) / total * 100)))


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
        lines.append(("ok",   "[W3C: OK]"))
    elif cat == "warning":
        lines.append(("warn", f"[W3C: VAROVÁNÍ {len(w3c['warnings'])}]"))
    elif cat == "error":
        lines.append(("err",  f"[W3C: CHYBA {len(w3c['errors'])}]"))
    elif cat == "warning_error":
        lines.append(("err",  f"[W3C: VAROVÁNÍ {len(w3c['warnings'])} + CHYBA {len(w3c['errors'])}]"))
    else:
        lines.append(("gray", "[W3C: přeskočeno]"))
        if w3c.get("error_msg"):
            lines.append(("plain", "\n  "))
            lines.append(("warn",  "[!]"))
            lines.append(("plain", f" {w3c['error_msg']}"))

    lines.append(("plain", "  "))
    if structure_issues:
        lines.append(("warn", f"[STRUKTURA: {pocet_problemu(len(structure_issues))}]"))
    else:
        lines.append(("ok",   "[STRUKTURA: OK]"))

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

    # ── Sdílená HTTP Session – keep-alive pro všechny requesty ───────────────
    # Jedna Session → opakované TCP spojení, výrazně rychlejší než nový
    # request.get() pro každou stránku. requests.Session je thread-safe při
    # paralelním čtení (získání různých URL).
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
                sys.stdout.write(f"\r  Staženo: {done}/{total}   ")
                sys.stdout.flush()
            time.sleep(FETCH_DELAY / FETCH_WORKERS)
    print()

    # ── Krok 2: W3C + Struktura + Image Audit ────────────────────────────────
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
                "image_audit": [],
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
                struct_res[0] = [f"Chyba při kontrole struktury: {e}"]

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
            "structure_issues": struct_res[0],
            "homepage_meta":    homepage_meta,
        }

    with ThreadPoolExecutor(max_workers=LOCAL_WORKERS) as ex:
        futures = {ex.submit(_do_validate, (i + 1, url)): i for i, url in enumerate(pages)}
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
    """
    Spustí jednorázové kontroly na úrovni domény paralelně:
      - robots.txt (JS/CSS blokování)
      - Uživatelské stránky (/uzivatel/, /login/, …)
    """
    robots_result = [[], False]
    user_result   = [[]]

    def _do_robots():
        try:
            issues, skipped  = check_robots_js_css(url)
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


# ── URL vstup s placeholderem a validací ─────────────────────────────────────

_URL_PLACEHOLDER = "https://www.example.cz/"
_URL_RE = re.compile(
    r'^https?://'
    r'(?:[a-zA-Z0-9-]+\.)+'   # alespoň jedna tečka (subdoména + doména)
    r'[a-zA-Z]{2,}'            # TLD
    r'(?:[/\S]*)?$',
    re.IGNORECASE,
)


def _read_with_placeholder(prompt: str, placeholder: str) -> str:
    """
    Čte řádek vstupu. Zobrazí šedý placeholder text, který zmizí při
    prvním stisku klávesy. Funguje na Windows (msvcrt) i Unix (termios).
    Pokud stdin není terminál (pipe/redirect), padne zpět na plain input().
    """
    if not sys.stdin.isatty():
        return input(prompt)

    if sys.platform == "win32":
        import msvcrt
        sys.stdout.write(prompt); sys.stdout.flush()
        gray(placeholder)           # Windows: gray() tiskne přímo

        chars: list[str] = []
        ph_active = True

        while True:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                sys.stdout.write("\n"); sys.stdout.flush()
                return "".join(chars)
            elif ch == "\x08":                   # Backspace
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b"); sys.stdout.flush()
            elif ch == "\x03":                   # Ctrl+C
                raise KeyboardInterrupt
            elif ch in ("\x00", "\xe0"):         # Speciální klávesky – přeskočíme
                msvcrt.getwch()
            else:
                if ph_active:
                    line = prompt + placeholder
                    sys.stdout.write("\r" + " " * len(line) + "\r" + prompt)
                    sys.stdout.flush()
                    ph_active = False
                chars.append(ch)
                sys.stdout.write(ch); sys.stdout.flush()
    else:
        import termios, tty, select

        fd  = sys.stdin.fileno()
        old = termios.tcgetattr(fd)

        # Unix: ANSI šedá barva přímo do výstupu
        sys.stdout.write(f"{prompt}\033[90m{placeholder}\033[0m")
        sys.stdout.flush()

        chars: list[str] = []
        ph_active = True

        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)

                if ch in ("\r", "\n"):
                    sys.stdout.write("\r\n"); sys.stdout.flush()
                    return "".join(chars)
                elif ch in ("\x7f", "\x08"):     # Backspace / Delete
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b"); sys.stdout.flush()
                elif ch == "\x03":               # Ctrl+C
                    raise KeyboardInterrupt
                elif ch == "\x1b":               # Escape sekvence (šipky apod.)
                    if select.select([sys.stdin], [], [], 0.05)[0]:
                        sys.stdin.read(1)        # [
                        if select.select([sys.stdin], [], [], 0.05)[0]:
                            sys.stdin.read(1)    # A/B/C/D
                else:
                    if ph_active:
                        line_len = len(prompt) + len(placeholder)
                        sys.stdout.write("\r" + " " * line_len + "\r" + prompt)
                        sys.stdout.flush()
                        ph_active = False
                    chars.append(ch)
                    sys.stdout.write(ch); sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _prompt_url() -> str:
    """
    Interaktivně se ptá na URL dokud nezadá validní adresu.
    Přidá https:// pokud chybí. Validuje že URL vypadá smysluplně.
    """
    while True:
        try:
            raw = _read_with_placeholder("  Zadej web: ", _URL_PLACEHOLDER).strip()
        except (KeyboardInterrupt, EOFError):
            sys.stdout.write("\n"); sys.exit(0)

        if not raw:
            warn("  [!]"); print(" Zadej prosím URL webu.")
            continue

        # Přidej https:// pokud chybí
        if not raw.startswith(("http://", "https://")):
            raw = "https://" + raw

        if not _URL_RE.match(raw):
            err("  [✗]"); print(f" '{raw}' nevypadá jako platná URL.")
            sys.stdout.write("     Zkus např. https://www.example.cz/\n")
            sys.stdout.flush()
            continue

        return raw


def main():
    parser = argparse.ArgumentParser(description="Web Validator – W3C + HTML struktura")
    parser.add_argument("url",               nargs="?", help="URL webu")
    parser.add_argument("--max-pages",       type=int,   default=500)
    parser.add_argument("--delay",           type=float, default=1.0)
    parser.add_argument("--no-update-check", action="store_true",
                        help="Přeskoč kontrolu verze vnu.jar")
    parser.add_argument("--no-interactive", action="store_true",
                        help="Žádné interaktivní dotazy ani ENTER na konci (vhodné pro CI/skript)")
    args = parser.parse_args()

    _start_time = time.time()   # ← Timer spuštěn hned po parsování argumentů

    # ── Banner ───────────────────────────────────────────────────────────────
    print()
    info("=" * 62); print()
    print("  "); bold("Web Validator"); gray("  |  vytvořil Péťa"); print()
    info("=" * 62); print()
    print(f"  {bold('Co kontroluje:')} ")
    print(f"    "); ok("1."); print(" W3C validace HTML (přes lokální vnu.jar)")
    print(f"    "); ok("2."); print(" Struktura HTML:")
    for item in ["existence a duplikáty <h1>", "pořadí nadpisů (žádné přeskočení)",
                 "prázdné tagy", "duplicitní ID", "meta description",
                 "alt texty u obrázků", "HTTP odkazy (místo HTTPS)",
                 "externí odkazy bez target/_blank/noopener",
                 "testovací/zástupný obsah (lorem ipsum, asdf…)",
                 "lang atribut na <html>", "meta viewport"]:
        gray(f"       - {item}"); print()
    print(f"    "); ok("3."); print(" Meta title a description délka (jen homepage)")
    print(f"    "); ok("4."); print(" Kontrola robots.txt – blokování CSS/JS pro Googlebot")
    print(f"    "); ok("5."); print(" Kontrola existence uživatelské sekce (/uzivatel/)")
    info("=" * 62); print()
    print()

    # ── Detekce vnu.jar ──────────────────────────────────────────────────────
    jar = find_vnu_jar()
    if jar:
        ok("  [LOCAL]"); print(f" Lokální validátor nalezen: {jar}")
        if not args.no_update_check:
            jar = check_and_update(jar, non_interactive=args.no_interactive)
        w3c_mod.vnu_jar = jar
        global _vnu_jar_path
        _vnu_jar_path = jar
        ok("  [✓]"); print(f" W3C validátor připraven: {jar}")
    else:
        print()
        warn("  [!] vnu.jar nebyl nalezen!"); print()
        print()

        if args.no_interactive:
            # Non-interactive: automaticky přeskoč W3C validaci
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
                _vnu_jar_path   = jar
                ok("  [✓]"); print(f" W3C validátor připraven: {jar}")
            else:
                warn("  W3C validace bude přeskočena."); print()
        else:
            gray("  Bez vnu.jar bude W3C validace přeskočena."); print()
            gray("  (Stáhni vnu.jar na github.com/validator/validator/releases"); print()
            gray("   a Javu na adoptium.net, pak restartuj program.)"); print()
    print()

    # ── URL ──────────────────────────────────────────────────────────────────
    if args.url:
        raw = args.url.strip()
        if not raw.startswith(("http://", "https://")):
            raw = "https://" + raw
        if not _URL_RE.match(raw):
            err(f"  [✗] '{raw}' nevypadá jako platná URL."); print()
            sys.exit(2)
        url = raw
    elif args.no_interactive:
        err("  [✗] Non-interactive mód vyžaduje URL jako argument."); print()
        sys.exit(2)
    else:
        url = _prompt_url()

    # ── Sitemap → Crawler fallback ────────────────────────────────────────────
    pages        : list[str] = []
    source_label : str       = ""

    info("  [SITEMAP]"); print(" Hledám sitemap.xml...")
    try:
        sm_pages = fetch_sitemap_urls(url, max_urls=args.max_pages)
        if sm_pages:
            ok("  [SITEMAP]")
            print(f" Nalezeno {len(sm_pages)} URL – crawler přeskočen.")
            pages        = sm_pages
            source_label = f"sitemap.xml ({len(sm_pages)} URL)"
        else:
            gray("  Sitemap nenalezena nebo prázdná."); print()
    except Exception as e:
        warn(f"  [!] Chyba při čtení sitemap: {e}"); print()

    if not pages:
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
        if not args.no_interactive:
            input("Stiskni ENTER pro ukončení...")
        return

    # ── Doménové kontroly ─────────────────────────────────────────────────────
    info("  [DOMAIN]"); print(" Kontroluji robots.txt a uživatelské stránky...")
    try:
        domain_info = run_domain_checks(url)
    except Exception as e:
        err(f"  [!] Chyba doménových kontrol: {e}"); print()
        domain_info = {"robots_issues": [], "robots_skipped": False, "user_pages": []}

    robots_issues  = domain_info.get("robots_issues",  [])
    robots_skipped = domain_info.get("robots_skipped", False)
    user_pages     = domain_info.get("user_pages",     [])
    user_found     = [p for p in user_pages if p.get("exists")]

    if robots_skipped:
        gray("  robots.txt check přeskočen (interní/dev doména)"); print()
    elif robots_issues:
        for issue in robots_issues:
            warn("  [!] "); print(issue)
    else:
        ok("  [✓]"); print(" robots.txt neblokuje JS/CSS")

    if user_found:
        warn("  [!] Nalezeny uživatelské stránky: ")
        print(", ".join(p["path"] for p in user_found))
    else:
        ok("  [✓]"); print(" Žádné uživatelské stránky nenalezeny")
    print()

    # ── Validace stránek ─────────────────────────────────────────────────────
    info("--- VALIDACE + KONTROLA HTML START ---")
    ok(f" ({len(pages)} stránek)"); print("\n")

    results = validate_pages(pages, jar_path=_vnu_jar_path)

    # ── Skóre ────────────────────────────────────────────────────────────────
    score    = calculate_score(results)
    score_fn = _score_color_fn(score)

    # ── Report ───────────────────────────────────────────────────────────────
    reports_dir = Path(__file__).resolve().parent / "excel reporty"
    reports_dir.mkdir(exist_ok=True)
    output_path = reports_dir / make_filename(url)
    try:
        write_report(results, output_path, url,
                     score=score,
                     source_label=source_label,
                     domain_info=domain_info)
    except Exception as e:
        err(f"  [✗] Chyba při generování Excel reportu: {e}"); print()
        import traceback; traceback.print_exc()

    # ── Souhrn ───────────────────────────────────────────────────────────────
    w3c_ok   = len([r for r in results if r["w3c_category"] == "ok"])
    w3c_warn = len([r for r in results if r["w3c_category"] in ("warning", "warning_error")])
    w3c_err  = len([r for r in results if r["w3c_category"] in ("error", "warning_error")])
    s_ok     = len([r for r in results if not r["structure_issues"]])
    s_bad    = len([r for r in results if r["structure_issues"]])

    def _line(label, color_fn, value):
        sys.stdout.write(f"  {label} "); sys.stdout.flush()
        color_fn(str(value)); sys.stdout.write("\n"); sys.stdout.flush()

    print()
    info("=" * 62); print()
    print("  "); ok("HOTOVO!"); print()

    sys.stdout.write("  Web Quality Score   : "); sys.stdout.flush()
    score_fn(f"{score}/100"); sys.stdout.write("\n"); sys.stdout.flush()

    info("-" * 62); print()
    _line("W3C – Bez problémů :", ok,                       w3c_ok)
    _line("W3C – Varování     :", warn if w3c_warn else ok, w3c_warn)
    _line("W3C – Chyby        :", err  if w3c_err  else ok, w3c_err)
    _line("Struktura – OK     :", ok,                       s_ok)
    _line("Struktura – Chyby  :", warn if s_bad    else ok, s_bad)

    sys.stdout.write(f"  Zdroj URL          : {source_label}\n"); sys.stdout.flush()
    sys.stdout.write(f"  Uloženo do         : "); sys.stdout.flush()
    blue(str(output_path)); sys.stdout.write("\n"); sys.stdout.flush()

    # Timer
    elapsed = time.time() - _start_time
    mins, secs = divmod(int(elapsed), 60)
    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    sys.stdout.write(f"  Celková doba       : "); sys.stdout.flush()
    gray(time_str); sys.stdout.write("\n"); sys.stdout.flush()

    info("=" * 62); print("\n")

    if not args.no_interactive:
        input("Stiskni ENTER pro ukončení...")


if __name__ == "__main__":
    main()