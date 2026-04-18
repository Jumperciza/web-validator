"""Kontrola a aktualizace vnu.jar z GitHub releases."""
import re
import sys
import time
from pathlib import Path

import requests

from colors import ok, warn, err, info, gray, bold
from validator_w3c import get_local_version

GITHUB_API = "https://api.github.com/repos/validator/validator/releases/latest"
HEADERS    = {
    "User-Agent": "Mozilla/5.0 (compatible; W3CValidator-Updater/1.0)",
    "Accept":     "application/vnd.github+json",
}


def _get_github_release() -> dict | None:
    try:
        resp = requests.get(GITHUB_API, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _extract_version(s: str) -> tuple:
    """
    Vrátí tuple čísel z verze pro porovnání, např. '26.4.11' → (26, 4, 11).
    Pokud verze není číslo (např. 'latest'), vrátí prázdný tuple.
    """
    parts = re.findall(r"\d+", s or "")
    return tuple(int(p) for p in parts) if parts else ()


def check_and_update(jar_path: str, non_interactive: bool = False) -> str:
    gray("  Kontroluji verzi vnu.jar..."); print()
    time.sleep(0.3)

    release = _get_github_release()
    if not release:
        warn("  [!]"); print(" Nelze ověřit verzi (GitHub nedostupný).")
        return jar_path

    raw_tag  = (release.get("tag_name") or "").strip().lstrip("v")
    local_v  = get_local_version(jar_path).strip()

    # Ošetření speciálních stavů
    if local_v == "java_missing":
        err("  [!]"); print(" Java není nainstalovaná.")
        return jar_path
    if local_v == "java_too_old":
        err("  [!]"); print(" Java je příliš stará – potřebuješ Javu 11+. Stáhni na adoptium.net")
        return jar_path

    # Pokud GitHub vrátí "latest" nebo nečíslo – porovnáme přes published_at datum
    gh_ver   = _extract_version(raw_tag)
    local_ver = _extract_version(local_v)

    if not gh_ver:
        # GitHub tag není číslo (např. "latest") – zkus published_at
        published = release.get("published_at", "")  # formát: "2026-04-11T..."
        date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", published)
        if date_match:
            y, m, d = date_match.groups()
            # vnu tag formát je YY.M.D nebo YYYY.M.D
            gh_ver = (int(y) % 100, int(m), int(d))
            gray(f"  (GitHub tag '{raw_tag}' → datum vydání: {y}-{m}-{d})"); print()
        else:
            warn("  [!]"); print(f" Nelze porovnat verze (GitHub tag: '{raw_tag}', lokální: '{local_v}').")
            return jar_path

    if not local_ver:
        warn("  [!]"); print(f" Nelze zjistit lokální verzi ('{local_v}').")
        return jar_path

    if local_ver >= gh_ver:
        ok(f"  [✓]"); print(f" vnu.jar je aktuální (verze {local_v}).")
        return jar_path

    # Je dostupná novější verze
    print()
    warn("  [↑]")
    gh_label = ".".join(str(x) for x in gh_ver)
    print(f" Dostupná novější verze: {gh_label}  (máš: {local_v})")
    print()

    vnu_asset = next(
        (a for a in release.get("assets", []) if a["name"] == "vnu.jar"), None
    )
    if not vnu_asset:
        warn("  [!]"); print(" vnu.jar nebyl nalezen v GitHub release assets.")
        return jar_path

    # Non-interactive mode: automatická aktualizace bez dotazu
    if non_interactive:
        gray("  (non-interactive mód – aktualizace přeskočena)"); print()
        return jar_path

    try:
        answer = input("  Chceš stáhnout aktualizaci? [a/N]: ").strip().lower()
    except EOFError:
        answer = "n"

    if answer not in ("a", "ano", "y", "yes"):
        gray("  Aktualizace přeskočena."); print()
        return jar_path

    download_url = vnu_asset["browser_download_url"]
    dest = Path(jar_path)
    tmp  = dest.with_suffix(".tmp")

    try:
        info("  Stahuji vnu.jar..."); print()
        resp = requests.get(download_url, headers=HEADERS, stream=True, timeout=120)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        done  = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                done += len(chunk)
                if total:
                    sys.stdout.write(f"\r  Staženo: {int(done/total*100)}%   ")
                    sys.stdout.flush()
        print()

        dest.unlink(missing_ok=True)
        tmp.rename(dest)
        ok("  [✓]"); print(f" Aktualizace úspěšná!")
        return str(dest)

    except Exception as e:
        err("  [✗]"); print(f" Aktualizace selhala: {e}")
        try: tmp.unlink(missing_ok=True)
        except Exception: pass
        return jar_path


def download_vnu_jar(dest_dir: str = "") -> str:
    """
    Stáhne vnu.jar z nejnovějšího GitHub release.
    Vrátí cestu k staženému souboru nebo prázdný řetězec při selhání.
    """
    dest_path = Path(dest_dir) / "vnu.jar" if dest_dir else Path(__file__).resolve().parent / "vnu.jar"

    print()
    info("  Stahuji informace o nejnovější verzi..."); print()
    time.sleep(0.3)

    release = _get_github_release()
    if not release:
        err("  [✗]"); print(" Nelze se připojit ke GitHubu.")
        return ""

    vnu_asset = next(
        (a for a in release.get("assets", []) if a["name"] == "vnu.jar"), None
    )
    if not vnu_asset:
        err("  [✗]"); print(" vnu.jar nebyl nalezen v GitHub release assets.")
        return ""

    gh_tag   = _extract_version(release.get("tag_name", ""))
    gh_label = ".".join(str(x) for x in gh_tag) if gh_tag else release.get("tag_name", "?")
    download_url = vnu_asset["browser_download_url"]
    tmp = dest_path.with_suffix(".tmp")

    try:
        info(f"  Stahuji vnu.jar (verze {gh_label})..."); print()
        resp = requests.get(download_url, headers=HEADERS, stream=True, timeout=120)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        done  = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                done += len(chunk)
                if total:
                    sys.stdout.write(f"\r  Staženo: {int(done/total*100)}%   ")

                    sys.stdout.flush()
        print()

        tmp.rename(dest_path)
        ok("  [✓]"); print(f" vnu.jar úspěšně stažen → {dest_path}")
        return str(dest_path)

    except Exception as e:
        err("  [✗]"); print(f" Stahování selhalo: {e}")
        try: tmp.unlink(missing_ok=True)
        except Exception: pass
        return ""