"""W3C validace přes lokální vnu.jar"""
import json
import subprocess
import re
from pathlib import Path

from colors import ok, warn, err, gray

# Globální cesta k vnu.jar – nastaví se při startu
vnu_jar: str = ""


def find_vnu_jar() -> str:
    """Hledá vnu.jar ve složce skriptu a aktuálním adresáři."""
    script_dir = Path(__file__).resolve().parent
    for candidate in [script_dir / "vnu.jar", Path("vnu.jar")]:
        if candidate.exists():
            return str(candidate)
    return ""


def get_local_version(jar_path: str) -> str:
    """Zjistí verzi vnu.jar – ignoruje chybové hlášky Javy."""
    try:
        r = subprocess.run(
            ["java", "-jar", jar_path, "--version"],
            capture_output=True, timeout=15,
        )
        out = (r.stdout + r.stderr).decode("utf-8", errors="replace").strip()

        # Pokud Java hodí UnsupportedClassVersionError, verzi nezjistíme
        if "UnsupportedClassVersionError" in out or "Exception in thread" in out:
            return "java_too_old"

        # Verze vnu.jar je ve formátu YY.MM.DD nebo podobném
        # Ignorujeme čísla jako 52.0 / 55.0 (to jsou Java class file verze, ne vnu verze)
        for m in re.finditer(r"(\d{2,4}\.\d{1,2}\.\d{1,2})", out):
            return m.group(1)

        # Fallback – kratší číslo
        m = re.search(r"(\d{2,4}[\.\d]+)", out)
        return m.group(1) if m else out[:30] or "neznama"
    except FileNotFoundError:
        return "java_missing"
    except Exception:
        return "neznama"


def _classify(messages: list) -> dict:
    warnings, errors = [], []
    for msg in messages:
        t  = msg.get("type", "")
        st = (msg.get("subType") or msg.get("subtype") or "").lower()
        if t == "error":
            errors.append(msg)
        elif t == "info" and st == "warning":
            warnings.append(msg)
    return {"warnings": warnings, "errors": errors}


def validate(html_bytes: bytes, jar: str = "") -> dict:
    """Validuje HTML lokálně přes vnu.jar. Vrátí result dict."""
    jar_path = jar or vnu_jar
    if not jar_path:
        return {"category": "validator_error", "warnings": [], "errors": [],
                "error_msg": "vnu.jar nenalezen"}

    try:
        result = subprocess.run(
            ["java", "-jar", jar_path, "--format", "json",
             "--exit-zero-always", "-"],
            input=html_bytes,
            capture_output=True,
            timeout=60,
        )

        # Výstup je na stderr
        raw = result.stderr.decode("utf-8", errors="replace").strip()

        if not raw:
            raw = result.stdout.decode("utf-8", errors="replace").strip()

        if not raw:
            return {"category": "validator_error", "warnings": [], "errors": [],
                    "error_msg": f"vnu.jar prázdný výstup (returncode={result.returncode})"}

        # Detekce staré Javy
        if "UnsupportedClassVersionError" in raw:
            return {"category": "validator_error", "warnings": [], "errors": [],
                    "error_msg": "Java je příliš stará! vnu.jar vyžaduje Javu 11+. Stáhni na adoptium.net"}

        data = json.loads(raw)
        msgs = data.get("messages", [])

    except FileNotFoundError:
        return {"category": "validator_error", "warnings": [], "errors": [],
                "error_msg": "Java neni nainstalovana – stahni na java.com"}
    except json.JSONDecodeError as je:
        preview = (result.stderr + result.stdout).decode("utf-8", errors="replace")[:300]
        return {"category": "validator_error", "warnings": [], "errors": [],
                "error_msg": f"JSON chyba: {je} | vystup: {preview}"}
    except Exception as e:
        return {"category": "validator_error", "warnings": [], "errors": [],
                "error_msg": str(e)}

    c = _classify(msgs)
    w = c["warnings"]
    e = c["errors"]
    if e and w:   cat = "warning_error"
    elif e:       cat = "error"
    elif w:       cat = "warning"
    else:         cat = "ok"
    return {"category": cat, "warnings": w, "errors": e, "error_msg": None}