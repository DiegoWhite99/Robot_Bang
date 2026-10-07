# Cliente del ayudante de WiFi del host (tools/wifi_helper.py).
#
# Igual que bt.py con el Bluetooth: el contenedor no puede hablar con
# NetworkManager, asi que el panel de WiFi del dashboard pasa por aqui: HTTP a
# la puerta de enlace de la red de Docker (= el host), con el token que el
# ayudante deja en data/.wifi_token.
#
# Se llama wifinet y no wifi para no chocar con nada del sistema.

import json
import socket
import struct
import urllib.error
import urllib.request
from pathlib import Path

PORT = 7011
_TOKEN_PATH = Path(__file__).resolve().parent.parent / "data" / ".wifi_token"


def _host_ip():
    """La puerta de enlace por defecto del contenedor, leida de /proc/net/route."""
    with open("/proc/net/route") as f:
        for line in f.readlines()[1:]:
            fields = line.split()
            if fields[1] == "00000000":
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    return "127.0.0.1"


_NOT_RUNNING = {
    "ok": False,
    "error": "El ayudante de WiFi no está corriendo en la placa. Instálalo una vez con: bash tools/install_wifi_helper.sh",
}


def _call(method, path, data=None, timeout=60):
    try:
        token = _TOKEN_PATH.read_text().strip()
    except FileNotFoundError:
        return _NOT_RUNNING
    body = json.dumps(data or {}).encode() if method == "POST" else None
    req = urllib.request.Request(
        f"http://{_host_ip()}:{PORT}{path}", data=body, method=method,
        headers={"X-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except (TimeoutError, socket.timeout):
        return {"ok": False, "error": "La red tardó demasiado en responder. Intenta otra vez."}
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return {"ok": False, "error": "La red tardó demasiado en responder. Intenta otra vez."}
        return _NOT_RUNNING
    except OSError:
        return _NOT_RUNNING


def status():
    return _call("GET", "/status", timeout=30)


def scan():
    return _call("POST", "/scan", timeout=45)


def connect(ssid, password=""):
    return _call("POST", "/connect", {"ssid": ssid, "password": password}, timeout=70)


def forget(ssid):
    return _call("POST", "/forget", {"ssid": ssid})


def can_reboot():
    """{"ok", "board"}: si el host puede reiniciar la placa entera."""
    return _call("GET", "/can_reboot", timeout=10)


def reboot(what="board", delay=6):
    """Le pide al host que reinicie la placa ("board") o solo la App ("app").
    Ver tools/wifi_helper.py: si no hay permiso para la placa, reinicia la App."""
    return _call("POST", "/reboot", {"what": what, "delay": delay}, timeout=15)
