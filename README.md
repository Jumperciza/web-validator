<<<<<<< HEAD
# 🔍 Web Validator

Nástroj pro automatizovaný technický audit webu. Zadáš URL, program projde celý web, zkontroluje každou stránku a vygeneruje přehledný Excel report.

---

## 📋 Co program kontroluje

### 1. W3C validace HTML
Každá stránka prochází lokální validací přes `vnu.jar` (offline, žádná data se neodesílají). Výsledky jsou rozděleny do kategorií:
- **OK** – žádné problémy
- **Varování** – doporučení k opravě
- **Chyba** – závažný problém neodpovídající standardu

### 2. Struktura HTML
Na každé stránce se kontroluje 11 věcí:

| Co se kontroluje | Popis |
|---|---|
| `<h1>` existence a unikátnost | Každá stránka by měla mít právě jeden H1 |
| Pořadí nadpisů | Nesmí se přeskakovat úrovně (H1 → H3 bez H2) |
| Prázdné tagy | `<div>`, `<p>`, `<span>` a další bez obsahu |
| Duplicitní ID | Stejné `id` atributy na více prvcích |
| Meta description | Musí existovat a nesmí být prázdná |
| Alt texty u obrázků | Každý `<img>` musí mít `alt` atribut |
| HTTP odkazy | Odhalí nezabezpečené `http://` odkazy místo `https://` |
| Externí odkazy | Musí mít `target="_blank"` a `rel="noopener"` (ochrana před tab-nappingem) |
| Testovací obsah | Detekuje lorem ipsum, asdf, qwerty a další zástupné texty |
| `lang` atribut | `<html lang="cs">` je důležitý pro SEO a čtečky obrazovky |
| Meta viewport | Bez něj se stránka na mobilech zobrazuje špatně |

### 3. Meta údaje homepage
Jen na úvodní stránce se měří délka:
- **Title:** 30–60 znaků
- **Meta description:** 70–160 znaků

### 4. robots.txt – blokování CSS/JS
Zkontroluje jestli `robots.txt` nebrání Googlebotu číst `.js` nebo `.css` soubory (včetně WordPress složek `/wp-content/` a `/wp-includes/`). Takové blokování znemožňuje Googlu správně renderovat stránku.

> ⚠️ Tato kontrola se automaticky přeskočí pro interní a dev prostředí (domény obsahující `poskireal.cz`, `poski.com`, `.cz.dev.`).

### 5. Uživatelská sekce
Testuje jestli existuje `/uzivatel/` — stránky jako přihlášení nebo profil, které crawler běžně nedosáhne.

---

## 📊 Web Quality Score

Na konci každého auditu program vypočítá skóre **0–100**:

```
Score = (stránky bez problémů / celkem stránek) × 100
```

Stránka je považována za **špatnou** pokud má W3C chyby, strukturální problémy, nebo se vůbec nepodařilo načíst. Varování skóre nesnižují.

| Skóre | Hodnocení | Barva |
|---|---|---|
| 80–100 | Výborný | 🟢 Zelená |
| 60–79 | Průměrný | 🟡 Oranžová |
| 0–59 | Špatný | 🔴 Červená |

---

## 🚀 Jak spustit

### Požadavky
- Python 3.10+
- Java 11+ (pro vnu.jar)
- Závislosti: `pip install requests beautifulsoup4 openpyxl`

### Instalace vnu.jar
Program sám nabídne stažení při prvním spuštění. Nebo ručně:
1. Stáhni `vnu.jar` z [github.com/validator/validator/releases](https://github.com/validator/validator/releases)
2. Ulož ho do stejné složky jako `main.py`

### Spuštění

```bash
# Interaktivní mód – program se sám zeptá na URL
python main.py

# S URL jako argumentem
python main.py https://www.example.cz/

# Omezení počtu stránek a zpomalení crawleru
python main.py https://www.example.cz/ --max-pages 100 --delay 2.0

# Non-interactive mód (pro CI/CD nebo scripty – žádné dotazy)
python main.py https://www.example.cz/ --no-interactive

# Přeskočit kontrolu aktualizace vnu.jar
python main.py --no-update-check
```

### Parametry

| Parametr | Výchozí | Popis |
|---|---|---|
| `url` | *(ptá se)* | URL webu k auditu |
| `--max-pages` | `500` | Maximální počet stránek |
| `--delay` | `1.0` | Pauza mezi dávkami crawleru (sekundy) |
| `--no-update-check` | — | Přeskočí kontrolu verze vnu.jar |
| `--no-interactive` | — | Žádné interaktivní dotazy ani závěrečný ENTER |

---

## 📁 Struktura projektu

```
├── main.py             ← Hlavní spouštěcí soubor
├── config.py           ← Sdílené konstanty (User-Agent, timeouty)
├── crawler.py          ← Paralelní crawler webu
├── sitemap.py          ← Načtení URL ze sitemap.xml
├── structure_check.py  ← Kontrola HTML struktury (11 kontrol)
├── validator_w3c.py    ← W3C validace přes vnu.jar
├── robots_check.py     ← Kontrola robots.txt + uživatelská sekce
├── report_excel.py     ← Generování Excel reportu
├── updater.py          ← Automatická aktualizace vnu.jar z GitHubu
├── colors.py           ← Barevný výstup (Windows + Unix)
└── vnu.jar             ← Lokální W3C validátor (stáhni samostatně)
```

---

## 📄 Excel report

Report se ukládá do složky `excel reporty/` vedle `main.py`. Název souboru odpovídá doméně, např. `example_validator.xlsx`.

Report obsahuje tyto sekce:

1. **Souhrn** – Web Quality Score + přehled počtů pro každou kategorii
2. **Meta homepage** – délka title a description s barevným hodnocením
3. **W3C validace** – seznam stránek s problémy jako klikatelné odkazy na online validátor
4. **HTML struktura** – problémy seskupené podle typu, s výpisem postižených URL
5. **Nedostupné stránky** – stránky které se nepodařilo načíst (timeout, 5xx) s chybovou hláškou
6. **robots.txt** – výsledek kontroly blokování JS/CSS
7. **Uživatelská sekce** – HTTP status `/uzivatel/`

---

## ⚙️ Jak funguje crawling

Program nejdřív zkusí načíst `sitemap.xml`:
1. Hledá `Sitemap:` direktivu v `robots.txt`
2. Zkouší `/sitemap.xml`, `/sitemap_index.xml`, `/sitemap-index.xml`
3. Pokud najde sitemap index, rekurzivně rozbalí všechny pod-sitemaps

Pokud sitemap nenajde nebo je prázdná, spustí vlastní crawler:
- Prochází web paralelně (5 threadů)
- Respektuje `robots.txt` (Disallow pravidla i Crawl-delay)
- Filtruje soubory jako obrázky, PDF, JS, CSS, query parametry
- `www.` varianta domény se považuje za stejnou doménu

---

## 🔒 Soukromí a bezpečnost

- W3C validace probíhá **zcela offline** přes lokální vnu.jar
- Program nikam neodesílá obsah stránek ani výsledky
- Crawler se identifikuje jako `WebValidator/1.0` v HTTP hlavičce

---

*Vytvořil Péťa*
=======
# web-validator
>>>>>>> 6184ca9b70475ab9fe5115d776258976cbbc71e3
