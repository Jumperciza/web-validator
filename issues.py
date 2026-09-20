"""
Strukturovaná reprezentace problémů nalezených v HTML.

Dřív bylo issue plain string a text-parsing v report_excel.py to řešil regexy.
Teď je to dataclass — typované, testovatelné, snadno rozšiřitelné.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List

from config import IMAGE_MAX_KB


class IssueType(Enum):
    """Kategorie HTML strukturálních problémů."""
    MISSING_H1        = "missing_h1"
    MULTIPLE_H1       = "multiple_h1"
    HEADING_SKIP      = "heading_skip"
    EMPTY_TAG         = "empty_tag"
    DUPLICATE_ID      = "duplicate_id"
    MISSING_META_DESC = "missing_meta_desc"
    EMPTY_META_DESC   = "empty_meta_desc"
    MISSING_ALT       = "missing_alt"
    HTTP_LINK         = "http_link"
    EXTERNAL_LINK     = "external_link"      # target/noopener
    FORBIDDEN_CONTENT = "forbidden_content"  # lorem ipsum atd.
    MISSING_LANG      = "missing_lang"
    MISSING_VIEWPORT  = "missing_viewport"
    NOINDEX           = "noindex"
    STAGING_URL       = "staging_url"
    MISSING_TITLE     = "missing_title"       # <title> chybí nebo je prázdný
    DUPLICATE_TITLE   = "duplicate_title"     # stejný <title> na více stránkách (celý web)
    MISSING_CANONICAL = "missing_canonical"
    CANONICAL_MISMATCH = "canonical_mismatch" # canonical míří jinam než na sebe
    CANONICAL_HTTP    = "canonical_http"      # http:// canonical na https stránce
    MISSING_OG        = "missing_og"          # chybí og:title / og:description / og:image
    IMG_NO_DIMENSIONS = "img_no_dimensions"   # <img> bez width/height → CLS
    BROKEN_LINK       = "broken_link"         # interní odkaz vrací 404 / je nedostupný
    IMG_BROKEN        = "img_broken"          # obrázek vrací 404 / je nedostupný
    IMG_TOO_LARGE     = "img_too_large"       # obrázek nad IMAGE_MAX_KB
    # Testovací / zástupný obsah – rozšíření (content_check.py)
    DEFAULT_META_TEXT = "default_meta_text"   # <title>/description/alt/og = výchozí hodnota
    PLACEHOLDER_IMAGE = "placeholder_image"   # via.placeholder.com, picsum.photos, dummy.jpg…
    TEMPLATE_VARIABLE = "template_variable"   # {{ name }}, {% %}, %NAME%, <?php v textu
    JS_VALUE_IN_TEXT  = "js_value_in_text"    # undefined / null / NaN / [object Object]
    DEV_ERROR_OUTPUT  = "dev_error_output"    # PHP Fatal error, Warning … on line, Stack trace
    DEFAULT_CMS_TEXT  = "default_cms_text"    # Hello world!, Just another WordPress site…
    # Dostupnost (availability_check.py + structure_check.py)
    SOFT_404          = "soft_404"            # HTTP 200, ale title/h1 = „Stránka nenalezena“
    EMPTY_HREF        = "empty_href"          # <a href="#"> / href="" / javascript:void(0) s textem
    OTHER             = "other"


# SEO modul – tyto typy se hledají a hlásí jen s přepínačem `--seo`
# (kousek D, 2026-09-20). Jsou to věci, které nerozhodují o tom, zda web
# „funguje“, a které pokrývají samostatné SEO testy: meta description,
# canonical, Open Graph, alt texty (chybějící alt navíc hlásí i vnu.jar jako
# W3C chybu), rozměry a velikost obrázků, noopener u externích odkazů,
# pořadí nadpisů a duplicitní <title> napříč webem. Všechno ostatní je
# „jádro“ a běží vždy: W3C, testovací obsah, dostupnost, noindex, staging
# URL, H1, <title>, lang, viewport, prázdné tagy/odkazy, http:// odkazy.
SEO_ISSUE_TYPES = frozenset({
    IssueType.HEADING_SKIP,
    IssueType.MISSING_META_DESC,
    IssueType.EMPTY_META_DESC,
    IssueType.MISSING_ALT,
    IssueType.EXTERNAL_LINK,
    IssueType.DUPLICATE_TITLE,
    IssueType.MISSING_CANONICAL,
    IssueType.CANONICAL_MISMATCH,
    IssueType.CANONICAL_HTTP,
    IssueType.MISSING_OG,
    IssueType.IMG_NO_DIMENSIONS,
    IssueType.IMG_TOO_LARGE,
})


def is_seo_issue(issue) -> bool:
    """True pro Issue (nebo IssueType) patřící do SEO modulu."""
    itype = issue if isinstance(issue, IssueType) else getattr(issue, "type", None)
    return itype in SEO_ISSUE_TYPES


# Český popis každého typu (pro zobrazení v reportu)
ISSUE_LABELS = {
    IssueType.MISSING_H1:        "Chybí <h1> tag",
    IssueType.MULTIPLE_H1:       "Duplicitní <h1> tag",
    IssueType.HEADING_SKIP:      "Přeskočení úrovně nadpisů",
    IssueType.EMPTY_TAG:         "Prázdné tagy",
    IssueType.DUPLICATE_ID:      "Duplicitní ID",
    IssueType.MISSING_META_DESC: "Chybí meta description",
    IssueType.EMPTY_META_DESC:   "Prázdná meta description",
    IssueType.MISSING_ALT:       "Chybějící alt texty",
    IssueType.HTTP_LINK:         "HTTP odkazy (nezabezpečené)",
    IssueType.EXTERNAL_LINK:     "Externí odkazy bez target/noopener",
    IssueType.FORBIDDEN_CONTENT: "Testovací / zástupný obsah",
    IssueType.MISSING_LANG:      "Chybějící lang atribut na <html>",
    IssueType.MISSING_VIEWPORT:  "Chybějící meta viewport",
    IssueType.NOINDEX:           "Stránka má noindex (nebude indexována Googlem)",
    IssueType.STAGING_URL:       "Odkaz na staging/dev doménu v HTML",
    IssueType.MISSING_TITLE:     "Chybí nebo prázdný <title>",
    IssueType.DUPLICATE_TITLE:   "Duplicitní <title> (stejný na více stránkách)",
    IssueType.MISSING_CANONICAL: "Chybí <link rel=\"canonical\">",
    IssueType.CANONICAL_MISMATCH: "Canonical míří na jinou URL než je stránka",
    IssueType.CANONICAL_HTTP:    "Canonical používá http:// na https stránce",
    IssueType.MISSING_OG:        "Chybí Open Graph meta (og:title / og:description / og:image)",
    IssueType.IMG_NO_DIMENSIONS: "Obrázky bez width/height (posun layoutu – CLS)",
    IssueType.BROKEN_LINK:       "Nefunkční odkazy (404 / nedostupné)",
    IssueType.IMG_BROKEN:        "Nedostupné obrázky (404)",
    IssueType.IMG_TOO_LARGE:     f"Příliš velké obrázky (nad {IMAGE_MAX_KB} kB)",
    IssueType.DEFAULT_META_TEXT: "Výchozí / nevyplněný text v <title>, description, alt nebo og:*",
    IssueType.PLACEHOLDER_IMAGE: "Zástupné obrázky (placeholder služby, dummy/sample soubory)",
    IssueType.TEMPLATE_VARIABLE: "Nevyrenderované šablonové proměnné v textu ({{ }}, {% %}, %X%, <?php)",
    IssueType.JS_VALUE_IN_TEXT:  "JavaScriptové hodnoty v textu (undefined / null / NaN / [object Object])",
    IssueType.DEV_ERROR_OUTPUT:  "Vývojářský výpis chyby v HTML (PHP Warning / Fatal error / Stack trace…)",
    IssueType.DEFAULT_CMS_TEXT:  "Výchozí text CMS / šablony (Hello world!, Sample Page, Text odstavce…)",
    IssueType.SOFT_404:          "Soft 404 – stránka hlásí „nenalezeno“, ale vrací HTTP 200",
    IssueType.EMPTY_HREF:        "Prázdné odkazy (href=\"#\", href=\"\", javascript:void(0)) – odkaz nikam nevede",
    IssueType.OTHER:             "Ostatní problémy",
}


@dataclass
class Issue:
    """
    Jeden problém nalezený na stránce.

    Příklad:
        Issue(type=IssueType.EMPTY_TAG, tag="div", count=5)
        Issue(type=IssueType.MISSING_ALT, items=["/logo.png", "/banner.jpg"])
        Issue(type=IssueType.EXTERNAL_LINK,
              items=["https://fb.com  [chybí: rel=noopener]"])
    """
    type: IssueType
    # Volitelný název tagu (pro EMPTY_TAG)
    tag: str = ""
    # Počet výskytů (celkem, i když items je oříznutý)
    count: int = 0
    # Konkrétní problémové položky (URL, img src, zakázaná slova, …)
    items: List[str] = field(default_factory=list)
    # Volitelná extra info (např. "H1 → H3" pro skip nadpisů)
    detail: str = ""

    @property
    def label(self) -> str:
        """Český popis včetně detailů (např. 'Prázdné tagy <div>')."""
        base = ISSUE_LABELS.get(self.type, str(self.type.value))
        if self.type == IssueType.EMPTY_TAG and self.tag:
            return f"{base} <{self.tag}>"
        if self.type == IssueType.DUPLICATE_TITLE and self.detail:
            # Excel seskupuje podle labelu → jeden řádek na každý duplicitní
            # title, ne jeden společný řádek pro všechny duplicity.
            return f'{base}: "{self.detail}"'
        return base

    @property
    def total_count(self) -> int:
        """Vrátí count pokud je zadaný, jinak délku items."""
        return self.count if self.count else len(self.items)

    def to_dict(self) -> dict:
        """Serializace pro JSON export."""
        return {
            "type":   self.type.value,
            "label":  self.label,
            "tag":    self.tag,
            "count":  self.total_count,
            "items":  self.items,
            "detail": self.detail,
        }