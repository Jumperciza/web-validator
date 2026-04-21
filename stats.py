"""
Sdílený výpočet statistik a skóre z výsledků validace.

Centralizace: main.py, report_excel.py a potenciální další moduly
můžou používat stejný zdroj pravdy.
"""
from dataclasses import dataclass


@dataclass
class Stats:
    """Statistiky z jedné validace."""
    total:     int = 0   # zkontrolováno stránek
    w3c_ok:    int = 0
    w3c_warn:  int = 0
    w3c_err:   int = 0
    w3c_failed: int = 0  # nepodařilo se načíst (validator_error)
    struct_ok:  int = 0
    struct_bad: int = 0
    score:      int = 0  # 0–100


def compute_stats(results: list) -> Stats:
    """
    Jeden zdroj pravdy pro statistiky — používá main.py i report_excel.py.

    Stránka je "špatná" pokud má:
      - W3C chyby (error, warning_error)       NEBO
      - Nepodařilo se ji načíst (validator_error) NEBO
      - Strukturální problémy v HTML
    Varování skóre nesnižují.
    """
    s = Stats(total=len(results))
    if not results:
        return s

    for r in results:
        cat = r["w3c_category"]
        has_struct_issues = bool(r.get("structure_issues"))

        if cat == "ok":                       s.w3c_ok += 1
        elif cat == "warning":                s.w3c_warn += 1
        elif cat == "error":                  s.w3c_err += 1
        elif cat == "warning_error":
            s.w3c_warn += 1
            s.w3c_err  += 1
        elif cat == "validator_error":        s.w3c_failed += 1

        if has_struct_issues: s.struct_bad += 1
        else:                 s.struct_ok  += 1

    bad = sum(
        1 for r in results
        if r["w3c_category"] in ("error", "warning_error", "validator_error")
        or bool(r.get("structure_issues"))
    )
    s.score = max(0, min(100, round((s.total - bad) / s.total * 100)))
    return s
