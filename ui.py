"""
Terminál UI helpers — placeholder input, URL prompt, banner.
Extrahováno z main.py aby bylo hlavní orchestrace čistší.
"""
import re
import sys

from colors import ok, warn, err, info, bold, gray

# ── URL validace ─────────────────────────────────────────────────────────────

_URL_PLACEHOLDER = "https://www.example.cz/"
# Přísnější regex — nedovolí nepovolené znaky v URL (mezery, newlines…)
_URL_RE = re.compile(
    r'^https?://'
    r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+'   # subdoména + doména
    r'[a-zA-Z]{2,63}'                                      # TLD
    r'(?:/[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]*)?$',    # validní URL znaky
    re.IGNORECASE,
)


def is_valid_url(url: str) -> bool:
    """Ověří že URL vypadá smysluplně (má doménu, TLD, validní znaky)."""
    return bool(_URL_RE.match(url))


def normalize_url_input(raw: str) -> str:
    """Přidá https:// pokud chybí."""
    raw = raw.strip()
    if raw and not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


# ── Placeholder input ────────────────────────────────────────────────────────

def _read_with_placeholder(prompt: str, placeholder: str) -> str:
    """
    Čte řádek vstupu se šedým placeholder textem který zmizí při prvním stisku.
    Funguje na Windows (msvcrt) i Unix (termios).
    Při piped stdin nebo non-TTY padne zpět na plain input().
    """
    if not sys.stdin.isatty():
        return input(prompt)

    if sys.platform == "win32":
        return _read_windows(prompt, placeholder)
    else:
        return _read_unix(prompt, placeholder)


def _read_windows(prompt: str, placeholder: str) -> str:
    import msvcrt
    sys.stdout.write(prompt); sys.stdout.flush()
    gray(placeholder)

    chars: list[str] = []
    ph_active = True

    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            sys.stdout.write("\n"); sys.stdout.flush()
            return "".join(chars)
        elif ch == "\x08":
            if chars:
                chars.pop()
                sys.stdout.write("\b \b"); sys.stdout.flush()
        elif ch == "\x03":
            raise KeyboardInterrupt
        elif ch in ("\x00", "\xe0"):
            msvcrt.getwch()
        else:
            if ph_active:
                line = prompt + placeholder
                sys.stdout.write("\r" + " " * len(line) + "\r" + prompt)
                sys.stdout.flush()
                ph_active = False
            chars.append(ch)
            sys.stdout.write(ch); sys.stdout.flush()


def _read_unix(prompt: str, placeholder: str) -> str:
    import termios, tty, select

    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    sys.stdout.write(f"{prompt}\033[90m{placeholder}\033[0m")
    sys.stdout.flush()

    chars: list[str] = []
    ph_active = True

    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                sys.stdout.write("\r\n"); sys.stdout.flush()
                return "".join(chars)
            elif ch in ("\x7f", "\x08"):
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b"); sys.stdout.flush()
            elif ch == "\x03":
                raise KeyboardInterrupt
            elif ch == "\x1b":
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    sys.stdin.read(1)
                    if select.select([sys.stdin], [], [], 0.05)[0]:
                        sys.stdin.read(1)
            else:
                if ph_active:
                    line_len = len(prompt) + len(placeholder)
                    sys.stdout.write("\r" + " " * line_len + "\r" + prompt)
                    sys.stdout.flush()
                    ph_active = False
                chars.append(ch)
                sys.stdout.write(ch); sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def prompt_url() -> str:
    """
    Interaktivně se ptá na URL dokud nezadá validní.
    Přidá https:// pokud chybí.
    """
    while True:
        try:
            raw = _read_with_placeholder("  Zadej web: ", _URL_PLACEHOLDER).strip()
        except (KeyboardInterrupt, EOFError):
            sys.stdout.write("\n"); sys.exit(0)

        if not raw:
            warn("  [!]"); print(" Zadej prosím URL webu.")
            continue

        raw = normalize_url_input(raw)

        if not is_valid_url(raw):
            err("  [✗]"); print(f" '{raw}' nevypadá jako platná URL.")
            sys.stdout.write("     Zkus např. https://www.example.cz/\n")
            sys.stdout.flush()
            continue

        return raw


# ── Banner ───────────────────────────────────────────────────────────────────

def print_banner() -> None:
    """Úvodní banner při spuštění."""
    print()
    info("=" * 62); print()
    print("  "); bold("Web Validator"); gray("  |  vytvořil Péťa"); print()
    info("=" * 62); print()
    print(f"  {bold('Co kontroluje:')} ")
    print(f"    "); ok("1."); print(" W3C validace HTML (přes lokální vnu.jar)")
    print(f"    "); ok("2."); print(" Struktura HTML:")
    for item in ["existence a duplikáty <h1>",
                 "pořadí nadpisů (žádné přeskočení)",
                 "prázdné tagy", "duplicitní ID", "meta description",
                 "alt texty u obrázků", "HTTP odkazy (místo HTTPS)",
                 "externí odkazy bez target/_blank/noopener",
                 "testovací/zástupný obsah (lorem ipsum, asdf…)",
                 "lang atribut na <html>", "meta viewport",
                 "noindex meta tag (mimo dev domény)"]:
        gray(f"       - {item}"); print()
    print(f"    "); ok("3."); print(" Meta title a description délka (jen homepage)")
    print(f"    "); ok("4."); print(" Kontrola robots.txt – blokování CSS/JS pro Googlebot")
    print(f"    "); ok("5."); print(" Kontrola existence uživatelské sekce (/uzivatel/)")
    info("=" * 62); print()
    print()


# ── Stdout helpery ───────────────────────────────────────────────────────────

def write(text: str) -> None:
    """Krátké psaní do stdout s flush."""
    sys.stdout.write(text); sys.stdout.flush()


def write_line(label: str, color_fn, value) -> None:
    """Formátovaný řádek pro souhrn: "  Label : HODNOTA"."""
    write(f"  {label} ")
    color_fn(str(value))
    write("\n")