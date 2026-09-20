# 🔍 Web Validator

Nástroj pro automatizovaný technický audit webu. Zadáš URL, program projde celý web a vygeneruje přehledný Excel report.

---

## 📋 Co program kontroluje

### 1. W3C validace HTML
Každá stránka prochází lokální validací přes `vnu.jar` (offline, žádná data se neodesílají). Výsledky jsou rozděleny na **OK**, **Varování** a **Chyby**.

Excel navíc obsahuje tabulku **„Nejčastější W3C chyby“** – stejné chyby ze všech stránek seskupené podle textu (bez čísla řádku): text chyby × počet stránek × počet výskytů × ukázková stránka (odkaz na validator.w3.org). U webů ze šablony tak hned vidíš, že např. 381 stránek s chybou = 2 chyby v šabloně. Zobrazuje se max 30 nejčastějších chyb; varování se neagregují.

### 2. Struktura HTML
Na každé stránce se kontroluje 17 věcí (+ 1 napříč webem):

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
| Staging/dev URL v HTML | Detekuje canonical/og:image/odkazy ukazující na dev domény |
| `<title>` | Musí existovat a nesmí být prázdný (na každé stránce, délka se hlídá jen na homepage) |
| Canonical | `<link rel="canonical">` musí existovat, mířit sám na sebe a nebýt `http://` na https webu |
| Open Graph | `og:title`, `og:description`, `og:image` (absolutní URL) – bez nich sdílení na Facebooku/LinkedInu nemá náhled |
| Rozměry obrázků | `<img>` bez `width`/`height` (nebo obojího v inline `style`) → posun layoutu při načítání (CLS) |
| Duplicitní `<title>` *(napříč webem)* | Stejný titulek na více stránkách – v reportu jeden řádek na každý duplicitní titulek |

> ⚠️ **Noindex check** je přeskočen pro dev/staging domény (`*.cz.dev.poski.com`, `*.poskireal.cz`), kde je `noindex` záměrný. Stejně tak **canonical check** – na dev/lokálním webu canonical běžně (a správně) míří na produkci.

> ⚠️ **Staging URL check** prochází `<a>`, `<img>`, `<script>`, `<link>` (canonical, alternate), `<iframe>`, `<video>`, `<form action>`, Open Graph (`og:image`, `og:url`), Twitter Cards a další. Stejně jako noindex je přeskočen pro dev domény.

### 3. Meta údaje homepage
- **Title:** 30–60 znaků
- **Meta description:** 70–160 znaků

### 4. robots.txt – kontrola indexace
- **Kritická kontrola:** detekuje `Disallow: /` pro Googlebot nebo `*` — to znamená že je celý web zablokovaný pro vyhledávače. Klasický staging artefakt který se zapomene změnit při nasazení na produkci.
- **JS/CSS blokování:** zkontroluje jestli `robots.txt` nebrání Googlebotu číst `.js` nebo `.css` soubory (bez nich Google nedokáže korektně renderovat stránku).
> ⚠️ Přeskočeno pro interní/dev prostředí (`poskireal.cz`, `poski.com`, `.cz.dev.`).

### 5. Uživatelská sekce
Testuje jestli existuje `/uzivatel/`.

### 6. Odkazy a obrázky (fáze `[LINKS]`)
Po stažení všech stránek se z jejich HTML posbírají všechny `<a href>`, `<img src>` / `data-src` a `og:image`, deduplikují se napříč webem a ověří HEAD requestem (cíle, které jsou samy auditované stránky, se znovu neověřují):

- **Nefunkční odkazy** – interní odkaz vrací 404 / 5xx nebo je nedostupný. V reportu sekce „Nefunkční odkazy“: cíl → status → na kterých stránkách odkaz je.
- **Nedostupné obrázky** – `src` vrací 404 / je nedostupný.
- **Příliš velké obrázky** – `Content-Length` nad 500 kB (`config.IMAGE_MAX_KB`).

> ⚠️ Cizí domény se ve výchozím stavu **neověřují** (u velkého webu jde o stovky serverů, které navíc často blokují HEAD od botů). Zapíná se přes `--check-external`; i pak se u externích cílů 401/403/405/429/999 nebere jako „nefunkční“ – to jen znamená, že server bota nepustil.

**Ochrana proti obřím webům** (e-shopy s filtry mají klidně 13 000 unikátních URL na 400 stránkách):
- Interní URL s parametry (`/kremy/?p13[0]=5`, `?r=cs`, `logo.png?v=3`) se **sloučí na základní URL** – filtr na funkční kategorii je funkční. Výjimka: skriptové cesty (`index.php?id=5`), kde parametry nesou obsah.
- Ověřuje se **max 1 500 cílů** (`LINK_CHECK_MAX_TARGETS`, přednost mají ty s nejvíc výskyty) a fáze má **časový rozpočet 10 min** (`LINK_CHECK_MAX_SECONDS`). Co se nestihne, je v reportu „neověřeno“, ne „nefunkční“.
- **Pojistka proti výpadku sítě:** DNS / connection chyba u vlastní domény webu se nikdy nehlásí jako nefunkční odkaz, a po 15 síťových chybách za sebou (`LINK_CHECK_ABORT_AFTER`) se fáze přeruší – nic z toho neovlivní skóre. Bez toho by výpadek Wi-Fi uprostřed běhu znamenal tisíce falešných 404.
- HEAD requesty jedou přes keep-alive spojení (jedno DNS + TLS na worker, ne na request).

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
| Chybí / prázdný `<title>` | **−15** |
| Chybí meta description | **−15** |
| Prázdná meta description | **−15** |
| Chybí meta viewport | **−15** |
| Chybí `lang` atribut na `<html>` | **−10** |
| Canonical míří na jinou URL (nebo je jich víc) | **−10** |
| Duplicitní `<h1>` | **−8** |
| Canonical používá `http://` na https stránce | **−8** |
| Přeskočení úrovně nadpisů | **−5** |
| Duplicitní `<title>` (každá z postižených stránek) | **−5** |
| Chybí canonical | **−3** |
| Chybí Open Graph meta (`og:title` / `og:description` / `og:image`) | **−3** |

### Počítané problémy (penalizace škáluje s počtem, ale s cap)

| Problém | Za každý výskyt | Max cap |
|---|---|---|
| Staging/dev URL v HTML | −8 | −20 |
| Nefunkční odkazy (404 / nedostupné) | −3 | −15 |
| Duplicitní ID | −3 | −15 |
| Nedostupné obrázky (404) | −2 | −10 |
| Příliš velké obrázky (nad 500 kB) | −2 | −10 |
| HTTP odkazy (mixed content) | −2 | −15 |
| Chybějící alt texty | −1.5 | −15 |
| Prázdné tagy | −0.5 | −8 |
| Externí odkazy bez `target/noopener` | −0.5 | −6 |
| Obrázky bez `width`/`height` | −0.5 | −5 |

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

# Vynechat blog a anglickou verzi, report neschovávat pod starý
python main.py https://example.cz/ --exclude "/blog/*" --exclude "/en/*" --keep

# CI / kontrola před nasazením: exit kód 1 když je skóre pod 80
python main.py https://example.cz/ --no-interactive --fail-under 80 --output reporty/

# Ověřit i odkazy a obrázky na cizích doménách, JSON uložit jinam
python main.py https://example.cz/ --check-external --json vysledky/
```

### Parametry

| Parametr | Výchozí | Popis |
|---|---|---|
| `url` | *(ptá se)* | URL webu k auditu |
| `--max-pages` | `500` | Maximální počet stránek |
| `--delay` | `1.0` | Pauza mezi dávkami crawleru (s) |
| `--exclude VZOR` | — | Vynechá URL odpovídající glob vzoru (`/blog/*`, `*.pdf`, `https://ex.cz/en/*`). Lze opakovat nebo oddělit čárkou; platí pro sitemap i crawler |
| `--output CESTA` | `excel reporty/<host>_validator.xlsx` | Soubor `.xlsx`, nebo adresář (v něm výchozí jméno) |
| `--keep` | — | Nepřepisovat starý report – do jména se přidá časová značka |
| `--fail-under N` | — | Exit kód 1, když je Web Quality Score < N (0–100) |
| `--check-external` | — | Ověřit i odkazy a obrázky na cizích doménách (výchozí: jen interní) |
| `--json CESTA` | `<stejně jako Excel>.json` | Kam uložit JSON výsledek – soubor `.json` nebo adresář |
| `--no-update-check` | — | Přeskočí kontrolu verze vnu.jar |
| `--no-interactive` | — | Žádné interaktivní dotazy |
| `--no-server` | — | Nepoužívat vnu.jar server mód |

**Exit kódy:** `0` = hotovo (a skóre ≥ prahu, pokud je zadaný), `1` = skóre pod `--fail-under` nebo žádné stránky k auditu, `2` = chybné argumenty, `130` = Ctrl+C.

> 💡 Výchozí report se při každém běhu **přepisuje** – je to snímek aktuálního stavu webu. Když je soubor otevřený v Excelu, uloží se vedle s časovou značkou. Historii verzí si vynutíš přes `--keep`.

---

## 📈 JSON výstup a porovnání s minulým během

Vedle Excelu se **vždy** uloží i `<host>_validator.json` (stejné jméno, i s časovou značkou při `--keep`; `--json CESTA` jen změní umístění). Obsahuje kompletní výsledek: skóre, souhrn, každou stránku s jejím skóre, strukturálními problémy (`Issue.to_dict()`) a W3C zprávami, nefunkční odkazy, obrázky, robots.txt a `/uzivatel/`. Hodí se pro napojení na cokoliv dalšího (CI, dashboard, vlastní skripty).

Při dalším běhu na stejnou doménu se minulý JSON načte (výchozí soubor, nebo nejnovější `_YYYYMMDD_HHMMSS` verze vedle něj) a v terminálu, v souhrnu Excelu i v JSON (`comparison`) se ukáže:

```
Změna od minula    : 72 → 85 (+13)  |  opraveno 12, nové 3  (minulý běh 15.09.2026 20:55)
```

Sekce „Změny od minulého běhu“ v Excelu vypíše konkrétní nové a opravené problémy (stránka + typ problému, včetně W3C chyb). Porovnávají se jen stránky přítomné v obou bězích – stránka, která z auditu zmizela, se nepočítá jako „opravená“.

---

## ⚡ Rychlost – server mód

Od verze s optimalizacemi program používá **vnu.jar server mód** — spustí vnu.jar jako lokální HTTP server a každou stránku validuje přes HTTP request místo nového JVM procesu.

**Rozdíl:**
- Subprocess mód: ~1s JVM startup × stránka × 4 workery → ~125s pro 500 stránek jen na startech
- Server mód: jeden JVM, validace ~50-200ms × stránka → **10-50× rychlejší**

Pokud server selže (port zablokovaný, problém se startem), automaticky se přepne na subprocess fallback.

V závěrečném souhrnu je řádek `Doba fází : stažení 41s | validace 3s | odkazy 54s` – když běh trvá dlouho, je hned vidět, která fáze za to může.

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
├── links_check.py      ← Dostupnost odkazů a obrázků (404, velikost)
├── report_excel.py     ← Generování Excel reportu
├── report_json.py      ← JSON výstup + porovnání s minulým během
├── updater.py          ← Aktualizace vnu.jar z GitHubu
├── colors.py           ← Barevný terminál
├── tests/              ← Unit testy (280 testů)
│   ├── test_structure_check.py
│   ├── test_other.py
│   ├── test_network_checks.py
│   ├── test_report_and_crawler.py
│   └── test_links_and_json.py
├── requirements.txt    ← Pinnuté závislosti
└── vnu.jar             ← Lokální W3C validátor (stáhni samostatně)
```

---

## 🧪 Testy

```bash
python -m unittest discover tests/
```

280 testů pokrývá všechny HTML kontroly (včetně noindex, staging URL, title, canonical, Open Graph a rozměrů obrázků), kontrolu odkazů a obrázků (mockované HEAD requesty, externí cíle, velikost, slučování URL s parametry, limit cílů, časový rozpočet, pojistka proti výpadku sítě), JSON export a porovnání s minulým během, CLI přepínače (`--exclude`, `--output`/`--keep`, `--fail-under` exit kódy), URL validaci, statistiky a agregaci W3C chyb, robots.txt parser (včetně detekce Disallow: /), sitemap parser (včetně `.xml.gz`), crawler (filtry, deduplikace, robots.txt, hybrid režim), detekci `/uzivatel/` (soft 404, přesměrování), kódování stažených stránek, zamčený Excel soubor a obsah vygenerovaného Excel reportu.

---

## 📄 Excel report

Report se ukládá do složky `excel reporty/`. Obsahuje:

1. **Souhrn** – Web Quality Score + přehled počtů (+ změna skóre od minulého běhu)
2. **Změny od minulého běhu** – nové a opravené problémy (jen když existuje minulý JSON)
3. **Meta homepage** – délka title a description
4. **W3C validace** – nejčastější chyby napříč webem (text × počet stránek × ukázka) a pak stránky s problémy jako klikatelné odkazy
5. **HTML struktura** – problémy seskupené podle typu
6. **Nefunkční odkazy** – cíl → status → stránky, kde odkaz je
7. **Obrázky** – nedostupné nebo větší než 500 kB
8. **Nedostupné stránky** – s chybovou hláškou
9. **robots.txt** – Disallow: / a blokování JS/CSS
10. **Uživatelská sekce** – status `/uzivatel/`

Vedle Excelu vzniká i JSON se stejným jménem (viz výše).

---

## ⚙️ Jak funguje crawling

1. Hledá `Sitemap:` direktivu v `robots.txt`
2. Zkouší `/sitemap.xml`, `/sitemap_index.xml`, `/sitemap-index.xml`
3. Rekurzivně rozbalí sitemap index (max hloubka 3)
4. Rozhoduje podle počtu nalezených URL:
   - **≥ 10 URL** (`SITEMAP_MIN_PAGES`) → použije se jen sitemap, crawler se přeskočí
   - **1–9 URL** → **hybrid režim** – sitemap URL slouží jako seed, crawler doplní zbytek webu (typicky neúplná sitemap)
   - **0 URL** → klasický crawler od homepage (paralelně, respektuje robots.txt)
5. Pokud sitemap obsahuje URL z cizích domén (typicky leftover po migraci), zobrazí se varování

---

## 🔒 Soukromí a bezpečnost

- W3C validace je **zcela offline** (lokální vnu.jar)
- Obsah stránek se nikam neodesílá
- Crawler se identifikuje jako `WebValidator/1.0`

---

*Vytvořil Péťa*