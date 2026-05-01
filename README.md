# 🔍 Web Validator

Nástroj pro automatizovaný technický audit webu. Zadáš URL, program projde celý web a vygeneruje přehledný Excel report.

---

## 📋 Co program kontroluje

### 1. W3C validace HTML
Každá stránka prochází lokální validací přes `vnu.jar` (offline, žádná data se neodesílají). Výsledky jsou rozděleny na **OK**, **Varování** a **Chyby**.

### 2. Struktura HTML
Na každé stránce se kontroluje 12 věcí:

| Co se kontroluje | Popis |
|---|---|
| `<h1>` existence a unikátnost | Každá stránka by měla mít právě jeden H1 |
| Pořadí nadpisů | Nesmí se přeskakovat úrovně (H1 → H3 bez H2) |
| Prázdné tagy | `<div>`, `<p>`, `<span>` a další bez obsahu |
| Duplicitní ID | Stejné `id` atributy na více prvcích |
| Meta description | Musí existovat a nesmí být prázdná |
| Alt texty u obrázků | Každý `<img>` musí mít `alt` atribut |
| HTTP odkazy | Odhalí nezabezpečené `http://` odkazy |
| Externí odkazy | Musí mít `target="_blank"` a `rel="noopener"` |
| Testovací obsah | Detekuje lorem ipsum, asdf, qwerty a další zástupné texty |
| `lang` atribut | `<html lang="cs">` je důležitý pro SEO a čtečky obrazovky |
| Meta viewport | Bez něj se stránka na mobilech zobrazuje špatně |
| `noindex` meta tag | Detekuje `<meta name="robots" content="noindex">` na produkci |

> ⚠️ **Noindex check** je přeskočen pro dev/staging domény (`*.cz.dev.poski.com`, `*.poskireal.cz`), kde je `noindex` záměrný.

### 3. Meta údaje homepage
- **Title:** 30–60 znaků
- **Meta description:** 70–160 znaků

### 4. robots.txt – kontrola indexace
- **Kritická kontrola:** detekuje `Disallow: /` pro Googlebot nebo `*` — to znamená že je celý web zablokovaný pro vyhledávače. Klasický staging artefakt který se zapomene změnit při nasazení na produkci.
- **JS/CSS blokování:** zkontroluje jestli `robots.txt` nebrání Googlebotu číst `.js` nebo `.css` soubory (bez nich Google nedokáže korektně renderovat stránku).
> ⚠️ Přeskočeno pro interní/dev prostředí (`poskireal.cz`, `poski.com`, `.cz.dev.`).

### 5. Uživatelská sekce
Testuje jestli existuje `/uzivatel/`.

---

## 📊 Web Quality Score

Skóre se počítá **váhově** — ne všechny problémy mají stejnou závažnost.

**Princip:** každá stránka začíná na 100 bodech. Za každý problém se odečítá podle typu a počtu. Celkové skóre webu = průměr skóre všech stránek.

### Kritické problémy (binární — buď jsou, nebo nejsou)

| Problém | Penalizace |
|---|---|
| Stránka má `noindex` (mimo dev domény) | **−25** |
| Testovací obsah v produkci (lorem ipsum, asdf…) | **−20** |
| Chybí `<h1>` | **−15** |
| Chybí meta description | **−15** |
| Prázdná meta description | **−15** |
| Chybí meta viewport | **−15** |
| Chybí `lang` atribut na `<html>` | **−10** |
| Duplicitní `<h1>` | **−8** |
| Přeskočení úrovně nadpisů | **−5** |

### Počítané problémy (penalizace škáluje s počtem, ale s cap)

| Problém | Za každý výskyt | Max cap |
|---|---|---|
| Duplicitní ID | −3 | −15 |
| HTTP odkazy (mixed content) | −2 | −15 |
| Chybějící alt texty | −1.5 | −15 |
| Prázdné tagy | −0.5 | −8 |
| Externí odkazy bez `target/noopener` | −0.5 | −6 |

### W3C chyby

- Každá chyba: **−2 body**, maximum **−20** bodů na stránku
- W3C **varování skóre neovlivňují** (většinou jde o kompatibilitu/styling)

### Speciální případy

- **Nedostupná stránka** (HTTP 404, timeout, chyba serveru) → automaticky **0 bodů**
- **Prázdný výsledek** → skóre 0

### Hodnocení

| Skóre | Hodnocení | Barva |
|---|---|---|
| 80–100 | Výborný | 🟢 |
| 60–79 | Průměrný | 🟡 |
| 0–59 | Špatný | 🔴 |

> 💡 **Ladění vah:** všechny konstanty jsou nahoře v `stats.py` (`_BINARY_PENALTIES`, `_COUNTED_PENALTIES`, `_W3C_ERROR_*`). Dají se snadno upravit podle potřeb konkrétního auditu.

---

## 🚀 Jak spustit

### Požadavky
- Python 3.10+
- Java 11+ (pro vnu.jar)

### Instalace

```bash
pip install -r requirements.txt
```

### Spuštění

```bash
# Interaktivní mód
python main.py

# S URL
python main.py https://www.example.cz/

# Non-interactive pro CI/CD
python main.py https://example.cz/ --no-interactive

# Bez vnu server módu (fallback na subprocess)
python main.py https://example.cz/ --no-server
```

### Parametry

| Parametr | Výchozí | Popis |
|---|---|---|
| `url` | *(ptá se)* | URL webu k auditu |
| `--max-pages` | `500` | Maximální počet stránek |
| `--delay` | `1.0` | Pauza mezi dávkami crawleru (s) |
| `--no-update-check` | — | Přeskočí kontrolu verze vnu.jar |
| `--no-interactive` | — | Žádné interaktivní dotazy |
| `--no-server` | — | Nepoužívat vnu.jar server mód |

---

## ⚡ Rychlost – server mód

Od verze s optimalizacemi program používá **vnu.jar server mód** — spustí vnu.jar jako lokální HTTP server a každou stránku validuje přes HTTP request místo nového JVM procesu.

**Rozdíl:**
- Subprocess mód: ~1s JVM startup × stránka × 4 workery → ~125s pro 500 stránek jen na startech
- Server mód: jeden JVM, validace ~50-200ms × stránka → **10-50× rychlejší**

Pokud server selže (port zablokovaný, problém se startem), automaticky se přepne na subprocess fallback.

---

## 📁 Struktura projektu

```
├── main.py             ← Hlavní spouštěcí soubor
├── config.py           ← Všechny konstanty (User-Agent, timeouty, workers…)
├── ui.py               ← Terminál UI (banner, prompt_url, helpers)
├── stats.py            ← Výpočet skóre a statistik
├── issues.py           ← Issue dataclass (strukturální problémy)
├── crawler.py          ← Paralelní crawler webu
├── sitemap.py          ← Načtení URL ze sitemap.xml
├── structure_check.py  ← HTML kontroly (vrací List[Issue])
├── validator_w3c.py    ← W3C validace (server + subprocess)
├── robots_check.py     ← robots.txt + /uzivatel/
├── report_excel.py     ← Generování Excel reportu
├── updater.py          ← Aktualizace vnu.jar z GitHubu
├── colors.py           ← Barevný terminál
├── tests/              ← Unit testy (68 testů)
│   ├── test_structure_check.py
│   └── test_other.py
├── requirements.txt    ← Pinnuté závislosti
└── vnu.jar             ← Lokální W3C validátor (stáhni samostatně)
```

---

## 🧪 Testy

```bash
python -m unittest discover tests/
```

68 testů pokrývá všechny HTML kontroly, URL validaci, statistiky, robots.txt parser (včetně detekce Disallow: /) a sitemap parser.

---

## 📄 Excel report

Report se ukládá do složky `excel reporty/`. Obsahuje:

1. **Souhrn** – Web Quality Score + přehled počtů
2. **Meta homepage** – délka title a description
3. **W3C validace** – stránky s problémy jako klikatelné odkazy
4. **HTML struktura** – problémy seskupené podle typu
5. **Nedostupné stránky** – s chybovou hláškou
6. **robots.txt** – Disallow: / a blokování JS/CSS
7. **Uživatelská sekce** – status `/uzivatel/`

---

## ⚙️ Jak funguje crawling

1. Hledá `Sitemap:` direktivu v `robots.txt`
2. Zkouší `/sitemap.xml`, `/sitemap_index.xml`
3. Rekurzivně rozbalí sitemap index (max hloubka 3)
4. Pokud sitemap neexistuje → spustí crawler (paralelně, respektuje robots.txt)

---

## 🔒 Soukromí a bezpečnost

- W3C validace je **zcela offline** (lokální vnu.jar)
- Obsah stránek se nikam neodesílá
- Crawler se identifikuje jako `WebValidator/1.0`

---

*Vytvořil Péťa*