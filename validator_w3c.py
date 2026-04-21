"""
W3C validace přes lokální vnu.jar.

Dva režimy:
  1. Server mód (preferovaný) — spustí vnu.jar jako HTTP server na localhost,
     každá validace = jeden HTTP request místo nového JVM procesu.
     Rychlost: ~10-50× rychlejší pro velké weby.
  2. Subprocess mód (fallback) — pro každou stránku nové `java -jar`.
     Funguje i když server selže, ale pomalý (~1s JVM startup každé stránce).

Automatický fallback: pokud server mód selže při startu, přepneme na subprocess.
"""
import atexit
import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

import requests

from colors import gray, warn
from config import W3C_TIMEOUT

# Globální cesta k vnu.jar – nastaví se při startu
vnu_jar: str = ""

# ── Server mód state ─────────────────────────────────────────────────────────
_server_proc: subprocess.Popen | None = None
_server_port: int                     = 0
_server_lock = threading.Lock()


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

        if "UnsupportedClassVersionError" in out or "Exception in thread" in out:
            return "java_too_old"

        for m in re.finditer(r"(\d{2,4}\.\d{1,2}\.\d{1,2})", out):
            return m.group(1)

        m = re.search(r"(\d{2,4}[\.\d]+)", out)
        return m.group(1) if m else out[:30] or "neznama"
    except FileNotFoundError:
        return "java_missing"
    except Exception:
        return "neznama"


# ── Server mód ───────────────────────────────────────────────────────────────

def _find_free_port() -> int:
    """Najde volný TCP port pro vnu server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 15.0) -> bool:
    """Čeká až server začne přijímat spojení. True = nastartoval, False = timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.2)
    return False


def start_server(jar_path: str) -> bool:
    """
    Spustí vnu.jar jako HTTP server na localhost.
    Vrátí True pokud server úspěšně nastartoval.

    Server běží na pozadí a ukončí se při exitu programu (atexit hook).
    """
    global _server_proc, _server_port

    with _server_lock:
        if _server_proc is not None and _server_proc.poll() is None:
            return True   # Už běží

        port = _find_free_port()

        try:
            # Spustit v tichém módu (žádný stdout z vnu.jar aby nezaplavoval terminál)
            _server_proc = subprocess.Popen(
                ["java", "-cp", jar_path, "nu.validator.servlet.Main", str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Windows: izolovaná process group aby Ctrl+C nezabil server přímo
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                              if os.name == "nt" else 0,
            )
        except FileNotFoundError:
            return False   # Java není k dispozici
        except Exception:
            return False

        if not _wait_for_server(port, timeout=20):
            stop_server()
            return False

        _server_port = port

        # Registruj cleanup při exitu
        atexit.register(stop_server)
        return True


def stop_server() -> None:
    """Ukončí vnu.jar server pokud běží."""
    global _server_proc, _server_port
    with _server_lock:
        if _server_proc is None:
            return
        try:
            _server_proc.terminate()
            try:
                _server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _server_proc.kill()
                _server_proc.wait(timeout=2)
        except Exception:
            pass
        _server_proc = None
        _server_port = 0


def _validate_via_server(html_bytes: bytes) -> dict | None:
    """
    Pošle HTML na běžící vnu server přes HTTP.
    Vrátí raw JSON dict nebo None při chybě → fallback na subprocess.
    """
    if _server_port == 0:
        return None
    try:
        resp = requests.post(
            f"http://127.0.0.1:{_server_port}/?out=json",
            data=html_bytes,
            headers={"Content-Type": "text/html; charset=utf-8"},
            timeout=W3C_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        return None
    return None


# ── Klasifikace zpráv ────────────────────────────────────────────────────────

def _classify(messages: list) -> dict:
    """
    Rozděl zprávy vnu.jar na varování a chyby.

    Ukládáme jen kompaktní info (type, message, line) místo celých dictů —
    úspora paměti pro velké weby.
    """
    warnings, errors = [], []
    for msg in messages:
        t  = msg.get("type", "")
        st = (msg.get("subType") or msg.get("subtype") or "").lower()

        # Kompaktní reprezentace — stačí pro count a případný detail
        compact = {
            "message": msg.get("message", "")[:200],
            "line":    msg.get("lastLine") or msg.get("firstLine"),
        }

        if t == "error":
            errors.append(compact)
        elif t == "info" and st == "warning":
            warnings.append(compact)
    return {"warnings": warnings, "errors": errors}


# ── Subprocess fallback ──────────────────────────────────────────────────────

def _validate_via_subprocess(html_bytes: bytes, jar_path: str) -> dict:
    """Fallback — spustí `java -jar vnu.jar` pro každou stránku."""
    try:
        result = subprocess.run(
            ["java", "-jar", jar_path, "--format", "json",
             "--exit-zero-always", "-"],
            input=html_bytes,
            capture_output=True,
            timeout=W3C_TIMEOUT,
        )

        raw = result.stderr.decode("utf-8", errors="replace").strip()
        if not raw:
            raw = result.stdout.decode("utf-8", errors="replace").strip()

        if not raw:
            return {"category": "validator_error", "warnings": [], "errors": [],
                    "error_msg": f"vnu.jar prázdný výstup (returncode={result.returncode})"}

        if "UnsupportedClassVersionError" in raw:
            return {"category": "validator_error", "warnings": [], "errors": [],
                    "error_msg": "Java je příliš stará! vnu.jar vyžaduje Javu 11+. Stáhni na adoptium.net"}

        data = json.loads(raw)
        return _build_result(data.get("messages", []))

    except FileNotFoundError:
        return {"category": "validator_error", "warnings": [], "errors": [],
                "error_msg": "Java neni nainstalovana – stahni na adoptium.net"}
    except json.JSONDecodeError as je:
        preview = (result.stderr + result.stdout).decode("utf-8", errors="replace")[:300]
        return {"category": "validator_error", "warnings": [], "errors": [],
                "error_msg": f"JSON chyba: {je} | vystup: {preview}"}
    except Exception as e:
        return {"category": "validator_error", "warnings": [], "errors": [],
                "error_msg": str(e)}


def _build_result(messages: list) -> dict:
    """Sestaví final result dict z list zpráv."""
    c = _classify(messages)
    w, e = c["warnings"], c["errors"]
    if e and w:   cat = "warning_error"
    elif e:       cat = "error"
    elif w:       cat = "warning"
    else:         cat = "ok"
    return {"category": cat, "warnings": w, "errors": e, "error_msg": None}


# ── Hlavní API ───────────────────────────────────────────────────────────────

def validate(html_bytes: bytes, jar: str = "") -> dict:
    """
    Validuje HTML přes vnu.jar. Vrátí result dict.
    Pokud server mód běží, použije ho. Jinak fallback na subprocess.
    """
    jar_path = jar or vnu_jar
    if not jar_path:
        return {"category": "validator_error", "warnings": [], "errors": [],
                "error_msg": "vnu.jar nenalezen"}

    # Zkus server mód (pokud je server spuštěný)
    if _server_port != 0:
        data = _validate_via_server(html_bytes)
        if data is not None:
            return _build_result(data.get("messages", []))
        # Server neodpovídá — spadneme na subprocess

    # Subprocess fallback
    return _validate_via_subprocess(html_bytes, jar_path)
