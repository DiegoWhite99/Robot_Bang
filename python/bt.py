# Cliente del ayudante de Bluetooth del host (tools/bt_helper.py).
#
# El contenedor no ve el Bluetooth de la placa, asi que el boton 🔵 BLUETOOTH
# del dashboard pasa por aqui: HTTP a la puerta de enlace de la red de Docker
# (= el host), con el token que el ayudante deja en data/.bt_token.

import json
import socket
import struct
import urllib.error
import urllib.request
from pathlib import Path

PORT = 7010
_TOKEN_PATH = Path(__file__).resolve().parent.parent / "data" / ".bt_token"


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
    "error": "El ayudante de Bluetooth no está corriendo en la placa. Instálalo una vez con: bash tools/install_bt_helper.sh",
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
        return {"ok": False, "error": "El Bluetooth tardó demasiado en responder. Intenta otra vez."}
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return {"ok": False, "error": "El Bluetooth tardó demasiado en responder. Intenta otra vez."}
        return _NOT_RUNNING
    except OSError:
        return _NOT_RUNNING


def status():
    return _call("GET", "/status", timeout=40)


def power_on():
    return _call("POST", "/power_on")


def scan(seconds=10):
    return _call("POST", "/scan", {"seconds": seconds}, timeout=seconds + 30)


def connect(mac):
    return _call("POST", "/connect", {"mac": mac}, timeout=70)


def disconnect(mac):
    return _call("POST", "/disconnect", {"mac": mac})
