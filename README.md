# 🔍 Web Validator

Nástroj pro automatizovaný technický audit webu – hlavně pro **výstupní kontrolu před předáním**. Zadáš URL, program projde celý web a vygeneruje přehledný Excel report.

Tři věci, na které se soustředí: **1. W3C validace**, **2. testovací / zástupný obsah** (lorem ipsum, `{{ proměnné }}`, výchozí texty…), **3. dostupnost** (nefunkční odkazy a obrázky, soft 404, sitemap). SEO kontroly (meta description, canonical, Open Graph, alt texty…) jsou ve výchozím stavu **vypnuté** a zapínají se přepínačem `--seo`.

---

## 📋 Co program kontroluje

### 1. W3C validace HTML
Každá stránka prochází lokální validací přes `vnu.jar` (offline, žádná data se neodesílají). Výsledky jsou rozděleny na **OK**, **Varování** a **Chyby**.

Excel navíc obsahuje tabulku **„Nejčastější W3C chyby“** – stejné chyby ze všech stránek seskupené podle textu (bez čísla řádku): text chyby × počet stránek × počet výskytů × ukázková stránka (odkaz na validator.w3.org). U webů ze šablony tak hned vidíš, že např. 381 stránek s chybou = 2 chyby v šabloně. Zobrazuje se max 30 nejčastějších chyb; varování se neagregují.

### 2. Struktura HTML
Na každé stránce se kontroluje 19 věcí (+ 1 napříč webem). Řádky označené **SEO** se hlásí jen s přepínačem `--seo` (viz **SEO modul** níž):

| Co se kontroluje | Popis |
|---|---|
| `<h1>` existence a unikátnost | Každá stránka by měla mít právě jeden H1 |
| Pořadí nadpisů **(SEO)** | Nesmí se přeskakovat úrovně (H1 → H3 bez H2) |
| Prázdné tagy | `<div>`, `<p>`, `<span>` a další bez obsahu |
| Duplicitní ID | Stejné `id` atributy na více prvcích |
| Meta description **(SEO)** | Musí existovat a nesmí být prázdná |
| Alt texty u obrázků **(SEO)** | Každý `<img>` musí mít `alt` atribut (chybějící `alt` hlásí i W3C validace) |
| HTTP odkazy | Odhalí nezabezpečené `http://` odkazy |
| Externí odkazy **(SEO)** | Musí mít `target="_blank"` a `rel="noopener"` |
| Testovací obsah | Detekuje lorem ipsum, asdf, qwerty a další zástupné texty – viz **Detekce testovacího obsahu** níž |
| `lang` atribut | `<html lang="cs">` je důležitý pro SEO a čtečky obrazovky |
| Meta viewport | Bez něj se stránka na mobilech zobrazuje špatně |
| `noindex` meta tag | Detekuje `<meta name="robots" content="noindex">` na produkci |
| Staging/dev URL v HTML | Detekuje canonical/og:image/odkazy ukazující na dev domény |
| `<title>` | Musí existovat a nesmí být prázdný (na každé stránce; délka se hlídá jen na homepage a jen s `--seo`) |
| Canonical **(SEO)** | `<link rel="canonical">` musí existovat, mířit sám na sebe a nebýt `http://` na https webu |
| Open Graph **(SEO)** | `og:title`, `og:description`, `og:image` (absolutní URL) – bez nich sdílení na Facebooku/LinkedInu nemá náhled |
| Rozměry obrázků **(SEO)** | `<img>` bez `width`/`height` (nebo obojího v inline `style`) → posun layoutu při načítání (CLS) |
| Soft 404 | Stránka vrací HTTP 200, ale `<title>`/`<h1>` hlásí „Stránka nenalezena“, „404“, „Page not found“ – Google ji indexuje jako běžnou stránku. Posuzuje se jen titulek/nadpis (článek „Jak nastavit 404 stránku“ se nehlásí) |
| Prázdné odkazy | `<a href="#">Text</a>`, `href=""`, `javascript:void(0)` **bez** jakéhokoli JS „háčku“ (class, id, data-*, role, aria-*, onclick…) = nedodělaný odkaz. Ovladače menu/modalů a odkazy jen s obrázkem se nehlásí |
| Duplicitní `<title>` *(napříč webem)* **(SEO)** | Stejný titulek na více stránkách – v reportu jeden řádek na každý duplicitní titulek |

> ⚠️ **Noindex check** je přeskočen pro dev/staging domény (`*.cz.dev.poski.com`, `*.poskireal.cz`), kde je `noindex` záměrný. Stejně tak **canonical check** – na dev/lokálním webu canonical běžně (a správně) míří na produkci.

> ⚠️ **Staging URL check** prochází `<a>`, `<img>`, `<script>`, `<link>` (canonical, alternate), `<iframe>`, `<video>`, `<form action>`, Open Graph (`og:image`, `og:url`), Twitter Cards a další. Stejně jako noindex je přeskočen pro dev domény.

### 3. SEO modul (`--seo`)
Nástroj slouží k výstupní kontrole – jestli web *funguje* a není v něm testovací obsah. SEO kvalita je jiná disciplína (a typicky ji hlídají samostatné testy), proto jsou SEO kontroly **ve výchozím stavu vypnuté** a zapínají se `--seo`:

- meta description (existence, prázdná), canonical (chybí / míří jinam / `http://`), Open Graph, duplicitní `<title>` napříč webem,
- `alt` texty, `<img>` bez rozměrů, obrázky nad 500 kB, externí odkazy bez `target="_blank" rel="noopener"`, pořadí nadpisů,
- **Meta údaje homepage** – délka `<title>` 30–60 znaků a meta description 70–160 znaků.

Bez `--seo` se tyto kontroly ani nehlásí, ani nepočítají do skóre; v souhrnu Excelu i v terminálu je jen poznámka, že jsou vypnuté. S `--seo` mají v Excelu vlastní sekci „SEO – souhrn problémů“ (+ „Meta – homepage“) a vstupují do skóre. Které typy patří do SEO modulu, určuje jediná množina `issues.SEO_ISSUE_TYPES`. Když se porovnává s minulým během a jen jeden z běhů měl `--seo`, SEO problémy se v porovnání ignorují (jinak by „přibyly“ nebo „zmizely“ bez změny na webu).

### 4. robots.txt – kontrola indexace
- **Kritická kontrola:** detekuje `Disallow: /` pro Googlebot nebo `*` — to znamená že je celý web zablokovaný pro vyhledávače. Klasický staging artefakt který se zapomene změnit při nasazení na produkci.
- **JS/CSS blokování:** zkontroluje jestli `robots.txt` nebrání Googlebotu číst `.js` nebo `.css` soubory (bez nich Google nedokáže korektně renderovat stránku).
> ⚠️ Přeskočeno pro interní/dev prostředí (`poskireal.cz`, `poski.com`, `.cz.dev.`).

### 5. Uživatelská sekce
Testuje jestli existuje `/uzivatel/`.

### 5b. Test vlastní 404 stránky
Jeden GET na náhodnou neexistující URL (`/wv-neexistujici-stranka-…/`). Správně má web vrátit **HTTP 404** (nebo 410). Hlásí se:
- **HTTP 200** – soft 404: každá překlepnutá URL vypadá pro Google jako platná stránka (když stránka zároveň zobrazuje text „Stránka nenalezena“, je jen špatně stavový kód).
- **Přesměrování na homepage** (301/302 → `/`) – totéž, jen skrytěji.
- **5xx** – chyba serveru pro neexistující URL.
Přesměrování na vlastní 404 stránku, která 404 vrátí, je v pořádku.

### 5c. Bot ochrana (Anubis, Cloudflare, …)
PoskiREAL weby mají ochranu **Anubis**, která podezřelým klientům vrátí místo obsahu ověřovací stránku „Making sure you're not a bot!“ (s HTTP 200). Validátor proto posílá poctivý User-Agent bez „Mozilla“ (`WebValidator/1.0 (+github…)`) a `Accept-Language`, aby projel. Když se ověřovací stránka přece vrátí (Anubis, Cloudflare „Just a moment…“, DDoS-Guard, Incapsula, Sucuri…), pozná se **před** W3C validací: stránka se nevaliduje, hlásí se jako nedostupná a v terminálu i na začátku Excelu je červené **„AUDIT NENÍ PLATNÝ“** s počtem zablokovaných stránek. Bez toho by se validovalo 400× stejné challenge HTML a report by vypadal věrohodně, ale byl by nesmysl.

### 6. Odkazy a obrázky (fáze `[LINKS]`)
Po stažení všech stránek se z jejich HTML posbírají všechny `<a href>`, `<img src>` / `data-src` a `og:image`, deduplikují se napříč webem a ověří HEAD requestem (cíle, které jsou samy auditované stránky, se znovu neověřují):

- **Nefunkční odkazy** – interní odkaz vrací 404 / 5xx nebo je nedostupný. V reportu sekce „Nefunkční odkazy“: cíl → status → na kterých stránkách odkaz je.
- **Nedostupné obrázky** – `src` vrací 404 / je nedostupný.
- **Příliš velké obrázky** *(jen s `--seo`)* – `Content-Length` nad 500 kB (`config.IMAGE_MAX_KB`).
- **Odkazy přes přesměrování** *(informativně, bez vlivu na skóre)* – interní odkaz vrací 301/302. První HEAD jde bez follow, u 3xx se dojde na konec; v reportu je tabulka „odkaz → kam“ a stránky, kde odkaz je. Přesměrování jen kvůli `http→https`, `www.` nebo koncovému lomítku jsou označená jako kosmetická a řazená až pod ostatní. Odkaz má mířit rovnou na cílovou URL.

> ⚠️ Cizí domény se ve výchozím stavu **neověřují** (u velkého webu jde o stovky serverů, které navíc často blokují HEAD od botů). Zapíná se přes `--check-external`; i pak se u externích cílů 401/403/405/429/999 nebere jako „nefunkční“ – to jen znamená, že server bota nepustil.

**Ochrana proti obřím webům** (e-shopy s filtry mají klidně 13 000 unikátních URL na 400 stránkách):
- Interní URL s parametry (`/kremy/?p13[0]=5`, `?r=cs`, `logo.png?v=3`) se **sloučí na základní URL** – filtr na funkční kategorii je funkční. Výjimka: skriptové cesty (`index.php?id=5`), kde parametry nesou obsah.
- Ověřuje se **max 1 500 cílů** (`LINK_CHECK_MAX_TARGETS`, přednost mají ty s nejvíc výskyty) a fáze má **časový rozpočet 10 min** (`LINK_CHECK_MAX_SECONDS`). Co se nestihne, je v reportu „neověřeno“, ne „nefunkční“.
- **Pojistka proti výpadku sítě:** DNS / connection chyba u vlastní domény webu se nikdy nehlásí jako nefunkční odkaz, a po 15 síťových chybách za sebou (`LINK_CHECK_ABORT_AFTER`) se fáze přeruší – nic z toho neovlivní skóre. Bez toho by výpadek Wi-Fi uprostřed běhu znamenal tisíce falešných 404.
- HEAD requesty jedou přes keep-alive spojení (jedno DNS + TLS na worker, ne na request).

### 7. Hygiena sitemap.xml
Když audit vychází ze sitemapy, po stažení stránek se zvlášť vypíše, co v ní nemá být: URL vracející **HTTP 4xx/5xx** („neexistuje – odstranit ze sitemapy“), URL, které se **přesměrovávají** jinam (do sitemapy patří cílová URL), a URL nedostupné kvůli síťové chybě. Sekce se v Excelu objeví jen u auditů ze sitemapy.

---

## 📊 Web Quality Score

Skóre se počítá **váhově** — ne všechny problémy mají stejnou závažnost.

**Princip:** každá stránka začíná na 100 bodech. Za každý problém se odečítá podle typu a počtu. Celkové skóre webu = průměr skóre všech stránek. Problémy ze SEO modulu (v tabulkách označené **SEO**) se počítají jen s `--seo` – bez něj je skóre čistě z jádra (W3C, testovací obsah, dostupnost, struktura).

### Kritické problémy (binární — buď jsou, nebo nejsou)

| Problém | Penalizace |
|---|---|
| Stránka má `noindex` (mimo dev domény) | **−25** |
| Testovací obsah v produkci (lorem ipsum, asdf…) | **−20** |
| Vývojářský výpis chyby v HTML (PHP Warning / Fatal error / Stack trace…) | **−20** |
| Nevyrenderované šablonové proměnné v textu (`{{ }}`, `{% %}`, `%X%`, `<?php`) | **−15** |
| Výchozí text CMS / šablony („Hello world!“, „Text odstavce“…) | **−15** |
| Chybí `<h1>` | **−15** |
| Chybí / prázdný `<title>` | **−15** |
| Chybí meta description **(SEO)** | **−15** |
| Prázdná meta description **(SEO)** | **−15** |
| Chybí meta viewport | **−15** |
| Chybí `lang` atribut na `<html>` | **−10** |
| Soft 404 – „Stránka nenalezena“ s HTTP 200 | **−10** |
| JavaScriptové hodnoty v textu (`undefined Kč`, `null`, `NaN`, `[object Object]`) | **−10** |
| Výchozí text v `<title>` / description / `alt` / `og:*` („Document“, `alt="image"`) | **−10** |
| Canonical míří na jinou URL (nebo je jich víc) **(SEO)** | **−10** |
| Duplicitní `<h1>` | **−8** |
| Canonical používá `http://` na https stránce **(SEO)** | **−8** |
| Přeskočení úrovně nadpisů **(SEO)** | **−5** |
| Duplicitní `<title>` (každá z postižených stránek) **(SEO)** | **−5** |
| Chybí canonical **(SEO)** | **−3** |
| Chybí Open Graph meta (`og:title` / `og:description` / `og:image`) **(SEO)** | **−3** |

### Počítané problémy (penalizace škáluje s počtem, ale s cap)

| Problém | Za každý výskyt | Max cap |
|---|---|---|
| Staging/dev URL v HTML | −8 | −20 |
| Nefunkční odkazy (404 / nedostupné) | −3 | −15 |
| Duplicitní ID | −3 | −15 |
| Nedostupné obrázky (404) | −2 | −10 |
| Příliš velké obrázky (nad 500 kB) **(SEO)** | −2 | −10 |
| HTTP odkazy (mixed content) | −2 | −15 |
| Chybějící alt texty **(SEO)** | −1.5 | −15 |
| Prázdné odkazy (`href="#"`, `href=""`, `javascript:void(0)`) | −1 | −5 |
| Prázdné tagy | −0.5 | −8 |
| Externí odkazy bez `target/noopener` **(SEO)** | −0.5 | −6 |
| Obrázky bez `width`/`height` **(SEO)** | −0.5 | −5 |

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

### Detekce testovacího obsahu

Kromě zakázaných frází (lorem ipsum, asdf, „vložte text“…) ve viditelném textu hlídá `content_check.py` šest dalších skupin. Každá má vlastní typ problému a váhu ve skóre; hlavní zásada je **žádné falešné poplachy** – hledá se jen na konkrétních místech (celý text prvku, celý `<title>`, konkrétní atribut) a obsah `<code>`, `<pre>`, `<script>`, `<style>`, `<textarea>` a `<template>` se ignoruje.

| Skupina | Co chytí | Příklad |
|---|---|---|
| Výchozí texty | `<title>`, meta description, `og:title`/`og:description` nebo `alt` rovné výchozí hodnotě editoru | `<title>Document</title>`, `alt="image"`, `og:title="Home"` |
| Zástupné obrázky | placeholder služby a soubory `dummy*`, `sample*`, `lorem*`, `placeholder*`, `test.jpg`/`test-1.png` (v `src`, `data-src`, `srcset`, `og:image`) | `https://via.placeholder.com/300`, `picsum.photos`, `/img/dummy.jpg` |
| Šablonové proměnné | nevyrenderované `{{ … }}`, `{% … %}`, `[[ … ]]`, `%NAME%`, `${…}` v textu / title / meta; `<?php` v HTML | `Vítejte, {{ user.name }}` |
| JS hodnoty | `undefined`, `null`, `NaN`, `[object Object]`, `Array` jako celý text prvku, s jednotkou („undefined Kč“) nebo v `href`/`src`/`alt` | `<span class="price">NaN Kč</span>` |
| Vývojářské výpisy | PHP `Warning/Notice/Fatal error … on line N`, `Stack trace`, `Uncaught …Exception`, `var_dump`/`print_r`, SQL chyby, Tracy, Whoops, Symfony | `Warning: Undefined variable $x in /var/www/index.php on line 12` |
| Výchozí texty CMS | WordPress („Hello world!“, „Just another WordPress site“, „Sample Page“), Joomla, Drupal, „Nadpis stránky“, „Text odstavce“, „Web je ve výstavbě“, „under construction“ | `<h2>Hello world!</h2>` |

Lazy-load technika `src="placeholder.png" data-src="real.jpg"` se nehlásí (kontroluje se `data-src`); `{{ }}` uvnitř prvků Vue/Angular/Alpine (`v-*`, `ng-*`, `x-*`) se nehlásí. Ověřeno na ~190 stránkách reálných webů bez jediného falešného poplachu.

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

# Zapnout i SEO kontroly (meta description, canonical, OG, alt, rozměry obrázků…)
python main.py https://www.example.cz/ --seo

# Ověřit i odkazy a obrázky na cizích doménách, JSON uložit jinam
python main.py https://example.cz/ --check-external --json vysledky/
```

### Parametry

| Parametr | Výchozí | Popis |
|---|---|---|
| `url` | *(ptá se)* | URL webu k auditu |
| `--max-pages` | `500` | Maximální počet stránek |
| `--delay` | `1.0` / `0.5` | Pauza mezi requesty (s) – platí pro crawler (výchozí 1.0) i stahování stránek (výchozí 0.5, `config.FETCH_DELAY`). Na lokálním hostu se pauzy nepoužijí |
| `--exclude VZOR` | — | Vynechá URL odpovídající glob vzoru (`/blog/*`, `*.pdf`, `https://ex.cz/en/*`). Lze opakovat nebo oddělit čárkou; platí pro sitemap i crawler |
| `--output CESTA` | `excel reporty/<host>_validator.xlsx` | Soubor `.xlsx`, nebo adresář (v něm výchozí jméno) |
| `--keep` | — | Nepřepisovat starý report – do jména se přidá časová značka |
| `--fail-under N` | — | Exit kód 1, když je Web Quality Score < N (0–100) |
| `--check-external` | — | Ověřit i odkazy a obrázky na cizích doménách (výchozí: jen interní) |
| `--seo` | — | Zapnout SEO modul: meta description, canonical, Open Graph, alt texty, rozměry a velikost obrázků, noopener, pořadí nadpisů, duplicitní `<title>`, délka title/description homepage (výchozí: vypnuto) |
| `--json CESTA` | `<stejně jako Excel>.json` | Kam uložit JSON výsledek – soubor `.json` nebo adresář |
| `--no-update-check` | — | Přeskočí kontrolu verze vnu.jar |
| `--no-interactive` | — | Žádné interaktivní dotazy |
| `--no-server` | — | Nepoužívat vnu.jar server mód |

**Exit kódy:** `0` = hotovo (a skóre ≥ prahu, pokud je zadaný), `1` = skóre pod `--fail-under` nebo žádné stránky k auditu, `2` = chybné argumenty, `130` = Ctrl+C.

> 💡 Výchozí report se při každém běhu **přepisuje** – je to snímek aktuálního stavu webu. Když je soubor otevřený v Excelu, uloží se vedle s časovou značkou. Historii verzí si vynutíš přes `--keep`.

---

## 📈 JSON výstup a porovnání s minulým během

Vedle Excelu se **vždy** uloží i `<host>_validator.json` (stejné jméno, i s časovou značkou při `--keep`; `--json CESTA` jen změní umístění). Obsahuje kompletní výsledek: skóre, souhrn, každou stránku s jejím skóre, strukturálními problémy (`Issue.to_dict()`) a W3C zprávami, nefunkční odkazy, obrázky, robots.txt, `/uzivatel/` a příznak `seo` (běželo s `--seo`). Hodí se pro napojení na cokoliv dalšího (CI, dashboard, vlastní skripty).

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
├── content_check.py    ← Detekce testovacího obsahu (6 skupin, volá structure_check)
├── validator_w3c.py    ← W3C validace (server + subprocess)
├── robots_check.py     ← robots.txt + /uzivatel/
├── links_check.py      ← Dostupnost odkazů a obrázků (404, velikost, přesměrování)
├── availability_check.py ← Soft 404, test vlastní 404 stránky, detekce bot ochrany
├── report_excel.py     ← Generování Excel reportu
├── report_json.py      ← JSON výstup + porovnání s minulým během
├── updater.py          ← Aktualizace vnu.jar z GitHubu
├── colors.py           ← Barevný terminál
├── tests/              ← Unit testy (387 testů)
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

387 testů pokrývá všechny HTML kontroly (včetně noindex, staging URL, title, canonical, Open Graph a rozměrů obrázků), SEO modul za `--seo` (výchozí stav bez SEO nálezů, skóre jen z jádra, sekce v Excelu, porovnání běhů s různým režimem), detekci testovacího obsahu (všech 6 skupin, včetně testů na falešné poplachy u běžného českého textu), dostupnost (soft 404 včetně falešných poplachů typu „404 m²“, prázdné odkazy vs. JS ovladače, test vlastní 404 stránky, detekci bot ochrany Anubis/Cloudflare, sitemap hygienu), kontrolu odkazů a obrázků (mockované HEAD requesty, přesměrování, externí cíle, velikost, slučování URL s parametry, limit cílů, časový rozpočet, pojistka proti výpadku sítě), JSON export a porovnání s minulým během, CLI přepínače (`--exclude`, `--output`/`--keep`, `--fail-under` exit kódy), URL validaci, statistiky a agregaci W3C chyb, robots.txt parser (včetně detekce Disallow: /), sitemap parser (včetně `.xml.gz`), crawler (filtry, deduplikace, robots.txt, hybrid režim), detekci `/uzivatel/` (soft 404, přesměrování), kódování stažených stránek, zamčený Excel soubor a obsah vygenerovaného Excel reportu.

---

## 📄 Excel report

Report se ukládá do složky `excel reporty/`. Obsahuje:

1. **Souhrn** – Web Quality Score + přehled počtů (+ změna skóre od minulého běhu); při zablokování bot ochranou červené varování „AUDIT NENÍ PLATNÝ“ hned pod nadpisem; bez `--seo` poznámka, že SEO kontroly jsou vypnuté
2. **Změny od minulého běhu** – nové a opravené problémy (jen když existuje minulý JSON)
3. **W3C validace** – nejčastější chyby napříč webem (text × počet stránek × ukázka) a pak stránky s problémy jako klikatelné odkazy
4. **HTML struktura** – problémy jádra seskupené podle typu
5. **SEO** *(jen s `--seo`)* – SEO problémy seskupené podle typu + **Meta – homepage** (délka title a description)
6. **Nefunkční odkazy** – cíl → status → stránky, kde odkaz je; pod tím odkazy vedoucí přes přesměrování (odkaz → kam)
7. **Obrázky** – nedostupné (s `--seo` i větší než 500 kB)
8. **Nedostupné stránky** – s chybovou hláškou (včetně stránek zablokovaných bot ochranou)
9. **Sitemap.xml** – neexistující / přesměrované URL (jen u auditů ze sitemapy)
10. **robots.txt** – Disallow: / a blokování JS/CSS
11. **Test 404 stránky** – jak web odpoví na neexistující URL
12. **Uživatelská sekce** – status `/uzivatel/`

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