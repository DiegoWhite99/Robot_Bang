# Chat BANG - chatbot de voz con los 5 guias de la metodologia BANG (CUN).
#
# La voz vive en la propia placa (headset USB o bocina Bluetooth), no en el
# celular: el robot escucha en continuo, reacciona al nombre de un guia
# ("Crispi", ...) y responde hablando. Ver voice.py para el detalle de como se
# escucha y se habla (y el modo "Alexa": fin de frase rapido, pitido y
# seguimiento sin repetir el nombre).
#
# La web (assets/) es el dashboard: muestra los guias, quien esta activo, lo
# que el robot escucha y responde, y una terminal con comandos (/unlock_carmel,
# /status, /bt...). Se sirve por HTTPS local (certs/), sin tuneles.
#
# Por ahora solo Crispi arranca desbloqueado; los demas se desbloquean desde
# la terminal (ver guides.py).
#
# Las personalidades y la llamada al LLM viven en brain.py; la metodologia
# BANG (fases solida/gaseosa/liquida, tarjetas, ideas) en bang.py; los gestos
# de la carita en gestures.py, para poder probarlos sin arrancar la App entera.
#
# Palabras de activacion: el nombre de un guia ("Crispi, ...") habla con ese
# guia. "Robot, ..." o "Bang, ..." sigue con el guia del reto en curso, o, si
# no hay ninguno, deja que la IA elija entre los guias desbloqueados.

import difflib
import re
import socket
import threading
import time
from pathlib import Path

from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App

import bang
import brain
import bt
import gestures
import guides
import voice
import wifinet

# Version del producto. FUENTE UNICA: el dashboard la pide al conectarse y
# /status la repite, asi no hay dos numeros distintos dando vueltas. El tag
# de git (1.1.0) se pone al final, sobre el commit ya validado.
APP_VERSION = "1.1.0"
VERSION_NOTICE = "Este producto está en desarrollo y seguirá recibiendo actualizaciones."

# HTTPS con el certificado autofirmado de certs/ (si falta, el brick lo
# genera solo). El navegador avisa la primera vez: "Avanzado -> continuar".
ui = WebUI(use_tls=True)

_current_persona = None
_follow_up = None  # guia al que se le puede contestar sin decir su nombre


def _broadcast_status(text):
    ui.send_message("status", {"text": text})


def _broadcast_debug(text):
    ui.send_message("debug", {"text": text})


# Cada fragmento que el STT va entendiendo (aunque no sea el nombre de un
# guia) y cada aviso de voice.py (sin microfono, TTS que falla...) se
# reenvia a la web, para ver en vivo que sonido esta captando el robot.
voice.set_debug_reporter(_broadcast_debug)

# La boca de la carita sigue el volumen de la voz mientras suena (ver
# _play_with_mouth() en voice.py).
voice.set_mouth_reporter(gestures.send_mouth)
# Y los brazos bailan nota por nota con la melodia de celebracion.
voice.set_arm_reporter(gestures.send_arm_step)

AUTO_WAKE = ("robot", "bang")


def _broadcast_bang(persona, sid=None):
    s = bang.session(persona) if persona else None
    ui.send_message("bang_state", s.state() if s else {"persona": persona}, sid)


def _personas_payload():
    unlocked = guides.unlocked()
    return {
        "personas": [
            {"key": k, "name": p["name"], "color": p["color"], "tagline": p["tagline"], "gender": p["gender"], "locked": k not in unlocked}
            for k, p in brain.PERSONAS.items()
        ]
    }


def on_ui_connect(sid):
    ui.send_message("version", {"version": APP_VERSION, "notice": VERSION_NOTICE}, sid)
    ui.send_message("personas", _personas_payload(), sid)
    ui.send_message("active_persona", {"key": _current_persona}, sid)
    _broadcast_bang(_current_persona, sid)


ui.on_connect(on_ui_connect)


# --- Terminal del dashboard ---------------------------------------------------

_HELP = """Comandos:
  /help                 esta ayuda
  /status               guias desbloqueados, guia activo y salida de audio
  /unlock_<guia>        desbloquea un guia (carmel, cesia, cori, cristal)
  /unlock_all           desbloquea todos
  /lock_<guia>          vuelve a bloquear un guia (Crispi no se bloquea)
  /lock_all             deja solo a Crispi
  /bt                   estado de la bocina Bluetooth
  /add_bt               busca equipos Bluetooth (10 s) y los lista numerados
  /bt_list              lista los equipos conocidos (sin buscar)
  /bt_connect <n>       conecta (o empareja) el equipo n de la lista
  /bt_disconnect <n>    desconecta el equipo n
  /bt_audio <n>         la bocina n de la lista es la que habla (si hay varias)
  /bt_mode [headset|music]  headset = mic y voz por la bocina (calidad llamada)
                        music = solo voz por la bocina, en alta calidad
  /test_audio           pitido de prueba por la salida actual
  /cara <guia>          muestra la cara de un guia en la pantalla y la hace
                        "hablar" 3 s (para probarla sin microfono)
  /bienvenida           repite la bienvenida al BANG (GIF + presentadora)
  /menu_guias           muestra el menú de los 5 guías en la pantalla, con su
                        tono; /menu_guias off lo cierra
  /aviso [off|mudo]     aviso de seguridad con campanilla y voz; "mudo" lo
                        muestra sin hablar, "off" lo quita
  /arranque             repite la secuencia completa: aviso → BANG → menú
  /wifi                 estado de la red y dirección del dashboard
  /qr [off]             muestra el QR que lleva al dashboard (para el celular)
  /wifi_sync            repite la animación de "WiFi conectado"
  /tarjeta <guia> <n>   muestra la tarjeta n (1-10) de ese guia en la
                        pantalla; /tarjeta sola la saca
  /menu                 vuelve al inicio: borra los retos de todos los guias,
                        sin guia activo, y la bienvenida pregunta con cual hablar
  /reset                borra el reto en curso del guia activo
  /clear                limpia la terminal"""


def _resolve_guide(name):
    """Tolera errores de tipeo: /unlock_carmerl -> carmel."""
    name = name.strip().lower()
    if name in brain.PERSONAS:
        return name
    match = difflib.get_close_matches(name, list(brain.PERSONAS), n=1, cutoff=0.6)
    return match[0] if match else None


def _set_lock(arg, unlock):
    if arg == "all":
        targets = list(brain.PERSONAS)
    else:
        key = _resolve_guide(arg)
        if key is None:
            return f"No conozco al guía '{arg}'. Opciones: {', '.join(brain.PERSONAS)}"
        targets = [key]

    lines = []
    for key in targets:
        name = brain.PERSONAS[key]["name"]
        o = _o(key)
        if unlock:
            lines.append(f"🔓 {name} desbloquead{o}" if guides.unlock(key) else f"{name} ya estaba desbloquead{o}")
        elif key == guides.ALWAYS_UNLOCKED:
            if arg != "all":
                lines.append(f"{name} no se puede bloquear")
        else:
            lines.append(f"🔒 {name} bloquead{o}" if guides.lock(key) else f"{name} ya estaba bloquead{o}")
    ui.send_message("personas", _personas_payload())
    return "\n".join(lines) or "Sin cambios"


def _status():
    unlocked = ", ".join(brain.PERSONAS[k]["name"] for k in guides.unlocked())
    active = brain.PERSONAS[_current_persona]["name"] if _current_persona else "ninguno"
    s = bang.session(_current_persona) if _current_persona else None
    reto = f"{bang.PHASE_LABELS[s.phase]} — {s.reto}" if s and s.reto else "sin reto"
    return f"Chat BANG v{APP_VERSION}\nDesbloqueados: {unlocked}\nActivo: {active} ({reto})\nVoz: {voice.output_name()}\nMicrófono: {voice.input_name()}"


def _bt():
    st = voice.bluetooth_status()
    mode = "headset (mic + voz por la bocina)" if st["mode"] == "headset" else "music (solo voz por la bocina)"
    lines = [f"Modo: {mode}"]
    if st["sink"]:
        lines.append(f"🔵 Bocina conectada: {st['sink']} · perfil {st['profile'] or '?'}")
    else:
        lines.append("Sin bocina Bluetooth conectada. /add_bt para buscarla, /bt_connect <n> para conectarla.")
    lines.append(f"Voz sale por: {voice.output_name()}")
    lines.append(f"Micrófono: {voice.input_name()}" + (" (se cambia al de la bocina en el próximo turno)" if st["mode"] == "headset" and st["source"] and not voice.input_name().startswith("Bluetooth") else ""))
    return "\n".join(lines)


def _bt_mode(arg):
    arg = arg.strip().lower()
    if not arg:
        return _bt()
    aliases = {"mic": "headset", "manos": "headset", "llamada": "headset", "musica": "music", "música": "music"}
    mode = aliases.get(arg, arg)
    if mode not in voice.BT_MODES:
        return "Uso: /bt_mode headset (mic + voz por la bocina) o /bt_mode music (solo voz, alta calidad)"
    voice.set_bt_mode(mode)
    return (
        f"Modo {mode} guardado. La bocina cambia de perfil en unos segundos (se puede oír un cortecito).\n"
        + ("Mic y voz van a salir por la bocina." if mode == "headset" else "La voz sale por la bocina en alta calidad; el mic vuelve al headset USB.")
    )


_bt_last = []  # ultimo listado, para /bt_connect <n>


def _bt_render(result):
    global _bt_last
    if not result.get("ok"):
        return f"⚠ {result.get('error', 'falló')}"
    # Los escaneos traen decenas de equipos BLE sin nombre (el "nombre" es la
    # propia MAC o un hex): esos se esconden, no son bocinas.
    devices = result.get("devices", [])
    _bt_last = [d for d in devices if d["connected"] or d["paired"] or not re.fullmatch(r"[0-9A-Fa-f-]{8,}", d["name"])]
    hidden = len(devices) - len(_bt_last)
    lines = []
    if result.get("message"):
        lines.append(f"✅ {result['message']}")
    lines.append(f"Bluetooth {'encendido' if result.get('powered') else 'apagado'} · la voz sale por {voice.output_name()}")
    if not _bt_last:
        lines.append("Ningún equipo encontrado. Pon la bocina en modo emparejar y repite /add_bt")
    for i, d in enumerate(_bt_last, 1):
        state = "🔊 conectada" if d["connected"] else "emparejada" if d["paired"] else "nueva"
        lines.append(f"  {i}. {d['name']:<22} {d['mac']}  {state}{' · audio' if d['audio'] else ''}")
    if hidden:
        lines.append(f"  (+{hidden} equipos sin nombre ocultos)")
    if _bt_last:
        lines.append("Conecta con /bt_connect <n>")
    return "\n".join(lines)


def _bt_target(arg):
    """Numero del ultimo listado o MAC directa."""
    arg = arg.strip()
    if arg.isdigit() and 1 <= int(arg) <= len(_bt_last):
        return _bt_last[int(arg) - 1]["mac"]
    if len(arg) == 17 and arg.count(":") == 5:
        return arg.upper()
    return None


def _bt_action(fn, arg):
    mac = _bt_target(arg)
    if mac is None:
        return "Uso: /bt_connect <n> (n del listado de /add_bt o /bt_list) o una MAC"
    return _bt_render(fn(mac))


def _reset():
    if not _current_persona or not bang.session(_current_persona):
        return "No hay reto en curso"
    bang.reset(_current_persona)
    _broadcast_bang(_current_persona)
    return f"Reto de {brain.PERSONAS[_current_persona]['name']} borrado"


def run_command(line):
    line = (line or "").strip()
    if not line:
        return ""
    if not line.startswith("/"):
        return "Los comandos empiezan con /. Escribe /help"
    cmd = line[1:].split()[0].lower()
    if cmd == "help":
        return _HELP
    if cmd == "status":
        return _status()
    if cmd == "bt":
        return _bt()
    arg = line.split(maxsplit=1)[1] if len(line.split()) > 1 else ""
    if cmd == "add_bt":
        return _bt_render(bt.scan(10))
    if cmd == "bt_list":
        return _bt_render(bt.status())
    if cmd == "bt_connect":
        return _bt_action(bt.connect, arg)
    if cmd == "bt_disconnect":
        return _bt_action(bt.disconnect, arg)
    if cmd == "bt_audio":
        mac = _bt_target(arg)
        if mac is None:
            return "Uso: /bt_audio <n> (n del listado de /add_bt o /bt_list)"
        voice.set_preferred_output(mac)
        name = next((d["name"] for d in _bt_last if d["mac"] == mac), mac)
        return f"🔊 La voz va a salir por {name} (cuando esté conectada)"
    if cmd == "bt_mode":
        return _bt_mode(arg)
    if cmd == "test_audio":
        voice.ack()
        return f"🔊 pitido por {voice.output_name()}"
    if cmd == "cara":
        return _face_demo(arg)
    if cmd == "menu":
        return _menu()
    if cmd == "tarjeta":
        return _card_cmd(arg)
    if cmd == "bienvenida":
        def _bienvenida_y_menu():
            _welcome()
            _show_menu()

        threading.Thread(target=_bienvenida_y_menu, daemon=True, name="welcome").start()
        return "🎉 bienvenida al BANG + menú de guías"
    if cmd == "aviso":
        if arg.strip().lower() in ("off", "cerrar", "salir"):
            gestures.send_aviso(False)
            return "🛡 aviso fuera de la pantalla"
        if arg.strip().lower() in ("mudo", "sin_voz"):
            gestures.send_aviso(True)
            return "🛡 aviso en pantalla, sin voz (se quita con /aviso off)"
        threading.Thread(target=_show_aviso, daemon=True, name="aviso").start()
        return "🛡 aviso de seguridad: campanilla + voz + GIF"
    if cmd == "arranque":
        threading.Thread(target=_boot_sequence, daemon=True, name="boot").start()
        return "▶️ secuencia completa: aviso → BANG → menú"
    if cmd == "wifi":
        nivel = _wifi_level()
        gestures.send_wifi(nivel)
        st = wifinet.status()
        red = st.get("ssid") or "?" if isinstance(st, dict) else "?"
        return f"📶 nivel {nivel}/4 · red: {red}\nDashboard: {dashboard_url() or 'sin IP'}"
    if cmd == "qr":
        if arg.strip().lower() in ("off", "cerrar", "salir"):
            gestures.send_qr()
            return "🔳 QR fuera de la pantalla"
        url = dashboard_url()
        if not url:
            return "⚠ no encuentro la IP de la placa: ¿está conectada a la red?"
        gestures.send_qr(url)
        return f"🔳 QR en la pantalla → {url}"
    if cmd == "wifi_sync":
        gestures.send_wifi_sync()
        return "📡 animación de WiFi sincronizado"
    if cmd == "menu_guias":
        # Para probar el menu de la pantalla sin microfono.
        if arg.strip().lower() in ("off", "cerrar", "salir"):
            gestures.send_menu()
            return "📺 menú cerrado, vuelve la cara"
        threading.Thread(target=_show_menu, daemon=True, name="menu-guias").start()
        return "📺 menú de guías en la pantalla (recorre los 5 con su tono)"
    if cmd == "reset":
        return _reset()
    if cmd.startswith("unlock_"):
        return _set_lock(cmd[len("unlock_") :], unlock=True)
    if cmd.startswith("lock_"):
        return _set_lock(cmd[len("lock_") :], unlock=False)
    return f"Comando desconocido: /{cmd}. Escribe /help"


# Boca de la demo: un vaiven de niveles, como una frase hablada.
_FACE_DEMO_MOUTH = [1, 2, 3, 4, 3, 2, 1, 0, 2, 4, 4, 3, 1, 0, 0, 1, 3, 4, 2, 1, 0]


def _face_demo(arg):
    key = _resolve_guide(arg)
    if key is None:
        return f"Uso: /cara <guia>. Opciones: {', '.join(brain.PERSONAS)}"

    def run():
        gestures.send(gestures.TALK, key)
        end = time.monotonic() + 3
        while time.monotonic() < end:
            for level in _FACE_DEMO_MOUTH:
                gestures.send_mouth(level)
                time.sleep(0.07)
        gestures.send_mouth(0)
        gestures.send(gestures.REST, key)

    threading.Thread(target=run, daemon=True, name="face-demo").start()
    return f"🙂 mostrando la cara de {brain.PERSONAS[key]['name']}"


def on_terminal(sid, data):
    line = data.get("line", "") if isinstance(data, dict) else str(data or "")
    try:
        return {"line": line, "output": run_command(line)}
    except Exception as exc:
        return {"line": line, "output": f"⚠ error: {exc}"}


ui.on_message("terminal", on_terminal)


# --- Boton de Bluetooth del dashboard ---------------------------------------
# La web manda {"action": "status"|"power_on"|"scan"|"connect"|"disconnect",
# "mac": ...}; la respuesta vuelve como 'bt_response'. Lo hace tools/bt_helper.py
# en el host (ver bt.py).

_BT_ACTIONS = {
    "status": lambda d: bt.status(),
    "power_on": lambda d: bt.power_on(),
    "scan": lambda d: bt.scan(10),
    "connect": lambda d: bt.connect(d.get("mac", "")),
    "disconnect": lambda d: bt.disconnect(d.get("mac", "")),
}


def on_bt(sid, data):
    data = data if isinstance(data, dict) else {}
    action = _BT_ACTIONS.get(data.get("action"), _BT_ACTIONS["status"])
    result = action(data)
    result["action"] = data.get("action", "status")
    result["audio_output"] = voice.output_name()
    return result


ui.on_message("bt", on_bt)

# --- Vigilante de WiFi -------------------------------------------------------
# La conversacion vive en la nube (ver INFORME-MODELOS-LOCALES.md): sin
# internet el robot no puede responder. Las barritas de la pantalla lo avisan
# de un vistazo, sin que nadie tenga que leer un log.

_WIFI_POLL_S = 10.0
_WIFI_PROBE = ("generativelanguage.googleapis.com", 443)


def _wifi_level():
    """0 = sin salida a internet; 1..4 = barras segun la calidad del enlace.

    Lo que de verdad importa es si se llega al servicio, no la potencia de la
    señal: una señal excelente sin router con internet deja al robot mudo
    igual. Por eso primero se prueba la conexion y solo despues se afina con
    la calidad del enlace, si el contenedor la puede leer.
    """
    try:
        with socket.create_connection(_WIFI_PROBE, timeout=4):
            pass
    except Exception:
        return 0

    try:
        for line in Path("/proc/net/wireless").read_text().splitlines()[2:]:
            parts = line.split()
            if len(parts) > 2:
                calidad = float(parts[2].rstrip("."))  # 0..70 tipico
                return max(1, min(4, round(calidad / 70 * 4)))
    except Exception:
        pass
    return 4  # hay internet pero no se puede medir la señal (cable, o sin permiso)


def dashboard_url():
    """La direccion a la que apunta el QR de la pantalla.

    Se resuelve sola en cada arranque: si la placa cambia de red, el QR sigue
    llevando al sitio correcto. Se descartan las redes internas de Docker,
    que no sirven para llegar al robot desde un telefono.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no manda nada: solo resuelve la ruta de salida
            ip = s.getsockname()[0]
        if ip and not ip.startswith(("172.", "127.")):
            return f"https://{ip}:7000"
    except Exception:
        pass
    st = wifinet.status()
    ip = st.get("ip") if isinstance(st, dict) else ""
    return f"https://{ip}:7000" if ip else ""


def _wifi_watch():
    ultimo = None
    while True:
        try:
            nivel = _wifi_level()
            if nivel != ultimo:
                gestures.send_wifi(nivel)
                if ultimo is not None:  # el primero no se anuncia
                    _broadcast_debug("📶 WiFi con señal" if nivel else "📵 sin internet: no puedo responder")
                    # De sin red a con red: se celebra en la pantalla.
                    if nivel and not ultimo:
                        gestures.send_wifi_sync()
                ultimo = nivel
        except Exception:
            pass  # un fallo al sondear no puede tumbar el hilo del vigilante
        time.sleep(_WIFI_POLL_S)


threading.Thread(target=_wifi_watch, daemon=True, name="wifi-watch").start()


# --- Panel de WiFi del dashboard ---------------------------------------------
# La web manda {"action": "status"|"scan"|"connect"|"forget", "ssid", "password"}
# y la respuesta vuelve como 'wifi_response'. El trabajo real lo hace
# tools/wifi_helper.py en el host (ver wifinet.py), porque el contenedor no
# puede hablar con NetworkManager.
#
# Sirve para CAMBIAR de red, no para la primera conexion: si la placa no tiene
# red, nadie puede abrir este dashboard.

_WIFI_ACTIONS = {
    "status": lambda d: wifinet.status(),
    "scan": lambda d: wifinet.scan(),
    "connect": lambda d: wifinet.connect(d.get("ssid", ""), d.get("password", "")),
    "forget": lambda d: wifinet.forget(d.get("ssid", "")),
}


def on_wifi(sid, data):
    data = data if isinstance(data, dict) else {}
    accion = data.get("action", "status")
    result = _WIFI_ACTIONS.get(accion, _WIFI_ACTIONS["status"])(data)
    result["action"] = accion
    result["dashboard_url"] = dashboard_url()
    # Si acabamos de conectar, la pantalla lo celebra.
    if accion == "connect" and result.get("ok"):
        gestures.send_wifi_sync()
    return result


ui.on_message("wifi", on_wifi)

# El primer chat con Gemini cuesta ~25 s y los siguientes ~1 s, asi que lo
# pagamos aqui, en segundo plano, mientras nadie esta preguntando todavia.
# Lo mismo con los canales de STT/TTS de Google.
threading.Thread(target=brain.warmup, daemon=True).start()
threading.Thread(target=voice.warmup, daemon=True).start()


# --- Bienvenida al BANG ------------------------------------------------------
# Al arrancar, la pantalla muestra el GIF "Bienvenidos a BANG" (el sketch lo
# prende solo al bootear) mientras una presentadora (voz "bang" de voice.py,
# no es ningun guia) da la bienvenida y pregunta con que guia conversar; al
# terminar se apaga y aparece la cara de Crispi. /bienvenida la repite.
# Para elegir basta decir el nombre solo ("Carmel"): ver _greet().

WELCOME_VOICE = "bang"

# Minimo que se queda el aviso de seguridad en pantalla. Si la voz dura mas,
# manda la voz: el aviso no se va hasta terminar de decirlo.
AVISO_S = 9.0

# Lo que el robot DICE mientras se ve el aviso. Va hablado y no solo escrito
# porque el producto arranca en 5 años, y a esa edad todavia no se lee.
# Cubre los tres limites del producto: que es virtual, que no pide datos, y
# que ante un problema se acude a un adulto.
AVISO_TEXTO = (
    "Antes de empezar, dos cositas. Soy un robot, un personaje virtual: no soy una "
    "persona de verdad. No me cuentes datos tuyos como tu dirección, tu teléfono o "
    "tus contraseñas, porque no los necesito. Y si algo te preocupa, cuéntaselo a una "
    "persona adulta en la que confíes. ¡Ahora sí, vamos a crear!"
)


def _welcome_text():
    names = [f"{p['name']}, {p['tagline'].lower()}" for p in brain.PERSONAS.values()]
    return (
        "¡Hola! Bienvenidos a BANG, la Academia de Innovación de la CUN. "
        "Aquí convertimos tus retos en ideas que se pueden construir, con la ayuda de cinco guías. "
        f"¿Con qué guía quieres conversar? {'; '.join(names[:-1])}; o {names[-1]}. "
        "Di su nombre para empezar."
    )


_welcome_lock = threading.Lock()


def _show_menu():
    """Paso 4: el menu de guias. Los presenta uno a uno (con su tono) y al
    terminar los deja TODOS IGUALES.

    Lo del final importa: si uno queda resaltado parece que hay un cursor y que
    toca pulsar algo. Aqui no se pulsa nada, se DICE el nombre, asi que el menu
    se queda en reposo con los cinco iguales y el microfono en el titulo.
    """
    keys = guides.unlocked()
    for i, key in enumerate(keys):
        gestures.send_menu(key)
        voice.nav_tone(subiendo=False)
        time.sleep(0.45)
    time.sleep(0.2)
    gestures.send_menu_idle()  # ninguno resaltado: no hay nada que pulsar
    _broadcast_status("🎤 di el nombre de un guía en voz alta")


def _show_aviso():
    """Paso 1: el aviso de seguridad, con campanilla y dicho en voz alta.

    Suena una campanilla de atencion, y mientras el GIF corre el robot LEE el
    aviso. La pantalla no pasa al siguiente paso hasta que termina de hablar
    (con un minimo de AVISO_S por si la voz falla y no suena nada).
    """
    _broadcast_status("🛡 aviso de seguridad")
    gestures.send_aviso(True)
    ui.send_message("reply", {"persona": WELCOME_VOICE, "text": AVISO_TEXTO})

    t0 = time.monotonic()
    try:
        voice.attention()          # campanilla: "atencion, esto importa"
        time.sleep(0.35)
        # Con musica de fondo: acordes suaves por debajo de la voz.
        voice.say_with_music(WELCOME_VOICE, AVISO_TEXTO)
    except Exception as exc:
        _broadcast_debug(f"⚠ el aviso no se pudo decir en voz alta: {exc}")

    # Si la voz fue mas corta que el minimo (o no sonó), se completa.
    restante = AVISO_S - (time.monotonic() - t0)
    if restante > 0:
        time.sleep(restante)
    gestures.send_aviso(False)


def _welcome():
    """Paso 2: la bienvenida de BANG (GIF + presentadora). Solo eso: quien
    encadena los pasos es _boot_sequence()."""
    if not _welcome_lock.acquire(blocking=False):
        return  # ya hay una sonando
    text = _welcome_text()
    try:
        gestures.send_splash(True)
        _broadcast_status("🎉 ¡Bienvenidos a BANG!")
        ui.send_message("reply", {"persona": WELCOME_VOICE, "text": text})
        # Con el GIF en pantalla el gesto solo mueve los brazos.
        gestures.send(gestures.HAPPY, guides.ALWAYS_UNLOCKED)
        voice.say(WELCOME_VOICE, text)
        gestures.send_splash(False)
    finally:
        gestures.send(gestures.REST, guides.ALWAYS_UNLOCKED)
        _welcome_lock.release()


# Cuanto se espera a que alguien conecte la placa a una red antes de seguir
# igualmente (con el robot mudo, pero sin dejarlo colgado para siempre).
NETWORK_WAIT_S = 150.0


def _network_if_needed():
    """Paso 3, SOLO si hace falta: sin internet no hay conversacion, asi que se
    pide ayuda a un adulto mostrando el QR que lleva al panel de red.

    Si hay internet no se muestra nada y el arranque sigue de largo.
    """
    if _wifi_level() > 0:
        return True

    url = dashboard_url()
    _broadcast_status("📵 sin internet: hay que conectar el robot a una red")
    msg = (
        "Necesito conectarme a una red para poder conversar contigo. "
        "Pídele a una persona adulta que apunte la cámara del celular al código "
        "que aparece en mi pantalla y elija la red."
    )
    ui.send_message("reply", {"persona": WELCOME_VOICE, "text": msg})
    if url:
        gestures.send_qr(url)
    voice.say(WELCOME_VOICE, msg)

    fin = time.monotonic() + NETWORK_WAIT_S
    conectado = False
    while time.monotonic() < fin:
        if _wifi_level() > 0:
            conectado = True
            break
        time.sleep(2.0)

    gestures.send_qr()  # se quita el QR de la pantalla
    if conectado:
        # La animacion la dispara tambien el vigilante, pero aqui se espera a
        # que termine para que no se solape con el menu.
        gestures.send_wifi_sync()
        time.sleep(2.2)
        _broadcast_status("📶 ¡conectado!")
    else:
        _broadcast_status("📵 sigo sin internet: puedo mostrar el menú, pero no conversar")
    return conectado


def _boot_sequence():
    """El arranque completo, en este orden:

        1. ADVERTENCIA   aviso de seguridad (lo primero que se ve)
        2. BANG          bienvenida con la presentadora
        3. RED           solo si no hay internet: QR + panel de red
        4. MENU          los 5 guias, para elegir por voz
        5. AGENTES       la conversacion, que la lleva loop() cuando el niño
                         dice un nombre (ver _greet())

    El sketch ya arranca mostrando el aviso sin esperar a Python (por si tarda
    o se cae); aqui solo se le dice cuando pasar al siguiente paso.
    """
    _show_aviso()
    _welcome()
    _network_if_needed()
    _show_menu()


def _card_cmd(arg):
    parts = (arg or "").split()
    if not parts:
        gestures.send_card()
        return "🃏 tarjeta fuera de la pantalla"
    key = _resolve_guide(parts[0])
    if key is None or len(parts) < 2 or not parts[1].isdigit() or not 1 <= int(parts[1]) <= gestures.CARDS_PER_GUIDE:
        return f"Uso: /tarjeta <guia> <1-{gestures.CARDS_PER_GUIDE}>, o /tarjeta sola para sacarla"
    gestures.send_card(key, int(parts[1]))
    return f"🃏 tarjeta {parts[1]} de {brain.PERSONAS[key]['name']} en pantalla"


def _menu():
    """/menu: todo vuelve al inicio y a elegir guia. Si justo hay una
    respuesta en camino, esa termina igual (no se corta a medio hablar)."""
    global _current_persona, _follow_up
    for key in brain.PERSONAS:
        bang.reset(key)
    _current_persona = None
    _follow_up = None
    ui.send_message("active_persona", {"key": None})
    _broadcast_bang(None)

    def _volver_al_inicio():
        _welcome()
        _show_menu()  # sin esto la pantalla se quedaba en la bienvenida

    threading.Thread(target=_volver_al_inicio, daemon=True, name="welcome").start()
    return "🏠 de vuelta al inicio: retos borrados, elige un guía"


def _greet(persona):
    """Dijeron solo el nombre del guia ("Cori"): se presenta y queda
    escuchando, sin que haya que repetir su nombre (seguimiento)."""
    global _current_persona, _follow_up
    p = brain.PERSONAS[persona]
    gestures.send_card()
    gestures.send_menu()  # eligio: se cierra el menu y aparece su cara
    _current_persona = persona
    ui.send_message("active_persona", {"key": persona})
    _broadcast_bang(persona)
    msg = f"¡Hola! Soy {p['name']}, {p['tagline'].lower()}. Cuéntame, ¿cuál es tu reto?"
    ui.send_message("reply", {"persona": persona, "text": msg})
    _broadcast_status(f"🔊 {p['name']} está hablando...")
    gestures.send(gestures.HAPPY, persona)
    voice.say(persona, msg)
    gestures.send(gestures.REST, persona)
    _follow_up = persona


def _pick_persona(wake, text):
    """Resuelve "robot"/"bang": el guia del reto en curso, o el que elija la IA
    entre los desbloqueados. Devuelve (persona, lo_que_dice_al_presentarse)."""
    if wake not in AUTO_WAKE:
        return wake, ""
    if _current_persona and guides.is_unlocked(_current_persona) and bang.session(_current_persona):
        return _current_persona, ""
    unlocked = guides.unlocked()
    if len(unlocked) == 1:
        # Con un solo guia no hay nada que clasificar: nos ahorramos una
        # llamada entera al LLM.
        persona = unlocked[0]
    else:
        _broadcast_status("🧭 eligiendo el mejor guía para tu reto...")
        persona = bang.classify(text)
        if persona not in unlocked:
            persona = guides.ALWAYS_UNLOCKED
    return persona, f"Para este reto te acompaño yo, {brain.PERSONAS[persona]['name']}. "


def _o(key):
    """Terminacion de genero para concordar con el guia: Cesia, Cori y
    Cristal son mujeres ("bloqueada"), Crispi y Carmel hombres ("bloqueado")."""
    return "a" if brain.PERSONAS[key]["gender"] == "f" else "o"


def _say_locked(wake):
    name = brain.PERSONAS[wake]["name"]
    crispi = guides.ALWAYS_UNLOCKED
    gestures.send_card()
    _broadcast_status(f"🔒 {name} todavía está bloquead{_o(wake)}")
    msg = f"{name} todavía está bloquead{_o(wake)}. Por ahora te acompaño yo, Crispi: di Crispi y cuéntame tu reto."
    ui.send_message("reply", {"persona": crispi, "text": msg})
    gestures.send(gestures.TALK, crispi)
    voice.say(crispi, msg)
    gestures.send(gestures.REST, crispi)


_welcomed = False


def loop():
    global _current_persona, _follow_up, _welcomed

    if not _welcomed:
        _welcomed = True
        _boot_sequence()

    follow_up, _follow_up = _follow_up, None
    if follow_up:
        _broadcast_status(f"🎧 te escucho... responde a {brain.PERSONAS[follow_up]['name']} sin decir su nombre.")
    else:
        _broadcast_status("🎧 escuchando... di 'Crispi' (o 'robot') y tu pregunta.")
    wake, text = voice.listen_turn(
        list(brain.PERSONAS.keys()) + list(AUTO_WAKE), follow_up=follow_up, name_only=tuple(brain.PERSONAS)
    )
    if wake in brain.PERSONAS and not text:
        # Solo el nombre (p. ej. respondiendo a la bienvenida): se presenta.
        voice.ack()
        if guides.is_unlocked(wake):
            _greet(wake)
        else:
            _say_locked(wake)
        return
    if not wake or not text:
        # Freno: sin esto, cualquier fallo que se repita rapido (p. ej. sin
        # headset conectado) satura la CPU en un bucle sin pausa y le quita
        # turno al servidor web, que deja de responder por completo.
        time.sleep(1)
        return

    heard_at = time.monotonic()
    voice.ack()  # "te escuche": suena mientras el LLM piensa

    if wake in brain.PERSONAS and not guides.is_unlocked(wake):
        ui.send_message("heard", {"persona": wake, "text": text})
        _say_locked(wake)
        return

    persona, intro = _pick_persona(wake, text)
    _current_persona = persona
    name = brain.PERSONAS[persona]["name"]
    ui.send_message("active_persona", {"key": persona})
    ui.send_message("heard", {"persona": persona, "text": text})
    _broadcast_status(f"🤔 {name} está pensando...")

    result = bang.turn(persona, text)
    reply = intro + result.reply
    gesture = gestures.emotion_of(reply)
    _broadcast_debug(f"⏱ respuesta del LLM en {time.monotonic() - heard_at:.1f}s")

    ui.send_message("reply", {"persona": persona, "text": reply})
    _broadcast_bang(persona)

    # Paso de fase BANG: se celebra antes de hablar, con la cancioncita y el
    # baile de brazos de Diome-chan (ver voice.celebrate()).
    if result.phase_changed:
        _broadcast_status(f"🎉 ¡{bang.PHASE_LABELS[bang.session(persona).phase]}!")
        gestures.send(gestures.HAPPY, persona)
        voice.celebrate()

    _broadcast_status(f"🔊 {name} está hablando...")

    # Tarjeta recien volteada: queda en la pantalla mientras el guia la
    # explica y la persona piensa; la saca la siguiente respuesta.
    if result.card:
        gestures.send_card(*result.card)
    else:
        gestures.send_card()

    # El gesto viaja ANTES de hablar: como Python controla el parlante, sabe
    # exactamente cuando empieza y termina la voz.
    gestures.send(gesture, persona)
    voice.say(persona, reply)
    gestures.send(gestures.REST, persona)

    # Como Alexa: unos segundos para contestarle sin repetir su nombre.
    _follow_up = persona


App.run(user_loop=loop)
