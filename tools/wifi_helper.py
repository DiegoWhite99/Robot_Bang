#!/usr/bin/env python3
# Ayudante de WiFi que corre en el HOST (la placa), no en el contenedor.
#
# Mismo motivo y mismo patron que tools/bt_helper.py: el contenedor de la App
# no puede hablar con NetworkManager, asi que el panel de WiFi del dashboard le
# pide las cosas a este servidorcito, que usa nmcli.
#
# OJO con el huevo y la gallina: esto sirve para CAMBIAR de red (del colegio a
# casa, por ejemplo), no para la primera conexion. Si la placa no tiene red,
# nadie puede abrir el dashboard para configurarla. Para el primer arranque
# haria falta modo punto de acceso, que es otro proyecto.
#
# Seguridad: cada peticion debe traer el token de data/.wifi_token. Sin token
# -> 403. La contraseña de la red NUNCA se guarda aqui ni se devuelve: se le
# pasa a nmcli y se olvida.
#
# API (JSON, POST salvo /status):
#   GET  /status                       {"ssid","ip","internet","wifi"}
#   POST /scan                         {"networks": [{"ssid","signal","secure","active"}]}
#   POST /connect {"ssid","password"}  conecta (crea el perfil si hace falta)
#   POST /forget  {"ssid"}             borra el perfil guardado
#
#   Instalacion: bash tools/install_wifi_helper.sh

import json
import secrets
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 7011
TOKEN_PATH = Path(__file__).resolve().parent.parent / "data" / ".wifi_token"
_PROBE = ("generativelanguage.googleapis.com", 443)


def _token():
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_hex(16))
        TOKEN_PATH.chmod(0o644)  # el contenedor lo lee con otro uid
    return TOKEN_PATH.read_text().strip()


def nm(*args, timeout=25):
    try:
        r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 1, "nmcli tardó demasiado"
    except FileNotFoundError:
        return 1, "nmcli no está instalado en la placa"


def _internet():
    try:
        with socket.create_connection(_PROBE, timeout=4):
            return True
    except Exception:
        return False


def _ip():
    code, out = nm("-g", "IP4.ADDRESS", "device", "show")
    for line in out.splitlines():
        addr = line.strip().split("/")[0]
        # Se ignoran las redes internas de Docker: no sirven para llegar al robot.
        if addr and not addr.startswith(("172.", "192.168.32.", "127.")):
            return addr
    return ""


def status():
    code, out = nm("-t", "-f", "ACTIVE,SSID", "device", "wifi")
    ssid = ""
    for line in out.splitlines():
        if line.startswith("sí:") or line.startswith("yes:"):
            ssid = line.split(":", 1)[1]
            break
    code2, estado = nm("-t", "-f", "WIFI", "radio")
    return {
        "ok": True,
        "ssid": ssid,
        "ip": _ip(),
        "internet": _internet(),
        "wifi": estado.strip() or "?",
    }


def scan():
    nm("device", "wifi", "rescan", timeout=20)
    code, out = nm("-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list")
    vistas, redes = set(), []
    for line in out.splitlines():
        partes = line.split(":")
        if len(partes) < 4:
            continue
        activa, ssid, señal, seguridad = partes[0], partes[1], partes[2], ":".join(partes[3:])
        if not ssid or ssid in vistas:
            continue
        vistas.add(ssid)
        redes.append({
            "ssid": ssid,
            "signal": int(señal) if señal.isdigit() else 0,
            "secure": bool(seguridad.strip()) and seguridad.strip() != "--",
            "active": activa in ("sí", "yes"),
        })
    redes.sort(key=lambda r: (not r["active"], -r["signal"]))
    return {"ok": True, "networks": redes}


def connect(ssid, password):
    if password:
        code, out = nm("device", "wifi", "connect", ssid, "password", password, timeout=45)
    else:
        code, out = nm("device", "wifi", "connect", ssid, timeout=45)
    if code != 0 or "Error" in out:
        motivo = "Contraseña incorrecta" if "Secrets were required" in out or "802-11-wireless-security" in out else out.strip()[-200:]
        return {"ok": False, "error": f"No se pudo conectar a {ssid}: {motivo}"}
    st = status()
    st["message"] = f"Conectado a {ssid}"
    return st


def forget(ssid):
    nm("connection", "delete", "id", ssid)
    st = status()
    st["message"] = f"Red {ssid} olvidada"
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
            ssid = str(data.get("ssid", "")).strip()
            if self.path == "/scan":
                return self._send(200, scan())
            if self.path in ("/connect", "/forget") and not ssid:
                return self._send(400, {"ok": False, "error": "falta el nombre de la red"})
            if self.path == "/connect":
                return self._send(200, connect(ssid, str(data.get("password", ""))))
            if self.path == "/forget":
                return self._send(200, forget(ssid))
            self._send(404, {"ok": False, "error": "no existe"})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)})

    def log_message(self, fmt, *args):
        pass  # la contraseña podria acabar en el log: mejor no registrar nada


if __name__ == "__main__":
    TOKEN = _token()
    print(f"wifi_helper escuchando en :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
