"""Barevný výstup do terminálu – Windows Console API + ANSI fallback."""
import sys
import ctypes

if sys.platform == "win32":
    _k32    = ctypes.windll.kernel32
    _stdout = _k32.GetStdHandle(-11)
    _COL    = {
        "reset": 7, "green": 10, "yellow": 14, "orange": 6,
        "red": 12,  "cyan": 11,  "gray": 8,    "blue": 9, "white": 15,
    }

    def _cp(text, color):
        _k32.SetConsoleTextAttribute(_stdout, _COL[color])
        sys.stdout.write(str(text)); sys.stdout.flush()
        _k32.SetConsoleTextAttribute(_stdout, _COL["reset"])

    def ok(s):   _cp(s, "green");  return ""
    def warn(s): _cp(s, "orange"); return ""
    def err(s):  _cp(s, "red");    return ""
    def info(s): _cp(s, "cyan");   return ""
    def bold(s): _cp(s, "white");  return ""
    def gray(s): _cp(s, "gray");   return ""
    def blue(s): _cp(s, "blue");   return ""
else:
    # ANSI escape kódy – stejné chování jako Windows verze:
    # printujeme přímo do stdout (s flushem) a vracíme "" pro zpětnou kompatibilitu.
    # Dříve tyto funkce jen vracely string s ANSI kódy, ale v kódu se volají
    # jako příkaz (např. `ok("text"); print(...)`), takže návratová hodnota
    # se zahazovala a text se na Linux/macOS vůbec nezobrazil.
    _ANSI = {
        "green":  "\033[92m",
        "orange": "\033[38;5;208m",
        "red":    "\033[91m",
        "cyan":   "\033[96m",
        "white":  "\033[1m",
        "gray":   "\033[90m",
        "blue":   "\033[94m",
        "reset":  "\033[0m",
    }

    def _cp(text, color):
        sys.stdout.write(f"{_ANSI[color]}{text}{_ANSI['reset']}")
        sys.stdout.flush()

    def ok(s):   _cp(s, "green");  return ""
    def warn(s): _cp(s, "orange"); return ""
    def err(s):  _cp(s, "red");    return ""
    def info(s): _cp(s, "cyan");   return ""
    def bold(s): _cp(s, "white");  return ""
    def gray(s): _cp(s, "gray");   return ""
    def blue(s): _cp(s, "blue");   return ""


def pocet_problemu(n: int) -> str:
    if n == 1:       return "1 problém"
    elif 2 <= n <= 4: return f"{n} problémy"
    else:             return f"{n} problémů"