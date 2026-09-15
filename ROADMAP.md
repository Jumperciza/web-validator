# ROADMAP – nápady na rozšíření Web Validatoru

Pracovní seznam věcí, které by nástroj mohl umět navíc. Vznikl 2026-09-15 po
dokončení auditu chyb (19 bodů, vše opraveno – 190 testů, pyflakes čistý).

Pravidla práce se souborem:
- Hotová položka se označí `[x]` a doplní se datum + stručně co se udělalo,
  ať se nic nedělá dvakrát.
- Rozhodnutí, která ovlivňují chování (viz "Otevřená rozhodnutí"), se sem zapíšou,
  jakmile padnou.
- Soubor nemá vliv na kód ani testy – je to jen poznámkový blok projektu.

---

## Otevřená rozhodnutí (čeká se na Péťu)

- [x] **Přepisování reportu** – *rozhodnuto 2026-09-15 (Claude, Péťa nechal
      výběr na mně): výchozí chování zůstává přepisování*, `--keep` přidá
      timestamp, `--output` vlastní cestu. Důvod: report je snímek aktuálního
      stavu; při opakovaném běhu během oprav by se hromadily soubory, ze
      kterých je aktuální jen poslední. Historii řeší `--keep` (a v budoucnu
      položka 9/10 přes JSON), zamčený soubor už dřív řešil fallback s timestampem.
- [ ] **`--delay` pro fázi stahování** – `--delay` platí jen pro crawler,
      stahování stránek k validaci má pevnou pauzu `config.FETCH_DELAY` (0.5 s).
      Nechat, nebo sjednotit pod `--delay`?

---

## 🟢 Malé, ale hodně užitečné (~1 h každé)

_(vše hotovo 2026-09-15 – viz sekce Hotovo)_

## 🟡 Střední (~půl dne)

- [ ] **6. Broken links (404) uvnitř webu** – crawler už vidí všechny interní
      odkazy; evidovat status a do Excelu přidat sekci „Nefunkční odkazy“
      (odkud → kam → status). Externí odkazy volitelně přes HEAD.
- [ ] **7. Kontrola obrázků** – velikost > 500 kB, chybějící `width`/`height`
      (CLS), nedostupné `src` (404). Navazuje na 6.
- [ ] **8. Open Graph meta** – `og:title`, `og:description`, `og:image`
      (+ existence obrázku). Pro klientské weby sdílené na sociálních sítích.
- [ ] **9. Porovnání s předchozím reportem** – při dalším běhu na stejnou
      doménu načíst minulý výsledek a ukázat „skóre 72 → 85, opraveno 12,
      nové 3“. Vyžaduje 10.
- [ ] **10. JSON export** – `--json`; `Issue.to_dict()` už existuje a nikde se
      nepoužívá. Základ pro 9 a pro napojení na cokoliv dalšího.

## 🔵 Větší (den+), jen kdyby bylo opravdu potřeba

- [ ] **11. Mixed content / HTTPS** – http `src` u `img`/`script`/`iframe`
      (dnes jen `<a href>`), přesměrování http → https, HSTS, platnost
      certifikátu.
- [ ] **12. Bezpečnostní hlavičky** – CSP, `X-Frame-Options`,
      `X-Content-Type-Options`, `Referrer-Policy`. Jeden request na homepage,
      badge v reportu.
- [ ] **13. Rychlost / velikost stránky** – doba odezvy, velikost HTML, počet
      requestů. Bez JS enginu jen hrubý odhad; pořádně = PageSpeed API
      (nová závislost).
- [ ] **14. Souhrnný list v Excelu** – druhý sheet „stránka × typ problému ×
      počet“ pro filtrování. Dnešní report je jeden dlouhý list, u 300 stránek
      se v něm špatně hledá.
- [ ] **15. Konfigurační soubor** (`validator.toml`) – domény pro skip
      robots/noindex jsou natvrdo v `config.py` (`poski.com`, `poskireal.cz`…).

## ❌ Záměrně nedělat

- Screenshoty / render přes headless Chrome – velká závislost, pomalé, křehké.
- Plný accessibility audit (WCAG) – samostatný projekt (axe-core), lépe volat
  externě.

---

## Doporučené pořadí na příště

1. **6** broken links
2. **10** JSON export (základ pro 9)
3. **14** souhrnný list v Excelu

Nejvíc přínosu za nejméně práce, bez nových závislostí.

## Hotovo

- [x] **1. `<title>` na každé stránce** – 2026-09-15. `IssueType.MISSING_TITLE`
      (kontrola 14 v `check_structure`, −15) + `DUPLICATE_TITLE` napříč webem
      (`structure_check.mark_duplicate_titles`, volá se v `main.validate_pages`
      po zpracování všech stránek, −5 na každé postižené stránce; label
      obsahuje text titulku → v Excelu jeden řádek na duplicitní titulek).
      `extract_title()` parsuje jen část po `</head>`, ignoruje `<svg><title>`.
- [x] **2. Canonical kontrola** – 2026-09-15. Kontrola 15: `MISSING_CANONICAL`
      (−3), `CANONICAL_MISMATCH` (−10; jiná URL po normalizaci bez www/lomítka,
      nebo víc canonicalů), `CANONICAL_HTTP` (−8; `http://` na https stránce).
      Přeskočeno na dev/lokálních doménách; canonical na staging hlásí jen
      kontrola 13 (bez dvojí penalizace).
- [x] **3. `--output` / `--keep`** – 2026-09-15. `main.build_output_path()`:
      `--output` soubor `.xlsx` nebo adresář, `--keep` přidá
      `_YYYYMMDD_HHMMSS` (stejný formát jako fallback při zamčeném souboru).
- [x] **4. Exit kód** – 2026-09-15. `--fail-under N` (0–100, jinak chyba
      argumentů = exit 2); `main()` vrací exit kód, `sys.exit(main())`.
      Exit 1 také když nejsou žádné stránky k auditu (dřív 0).
- [x] **5. `--exclude`** – 2026-09-15. `crawler.is_excluded()` – glob
      (`fnmatchcase`, case-insensitive) proti cestě i celé URL; opakovatelné
      nebo čárkou; aplikuje se v crawleru (URL se nestahuje ani neprochází) i v
      sitemapě (před limitem `max_urls`). Vzory se propíší do "Zdroj URL" v reportu.

Stav po této části: 228 testů, pyflakes čistý, README aktualizované.
