"""
Sdílený výpočet statistik a skóre z výsledků validace.

Centralizace: main.py, report_excel.py a potenciální další moduly
můžou používat stejný zdroj pravdy.

Web Quality Score
─────────────────
Váhový systém — ne všechny problémy jsou stejně závažné.

Každá stránka začíná na 100 bodech. Podle typu a počtu nalezených
problémů se odčítají penalizace. Finální skóre webu = průměr skóre
všech stránek.

Kategorie penalizací:
  • KRITICKÉ (−15 až −20)   — SEO/mobile killer, narušuje funkčnost
  • STŘEDNÍ  (−8 až −10)    — horší UX nebo accessibility
  • MENŠÍ    (−0.5 až −5)   — kosmetika, validita HTML

Nenačtené stránky (HTTP chyba) dostanou 0 bodů.
Samotné W3C varování skóre neovlivňují (jen chyby).
"""
from dataclasses import dataclass

from issues import IssueType


# ── Váhy penalizací ──────────────────────────────────────────────────────────

# Penalizace za problémy které jsou "binární" — buď jsou, nebo nejsou
# (počet výskytů u nich nedává smysl nebo je vždy ~1)
_BINARY_PENALTIES: dict[IssueType, float] = {
    # Kritické — SEO / mobile killer
    IssueType.NOINDEX:            25,   # web nebude indexován Googlem = SEO katastrofa
    IssueType.FORBIDDEN_CONTENT:  20,   # lorem ipsum v produkci = katastrofa
    IssueType.MISSING_H1:         15,
    IssueType.MISSING_META_DESC:  15,
    IssueType.EMPTY_META_DESC:    15,
    IssueType.MISSING_VIEWPORT:   15,

    # Střední
    IssueType.MISSING_LANG:       10,
    IssueType.MULTIPLE_H1:         8,
    IssueType.HEADING_SKIP:        5,
}

# Penalizace za problémy které se kumulují s počtem výskytů.
# Formát: (per_výskyt, max_cap)
# Cap brání tomu aby jeden typ problému sám sestřelil skóre do 0.
_COUNTED_PENALTIES: dict[IssueType, tuple[float, float]] = {
    IssueType.STAGING_URL:   (8.0, 20),   # leftover dev/staging URL = SEO problém
    IssueType.DUPLICATE_ID:  (3.0, 15),
    IssueType.HTTP_LINK:     (2.0, 15),
    IssueType.MISSING_ALT:   (1.5, 15),
    IssueType.EMPTY_TAG:     (0.5,  8),
    IssueType.EXTERNAL_LINK: (0.5,  6),
}

# W3C chyby — 2 body za každou, ale max −20 na stránku
_W3C_ERROR_PER_ISSUE: float = 2.0
_W3C_ERROR_MAX:       float = 20.0

# Default pro neznámé typy problémů (IssueType.OTHER a podobně)
_DEFAULT_PENALTY: float = 3.0


# ── Dataclass ────────────────────────────────────────────────────────────────

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
    score:      int = 0  # 0–100 (váhové skóre, viz _page_score)


# ── Interní výpočet skóre ────────────────────────────────────────────────────

def _page_score(result: dict) -> float:
    """
    Spočítá kvalitní skóre jedné stránky (0–100).

    Algoritmus:
      1. Nedostupná stránka (validator_error) → 0 bodů.
      2. Jinak začneme na 100 a odečítáme:
         • W3C chyby (2 body každá, max −20)
         • Binární strukturální problémy (5–20 podle závažnosti)
         • Počítané strukturální problémy (penalizace × počet, s cap)
      3. Výsledek clamp na 0–100.
    """
    if result.get("w3c_category") == "validator_error":
        return 0.0

    score = 100.0

    # ── W3C chyby ────────────────────────────────────────────────────────────
    n_w3c_errors = len(result.get("w3c_errors", []))
    score -= min(n_w3c_errors * _W3C_ERROR_PER_ISSUE, _W3C_ERROR_MAX)

    # ── Strukturální problémy ────────────────────────────────────────────────
    for issue in result.get("structure_issues", []):
        itype = getattr(issue, "type", None)
        if itype is None:
            continue

        if itype in _BINARY_PENALTIES:
            score -= _BINARY_PENALTIES[itype]
        elif itype in _COUNTED_PENALTIES:
            per, cap = _COUNTED_PENALTIES[itype]
            count = getattr(issue, "total_count", 0) or 1
            score -= min(per * count, cap)
        else:
            score -= _DEFAULT_PENALTY

    # Nikdy nejdeme pod 0
    return max(0.0, score)


# ── Veřejné API ──────────────────────────────────────────────────────────────

def compute_stats(results: list) -> Stats:
    """
    Jeden zdroj pravdy pro statistiky — používá main.py i report_excel.py.

    Počty (w3c_ok, w3c_err, struct_ok, …) zůstávají zachované pro přehled
    v souhrnu. Skóre se ale počítá váhově — viz _page_score.
    """
    s = Stats(total=len(results))
    if not results:
        return s

    page_scores: list[float] = []

    for r in results:
        cat = r["w3c_category"]
        has_struct_issues = bool(r.get("structure_issues"))

        # Počty podle kategorie (stejné jako dříve — pro přehled v UI)
        if cat == "ok":                    s.w3c_ok += 1
        elif cat == "warning":             s.w3c_warn += 1
        elif cat == "error":               s.w3c_err += 1
        elif cat == "warning_error":
            s.w3c_warn += 1
            s.w3c_err  += 1
        elif cat == "validator_error":     s.w3c_failed += 1

        if has_struct_issues: s.struct_bad += 1
        else:                 s.struct_ok  += 1

        # Per-page skóre — váhové
        page_scores.append(_page_score(r))

    # Celkové skóre = průměr skóre všech stránek
    avg = sum(page_scores) / len(page_scores) if page_scores else 0.0
    s.score = max(0, min(100, round(avg)))
    return s