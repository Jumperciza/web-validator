"""
JSON výstup auditu + porovnání s minulým během.

JSON se zapisuje VŽDY vedle Excelu (`<host>_validator.json`), `--json CESTA`
jen mění umístění. Díky tomu má další běh na stejnou doménu co načíst a
umí říct „skóre 72 → 85, opraveno 12, nové 3“ bez jakéhokoli přepínače.

Schéma (verze 1):
  {
    "version": 1, "generated_at": ISO čas, "url": start URL, "score": 0–100,
    "source": popis zdroje URL, "summary": {...Stats...},
    "pages": [{"url", "title", "score", "w3c_category", "w3c_error_msg",
               "w3c_errors": [{"message","line"}], "w3c_warnings": [...],
               "issues": [Issue.to_dict()]}],
    "broken_links": [...], "images": [...],      # z links_check.check_resources
    "domain": {"robots_issues", "robots_skipped", "user_pages"},
    "comparison": {...} | null                   # viz compare_runs
  }
"""
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from issues import Issue
from stats import compute_stats, page_score

JSON_VERSION = 1

# Maximum položek v seznamech "opraveno" / "nové" (JSON i Excel)
COMPARE_MAX_ITEMS = 200

_STAMP_RE = re.compile(r"_\d{8}_\d{6}$")


# ── Sestavení a zápis ────────────────────────────────────────────────────────

def _page_key(url: str) -> str:
    """Identita stránky napříč běhy: bez schématu, www. a koncového lomítka."""
    p = urlparse(url)
    return p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")


def build_json(results: list, start_url: str, source_label: str = "",
               domain_info: dict | None = None, link_report: dict | None = None,
               comparison: dict | None = None, generated_at: datetime | None = None,
               sitemap_report: dict | None = None) -> dict:
    """Sestaví serializovatelný dict z výsledků auditu."""
    stats = compute_stats(results)
    domain_info = domain_info or {}
    link_report = link_report or {}

    pages = []
    for r in results:
        issues = [i.to_dict() for i in r.get("structure_issues", [])
                  if isinstance(i, Issue)]
        pages.append({
            "url":           r["url"],
            "title":         r.get("title", ""),
            "score":         round(page_score(r)),
            "w3c_category":  r.get("w3c_category", ""),
            "w3c_error_msg": r.get("w3c_error_msg", "") or "",
            "http_status":   r.get("http_status", 0) or 0,
            "final_url":     r.get("final_url", "") or "",
            "bot_challenge": r.get("bot_challenge", "") or "",
            "w3c_errors":    _messages(r.get("w3c_errors") or []),
            "w3c_warnings":  _messages(r.get("w3c_warnings") or []),
            "issues":        issues,
        })

    return {
        "version":      JSON_VERSION,
        "generated_at": (generated_at or datetime.now()).isoformat(timespec="seconds"),
        "url":          start_url,
        "score":        stats.score,
        "source":       source_label,
        "summary": {
            "total":       stats.total,
            "w3c_ok":      stats.w3c_ok,
            "w3c_warn":    stats.w3c_warn,
            "w3c_err":     stats.w3c_err,
            "w3c_skipped": stats.w3c_skipped,
            "w3c_failed":  stats.w3c_failed,
            "struct_ok":   stats.struct_ok,
            "struct_bad":  stats.struct_bad,
        },
        "pages":        pages,
        "broken_links": link_report.get("broken_links", []),
        "images":       link_report.get("images", []),
        "redirects":    link_report.get("redirects", []),
        "sitemap":      sitemap_report,
        "links_summary": {
            "checked_links":    link_report.get("checked_links", 0),
            "checked_images":   link_report.get("checked_images", 0),
            "skipped_external": link_report.get("skipped_external", 0),
            "collapsed_query":  link_report.get("collapsed_query", 0),
            "skipped_limit":    link_report.get("skipped_limit", 0),
            "skipped_time":     link_report.get("skipped_time", 0),
            "unverified":       link_report.get("unverified", 0),
            "aborted":          link_report.get("aborted", ""),
            "elapsed":          link_report.get("elapsed", 0.0),
            "check_external":   link_report.get("check_external", False),
        },
        "domain": {
            "robots_issues":  domain_info.get("robots_issues", []),
            "robots_skipped": domain_info.get("robots_skipped", False),
            "user_pages":     domain_info.get("user_pages", []),
            "not_found":      domain_info.get("not_found"),
        },
        "comparison":   comparison,
    }


def _messages(items: list) -> list[dict]:
    """W3C zprávy do jednotného tvaru {"message", "line"} (toleruje i stringy)."""
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append({"message": it.get("message", ""), "line": it.get("line")})
        else:
            out.append({"message": str(it), "line": None})
    return out


def write_json(data: dict, path: Path) -> Path:
    """
    Uloží JSON (UTF-8, s diakritikou). Při zamčeném souboru uloží vedle
    s časovou značkou – stejně jako Excel (`report_excel._save_workbook`).
    """
    text = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        path.write_text(text, encoding="utf-8")
        return path
    except PermissionError:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        alt = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
        alt.write_text(text, encoding="utf-8")
        return alt


def build_json_path(xlsx_path: Path, json_arg: str | None = None) -> Path:
    """
    Kam uložit JSON: výchozí = stejná cesta jako Excel s příponou .json
    (včetně případné časové značky z `--keep`). `--json CESTA` = konkrétní
    soubor (.json) nebo adresář, ve kterém se použije výchozí jméno.
    """
    default_name = xlsx_path.with_suffix(".json").name
    if json_arg:
        p = Path(json_arg).expanduser()
        if p.suffix.lower() == ".json" and not p.is_dir():
            return p
        return p / default_name
    return xlsx_path.with_suffix(".json")


# ── Načtení minulého běhu ────────────────────────────────────────────────────

def find_previous_json(json_path: Path) -> Path | None:
    """
    Najde JSON z minulého běhu pro stejný report: buď přesně `json_path`
    (výchozí přepisovaný soubor), nebo nejnovější `<jméno>_YYYYMMDD_HHMMSS.json`
    vedle něj (běhy s `--keep`). Vybírá se nejnovější podle času změny,
    takže funguje i při střídání obou režimů.
    """
    base = _STAMP_RE.sub("", json_path.stem)
    candidates = []
    if json_path.exists():
        candidates.append(json_path)
    plain = json_path.with_name(f"{base}.json")
    if plain != json_path and plain.exists():
        candidates.append(plain)
    for p in json_path.parent.glob(f"{base}_*.json"):
        if _STAMP_RE.search(p.stem) and p != json_path:
            candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_previous(path: Path | None) -> dict | None:
    """Načte minulý JSON; při chybě / cizím formátu vrátí None (audit jede dál)."""
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        return None
    return data


# ── Porovnání ────────────────────────────────────────────────────────────────

def _issue_keys(page: dict) -> set[str]:
    """
    Množina „problémů“ stránky pro porovnání: label každého strukturálního
    Issue + každá W3C chyba (podle textu, bez čísla řádku – posun o řádek
    není oprava ani nový problém).
    """
    keys = {i.get("label", "") for i in page.get("issues", []) if i.get("label")}
    for e in page.get("w3c_errors", []):
        msg = (e.get("message") if isinstance(e, dict) else str(e)) or ""
        if msg:
            keys.add(f"W3C: {msg}")
    return keys


def compare_runs(previous: dict, current: dict) -> dict:
    """
    Porovná dva JSON výsledky (stejná doména). Porovnávají se jen stránky
    přítomné v obou bězích – stránka, která z auditu zmizela, by jinak
    „opravila“ všechny své problémy.

    Vrací:
      {"previous_date", "previous_score", "score", "delta",
       "fixed": [{"url","label"}], "new": [...], "fixed_count", "new_count",
       "pages_common", "pages_added", "pages_removed"}
    """
    prev_pages = {_page_key(p["url"]): p for p in previous.get("pages", []) if p.get("url")}
    cur_pages  = {_page_key(p["url"]): p for p in current.get("pages", [])  if p.get("url")}
    common = [k for k in cur_pages if k in prev_pages]

    fixed: list[dict] = []
    new:   list[dict] = []
    for k in common:
        before = _issue_keys(prev_pages[k])
        after  = _issue_keys(cur_pages[k])
        url = cur_pages[k]["url"]
        for label in sorted(before - after):
            fixed.append({"url": url, "label": label})
        for label in sorted(after - before):
            new.append({"url": url, "label": label})

    prev_score = int(previous.get("score", 0) or 0)
    cur_score  = int(current.get("score", 0) or 0)
    return {
        "previous_date":  previous.get("generated_at", ""),
        "previous_score": prev_score,
        "score":          cur_score,
        "delta":          cur_score - prev_score,
        "fixed":          fixed[:COMPARE_MAX_ITEMS],
        "new":            new[:COMPARE_MAX_ITEMS],
        "fixed_count":    len(fixed),
        "new_count":      len(new),
        "pages_common":   len(common),
        "pages_added":    sum(1 for k in cur_pages if k not in prev_pages),
        "pages_removed":  sum(1 for k in prev_pages if k not in cur_pages),
    }


def format_previous_date(iso: str) -> str:
    """'2026-09-15T20:55:10' → '15.09.2026 20:55' (pro terminál a Excel)."""
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return iso or "?"
