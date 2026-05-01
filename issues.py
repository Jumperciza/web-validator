"""
Strukturovaná reprezentace problémů nalezených v HTML.

Dřív bylo issue plain string a text-parsing v report_excel.py to řešil regexy.
Teď je to dataclass — typované, testovatelné, snadno rozšiřitelné.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List


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
    OTHER             = "other"


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