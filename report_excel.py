"""Excel report – jeden list s přehledem, W3C chybami, strukturou,
robots.txt a uživatelskou sekcí."""
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# ── Paleta ───────────────────────────────────────────────────────────────────
G_BG="C6EFCE"; G_FT="1E6823"
R_BG="FFC7CE"; R_FT="9C0006"
O_BG="FFEB9C"; O_FT="7D5A00"
B_BG="DDEEFF"; B_FT="1F4E79"
GR_BG="F5F5F5"; GR_FT="555555"
HDR="1F3864"; SUB="2E75B6"; SUB2="1A5276"

SCORE_COLORS = {
    "good": ("C6EFCE", "1E6823"),
    "warn": ("FFEB9C", "7D5A00"),
    "bad":  ("FFC7CE", "9C0006"),
}


# ── Stylovací helpers ─────────────────────────────────────────────────────────
def _hf(sz=10, color="FFFFFF"):
    return Font(name="Arial", bold=True, color=color, size=sz)

def _bf(bold=False, color="222222", sz=10):
    return Font(name="Arial", bold=bold, color=color, size=sz)

def _fill(c):
    return PatternFill("solid", fgColor=c)

def _al(h="left"):
    return Alignment(horizontal=h, vertical="center", wrap_text=True)

def _brd():
    s = Side(style="thin", color="CCCCCC")
    return Border(left=s, right=s, top=s, bottom=s)

def _dc(ws, r, c, v, bold=False, bg=None, ft=None, align="left", sz=10, link=None):
    cell = ws.cell(row=r, column=c, value=v)
    cell.font = _bf(bold=bold, color=ft or "222222", sz=sz)
    cell.alignment = _al(align)
    cell.border = _brd()
    if bg:   cell.fill = _fill(bg)
    if link:
        cell.hyperlink = link
        cell.font = Font(name="Arial", color="1155CC", underline="single", sz=sz)
    return cell

def _badge(ws, r, c, txt, bg, ft):
    cell = ws.cell(row=r, column=c, value=txt)
    cell.font = _bf(bold=True, color=ft, sz=10)
    cell.fill = _fill(bg); cell.alignment = _al("center"); cell.border = _brd()

def _title_row(ws, row, text, bg, end_col="G", sz=11):
    ws.merge_cells(f"A{row}:{end_col}{row}")
    c = ws[f"A{row}"]
    c.value = text
    c.font = _hf(sz); c.fill = _fill(bg); c.alignment = _al("center")
    ws.row_dimensions[row].height = 24

def _hdr_row(ws, cols_def, row, bg=None):
    for i, (title, _) in enumerate(cols_def, 1):
        c = ws.cell(row=row, column=i, value=title)
        c.font = _hf(10); c.fill = _fill(bg or HDR)
        c.alignment = _al("center"); c.border = _brd()
    ws.row_dimensions[row].height = 22

def _spacer(ws, row, height=10):
    ws.row_dimensions[row].height = height
    return row + 1

def _w3c_link(url: str) -> str:
    return "https://validator.w3.org/nu/?doc=" + quote(url, safe="")

def _score_palette(score: int) -> tuple[str, str]:
    if score >= 80: return SCORE_COLORS["good"]
    if score >= 60: return SCORE_COLORS["warn"]
    return SCORE_COLORS["bad"]


# ── Hlavní funkce ─────────────────────────────────────────────────────────────

def write_report(results: list, output_path: Path, start_url: str,
                 score: int = -1, source_label: str = "",
                 domain_info: dict | None = None) -> None:
    """
    Generuje Excel report.

    results      … seznam výsledků per stránka (každý má 'image_audit' klíč)
    score        … Web Quality Score 0–100 (nebo -1 = nepočítáno)
    source_label … 'sitemap.xml (42 URL)' nebo 'crawler (38 URL)'
    domain_info  … {robots_issues, robots_skipped, user_pages}
    """
    if domain_info is None:
        domain_info = {}

    wb = Workbook()
    wb.remove(wb.active)

    # ── Statistiky ────────────────────────────────────────────────────────────
    w3c_ok   = len([r for r in results if r["w3c_category"] == "ok"])
    w3c_warn = len([r for r in results if r["w3c_category"] in ("warning","warning_error")])
    w3c_err  = len([r for r in results if r["w3c_category"] in ("error","warning_error")])
    s_ok     = len([r for r in results if not r["structure_issues"]])
    s_bad    = len([r for r in results if r["structure_issues"]])

    robots_issues  = domain_info.get("robots_issues",  [])
    robots_skipped = domain_info.get("robots_skipped", False)
    user_pages     = domain_info.get("user_pages",     [])
    user_found     = [p for p in user_pages if p.get("exists")]

    # ════════════════════════════════════════════════════════════════════════
    # LIST 1 – HLAVNÍ REPORT
    # ════════════════════════════════════════════════════════════════════════
    ws = wb.create_sheet("📋 Report")
    ws.sheet_view.showGridLines = False

    for col, w in zip("ABCDEFG", [80.69, 12.51, 8.76, 8.0, 30, 14, 40]):
        ws.column_dimensions[col].width = w

    row = 1

    # ── Titulek ──────────────────────────────────────────────────────────────
    ws.merge_cells("A1:G1")
    t = ws["A1"]
    t.value = "WEB VALIDATOR – REPORT"
    t.font  = Font(name="Arial", bold=True, size=16, color="FFFFFF")
    t.fill  = _fill(HDR); t.alignment = _al("center")
    ws.row_dimensions[1].height = 38

    ws.merge_cells("A2:G2")
    m = ws["A2"]
    src_info = f"  |  Zdroj: {source_label}" if source_label else ""
    m.value  = (f"Web: {start_url}     |     Datum: "
                f"{datetime.now().strftime('%d.%m.%Y  %H:%M')}{src_info}")
    m.font   = Font(name="Arial", size=10, color="555555"); m.alignment = _al("center")
    ws.row_dimensions[2].height = 20
    ws.row_dimensions[3].height = 10
    row = 4

    # ── SOUHRN ───────────────────────────────────────────────────────────────
    _title_row(ws, row, "SOUHRN", SUB); row += 1

    # Web Quality Score
    if score >= 0:
        sc_bg, sc_ft = _score_palette(score)
        sc_label = "Výborný" if score >= 80 else "Průměrný" if score >= 60 else "Špatný"
        ws.merge_cells(f"A{row}:E{row}")
        lc = ws.cell(row=row, column=1, value="Web Quality Score")
        lc.font = Font(name="Arial", bold=True, size=13)
        lc.alignment = _al(); lc.border = _brd()
        ws.merge_cells(f"F{row}:G{row}")
        vc = ws.cell(row=row, column=6, value=f"{score}/100  –  {sc_label}")
        vc.font = Font(name="Arial", bold=True, size=14, color=sc_ft)
        vc.fill = _fill(sc_bg); vc.alignment = _al("center"); vc.border = _brd()
        ws.row_dimensions[row].height = 32; row += 1

    cards = [
        ("Zkontrolováno stránek", len(results), B_BG, B_FT),
        ("W3C – Bez problémů",    w3c_ok,       G_BG, G_FT),
        ("W3C – Varování",        w3c_warn,     O_BG if w3c_warn else G_BG,
                                                O_FT if w3c_warn else G_FT),
        ("W3C – Chyby",           w3c_err,      R_BG if w3c_err  else G_BG,
                                                R_FT if w3c_err  else G_FT),
        ("Struktura – OK",        s_ok,         G_BG, G_FT),
        ("Struktura – Problémy",  s_bad,        O_BG if s_bad else G_BG,
                                                O_FT if s_bad else G_FT),
    ]

    for label, val, bg, ft in cards:
        ws.merge_cells(f"A{row}:E{row}")
        lc = ws.cell(row=row, column=1, value=label)
        lc.font = _bf(bold=True, sz=11); lc.alignment = _al(); lc.border = _brd()
        ws.merge_cells(f"F{row}:G{row}")
        vc = ws.cell(row=row, column=6, value=val)
        vc.font = Font(name="Arial", bold=True, size=13, color=ft)
        vc.fill = _fill(bg); vc.alignment = _al("center"); vc.border = _brd()
        ws.row_dimensions[row].height = 22; row += 1

    # ── META HOMEPAGE ─────────────────────────────────────────────────────────
    row = _spacer(ws, row)
    _title_row(ws, row, "META – HOMEPAGE", SUB); row += 1

    homepage_meta = results[0]["homepage_meta"] if results else []
    if not homepage_meta:
        ws.merge_cells(f"A{row}:G{row}")
        ws.cell(row=row, column=1, value="Žádná data").font = _bf(color="888888")
        ws.row_dimensions[row].height = 18; row += 1
    else:
        for line in homepage_meta:
            line = line.strip()
            if not line: continue
            if "v pořádku" in line:
                bg2, ft2, badge = G_BG, G_FT, "OK"
            elif any(k in line for k in ["příliš", "Chybí"]):
                bg2, ft2, badge = R_BG, R_FT, "PROBLÉM"
            else:
                bg2, ft2, badge = GR_BG, GR_FT, "INFO"
            if ":" in line and not line.startswith(" "):
                key, val2 = line.split(":", 1)[0].strip(), line.split(":", 1)[1].strip()
            else:
                key, val2 = line, ""
            ws.merge_cells(f"A{row}:B{row}"); _dc(ws, row, 1, key, bold=True)
            ws.merge_cells(f"C{row}:E{row}"); _dc(ws, row, 3, val2)
            ws.merge_cells(f"F{row}:G{row}"); _badge(ws, row, 6, badge, bg2, ft2)
            ws.row_dimensions[row].height = 20; row += 1

    # ── W3C VALIDACE ──────────────────────────────────────────────────────────
    row = _spacer(ws, row)
    _title_row(ws, row, "W3C VALIDACE – STRÁNKY S PROBLÉMY", SUB); row += 1

    w3c_issues = [r for r in results if r["w3c_category"] not in ("ok","validator_error")]
    if not w3c_issues:
        ws.merge_cells(f"A{row}:G{row}")
        ws.cell(row=row, column=1, value="✓ Žádné W3C problémy nalezeny")
        ws.cell(row=row, column=1).font = _bf(color=G_FT, bold=True)
        ws.row_dimensions[row].height = 20; row += 1
    else:
        _hdr_row(ws, [("W3C Validator URL", 80), ("Status", 13),
                      ("Varování", 9), ("Chyby", 8)], row=row, bg=SUB2)
        row += 1
        for r in w3c_issues:
            cat = r["w3c_category"]
            w_txt = "VAROVÁNÍ" if cat == "warning" else "CHYBA" if cat == "error" else "VAR+CHYBA"
            w_bg  = O_BG if cat == "warning" else R_BG
            w_ft  = O_FT if cat == "warning" else R_FT
            vurl  = _w3c_link(r["url"])
            _dc(ws, row, 1, vurl, link=vurl)
            _badge(ws, row, 2, w_txt, w_bg, w_ft)
            _dc(ws, row, 3, len(r["w3c_warnings"]) or "",
                bg=O_BG if r["w3c_warnings"] else None, align="center")
            _dc(ws, row, 4, len(r["w3c_errors"]) or "",
                bg=R_BG if r["w3c_errors"]   else None, align="center")
            ws.row_dimensions[row].height = 18; row += 1

    # ── HTML STRUKTURA ────────────────────────────────────────────────────────
    row = _spacer(ws, row)
    _title_row(ws, row, "HTML STRUKTURA – SOUHRN PROBLÉMŮ", SUB); row += 1

    from collections import defaultdict
    import re as _re
    grouped: dict[str, list[str]] = defaultdict(list)
    for r in results:
        for issue in r["structure_issues"]:
            s = str(issue)
            if "Prázdný tag" in s:
                m   = _re.search(r"<([^>]+)>", s)
                key = f"Prázdné tagy <{m.group(1)}>" if m else "Prázdné tagy"
            elif "Chybí <h1>" in s:           key = "Chybí <h1> tag"
            elif "Více <h1>" in s:             key = "Duplicitní <h1> tag"
            elif "Přeskočení" in s:            key = "Přeskočení úrovně nadpisů"
            elif "Duplicitní ID" in s:         key = "Duplicitní ID"
            elif "description" in s.lower():   key = "Meta description"
            elif "alt" in s.lower() or "Obrázky" in s: key = "Chybějící alt texty"
            elif "HTTP" in s and "noopener" not in s:   key = "HTTP odkazy (nezabezpečené)"
            elif "noopener" in s or "target" in s or "externí" in s.lower():
                                               key = "Externí odkazy bez target/noopener"
            elif "testovací" in s.lower() or "zástupný" in s.lower() or "lorem" in s.lower():
                                               key = "Testovací / zástupný obsah"
            elif "lang" in s.lower():          key = "Chybějící lang atribut"
            elif "viewport" in s.lower():      key = "Chybějící meta viewport"
            elif s.startswith("    "):         continue
            else:                              key = "Ostatní problémy"
            if r["url"] not in grouped[key]:
                grouped[key].append(r["url"])

    if not grouped:
        ws.merge_cells(f"A{row}:G{row}")
        ws.cell(row=row, column=1, value="✓ Žádné strukturální problémy nalezeny")
        ws.cell(row=row, column=1).font = _bf(color=G_FT, bold=True)
        ws.row_dimensions[row].height = 20; row += 1
    else:
        _hdr_row(ws, [("Typ problému", 35), ("Počet URL", 10),
                      ("Postižené stránky", 110)], row=row, bg=SUB2)
        ws.merge_cells(f"C{row}:G{row}")
        for col in range(4, 8):
            ws.cell(row=row, column=col).fill   = _fill(SUB2)
            ws.cell(row=row, column=col).border = _brd()
        row += 1
        for issue_type, urls in sorted(grouped.items(), key=lambda x: -len(x[1])):
            n   = len(urls)
            bg3 = O_BG if n <= 3 else R_BG
            ft3 = O_FT if n <= 3 else R_FT
            _dc(ws, row, 1, issue_type, bold=True)
            _dc(ws, row, 2, n, bg=bg3, ft=ft3, align="center", bold=True)
            ws.merge_cells(f"C{row}:G{row}")
            _dc(ws, row, 3, "\n".join(urls))
            ws.row_dimensions[row].height = max(18, 15 * min(n, 8)); row += 1

    # ════════════════════════════════════════════════════════════════════════
    # SEKCE: NEDOSTUPNÉ STRÁNKY (HTTP chyby, timeouty)
    # ════════════════════════════════════════════════════════════════════════
    failed_pages = [r for r in results if r["w3c_category"] == "validator_error"]
    if failed_pages:
        row = _spacer(ws, row)
        _title_row(ws, row, "NEDOSTUPNÉ STRÁNKY – NEPODAŘILO SE NAČÍST", SUB); row += 1

        # Záhlaví: A-D = URL, E-G = chybová hláška
        for ci, label in enumerate(["URL stránky", "", "", "", "Chybová hláška", "", ""], 1):
            c = ws.cell(row=row, column=ci, value=label)
            c.font = _hf(10); c.fill = _fill(SUB2)
            c.alignment = _al("center"); c.border = _brd()
        ws.merge_cells(f"A{row}:D{row}")
        ws.merge_cells(f"E{row}:G{row}")
        ws.row_dimensions[row].height = 22; row += 1

        for r in failed_pages:
            ws.merge_cells(f"A{row}:D{row}")
            _dc(ws, row, 1, r["url"], bg=R_BG, ft=R_FT)
            ws.merge_cells(f"E{row}:G{row}")
            err_msg = str(r.get("w3c_error_msg") or "")[:200]
            _dc(ws, row, 5, err_msg, bg=R_BG, ft=R_FT)
            ws.row_dimensions[row].height = 20; row += 1

    # ════════════════════════════════════════════════════════════════════════
    # SEKCE: ROBOTS.TXT
    # ════════════════════════════════════════════════════════════════════════
    row = _spacer(ws, row)
    _title_row(ws, row, "ROBOTS.TXT – BLOKOVÁNÍ JS/CSS (GOOGLEBOT)", SUB); row += 1

    if robots_skipped:
        ws.merge_cells(f"A{row}:G{row}")
        ws.cell(row=row, column=1,
                value="ℹ Kontrola přeskočena – interní / dev prostředí")
        ws.cell(row=row, column=1).font = _bf(color=GR_FT)
        ws.cell(row=row, column=1).fill = _fill(GR_BG)
        ws.cell(row=row, column=1).border = _brd()
        ws.row_dimensions[row].height = 20; row += 1
    elif not robots_issues:
        ws.merge_cells(f"A{row}:G{row}")
        ws.cell(row=row, column=1,
                value="✓ JS a CSS soubory nejsou blokovány – Googlebot může renderovat stránku")
        ws.cell(row=row, column=1).font = _bf(color=G_FT, bold=True)
        ws.row_dimensions[row].height = 20; row += 1
    else:
        _hdr_row(ws, [("Zjištěný problém v robots.txt", 110)], row=row, bg=SUB2)
        ws.merge_cells(f"B{row}:G{row}")
        for col in range(2, 8):
            ws.cell(row=row, column=col).fill   = _fill(SUB2)
            ws.cell(row=row, column=col).border = _brd()
        row += 1
        for issue in robots_issues:
            ws.merge_cells(f"A{row}:G{row}")
            _dc(ws, row, 1, issue, bg=R_BG, ft=R_FT)
            ws.row_dimensions[row].height = 20; row += 1

    # ════════════════════════════════════════════════════════════════════════
    # SEKCE: UŽIVATELSKÉ STRÁNKY
    # ════════════════════════════════════════════════════════════════════════
    row = _spacer(ws, row)
    _title_row(ws, row, "UŽIVATELSKÉ STRÁNKY – DETEKCE", SUB); row += 1

    if not user_pages:
        ws.merge_cells(f"A{row}:G{row}")
        ws.cell(row=row, column=1, value="Kontrola neproběhla nebo nedostupná")
        ws.cell(row=row, column=1).font = _bf(color=GR_FT)
        ws.row_dimensions[row].height = 18; row += 1
    else:
        # Záhlaví: A=Cesta, B-D=URL, E-F=HTTP Status, G=Existence
        for ci, label in enumerate(["Cesta", "URL", "", "", "HTTP Status", "", "Existence"], 1):
            c = ws.cell(row=row, column=ci, value=label)
            c.font = _hf(10); c.fill = _fill(SUB2)
            c.alignment = _al("center"); c.border = _brd()
        ws.merge_cells(f"B{row}:D{row}")
        ws.merge_cells(f"E{row}:F{row}")
        ws.row_dimensions[row].height = 22
        row += 1

        for p in user_pages:
            exists = p.get("exists", False)
            sc     = p.get("status_code", 0)

            if exists:
                bg_e, ft_e, badge = O_BG, O_FT, "EXISTUJE ⚠"
            elif sc == 404 or sc == 0:
                bg_e, ft_e, badge = G_BG, G_FT, "Neexistuje"
            else:
                bg_e, ft_e, badge = GR_BG, GR_FT, f"HTTP {sc}"

            _dc(ws, row, 1, p["path"], bold=True)
            ws.merge_cells(f"B{row}:D{row}")
            _dc(ws, row, 2, p["url"])
            ws.merge_cells(f"E{row}:F{row}")
            _dc(ws, row, 5, sc if sc else "–", align="center",
                bg=O_BG if exists else GR_BG)
            ws.merge_cells(f"G{row}:G{row}")
            _badge(ws, row, 7, badge, bg_e, ft_e)

            ws.row_dimensions[row].height = 18; row += 1

    # ── Finální nastavení ─────────────────────────────────────────────────────
    ws.freeze_panes = "A4"
    wb.save(output_path)