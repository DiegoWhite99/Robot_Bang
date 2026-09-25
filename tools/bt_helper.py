#!/usr/bin/env python3
# Ayudante de Bluetooth que corre en el HOST (la placa), no en el contenedor.
#
# El contenedor de la App no ve el Bluetooth (no tiene el bus D-Bus de BlueZ
# ni /dev/rfkill), asi que el boton 🔵 BLUETOOTH del dashboard le pide las
# cosas a este servidorcito, que usa bluetoothctl. Corre como servicio de
# usuario (systemd --user, ver tools/install_bt_helper.sh), sin sudo: el
# usuario arduino esta en los grupos bluetooth y netdev.
#
# Seguridad: cada peticion debe traer el token de data/.bt_token (lo crea este
# script; el contenedor lo lee en /app/data/.bt_token). Sin token -> 403.
#
# API (JSON, POST salvo /status):
#   GET  /status                   {"powered", "devices": [{"mac","name","paired","connected"}]}
#   POST /power_on                 desbloquea rfkill y prende el adaptador
#   POST /scan    {"seconds": 10}  busca equipos cercanos y devuelve /status
#   POST /connect {"mac"}          conecta (y si no esta emparejado, empareja + confia)
#   POST /disconnect {"mac"}

import json
import re
import secrets
import struct
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 7010
TOKEN_PATH = Path(__file__).resolve().parent.parent / "data" / ".bt_token"
_MAC = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.I)


def _token():
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_hex(16))
        TOKEN_PATH.chmod(0o644)  # el contenedor lo lee con otro uid
    return TOKEN_PATH.read_text().strip()


def btctl(*args, timeout=15):
    try:
        r = subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired as exc:
        return (exc.stdout or b"").decode(errors="ignore") if isinstance(exc.stdout, bytes) else (exc.stdout or "")


def power_on():
    for r in Path("/sys/class/rfkill").glob("rfkill*"):
        if (r / "type").read_text().strip() == "bluetooth" and (r / "soft").read_text().strip() == "1":
            # struct rfkill_event { u32 idx; u8 type; u8 op; u8 soft; u8 hard }:
            # type 2 = bluetooth, op 3 = CHANGE_ALL, soft 0 = desbloqueado.
            with open("/dev/rfkill", "wb") as f:
                f.write(struct.pack("=IBBBB", 0, 2, 3, 0, 0))
            time.sleep(1)
            break
    btctl("power", "on")
    btctl("pairable", "on")
    return status()


def _is_audio(info):
    return "Audio Sink" in info or "audio-" in info


def _device_list(*filt):
    found = {}
    for line in btctl("devices", *filt).splitlines():
        m = re.match(r"^Device ([0-9A-F:]{17}) (.*)$", line.strip(), re.I)
        if m:
            found[m.group(1)] = m.group(2)
    return found


_UNNAMED = re.compile(r"^[0-9A-F-]{8,}$", re.I)


def status():
    """3 llamadas a bluetoothctl en total (no una por equipo: tras un escaneo
    hay decenas de equipos BLE y eso tardaba demasiado). El 'info' solo se
    pide para los equipos con nombre, para saber si son de audio."""
    powered = "Powered: yes" in btctl("show")
    everything = _device_list()
    paired = _device_list("Paired")
    connected = _device_list("Connected")
    devices = []
    for mac, name in everything.items():
        named = not _UNNAMED.match(name)
        info = btctl("info", mac, timeout=5) if (named or mac in paired) else ""
        devices.append({
            "mac": mac,
            "name": name,
            "paired": mac in paired,
            "connected": mac in connected,
            "audio": _is_audio(info),
        })
    # Primero lo conectado/emparejado, luego las bocinas, luego el resto.
    devices.sort(key=lambda d: (not d["connected"], not d["paired"], not d["audio"], d["name"].lower()))
    return {"ok": True, "powered": powered, "devices": devices}


def scan(seconds):
    power_on()
    btctl("--timeout", str(seconds), "scan", "on", timeout=seconds + 5)
    return status()


def connect(mac):
    info = btctl("info", mac, timeout=5)
    msg = ""
    if "not available" in info:
        # BlueZ olvida lo encontrado en un escaneo ~30 s despues: se busca otra vez.
        btctl("--timeout", "8", "scan", "on", timeout=13)
        info = btctl("info", mac, timeout=5)
    if "Paired: yes" not in info:
        out = btctl("pair", mac, timeout=30)
        if "Pairing successful" not in out and "AlreadyExists" not in out:
            return {"ok": False, "error": "No se pudo emparejar: pon la bocina en modo emparejar y vuelve a intentar.", "detail": out[-300:]}
        msg = "emparejada y "
    btctl("trust", mac)
    out = btctl("connect", mac, timeout=25)
    if "Connection successful" not in out and "Connected: yes" not in btctl("info", mac, timeout=5):
        return {"ok": False, "error": "No respondió: ¿está prendida y cerca?", "detail": out[-300:]}
    st = status()
    st["message"] = f"Bocina {msg}conectada"
    return st


def disconnect(mac):
    btctl("disconnect", mac)
    st = status()
    st["message"] = "Desconectada"
    return st


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        return secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

    def do_GET(self):
        if not self._authorized():
            return self._send(403, {"ok": False, "error": "token"})
        if self.path == "/status":
            return self._send(200, status())
        self._send(404, {"ok": False, "error": "no existe"})

    def do_POST(self):
        if not self._authorized():
            return self._send(403, {"ok": False, "error": "token"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            mac = str(data.get("mac", ""))
            if self.path in ("/connect", "/disconnect") and not _MAC.match(mac):
                return self._send(400, {"ok": False, "error": "MAC inválida"})
            if self.path == "/power_on":
                return self._send(200, power_on())
            if self.path == "/scan":
                return self._send(200, scan(max(3, min(30, int(data.get("seconds", 10))))))
            if self.path == "/connect":
                return self._send(200, connect(mac))
            if self.path == "/disconnect":
                return self._send(200, disconnect(mac))
            self._send(404, {"ok": False, "error": "no existe"})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    TOKEN = _token()
    power_on()  # al arrancar la placa, el Bluetooth queda prendido
    print(f"bt_helper escuchando en :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
