# Web Validator

Skript na kontrolu webu před předáním klientovi. Zadáš URL, projde celý web
a udělá z toho Excel se seznamem věcí k opravě.

Kontroluje hlavně tři věci:

1. W3C validitu HTML,
2. zapomenutý testovací obsah (lorem ipsum, `{{ proměnné }}`, „Hello world!“…),
3. nefunkční stránky, odkazy a obrázky.

SEO kontroly (meta description, canonical, Open Graph, alt texty…) jsou
defaultně vypnuté, zapínají se přes `--seo`.

## Instalace

Potřebuješ Python 3.10+ a Javu 11+ (kvůli vnu.jar).

```bash
pip install -r requirements.txt
```

`vnu.jar` (W3C validátor) se do repa nenahrává. Když chybí, program nabídne,
že ho stáhne.

### Firemní domény

Dev a staging domény se nedávají do kódu, ale do souboru `firemni_domeny.txt`
(je v `.gitignore`). Vzor je v `firemni_domeny.example.txt`:

```
[dev]
dev.mojefirma.cz
staging.mojefirma.cz

[robots]
mojefirma.
```

- `[dev]` – na těchhle doménách se nehlásí `noindex` (je tam schválně)
  a canonical. Když na ně odkazuje produkční web, hlásí se to jako chyba.
- `[robots]` – navíc se tu přeskočí kontrola robots.txt.

Stačí část názvu, hledá se, jestli je v doméně obsažená. Bez souboru program
funguje normálně, jen žádné dev domény nezná.

## Spuštění

```bash
python main.py                                  # zeptá se na URL
python main.py https://www.example.cz/
python main.py https://example.cz/ --seo        # i SEO kontroly
python main.py https://example.cz/ --exclude "/blog/*" --exclude "/en/*"
python main.py https://example.cz/ --no-interactive --fail-under 80   # pro CI
```

| Parametr | Výchozí | Co dělá |
|---|---|---|
| `url` | ptá se | web k auditu |
| `--max-pages N` | 500 | max počet stránek |
| `--delay S` | 1.0 / 0.5 | pauza mezi requesty (crawler / stahování), na localhostu žádná |
| `--exclude VZOR` | – | vynechá URL podle vzoru (`/blog/*`, `*.pdf`), jde opakovat |
| `--output CESTA` | `excel reporty/<web>_validator.xlsx` | kam uložit Excel (soubor nebo složka) |
| `--keep` | – | nepřepisovat starý report, přidá se datum a čas |
| `--json CESTA` | vedle Excelu | kam uložit JSON |
| `--seo` | vypnuto | zapne SEO kontroly |
| `--check-external` | vypnuto | ověřuje i odkazy na cizí weby |
| `--fail-under N` | – | exit kód 1, když je skóre pod N |
| `--no-interactive` | – | na nic se neptá |
| `--no-server` | – | vnu.jar nepoběží jako server (pomalejší) |
| `--no-update-check` | – | nekontroluje novou verzi vnu.jar |

Exit kódy: `0` hotovo, `1` skóre pod `--fail-under` nebo žádné stránky,
`2` špatné argumenty, `130` Ctrl+C.

Report se při každém běhu přepíše. Když je zrovna otevřený v Excelu, uloží se
vedle s datem v názvu.

## Co se kontroluje

### W3C validace

Každá stránka jde přes lokální `vnu.jar`, nic se nikam neposílá. Výsledek je
OK / varování / chyby a u každé stránky je odkaz na validator.w3.org.

Nahoře v Excelu je tabulka nejčastějších chyb přes celý web (max 30). Hodí se
u webů ze šablony: 381 stránek s chybou často znamená 2 chyby v šabloně.
Varování se do ní nepočítají.

### Testovací obsah

Hledá se ve viditelném textu, `<title>`, meta description, `alt` a OG tazích.
Obsah `<code>`, `<pre>`, `<script>`, `<style>`, `<textarea>` a `<template>`
se přeskakuje. Hlavní je nemít falešné poplachy, proto se většinou porovnává
celý text prvku, ne jen jeho kus.

| Co | Příklad |
|---|---|
| zakázané fráze | lorem ipsum, asdf, qwerty, „vložte text“ |
| výchozí texty v meta | `<title>Document</title>`, `alt="image"`, `og:title="Home"` |
| zástupné obrázky | placeholder.com, picsum.photos, `/img/dummy.jpg`, `test-1.png` |
| šablonové proměnné | `{{ user.name }}`, `{% %}`, `[[ ]]`, `%NAME%`, `${}`, `<?php` |
| JS hodnoty | `NaN Kč`, `undefined`, `null`, `[object Object]` |
| výpisy chyb | PHP Warning / Fatal error, Stack trace, SQL chyby, Tracy, Whoops |
| výchozí texty CMS | „Hello world!“, „Sample Page“, „Text odstavce“, „Web je ve výstavbě“ |

Lazy-load (`src="placeholder.png" data-src="foto.jpg"`) se nehlásí, stejně
jako `{{ }}` uvnitř Vue/Angular/Alpine komponent.

### Dostupnost

- **Nefunkční odkazy a obrázky.** Ze všech stránek se posbírají `<a href>`,
  `<img src>` a `og:image` a ověří se HEAD requestem. Cizí domény jen
  s `--check-external` (a i pak se 401/403/429 nebere jako chyba, to jen
  server nepustil bota).
- **Přesměrování** – odkazy, které vedou přes 301/302. Jen pro informaci,
  do skóre se nepočítají. Přesměrování kvůli `http→https`, `www.` nebo
  lomítku na konci jsou až dole.
- **Soft 404** – stránka vrátí 200, ale v titulku nebo H1 má „Stránka
  nenalezena“. Posuzuje se jen titulek a nadpis, takže článek „Jak nastavit
  404 stránku“ ani „Byt 4+kk, 404 m²“ se nehlásí.
- **Test 404 stránky** – jeden request na neexistující URL. Má přijít 404
  nebo 410. Hlásí se 200, přesměrování na homepage a 5xx.
- **Prázdné odkazy** – `href="#"`, `href=""`, `javascript:void(0)` bez
  jakékoli třídy, id, `data-*`, `onclick`… (ovladače menu se nehlásí).
- **Sitemap** – URL v sitemapě, které vrací chybu nebo se přesměrovávají.

U velkých webů (e-shop s filtry měl 13 000 unikátních odkazů na 400
stránkách) platí limity:

- URL s parametry se slučují (`/kremy/?p13[0]=5` → `/kremy/`), kromě
  skriptů typu `index.php?id=5`.
- Ověří se max 1 500 cílů a fáze má 10 minut. Co se nestihne, je
  „neověřeno“, ne „nefunkční“.
- Když spojení spadne (timeout apod.), zkusí se to po 2 s ještě jednou,
  tentokrát po jednom. Některé servery nezvládnou souběžné requesty. Druhé
  kolo má limit 50 cílů.
- Po 15 síťových chybách za sebou se fáze ukončí (spadla Wi-Fi) a nic se
  nepočítá jako chyba.

Konstanty jsou v `config.py` (`LINK_CHECK_*`).

### Bot ochrana

Některé weby mají ochranu (Anubis, Cloudflare, DDoS-Guard…), která místo
obsahu vrátí ověřovací stránku, a to s kódem 200. Proto program posílá
vlastní User-Agent bez „Mozilla“ a `Accept-Language`, se kterými se většinou
projde. Když ověřovací stránka přesto přijde, nevaliduje se, stránka se
hlásí jako nedostupná a v terminálu i Excelu svítí „AUDIT NENÍ PLATNÝ“.

### Struktura HTML

Na každé stránce: jeden `<h1>`, `<title>`, `lang` na `<html>`, meta viewport,
duplicitní `id`, prázdné tagy, `http://` odkazy na https webu, `noindex`
na produkci, odkazy na dev/staging domény.

### robots.txt

- `Disallow: /` pro Googlebot nebo `*` (zapomenuté ze stagingu),
- blokování `.js` / `.css`, které Google potřebuje k vykreslení stránky.

Přeskakuje se na lokálních a firemních interních doménách.

Dál se zkouší, jestli existuje `/uzivatel/`.

### SEO (`--seo`)

Meta description, canonical (chybí / míří jinam / `http://`), Open Graph,
duplicitní `<title>` na více stránkách, `alt`, `<img>` bez rozměrů, obrázky
nad 500 kB, externí odkazy bez `rel="noopener"`, přeskočené úrovně nadpisů
a délka title (30–60 znaků) a description (70–160) na homepage.

Bez `--seo` se tohle vůbec nehlásí ani nepočítá do skóre. Které typy patří
do SEO, je v `issues.SEO_ISSUE_TYPES`.

## Skóre

Každá stránka začíná na 100 a za problémy se odečítá. Skóre webu je průměr
stránek. 80+ je dobré, 60–79 průměr, pod 60 špatné. Nedostupná stránka má 0.

Hlavní penalizace (celý seznam a váhy jsou nahoře v `stats.py`):

| Problém | Body |
|---|---|
| `noindex` na produkci | −25 |
| testovací obsah, výpis PHP chyby | −20 |
| šablonové proměnné, výchozí texty CMS, chybí `<h1>` / `<title>` / viewport | −15 |
| chybí `lang`, soft 404, JS hodnoty v textu, výchozí meta texty | −10 |
| duplicitní `<h1>` | −8 |
| W3C chyba | −2 za kus, max −20 |
| odkaz na staging | −8 za kus, max −20 |
| nefunkční odkaz, duplicitní `id` | −3 za kus, max −15 |
| nedostupný obrázek, `http://` odkaz | −2 za kus |
| prázdný odkaz / prázdný tag | −1 / −0,5 za kus |

W3C varování se nepočítají. SEO problémy jen s `--seo`.

## Excel a JSON

Excel jde do `excel reporty/`. Obsahuje souhrn se skóre, změny od minulého
běhu, W3C (nejčastější chyby + stránky), strukturu, SEO (jen s `--seo`),
nefunkční odkazy a přesměrování, obrázky, nedostupné stránky, sitemapu,
robots.txt, test 404 a `/uzivatel/`.

Vedle se vždy uloží JSON se stejným jménem. Při dalším běhu na stejný web
se z něj porovná, co přibylo a co se opravilo:

```
Změna od minula    : 72 → 85 (+13)  |  opraveno 12, nové 3
```

Porovnávají se jen stránky, které jsou v obou bězích. Když jen jeden z běhů
měl `--seo`, SEO problémy se při porovnání ignorují.

## Jak se hledají stránky

1. `Sitemap:` v robots.txt, pak `/sitemap.xml`, `/sitemap_index.xml`…
2. Sitemap index se rozbalí (max 3 úrovně).
3. 10+ URL → jen sitemapa. 1–9 URL → sitemapa + crawler doplní zbytek.
   Nic → crawler od homepage (respektuje robots.txt).

vnu.jar běží jako lokální server, takže se JVM nestartuje pro každou stránku
zvlášť (10–50× rychlejší). Když server nenaběhne, přepne se na spouštění
po jednom. Na konci běhu se vypíše, jak dlouho trvaly jednotlivé fáze.

## Soubory

```
main.py               spuštění, CLI, průběh auditu
config.py             konstanty a načtení firemni_domeny.txt
crawler.py, sitemap.py           hledání stránek
validator_w3c.py, updater.py     vnu.jar (validace, aktualizace)
structure_check.py    HTML kontroly
content_check.py      testovací obsah
links_check.py        odkazy a obrázky
availability_check.py soft 404, test 404, bot ochrana
robots_check.py       robots.txt a /uzivatel/
stats.py, issues.py   skóre a typy problémů
report_excel.py, report_json.py  výstupy
ui.py, colors.py      terminál
tests/                testy (398)
```

## Testy

```bash
python -m unittest discover tests/
```

Testy jedou bez sítě, requesty jsou mockované.

---

*Vytvořil Péťa*
