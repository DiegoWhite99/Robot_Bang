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
# El "cerebro" tiene dos modos (llm_router.py): PLUS = Gemini en la nube y
# ESSENTIALS = modelo local en la placa, con RAG sobre knowledge/ (rag.py).
# Se elige desde el dashboard (boton ESSENTIALS / PLUS) o con /modo.
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
import curioso
import gestures
import guardrails
import guides
import intro
import llm_router
import song
import voice
import wifinet

# Version del producto. FUENTE UNICA: el dashboard la pide al conectarse y
# /status la repite, asi no hay dos numeros distintos dando vueltas. El tag
# de git (1.1.0) se pone al final, sobre el commit ya validado.
APP_VERSION = "1.1.1"
# "beta" mientras se prueba, "stable" la que se entrega. La pantalla del robot
# lo muestra tambien (APP_CHANNEL de sketch/sketch.ino: tienen que coincidir).
APP_CHANNEL = "stable"
VERSION_NOTICE = "Versión estable. Seguirá recibiendo actualizaciones."

# HTTPS con el certificado autofirmado de certs/ (si falta, el brick lo
# genera solo). El navegador avisa la primera vez: "Avanzado -> continuar".
ui = WebUI(use_tls=True)

_current_persona = None
_follow_up = None  # guia al que se le puede contestar sin decir su nombre
# Despues de una interrupcion (escucha activa): lo proximo que diga el niño a
# este guia es un APORTE para su reto (bang.contribute), no un turno normal.
# Solo en modo BANG; en Curioso una interrupcion es un turno mas.
# {"persona", "spoken", "depth", "at", "oido"}
_awaiting_aporte = None
APORTE_WAIT_S = 20.0  # pasado esto, lo que diga ya es un turno normal


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
# Cada emocion de la carita tiene ademas su sonido (brillos al ponerse feliz,
# un gruñido jugueton al enojarse...): para un niño de 5 años que todavia no
# lee, y que la mitad del tiempo no esta mirando la pantalla, el sonido es lo
# que le dice como se siente el robot. Ver voice.emotion_sound().
gestures.set_sound_reporter(voice.emotion_sound)
# Los respaldos de Gemini al modelo local (y sus fallos) se ven en el panel.
llm_router.set_reporter(_broadcast_debug)

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


def _llm_mode_payload():
    m = llm_router.mode()
    return {"mode": m, "label": llm_router.MODE_LABELS[m], "modes": list(llm_router.MODES)}


def on_ui_connect(sid):
    ui.send_message("version", {"version": f"{APP_VERSION} {APP_CHANNEL}", "notice": VERSION_NOTICE}, sid)
    ui.send_message("llm_mode", _llm_mode_payload(), sid)
    ui.send_message("personas", _personas_payload(), sid)
    ui.send_message("active_persona", {"key": _current_persona}, sid)
    ui.send_message("chat_mode", _chat_mode_payload(), sid)
    ui.send_message("escucha", _escucha_payload(), sid)
    _broadcast_bang(_current_persona, sid)


ui.on_connect(on_ui_connect)


# --- Terminal del dashboard ---------------------------------------------------

_HELP = """Comandos:
  /help                 esta ayuda
  /status               guias desbloqueados, guia activo y salida de audio
  /modo [plus|essentials]  cerebro del robot: plus = Gemini en la nube, los 5
                        guías; essentials = todo en la placa, sin internet
                        (modelo Qwen, oído con Vosk y voz con Piper), solo
                        Cristal y más lento. En el arranque se elige por voz
  /elegir_cerebro       vuelve a preguntar por voz PLUS o ESSENTIAL
  /modo_chat [bang|curioso]  cómo conversa el robot: bang = acompaña el reto
                        por sus fases; curioso = charla libre, responde lo que
                        le pregunten y obedece "ponte feliz", "baila"...
                        Por voz: "modo curioso" / "modo bang"
  /elegir_modo          pregunta por voz cuál de los dos modos de charla
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
  /barge                estado de la escucha activa (siempre encendida: si el
                        niño habla, el guía se calla y escucha). Local (Vosk),
                        sin costo de API
  /interrumpir <texto>  simula una interrupción (p. ej. /interrumpir cori se
                        me ocurrió algo); si nadie habla, se dispara con la
                        próxima respuesta
  /mic [segundos]       mide lo que ENTRA por el micrófono (nivel y picos),
                        sin reconocimiento de por medio: separa "el micrófono
                        no capta" de "capta pero no se entiende"
  /test_audio           pitido de prueba por la salida actual
  /cara <guia>          muestra la cara de un guia en la pantalla y la hace
                        "hablar" 3 s (para probarla sin microfono)
  /gesto <nombre>       dispara un solo gesto (cara + brazos) sin hablar, para
                        ver qué hace cada servo: reposo, hablar, feliz,
                        sorpresa, enojado, uff, triste, saludo, aplauso,
                        pensar, si, no, baile, abrazo, dormir, estiron
  /baila                la canción de baile (~30 s) con la coreografía de
                        brazos; háblale al robot para cortarla
  /cantar               "¡A despegar!" (assets/audio/): la canción de verdad,
                        con la boca siguiendo la música y los brazos en el
                        golpe; háblale al robot para cortarla
  /bienvenida           repite la bienvenida al BANG (GIF + presentadora) y
                        la presentación del cerebro que esté activo
  /menu_guias           muestra el menú de los 5 guías en la pantalla, con su
                        tono; /menu_guias off lo cierra
  /aviso [off|mudo]     aviso de seguridad con campanilla y voz; "mudo" lo
                        muestra sin hablar, "off" lo quita
  /arranque             repite la secuencia completa: aviso → BANG (quién lo
                        hizo) → QR del panel → PLUS/ESSENTIAL → presentación
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
    cerebro = llm_router.MODE_LABELS[llm_router.mode()]
    bs = voice.barge_status()
    escucha = "lista" if bs["lista"] else f"NO disponible ({bs['vosk']})"
    modo = curioso.MODE_NAMES[curioso.mode()]
    reto = reto if not curioso.is_curioso() else "charla libre (modo Curioso)"
    gemini = ""
    if not llm_router.is_local():
        # Como va cada modelo AHORA, en el orden en que se le pregunta (ver
        # brain._ranked()): si la charla se siente lenta, aqui se ve por que.
        gemini = "\nGemini: " + " · ".join(
            f"{m['model']} {m['ms']} ms" + (f" (apartado {m['apartado_s']} s)" if m["apartado_s"] else "")
            for m in brain.model_stats()
        )
    return (
        f"Chat BANG v{APP_VERSION} {APP_CHANNEL}\nModo: {modo}\nCerebro: {cerebro}{gemini}\nDesbloqueados: {unlocked}\n"
        f"Activo: {active} ({reto})\nVoz: {voice.output_name()}\nMicrófono: {voice.input_name()}\n"
        f"Escucha activa: nativa, siempre encendida — {escucha} (Vosk: {bs['vosk']})"
    )


# La escucha activa ya no se prende ni se apaga: es parte de como conversa el
# robot (ver voice.py). Lo unico que se muestra es si Vosk esta listo.
_VOSK_LABELS = {
    "listo": "lista: si hablas mientras el guía habla, se calla y te escucha",
    "cargando": "cargando el modelo (unos segundos)...",
    "sin vosk": "⚠ falta el paquete vosk: revisa python/requirements.txt",
    "sin modelo": "⚠ no hay modelo Vosk: corre tools/install_vosk_model.py",
    "error": "⚠ Vosk no cargó: revisa los logs",
}


def _escucha_payload():
    bs = voice.barge_status()
    return {"vosk": bs["vosk"], "lista": bs["lista"], "minimo": bs["minimo"],
            "label": _VOSK_LABELS.get(bs["vosk"], f"⚠ Vosk: {bs['vosk']}")}


def _barge_cmd(arg):
    bs = voice.barge_status()
    nota = _VOSK_LABELS.get(bs["vosk"], f"⚠ Vosk: {bs['vosk']}")
    salida = "bocina Bluetooth" if voice.bluetooth_status()["sink"] else "headset USB"
    return (
        f"✋ Escucha activa NATIVA (no se apaga): {nota}\n"
        f"Con {salida} hacen falta {bs['minimo']} palabras nuevas para callar al guía.\n"
        "Para probarla sin micrófono: /interrumpir <texto>"
    )


# --- Modo de conversacion: BANG o Curioso ------------------------------------


def _chat_mode_payload():
    m = curioso.mode()
    return {"mode": m, "name": curioso.MODE_NAMES[m], "label": curioso.MODE_LABELS[m], "modes": list(curioso.MODES)}


_MODO_DICHO = {
    "bang": "¡Listo! Modo BANG: te acompaño a convertir tu reto en ideas. Elige un guía y cuéntame tu reto.",
    "curioso": "¡Listo! Modo Curioso: pregúntame lo que quieras, o pídeme cosas como ponerme feliz o bailar.",
}


def _set_chat_mode(arg, hablado=False, voz=None):
    """Cambia el modo de conversacion (lo usan /modo_chat, el dashboard y la
    voz). Los retos en curso NO se borran: si se vuelve a BANG, siguen ahi.

    hablado: ademas lo dice en voz alta (con `voz`, o la presentadora)."""
    arg = (arg or "").strip()
    if not arg:
        return f"Modo actual: {curioso.MODE_LABELS[curioso.mode()]}. Uso: /modo_chat bang o /modo_chat curioso"
    try:
        m = curioso.set_mode(arg)
    except ValueError as exc:
        return f"⚠ {exc}"
    ui.send_message("chat_mode", _chat_mode_payload())
    _broadcast_debug(f"🎛 modo {curioso.MODE_NAMES[m]}")
    if hablado:
        texto = _MODO_DICHO[m]
        persona = voz or WELCOME_VOICE
        ui.send_message("reply", {"persona": persona, "text": texto})
        gestures.send(gestures.HAPPY, persona if persona in brain.PERSONAS else guides.default_guide())
        voice.say(persona, texto)
        gestures.send(gestures.REST, persona if persona in brain.PERSONAS else guides.default_guide())
    return f"🎛 {curioso.MODE_LABELS[m]}"


def _interrumpir_cmd(arg):
    if not arg.strip():
        return "Uso: /interrumpir <texto>, p. ej. /interrumpir cori se me ocurrió algo"
    what, trig = voice.inject_barge(arg)
    if what == "now":
        return f"✋ interrumpido: {trig['kind']} → {trig['persona']}"
    if what == "armed":
        return "✋ nadie está hablando: la interrupción se dispara en la próxima respuesta de un guía"
    return "No reconocí una interrupción en ese texto (usa un nombre de guía o 'se me ocurrió algo', 'pásame con...')"


def _set_llm_mode(arg):
    """Cambia el cerebro (lo usan /modo y el boton del dashboard).

    Los retos en curso se conservan; solo se borran las memorias de chat,
    porque el historial de un cerebro no le sirve al otro.
    """
    arg = (arg or "").strip()
    if not arg:
        return f"Cerebro actual: {llm_router.MODE_LABELS[llm_router.mode()]}. Uso: /modo plus o /modo essentials"
    before = llm_router.mode()
    try:
        m = llm_router.set_mode(arg)
    except ValueError as exc:
        return f"⚠ {exc}"
    global _current_persona, _follow_up
    if m != before:
        brain.clear_all()
        # El modo cambia QUIEN esta disponible (en Essentials solo Cristal), asi
        # que un guia activo que ya no existe en este modo tiene que soltarse: si
        # no, el proximo turno seguiria hablandole a alguien que no esta.
        if _current_persona and not guides.is_unlocked(_current_persona):
            _broadcast_debug(f"🔀 {brain.PERSONAS[_current_persona]['name']} no está en este modo: pasa a {brain.PERSONAS[guides.default_guide()]['name']}")
            _current_persona = guides.default_guide()
            ui.send_message("active_persona", {"key": _current_persona})
        _follow_up = None
        ui.send_message("personas", _personas_payload())
    ui.send_message("llm_mode", _llm_mode_payload())
    if m == "essentials":
        _broadcast_debug("🧠 modo ESSENTIALS: todo en la placa (modelo Qwen, oído Vosk, voz Piper); cargando, ~20 s")
        threading.Thread(target=voice.warmup, daemon=True, name="voz-warmup").start()
        return ("🧠 ESSENTIALS: todo corre en la placa, sin internet — modelo Qwen, escucha con Vosk y voz "
                f"con Piper. Te acompaña {brain.PERSONAS[guides.ESSENTIALS_GUIDE]['name']}, la única guía de este "
                "modo, y las respuestas son más lentas que en PLUS.")
    _broadcast_debug("☁️ modo PLUS: Gemini en la nube (si falla, contesta el modelo local)")
    threading.Thread(target=voice.warmup, daemon=True, name="voz-warmup").start()
    return "☁️ PLUS: Gemini en la nube y voz de Google. Si Gemini falla, ese turno lo contesta el modelo local."


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
    if cmd == "modo":
        return _set_llm_mode(line.split(maxsplit=1)[1] if len(line.split()) > 1 else "")
    if cmd == "modo_chat":
        return _set_chat_mode(line.split(maxsplit=1)[1] if len(line.split()) > 1 else "")
    if cmd in ("elegir_cerebro", "cerebro"):
        threading.Thread(target=_elegir_cerebro, daemon=True, name="cerebro").start()
        return "🧠 pregunto por voz: ¿PLUS o ESSENTIAL?"
    if cmd == "elegir_modo":
        threading.Thread(target=_elegir_modo, daemon=True, name="modo").start()
        return "🎛 preguntando por voz: BANG o Curioso"
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
    if cmd == "barge":
        return _barge_cmd(arg)
    if cmd == "interrumpir":
        return _interrumpir_cmd(arg)
    if cmd in ("mic", "microfono", "micro"):
        # Diagnóstico honesto: mide lo que ENTRA, sin STT de por medio, para
        # separar "el micrófono no capta" de "capta pero no se entiende".
        return voice.mic_report(float(arg) if arg.replace(".", "").isdigit() else 4.0)
    if cmd == "test_audio":
        voice.ack()
        return f"🔊 pitido por {voice.output_name()}"
    if cmd == "cara":
        return _face_demo(arg)
    if cmd == "gesto":
        return _gesture_cmd(arg)
    if cmd == "baila":
        return _dance_cmd()
    if cmd in ("cantar", "canta", "cancion"):
        return _cantar_cmd()
    if cmd == "menu":
        return _menu()
    if cmd == "tarjeta":
        return _card_cmd(arg)
    if cmd == "bienvenida":
        if boot_running():
            return "🎉 ya hay un arranque en curso: espera a que termine"

        def _bienvenida_y_menu():
            if not _boot_lock.acquire(blocking=False):
                return
            try:
                _welcome()
                _presentar_cerebro()
            finally:
                _boot_lock.release()

        threading.Thread(target=_bienvenida_y_menu, daemon=True, name="welcome").start()
        return "🎉 bienvenida al BANG + la presentación del cerebro activo"
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
        if boot_running():
            return "▶️ ya hay un arranque en curso: espera a que termine (o /menu para cortarlo)"
        threading.Thread(target=_boot_sequence, daemon=True, name="boot").start()
        return "▶️ secuencia completa: aviso → BANG → QR del panel → PLUS/ESSENTIAL → presentación"
    if cmd == "wifi":
        nivel = _wifi_level()
        gestures.send_wifi(nivel)
        st = wifinet.status()
        red = st.get("ssid") or "?" if isinstance(st, dict) else "?"
        return f"📶 nivel {nivel}/4 · red: {red}\nDashboard: {dashboard_url() or 'sin IP'}"
    if cmd == "qr":
        texto = arg.strip()
        if texto.lower() in ("off", "cerrar", "salir"):
            gestures.send_qr()
            return "🔳 QR fuera de la pantalla"
        # Con un texto detras ("/qr http://a.bc") se dibuja ESE codigo. Sirve
        # para probar la pantalla con un QR mas chico: el mensaje del Bridge
        # crece con el cuadrado del numero de modulos, y el buffer RPC son 256
        # bytes, asi que un QR largo puede no caber. Sin texto, el dashboard.
        url = texto or dashboard_url()
        if not url:
            return "⚠ no encuentro la IP de la placa: ¿está conectada a la red?"
        info = gestures.send_qr(url)
        if not info.get("ok"):
            return f"⚠ no pude mostrar el QR de {url}: {info.get('error')}"
        return (f"🔳 QR en la pantalla → {url}\n"
                f"   {info['size']}×{info['size']} módulos · {info['bytes']} bytes por el Bridge")
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


# Nombre hablado -> gesto, para /gesto. Es el mismo orden en que se prueban
# los brazos al calibrar los servos (ver la cabecera de sketch/sketch.ino).
_GESTOS = {
    "reposo": gestures.REST, "hablar": gestures.TALK, "feliz": gestures.HAPPY,
    "sorpresa": gestures.SURPRISE, "enojado": gestures.ANGRY,
    "uff": gestures.FRUSTRATED, "triste": gestures.SAD, "saludo": gestures.WAVE,
    "aplauso": gestures.CLAP, "pensar": gestures.THINK, "si": gestures.YES,
    "no": gestures.NO, "baile": gestures.DANCE, "abrazo": gestures.HUG,
    "dormir": gestures.SLEEP, "estiron": gestures.STRETCH,
}


def _gesture_cmd(arg):
    """/gesto <nombre>: dispara UN gesto en la cara y los brazos, sin hablar.

    Es la forma de ver que hace cada servo sin tener que adivinar la frase que
    lo dispara, y es lo que hay que usar para calibrar ARM1_DIR / ARM2_DIR en
    sketch/sketch.ino: con /gesto feliz los DOS brazos tienen que subir.
    """
    nombre = (arg or "").strip().lower().replace("ó", "o").replace("í", "i")
    if nombre not in _GESTOS:
        return "Uso: /gesto <nombre>. Opciones: " + ", ".join(_GESTOS)
    key = _current_persona or "crispi"
    gesto = _GESTOS[nombre]

    def run():
        # /gesto existe para PROBAR los gestos uno a uno, asi que aqui si se
        # quiere oir el sonido aunque esa emocion acabe de sonar.
        voice.emotion_sound_reset()
        gestures.send(gesto, key)
        time.sleep(4)  # lo que dura la entrada del gesto mas un poco de vaiven
        gestures.send(gestures.REST, key)

    threading.Thread(target=run, daemon=True, name="gesture-demo").start()
    return f"🦾 gesto «{nombre}» con {brain.PERSONAS[key]['name']} (vuelve a reposo en 4 s)"


def _cantar_cmd():
    """/cantar: pone la canción sin tener que pedirla por voz (para probar)."""
    persona = _current_persona if _current_persona in brain.PERSONAS else guides.default_guide()
    if not voice.song_ready():
        return "⚠ no hay ninguna canción en assets/audio/"
    def run():
        gestures.send(gestures.DANCE, persona)
        res = voice.sing(barge=voice.make_barge(persona, tuple(brain.PERSONAS)))
        gestures.send(gestures.REST, persona)
        if res.get("reason"):
            _broadcast_debug(f"⚠ no pude cantar: {res['reason']}")
    threading.Thread(target=run, daemon=True, name="cantar").start()
    return "🎤 ¡A despegar! (háblale para que pare)"


def _dance_cmd():
    """/baila: la cancion larga con la coreografia, sin tener que hablarle.

    Es la misma voice.dance() de la orden "baila", para poder probar el ritmo
    y las poses de los servos desde la terminal del dashboard.
    """
    key = _current_persona or "crispi"

    def run():
        gestures.send(gestures.DANCE, key)
        voice.dance(barge=voice.make_barge(key, tuple(brain.PERSONAS)))
        gestures.send(gestures.REST, key)

    threading.Thread(target=run, daemon=True, name="dance-demo").start()
    return f"🕺 {brain.PERSONAS[key]['name']} está bailando (~30 s; háblale para parar)"


def on_terminal(sid, data):
    line = data.get("line", "") if isinstance(data, dict) else str(data or "")
    try:
        return {"line": line, "output": run_command(line)}
    except Exception as exc:
        return {"line": line, "output": f"⚠ error: {exc}"}


ui.on_message("terminal", on_terminal)


# --- Boton ESSENTIALS / PLUS del dashboard ----------------------------------
# La web manda {"mode": "plus"|"essentials"} (o {} para solo consultar); el
# estado vuelve como 'llm_mode_response' al que pregunto y como 'llm_mode' a
# todas las pestanas abiertas.


def on_llm_mode(sid, data):
    data = data if isinstance(data, dict) else {}
    message = _set_llm_mode(data.get("mode", "")) if data.get("mode") else ""
    return dict(_llm_mode_payload(), message=message)


ui.on_message("llm_mode", on_llm_mode)


# Estado de la escucha activa: solo se consulta (es nativa, no se apaga).
def on_escucha(sid, data):
    return dict(_escucha_payload(), message=_barge_cmd(""))


ui.on_message("escucha", on_escucha)


# Selector BANG / CURIOSO del dashboard: {"mode": "bang"|"curioso"} (o {} para
# consultar). Vuelve como 'chat_mode_response' y 'chat_mode'.
def on_chat_mode(sid, data):
    data = data if isinstance(data, dict) else {}
    message = _set_chat_mode(data.get("mode", "")) if data.get("mode") else ""
    return dict(_chat_mode_payload(), message=message)


ui.on_message("chat_mode", on_chat_mode)


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


# La direccion del dashboard se pregunta varias veces (el QR del arranque, el
# del panel de red, /wifi, /qr) y averiguarla cuesta una llamada HTTP al
# ayudante del host: se cachea un rato.
_DASHBOARD_TTL_S = 30.0
_dashboard_cache = {"url": "", "at": -1e9}


def dashboard_url(force=False):
    """La direccion a la que apunta el QR de la pantalla.

    OJO CON EL ORDEN, que aqui estuvo el bug del QR inservible: esta App corre
    DENTRO de un contenedor, y desde dentro el truco de sockets (abrir un UDP
    contra 8.8.8.8 y mirar que IP local eligio la ruta) devuelve la IP DEL
    CONTENEDOR en una red interna de Docker. En esta placa daba
    192.168.48.3 — que ademas NO empieza por "172.", asi que el filtro de
    antes la dejaba pasar tal cual. El QR se dibujaba bien y se escaneaba
    bien, pero no abria nada: esa direccion no existe fuera de Docker.

    Quien SI conoce la IP de la placa en la red de verdad es el ayudante de
    WiFi del host (tools/wifi_helper.py, via wifinet.status()), porque corre
    FUERA del contenedor y se lo pregunta a NetworkManager. Por eso va
    primero. El truco del socket queda de respaldo por si el ayudante no esta
    instalado, y ahi se descartan tambien las /16 privadas tipicas de Docker.

    Se resuelve sola en cada arranque: si la placa cambia de red, el QR sigue
    llevando al sitio correcto.
    """
    ahora = time.monotonic()
    if not force and ahora - _dashboard_cache["at"] < _DASHBOARD_TTL_S:
        return _dashboard_cache["url"]

    url = ""
    # 1. El ayudante del host: la IP de la placa en la red de verdad.
    try:
        st = wifinet.status()
        ip = st.get("ip") if isinstance(st, dict) else ""
        if ip:
            url = f"https://{ip}:7000"
    except Exception as exc:
        logger.debug(f"No pude preguntarle la IP al ayudante de WiFi: {exc}")

    # 2. Respaldo: la ruta de salida de este proceso.
    if not url:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))  # no manda nada: solo resuelve la ruta
                ip = s.getsockname()[0]
            if ip and not _es_ip_de_docker(ip):
                url = f"https://{ip}:7000"
        except Exception:
            pass

    _dashboard_cache.update(url=url, at=ahora)
    return url


# Las redes que Docker se inventa para sus contenedores. 172.16/12 es el rango
# que usa por defecto; 192.168.x.x lo usa tambien cuando se queda sin sitio
# (es lo que paso en esta placa), y ahi hay que hilar mas fino porque 192.168
# es TAMBIEN el rango de casi cualquier casa: solo se descarta si esa IP cae en
# una red que este contenedor tenga montada, que es justo lo que delata que es
# suya y no de la red del colegio.
def _es_ip_de_docker(ip):
    if ip.startswith("127."):
        return True
    try:
        import ipaddress

        addr = ipaddress.ip_address(ip)
        if addr in ipaddress.ip_network("172.16.0.0/12"):
            return True
        # /proc/net/route trae las redes de las interfaces de este contenedor.
        import socket as _s
        import struct

        with open("/proc/net/route") as f:
            for linea in f.readlines()[1:]:
                campos = linea.split()
                if len(campos) < 8 or campos[1] == "00000000":
                    continue  # la default no dice a que red pertenece
                destino = _s.inet_ntoa(struct.pack("<L", int(campos[1], 16)))
                mascara = _s.inet_ntoa(struct.pack("<L", int(campos[7], 16)))
                if addr in ipaddress.ip_network(f"{destino}/{mascara}", strict=False):
                    return True
    except Exception:
        pass
    return False


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
# En Essentials lo que se paga es la carga del modelo local (~22 s): ver
# brain.warmup(). Lo mismo con los canales de STT/TTS de Google.
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

# Lo que el robot DICE mientras se ve el aviso vive en python/intro.py, junto
# con el resto de las locuciones del arranque (ahi esta explicado por que).
AVISO_TEXTO = intro.AVISO

def _welcome_text():
    """Lo que se ESCRIBE en el dashboard durante la bienvenida.

    Lo que se OYE es la locucion grabada intro_bang (ver python/intro.py): no
    depende del modo ni de cuantos guias haya, porque solo cuenta que es BANG
    y quien lo hizo. A quien te acompaña lo dice el paso siguiente
    (intro_plus / intro_essential).
    """
    return intro.text("intro_bang")


# --- Locuciones grabadas ------------------------------------------------------


def _locucion(clave, barge=None, status=None, music=False):
    """Dice una locucion del arranque y devuelve lo que paso.

    Primero intenta el MP3 grabado (assets/audio/<clave>.mp3, ver
    tools/make_intro_audio.py): suena siempre igual, no cuesta cuota y
    funciona sin red, que es lo que hace que el arranque de Essentials suene
    tan bien como el de Plus.

    Si el MP3 no esta —instalacion nueva, o alguien vacio assets/audio/— se
    sintetiza el mismo texto con la voz del momento. Nunca hay un paso mudo.

    music=True le pone debajo el colchon lo-fi (voice._lofi_bed).
    """
    text = intro.text(clave)
    if status:
        _broadcast_status(status)
    ui.send_message("reply", {"persona": WELCOME_VOICE, "text": text})
    res = voice.say_clip(clave, text=text, barge=barge, music=music)
    if res.get("ok"):
        return res
    _broadcast_debug(f"⚠ falta la locución grabada '{clave}': la digo con la voz del momento")
    return voice.say(WELCOME_VOICE, text, barge=barge)


def _warm_clips():
    """Deja las locuciones del arranque decodificadas antes de que hagan falta."""
    try:
        voice.warm_clips(list(intro.CLIPS))
    except Exception as exc:
        _broadcast_debug(f"⚠ no pude precargar las locuciones: {exc}")


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


# Paso 4 del arranque: el niño elige COMO quiere conversar. Se pregunta por
# voz (no hay botones en el robot) y se acepta tambien por el dashboard.
MODO_WAIT_S = 40.0  # cuanto se espera la respuesta
_MODO_FOLLOW = "__modo__"  # clave falsa: listen_turn() devuelve con esto lo que se oiga
_MODO_PREGUNTA = (
    "Antes de empezar, dime cómo quieres que hablemos. Si dices BANG, te acompaño paso a paso a "
    "convertir tu reto en ideas, con mis cinco guías. Si dices CURIOSO, charlamos libre: me preguntas "
    "lo que quieras y hasta puedes pedirme que me ponga feliz o que baile. ¿BANG o Curioso?"
)
_MODO_REPITE = "No te escuché bien. Dime BANG, o dime Curioso."
_modo_lock = threading.Lock()
_pending_turn = None  # (wake, texto) que ya se oyo y loop() tiene que atender


def _elegir_modo(intentos=2):
    """Pregunta por voz BANG o Curioso y deja el modo elegido.

    Devuelve True si el niño, en vez del modo, dijo el nombre de un guía: ahí
    el modo se queda como estaba y ese turno pasa a loop() por _pending_turn
    (no se pierde lo que dijo).
    """
    global _pending_turn
    if not _modo_lock.acquire(blocking=False):
        return False  # ya se está preguntando
    try:
        for intento in range(intentos):
            texto = _MODO_PREGUNTA if intento == 0 else _MODO_REPITE
            _broadcast_status("🎛 ¿BANG o Curioso? dilo en voz alta")
            ui.send_message("reply", {"persona": WELCOME_VOICE, "text": texto})
            gestures.send(gestures.TALK, guides.default_guide())
            # La pregunta también se puede interrumpir: si el niño contesta
            # antes de que termine, lo que oyó Vosk sirve de respuesta.
            res = voice.say(WELCOME_VOICE, texto, barge=voice.make_barge(WELCOME_VOICE, tuple(brain.PERSONAS)))
            gestures.send(gestures.REST, guides.default_guide())
            wake, text = voice.listen_turn(
                list(brain.PERSONAS) + list(AUTO_WAKE), follow_up=_MODO_FOLLOW,
                follow_up_s=MODO_WAIT_S, name_only=tuple(brain.PERSONAS) + AUTO_WAKE,
            )
            if wake in brain.PERSONAS:
                _pending_turn = (wake, text or "")
                _broadcast_debug(f"🎛 eligió guía sin elegir modo: sigo en {curioso.MODE_NAMES[curioso.mode()]}")
                return True
            dicho = f"{wake if wake in AUTO_WAKE else ''} {text or ''}".strip()
            if not dicho:
                # No alcanzó a entrar al STT, pero la escucha activa sí lo oyó.
                dicho = ((res.get("trigger") or {}).get("text") or "").strip()
            m = curioso.find_mode(dicho, bare=True)
            if m:
                _set_chat_mode(m, hablado=True)
                return False
        _set_chat_mode(curioso.mode(), hablado=True)
        return False
    finally:
        _modo_lock.release()


# --- Paso 4: el panel de control (QR) -----------------------------------------

# Cuanto se queda el QR en la pantalla. La locucion dura ~18 s, y con eso no
# alcanzaba: hay que sacar el celular, desbloquearlo, abrir la camara y
# apuntar. Un minuto es el tiempo real de una persona haciendo eso sin prisa.
#
# OJO: ese minuto NO se pasa esperando. La primera version hacia sleep(42) con
# el robot mudo y la pantalla quieta, y el arranque se sentia colgado. Ahora el
# QR se queda puesto y el arranque SIGUE: mientras se ve el codigo, el robot ya
# esta preguntando si PLUS o ESSENTIAL. El QR se retira solo al cumplirse el
# minuto, o antes si hay que pintar otra cosa encima (el menu de guias).
#
# Que la cara no lo borre esta cubierto por el sketch: con el QR puesto, un
# gesto solo cambia el color de fondo, no repinta (ver consumePendingGesture).
QR_PANEL_S = 60.0


def _show_qr_panel():
    """Muestra el QR del dashboard en la pantalla y avisa de que existe.

    Va dirigido a la persona adulta que esta con el niño: el panel es donde se
    ve lo que el robot oye y responde, se cambia el cerebro y se le escribe.
    Si no se sabe que existe, no se usa — de ahi que sea un paso del arranque
    y no una linea en el README.

    El QR va SOLO en la pantalla del robot, nunca en el dashboard: el codigo
    sirve para LLEGAR al dashboard, asi que enseñarlo dentro del dashboard no
    le sirve a nadie — quien lo ve ya esta dentro.

    Se calcula con la IP de AHORA (gestures.send_qr), asi sigue siendo
    correcto cuando la placa cambia de red. assets/img/qr_webside/ guarda una
    copia del mismo codigo, solo para documentacion.

    Devuelve el instante (time.monotonic) hasta el que el codigo tiene que
    quedarse puesto; quien lo retira es _quitar_qr(), mas tarde.
    """
    url = dashboard_url()
    if not url:
        # Sin red no hay direccion a la que llevar: decir "apunta la camara al
        # codigo" sin codigo en la pantalla es peor que callarse. El paso de
        # red (_network_if_needed) ya enseña su propio QR cuando hace falta.
        _broadcast_debug("📱 me salto el QR del panel: todavía no tengo dirección en la red")
        return
    t0 = time.monotonic()
    info = gestures.send_qr(url)
    if not info.get("ok"):
        _broadcast_debug(f"⚠ no pude mostrar el QR: {info.get('error')}")
        return 0.0
    _locucion("intro_panel", status="📱 panel de control: apunta la cámara al código")
    # No se espera aqui: el arranque sigue con el codigo puesto. Devuelve hasta
    # cuando tiene que quedarse, y _boot_sequence() lo retira a su hora.
    return t0 + QR_PANEL_S


def _quitar_qr(hasta):
    """Retira el QR de la pantalla, esperando si no ha cumplido su minuto."""
    restante = hasta - time.monotonic()
    if restante > 0:
        _broadcast_status(f"📱 el código sigue en pantalla {int(restante)} s más")
        time.sleep(restante)
    gestures.send_qr()


# --- Paso 5: que cerebro usa el robot -----------------------------------------
# Esto se elige ANTES que nada porque de ello depende todo lo que viene
# despues: con PLUS hay cinco guias y se muestra el menu; con ESSENTIAL hay
# una sola (Cristal) y el menu NO se muestra.

CEREBRO_WAIT_S = 40.0  # cuanto se espera la respuesta
_cerebro_lock = threading.Lock()


def _elegir_cerebro(intentos=2):
    """Pregunta por voz PLUS o ESSENTIAL y deja el cerebro elegido.

    Devuelve True si el niño, en vez del cerebro, dijo el nombre de un guia:
    ahi se queda el cerebro que estuviera guardado y ese turno pasa a loop()
    por _pending_turn, para no perder lo que dijo.

    Si no contesta en dos intentos se queda el guardado (que de fabrica es
    PLUS) y el arranque sigue: dejar al robot preguntando para siempre es peor
    que elegir por el.
    """
    global _pending_turn
    if not _cerebro_lock.acquire(blocking=False):
        return False  # ya se está preguntando
    try:
        for intento in range(intentos):
            clave = "menu_cerebro" if intento == 0 else "menu_cerebro_repite"
            gestures.send(gestures.TALK, guides.default_guide())
            # Se puede contestar sin esperar a que termine la pregunta: lo que
            # oiga la escucha activa mientras habla ya vale de respuesta.
            res = _locucion(
                clave,
                barge=voice.make_barge(WELCOME_VOICE, tuple(brain.PERSONAS)),
                status="🧠 ¿PLUS o ESSENTIAL? dilo en voz alta",
            )
            gestures.send(gestures.REST, guides.default_guide())
            wake, text = voice.listen_turn(
                list(brain.PERSONAS) + list(AUTO_WAKE), follow_up=_MODO_FOLLOW,
                follow_up_s=CEREBRO_WAIT_S, name_only=tuple(brain.PERSONAS) + AUTO_WAKE,
            )
            if wake in brain.PERSONAS:
                _pending_turn = (wake, text or "")
                _broadcast_debug(f"🧠 eligió guía sin elegir cerebro: sigo en {llm_router.MODE_LABELS[llm_router.mode()]}")
                return True
            dicho = f"{wake if wake in AUTO_WAKE else ''} {text or ''}".strip()
            if not dicho:
                # No alcanzó a entrar al STT, pero la escucha activa sí lo oyó.
                dicho = ((res.get("trigger") or {}).get("text") or "").strip()
            elegido = llm_router.find_cerebro(dicho, bare=True)
            if elegido:
                _broadcast_debug(_set_llm_mode(elegido))
                return False
        _broadcast_debug(f"🧠 nadie eligió: me quedo en {llm_router.MODE_LABELS[llm_router.mode()]}")
        return False
    finally:
        _cerebro_lock.release()


# --- Paso 6: que se cuenta despues, segun el cerebro elegido -------------------


def _presentar_plus():
    """Rama PLUS: como funciona la app y quienes son los cinco guias.

    Al terminar se muestra el menu de los cinco en la pantalla, para que el
    niño vea las caras mientras decide a quien llamar.
    """
    _locucion("intro_plus", status="✨ modo PLUS: te presento a los 5 guías")
    _show_menu()


def _presentar_essentials():
    """Rama ESSENTIAL: lo que este modo NO puede hacer, dicho de frente.

    Aqui NO se muestra el menu de guias, y es a proposito: en este modo solo
    esta Cristal (guides.ESSENTIALS_GUIDE). Enseñar cinco caras y que conteste
    siempre la misma seria prometer lo que no hay — es el mismo motivo por el
    que la locucion dice que las respuestas son mas lentas y que, para algo
    mejor, hay que pasarse a PLUS.
    """
    gestures.send_menu()  # por si quedaba abierto de un arranque anterior
    _locucion("intro_essential", status="🧠 modo ESSENTIAL: te acompaña Cristal")
    gestures.send(gestures.REST, guides.ESSENTIALS_GUIDE)
    _broadcast_status(f"🎤 di {brain.PERSONAS[guides.ESSENTIALS_GUIDE]['name']} y cuéntale tu reto")


def _presentar_cerebro():
    """La rama que toque segun el cerebro que quedo elegido."""
    if llm_router.is_local():
        _presentar_essentials()
    else:
        _presentar_plus()


def _show_aviso():
    """Paso 1: el aviso de seguridad, con campanilla y dicho en voz alta.

    Suena una campanilla de atencion, y mientras el GIF corre el robot LEE el
    aviso (grabado, con musica lo-fi debajo). La pantalla no pasa al siguiente
    paso hasta que termina de hablar (con un minimo de AVISO_S por si la voz
    falla y no suena nada).
    """
    gestures.send_aviso(True)
    t0 = time.monotonic()
    try:
        voice.attention()          # campanilla: "atencion, esto importa"
        time.sleep(0.35)
        # Grabado, y con la musica lo-fi por debajo. Antes se sintetizaba con
        # Google en cada encendido: eran ~10 s de robot callado antes de la
        # primera palabra, y sin red no sonaba.
        _locucion("intro_aviso", status="🛡 aviso de seguridad", music=True)
    except Exception as exc:
        _broadcast_debug(f"⚠ el aviso no se pudo decir en voz alta: {exc}")

    # Si la voz fue mas corta que el minimo (o no sonó), se completa.
    restante = AVISO_S - (time.monotonic() - t0)
    if restante > 0:
        time.sleep(restante)
    gestures.send_aviso(False)


def _welcome():
    """Paso 2: que es BANG y quien lo hizo (GIF + la locucion grabada).

    Es la carta de presentacion del producto, y por eso va GRABADA
    (assets/audio/intro_bang.mp3, ver python/intro.py): suena igual de bien en
    una feria sin red que en el laboratorio, y suena igual en los dos modos.

    Solo eso: quien encadena los pasos es _boot_sequence().
    """
    if not _welcome_lock.acquire(blocking=False):
        return  # ya hay una sonando
    try:
        gestures.send_splash(True)
        # Con el GIF en pantalla el gesto solo mueve los brazos.
        gestures.send(gestures.HAPPY, guides.default_guide())
        # Con musica lo-fi por debajo: es la presentacion del producto y se
        # oye en ferias y auditorios, donde el silencio entre frases suena a
        # que el robot se colgo.
        _locucion("intro_bang", status="🎉 ¡Bienvenidos a BANG!", music=True)
        gestures.send_splash(False)
    finally:
        gestures.send(gestures.REST, guides.default_guide())
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


# UN SOLO ARRANQUE A LA VEZ.
#
# Sin esto, /arranque (o /menu, o /bienvenida) lanzaba una secuencia NUEVA
# encima de la que ya estaba corriendo, y las dos se peleaban por la pantalla y
# por el parlante. Se vio asi en una traza real: mientras una estaba en el paso
# del QR —que se queda su minuto— la otra llego al paso de los guias y pinto el
# MENU ENCIMA, asi que el QR desaparecia a los dos segundos de salir.
#
# Los candados que ya habia (_welcome_lock, _cerebro_lock) no alcanzaban: cada
# uno protege SU paso, asi que la segunda secuencia se saltaba los pasos
# ocupados y adelantaba a la primera, que es exactamente lo que no se quiere.
_boot_lock = threading.Lock()


def boot_running():
    return _boot_lock.locked()


def _boot_sequence():
    """El arranque completo, en este orden:

        1. ADVERTENCIA  aviso de seguridad, con campanilla y musica (_show_aviso())
        2. BANG         que es BANG y que lo hizo la CUN (_welcome())
        3. RED          solo si no hay internet: QR + panel de red
        4. PANEL        el QR del dashboard, para el adulto, un minuto en
                        pantalla (_show_qr_panel())
        5. CEREBRO      PLUS o ESSENTIAL, elegido por voz (_elegir_cerebro())
        6. PRESENTACION la que toque segun el paso 5:
                        PLUS      -> como funciona la app + los 5 guias + menu
                        ESSENTIAL -> sus limites + Cristal, y SIN menu de guias
        7. AGENTES      la conversacion, que la lleva loop() cuando el niño
                        dice un nombre (ver _greet())

    La red va ANTES del QR del panel a proposito: la direccion del dashboard
    depende de la red, asi que preguntarla antes de tenerla daria un QR vacio.

    Del paso 5 depende todo lo demas, y por eso se pregunta antes de presentar
    a nadie: no se puede ofrecer cinco guias y despues resultar que solo hay uno.

    El sketch ya arranca mostrando el aviso sin esperar a Python (por si tarda
    o se cae); aqui solo se le dice cuando pasar al siguiente paso.

    Como quiere hablar (BANG o Curioso) ya NO se pregunta aqui: alargaba el
    arranque justo despues de haberle dicho al niño que dijera un nombre, y se
    contradecian. Se sigue pudiendo cambiar en cualquier momento diciendo
    "modo curioso" / "modo bang", con /modo_chat o con /elegir_modo.
    """
    if not _boot_lock.acquire(blocking=False):
        _broadcast_debug("▶️ ya hay un arranque en curso: no lo duplico")
        return
    try:
        _show_aviso()
        _welcome()
        _network_if_needed()
        # El QR se queda puesto mientras se pregunta el cerebro: asi cumple su
        # minuto en pantalla sin que nadie espere mirando una pantalla quieta.
        qr_hasta = _show_qr_panel()
        try:
            if _elegir_cerebro():
                return  # dijo el nombre de un guia: loop() atiende ese turno
        finally:
            _quitar_qr(qr_hasta)
        _presentar_cerebro()
    finally:
        _boot_lock.release()


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
    global _current_persona, _follow_up, _awaiting_aporte
    for key in brain.PERSONAS:
        bang.reset(key)
    _current_persona = None
    _follow_up = None
    _awaiting_aporte = None
    ui.send_message("active_persona", {"key": None})
    _broadcast_bang(None)

    def _volver_al_inicio():
        # No se vuelve a preguntar el cerebro: eso lo elige un adulto una vez
        # (y sigue en /modo). Solo se repite la bienvenida y la presentacion
        # que toque — sin esto la pantalla se quedaba en la bienvenida.
        #
        # Bajo el mismo candado que el arranque: si no, esto le pintaba el menu
        # encima a una secuencia que seguia corriendo.
        if not _boot_lock.acquire(blocking=False):
            _broadcast_debug("🏠 hay un arranque en curso: no le pinto encima")
            return
        try:
            _welcome()
            _presentar_cerebro()
        finally:
            _boot_lock.release()

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
    msg = curioso.greeting(persona) if curioso.is_curioso() else f"¡Hola! Soy {p['name']}, {p['tagline'].lower()}. Cuéntame, ¿cuál es tu reto?"
    ui.send_message("reply", {"persona": persona, "text": msg})
    if llm_router.is_local():
        # Mientras se presenta, el modelo local lee y cachea el prompt del guia.
        threading.Thread(target=llm_router.warmup_local, args=(brain.local_system(persona),), daemon=True, name="llm-warmup").start()
    _broadcast_status(f"🔊 {p['name']} está hablando...")
    gestures.send(gestures.WAVE, persona)  # se presenta: saluda con el brazo
    if _speak(persona, msg):
        return  # lo interrumpieron: _on_barge() ya dejo todo listo
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
    if curioso.is_curioso():
        # En Curioso no hay reto que clasificar: sigue el guia de siempre (o
        # Crispi), y asi no se paga una llamada al LLM por cada pregunta.
        if _current_persona in unlocked:
            return _current_persona, ""
        persona = unlocked[0] if unlocked else guides.default_guide()
        return persona, f"Te acompaño yo, {brain.PERSONAS[persona]['name']}. "
    if guardrails.respuesta_fija(text, ""):
        # Pregunta de identidad, datos privados...: la contesta bang.turn()
        # con respuesta fija; no vale la pena clasificar.
        persona = _current_persona if _current_persona in unlocked else guides.default_guide()
        return persona, ""
    if len(unlocked) == 1:
        # Con un solo guia no hay nada que clasificar: nos ahorramos una
        # llamada entera al LLM.
        persona = unlocked[0]
    else:
        _broadcast_status("🧭 eligiendo el mejor guía para tu reto...")
        persona = bang.classify(text, unlocked)
        if persona not in unlocked:
            persona = guides.default_guide()
    return persona, f"Para este reto te acompaño yo, {brain.PERSONAS[persona]['name']}. "


def _o(key):
    """Terminacion de genero para concordar con el guia: Cesia, Cori y
    Cristal son mujeres ("bloqueada"), Crispi y Carmel hombres ("bloqueado")."""
    return "a" if brain.PERSONAS[key]["gender"] == "f" else "o"


def _say_locked(wake):
    """El niño llamo a un guia que ahora no esta: contesta el que si esta.

    Son dos situaciones distintas y el mensaje tiene que notarlo. En Plus es un
    candado (/lock_cesia) y se puede quitar. En Essentials no hay candado: el
    modo entero corre con un solo guia (guides.ESSENTIALS_GUIDE), asi que lo
    honesto es decir que en este modo esta ella sola, no que "esta bloqueada".
    """
    name = brain.PERSONAS[wake]["name"]
    quien = guides.default_guide()
    yo = brain.PERSONAS[quien]["name"]
    gestures.send_card()
    if guides.essentials_only():
        _broadcast_status(f"🔒 en modo Essentials solo está {yo}")
        msg = (f"En este modo estoy yo sola, {yo}. {name} vuelve cuando cambien el robot a modo Plus. "
               f"Mientras tanto te acompaño yo: cuéntame tu reto.")
    else:
        _broadcast_status(f"🔒 {name} todavía está bloquead{_o(wake)}")
        msg = f"{name} todavía está bloquead{_o(wake)}. Por ahora te acompaño yo, {yo}: di {yo} y cuéntame tu reto."
    ui.send_message("reply", {"persona": quien, "text": msg})
    gestures.send(gestures.TALK, quien)
    voice.say(quien, msg)
    gestures.send(gestures.REST, quien)


# --- Escucha activa y cambio de guia -------------------------------------------
# Mientras el guia habla, voice.BargeIn escucha en local (Vosk, sin costo de
# API) su nombre y frases como "se me ocurrio algo" o "pasame con Cesia". Si
# dispara, la voz se corta, la boca se cierra y:
# - "pasame con <guia>" (o el nombre de OTRO guia): _switch_to(), que le pasa
#   el reto entero al nuevo guia (bang.handover) y lo presenta;
# - si no: el guia dice "¡Dime!" (audio en cache, sin esperar al TTS) y lo
#   proximo que le digan es un APORTE (bang.contribute) en vez de un turno.


def _speak(persona, text, depth=0):
    """voice.say() con escucha activa. SIEMPRE con escucha: el guía nunca
    habla encima del niño, pase lo que pase (tampoco en la respuesta a un
    aporte, ni en la tercera interrupcion seguida).

    Devuelve True si lo interrumpieron: en ese caso _on_barge() ya atendio la
    interrupcion y el que llama NO debe tocar _follow_up ni el gesto."""
    barge = voice.make_barge(persona, tuple(brain.PERSONAS))
    res = voice.say(persona, text, barge=barge)
    if not res.get("interrupted"):
        return False
    _on_barge(persona, res, depth)
    return True


def _on_barge(persona, res, depth):
    global _follow_up, _awaiting_aporte
    trig = res.get("trigger") or {}
    name = brain.PERSONAS[persona]["name"]
    gestures.send(gestures.REST, persona)  # la boca ya la cerro voice (visema 0)
    bang.mark_interrupted(persona, res.get("spoken", ""))
    ui.send_message("interrupted", {
        "persona": persona, "spoken": res.get("spoken", ""), "kind": trig.get("kind"),
        "to": trig.get("persona"), "text": trig.get("text", ""),
    })
    _broadcast_bang(persona)
    target = trig.get("persona")
    if trig.get("kind") == "switch" and target in brain.PERSONAS and target != persona:
        _switch_to(target, persona, depth=depth + 1)
        return
    # "speech": el niño ya está hablando. El guía NO contesta nada (hablarle
    # encima es justo lo que hay que evitar): se calla y escucha. Solo cuando
    # la interrupción fue una palabra clave corta ("¡Cori!", "espera") dice
    # "¡Dime!", porque ahí el niño está esperando turno.
    hablando = trig.get("kind") == "speech"
    if hablando:
        _broadcast_status(f"✋ {name} se calló: te escucha")
    else:
        _broadcast_status(f"✋ {name} te escucha: cuéntale tu idea")
        gestures.send(gestures.TALK, persona)
        voice.say_cached(persona, "dime")
        gestures.send(gestures.REST, persona)
    _awaiting_aporte = {
        "persona": persona, "spoken": res.get("spoken", ""), "depth": depth + 1,
        "at": time.monotonic(),
        # Lo que Vosk ya entendió: si el STT de Google no alcanza a captar nada
        # (pasa cuando el niño dice una frase corta justo al interrumpir), se
        # usa esto en vez de perder el turno.
        "oido": trig.get("text", "") if hablando else "",
    }
    _follow_up = persona


def _switch_to(new, old, depth=0, ask=True):
    """Cambio de guia pedido por el niño ("quiero hablar con Cori"): cara del
    nuevo guia, el reto pasa con todo (fase, pregunta, ideas, aportes) y un
    saludo de plantilla que lo cuenta, sin llamar al LLM. Devuelve "ok",
    "locked" o "interrupted" (el saludo tambien se puede interrumpir)."""
    global _current_persona, _follow_up, _awaiting_aporte
    _awaiting_aporte = None
    if not guides.is_unlocked(new):
        if old in brain.PERSONAS and guides.is_unlocked(old):
            # Lo dice el guia que ya estaba (con su cara), no Crispi: el reto
            # sigue con el.
            name = brain.PERSONAS[new]["name"]
            msg = f"{name} todavía está bloquead{_o(new)}, así que sigo yo contigo. ¿Seguimos?"
            _broadcast_status(f"🔒 {name} todavía está bloquead{_o(new)}")
            gestures.send_card()
            ui.send_message("reply", {"persona": old, "text": msg})
            gestures.send(gestures.TALK, old)
            voice.say(old, msg)
            gestures.send(gestures.REST, old)
        else:
            _say_locked(new)
        _follow_up = old
        return "locked"
    s = bang.handover(old, new) if old else None
    if s is None and bang.has_reto(new):
        s = bang.session(new)  # ya tenia su propio reto: lo retoma
    gestures.send_card()
    gestures.send_menu()  # por si estaba el menu: aparece su cara
    _current_persona = new
    ui.send_message("active_persona", {"key": new})
    _broadcast_bang(new)
    if old and old != new:
        carried = "con el reto" if s is not None and s.relevo == old else "sin reto en curso"
        _broadcast_debug(f"🔀 {brain.PERSONAS[old]['name']} → {brain.PERSONAS[new]['name']} ({carried})")
    msg = curioso.greeting(new) if curioso.is_curioso() else bang.handover_greeting(new, s, ask=ask)
    ui.send_message("reply", {"persona": new, "text": msg})
    if llm_router.is_local():
        threading.Thread(target=llm_router.warmup_local, args=(brain.local_system(new),), daemon=True, name="llm-warmup").start()
    _broadcast_status(f"🔊 {brain.PERSONAS[new]['name']} está hablando...")
    gestures.send(gestures.WAVE, new)  # llega al relevo: saluda
    if _speak(new, msg, depth):
        return "interrupted"
    gestures.send(gestures.REST, new)
    _follow_up = new
    return "ok"


# Frases de relleno (Essentials, o Plus cuando Gemini se demora). Los textos
# viven en voice._CACHED_PHRASES ("pensar_0", "pensar_1"): van cacheados a
# disco para que suenen al instante (ver alli por que importa tanto en local).
_filler_turn = 0
# s sin respuesta de Gemini antes de decir la frase de relleno. Estaba en 3,0,
# pero con la peticion escalonada (brain.HEDGE_S) un turno lento contesta en
# ~2,5-3 s: el relleno arrancaba justo cuando llegaba la respuesta, y entonces
# la respuesta tenia que esperar a que terminara el relleno (~2 s mas).
PLUS_FILLER_AFTER = 3.5

_SOURCE_LABELS = {"gemini": "☁️ Gemini", "gemini_web": "☁️🔎 Gemini + internet", "local": "🧠 modelo local",
                  "fijo": "🛡 respuesta fija", "plantilla": "📋 plantilla"}


def _say_filler(persona):
    global _filler_turn
    _filler_turn += 1
    try:
        # Gesto de pensar: un brazo arriba, quieto. Es exactamente lo que dice
        # la frase ("déjame pensarlo"), y se nota que el robot no se colgó.
        gestures.send(gestures.THINK, persona)
        voice.say_cached(persona, voice.FILLER_KEYS[_filler_turn % len(voice.FILLER_KEYS)])
    except Exception as exc:
        _broadcast_debug(f"⚠ no pude decir la frase de relleno: {exc}")


def _turn_worker(job, box):
    try:
        box["result"] = job()
    except Exception as exc:
        _broadcast_debug(f"⚠ el turno falló: {exc}")


_welcomed = False


def loop():
    global _current_persona, _follow_up, _welcomed, _awaiting_aporte, _pending_turn

    if not _welcomed:
        _welcomed = True
        # Decodificar el MP3 y buscarle el pulso cuesta ~3 s: se hace ahora, en
        # segundo plano, para que cuando el niño pida la canción suene ya.
        threading.Thread(target=song.warmup, args=(voice.TTS_SAMPLE_RATE,), daemon=True, name="song-warmup").start()
        # Y lo mismo con las locuciones del arranque: decodificar cada MP3
        # cuesta ~1 s, y la primera se necesita ya. Va antes de _boot_sequence()
        # para que la bienvenida no espere al ffmpeg.
        _warm_clips()
        _boot_sequence()

    # Mientras se le pregunta algo en el arranque (el cerebro con
    # /elegir_cerebro, el modo de charla con /elegir_modo) manda ESA escucha:
    # dos sesiones de microfono a la vez se pisan.
    while _modo_lock.locked() or _cerebro_lock.locked():
        time.sleep(0.2)

    follow_up, _follow_up = _follow_up, None
    pendiente, _pending_turn = _pending_turn, None
    if pendiente:
        # Ya se oyó mientras se elegía el modo: no se vuelve a escuchar.
        wake, text = pendiente
        switch = False
    else:
        if follow_up:
            _broadcast_status(f"🎧 te escucho... responde a {brain.PERSONAS[follow_up]['name']} sin decir su nombre.")
        else:
            _broadcast_status("🎧 escuchando... di 'Crispi' (o 'robot') y tu pregunta.")
        wake, text = voice.listen_turn(
            list(brain.PERSONAS.keys()) + list(AUTO_WAKE), follow_up=follow_up, name_only=tuple(brain.PERSONAS)
        )
        switch = voice.last_switch()

    # Tras una interrupcion, lo siguiente que le dicen a ESE guia es un aporte.
    espera, _awaiting_aporte = _awaiting_aporte, None
    if espera and time.monotonic() - espera["at"] <= APORTE_WAIT_S and espera.get("oido") and not (wake and text):
        # El niño interrumpió con una frase corta y el STT de Google no alcanzó
        # a abrirse: se usa lo que Vosk ya entendió en local, para no perder el
        # turno ni obligarlo a repetir.
        wake, text, switch = espera["persona"], espera["oido"], False
        _broadcast_debug(f"🗣 uso lo que oí al interrumpir: «{text}»")
    aporte = espera
    if aporte and (switch or wake != aporte["persona"] or time.monotonic() - aporte["at"] > APORTE_WAIT_S):
        aporte = None
    # Lo que el guia alcanzo a decir antes de que lo cortaran. En BANG se guarda
    # en la sesion (bang.mark_interrupted) y viaja en _context(); en Curioso no
    # hay sesion donde guardarlo, asi que se lleva a mano hasta curioso.turn().
    cortado = (aporte or {}).get("spoken", "") if aporte else ""
    if curioso.is_curioso():
        aporte = None  # en Curioso no hay reto: una interrupción es un turno más

    # Cambio de guia: "quiero hablar con Cori", "pasame a Cristal"... O solo
    # el nombre de otro guia mientras hay un reto en curso: el reto se pasa.
    if wake in brain.PERSONAS and (
        switch or (not text and _current_persona and wake != _current_persona and bang.has_reto(_current_persona))
    ):
        voice.ack()
        if text:
            ui.send_message("heard", {"persona": wake, "text": text})
        if _switch_to(wake, _current_persona, ask=not text) != "ok" or not text:
            return
        # Traia algo mas ("pasame con Cori, se me ocurrio un mural"): eso
        # sigue como turno normal del guia nuevo.
        _follow_up = None

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

    # "Modo curioso", "cambia a modo BANG": cambia como conversa el robot y lo
    # dice en voz alta. Va antes del turno: no se le pasa al LLM.
    pedido = curioso.find_mode(text)
    if pedido and pedido != curioso.mode():
        voz = _current_persona if _current_persona in brain.PERSONAS and guides.is_unlocked(_current_persona) else None
        ui.send_message("heard", {"persona": voz or guides.default_guide(), "text": text})
        voice.ack()
        _set_chat_mode(pedido, hablado=True, voz=voz)
        _follow_up = voz
        return

    heard_at = time.monotonic()
    if not switch:
        voice.ack()  # "te escuche": suena mientras el LLM piensa (en el cambio ya sono)

    if wake in brain.PERSONAS and not guides.is_unlocked(wake):
        ui.send_message("heard", {"persona": wake, "text": text})
        _say_locked(wake)
        return

    persona, intro = _pick_persona(wake, text)
    _current_persona = persona
    name = brain.PERSONAS[persona]["name"]
    ui.send_message("active_persona", {"key": persona})
    if not switch:
        ui.send_message("heard", {"persona": persona, "text": text, "aporte": bool(aporte)})
    _broadcast_status(f"🤔 {name} está pensando...")

    # El turno se piensa en un hilo. Essentials: el modelo local tarda
    # (~10-20 s), asi que el guia dice enseguida una frase corta para que nadie
    # crea que se colgo. Plus: Gemini suele contestar en 1-2 s; si se demora
    # mas de PLUS_FILLER_AFTER (503, cuota, respaldo local), tambien la dice.
    # slow_turn() se mira ANTES de arrancar el hilo: turn() cambia la sesion.
    # Un aporte tras "¡Dime!" va por bang.contribute(): en Essentials es una
    # plantilla (sin modelo local), en Plus una sola llamada a Gemini.
    depth = 0
    if aporte:
        depth = aporte["depth"]
        spoken = aporte["spoken"]
        slow = not bang.has_reto(persona) and bang.slow_turn(persona, text)
        job = lambda: bang.contribute(persona, text, spoken, use_llm=not llm_router.is_local())  # noqa: E731
        _broadcast_status(f"💡 {name} agrega tu aporte al reto...")
    elif curioso.is_curioso():
        # Modo Curioso: charla libre con la personalidad del guía, mas las
        # ordenes cortas ("ponte feliz", "baila"), que no pasan por el LLM.
        # Si venia de una interrupcion, el guia retoma donde lo cortaron en vez
        # de arrancar de cero (es lo que hace que la charla no se sienta a
        # saltos cuando el niño le habla encima).
        slow = curioso.slow_turn(persona, text)
        job = lambda: curioso.turn(persona, text, interrumpido=cortado)  # noqa: E731
    else:
        slow = bang.slow_turn(persona, text)
        job = lambda: bang.turn(persona, text)  # noqa: E731
    box = {}
    worker = threading.Thread(target=_turn_worker, args=(job, box), daemon=True, name="turn")
    worker.start()
    if slow:
        _broadcast_status(f"🤔 {name} está pensando (modelo local)...")
        _say_filler(persona)
    else:
        worker.join(PLUS_FILLER_AFTER)
        if worker.is_alive():
            _say_filler(persona)
    worker.join()
    result = box.get("result") or bang.Turn(brain.FALLBACK_REPLY)
    reply = intro + result.reply
    # En Curioso, "ponte triste" trae su propio gesto; si no, se saca del texto.
    pedido_gesto = getattr(result, "gesture", None)
    gesture = pedido_gesto if pedido_gesto is not None else gestures.emotion_of(reply)
    _broadcast_debug(f"⏱ respuesta en {time.monotonic() - heard_at:.1f}s ({_SOURCE_LABELS.get(result.source, result.source or '?')})")

    ui.send_message("reply", {"persona": persona, "text": reply})
    _broadcast_bang(persona)

    # Paso de fase BANG: se celebra antes de hablar, con la cancioncita y el
    # baile de brazos de Diome-chan (ver voice.celebrate()).
    if result.phase_changed:
        _broadcast_status(f"🎉 ¡{bang.PHASE_LABELS[bang.session(persona).phase]}!")
        gestures.send(gestures.CLAP, persona)  # aplaude el paso de fase
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
    if _speak(persona, reply, depth):
        return  # lo interrumpieron: _on_barge() ya dejo el seguimiento armado
    if getattr(result, "sing", False):
        # "¡A despegar!": la canción de assets/audio/, con la boca siguiendo la
        # música y los brazos en el golpe (ver voice.sing() y song.py). Son
        # ~170 s, así que se puede cortar hablando, igual que el baile.
        _broadcast_status(f"🎤 ¡{name} está cantando! (dile algo para parar)")
        res = voice.sing(barge=voice.make_barge(persona, tuple(brain.PERSONAS)))
        gestures.send(gestures.REST, persona)
        if res.get("reason"):
            # Sin canción no se deja al niño con la frase colgando: baila.
            _broadcast_debug(f"⚠ no pude cantar ({res['reason']}): bailo en su lugar")
            res = voice.dance(barge=voice.make_barge(persona, tuple(brain.PERSONAS)))
            gestures.send(gestures.REST, persona)
        if res.get("interrupted"):
            _broadcast_debug("🗣 la canción se cortó porque el niño habló")
            _on_barge(persona, {"interrupted": True, "spoken": "", "trigger": res.get("trigger")}, depth)
            return
    elif getattr(result, "dance", False):
        # "¡Baila!": la cancion larga (~30 s) con la coreografia de brazos,
        # DESPUES de decir la frase (bailar mientras habla se pisa con la
        # boca). Se puede cortar hablando: 30 s sordo serian demasiados.
        _broadcast_status(f"🕺 ¡{name} está bailando! (dile algo para parar)")
        res = voice.dance(barge=voice.make_barge(persona, tuple(brain.PERSONAS)))
        gestures.send(gestures.REST, persona)
        if res.get("interrupted"):
            _broadcast_debug("🗣 el baile se cortó porque el niño habló")
            _on_barge(persona, {"interrupted": True, "spoken": "", "trigger": res.get("trigger")}, depth)
            return
    elif getattr(result, "celebrate", False):
        # Paso de fase o "¡celebra!": la cancioncita corta de siempre.
        _broadcast_status(f"🎉 ¡{name} está celebrando!")
        voice.celebrate()
    gestures.send(gestures.REST, persona)

    # Como Alexa: unos segundos para contestarle sin repetir su nombre.
    _follow_up = persona


App.run(user_loop=loop)
