# Voz del robot: oidos y boca en la propia placa, sin pasar por el celular.
#
# - Escucha (STT): streaming de Google Cloud Speech-to-Text sobre el
#   microfono. Se usa la API "cruda" (no el brick arduino:cloud_asr) porque
#   ese brick solo acepta una api_key simple, y Google Cloud la rechaza
#   ("API keys are not supported by this API"): hace falta la service
#   account de google-credentials.json.
# - Habla (TTS): Google Cloud Text-to-Speech, una voz Chirp3-HD distinta por
#   guia. Si falla (sin internet, credencial invalida...) se cae a espeak si
#   esta disponible; el contenedor de esta App no lo trae instalado (a
#   diferencia del sistema host), asi que en la practica ese turno se salta
#   en vez de sonar robotico.
#
# Ambos usan la MISMA credencial (google-credentials.json, en la raiz de la
# App) porque las dos APIs viven en el mismo proyecto de Google Cloud.
#
# Modo "Alexa" (lo que baja la espera):
# - Fin de frase local: no se espera el is_final de Google (llega 1-2 s
#   despues de que la persona se calla); en cuanto lo transcrito deja de
#   cambiar _ENDPOINT_S, se da la frase por terminada y se lanza.
# - Seguimiento: justo despues de que el guia habla, FOLLOW_UP_S segundos en
#   los que se le puede responder sin volver a decir su nombre.
# - Pitido corto (ack()) apenas se cierra la frase, para saber que escucho.
# - La respuesta se sintetiza por frases EN PARALELO y empieza a sonar en
#   cuanto esta lista la primera, sin esperar el audio completo.
# - Escucha activa (barge-in): mientras el guia habla, Vosk escucha EN LOCAL
#   (gratis) su nombre, "se me ocurrio algo" o "pasame con <guia>", y si oye
#   algo corta la voz al toque (ver BargeIn). Sin vosk se apaga sola.
#
# Salida de audio: si hay una bocina Bluetooth conectada en el host (nodo
# bluez_output.* de PipeWire) se usa esa; si no, el headset USB. Se revisa en
# segundo plano cada _OUTPUT_POLL_S, asi que prender/apagar la bocina cambia
# la salida sola, sin reiniciar la App.

import difflib
import hashlib
import io
import json
import queue
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from google.cloud import speech, texttospeech
from google.oauth2 import service_account

from arduino.app_peripherals.microphone import Microphone
from arduino.app_peripherals.speaker import Speaker
from arduino.app_utils import Logger

import gestures  # solo por las constantes VISEME_* y send_viseme (no importa a voice)

logger = Logger("chat-bang")

# python/voice.py -> parent (python/) -> parent (raiz de la App).
_CREDENTIALS_PATH = Path(__file__).resolve().parent.parent / "google-credentials.json"

_SAMPLE_RATE = 16000  # mic, para el STT
_TTS_SAMPLE_RATE = 24000  # parlante, para el TTS (Chirp3-HD)
_LANGUAGE = "es-CO"

# Una voz Chirp3-HD (las mas naturales del catalogo) por guia, para que se
# distingan al hablar. Crispi y Carmel son hombres (MALE en el catalogo);
# Cesia, Cori y Cristal son mujeres: sus voces tienen que ser FEMALE
# (Algenib, la que tenia Cori antes, es de hombre). Ajustable: `arduino-app-cli` no tiene forma de listar
# voces, pero el catalogo completo sale de
# https://texttospeech.googleapis.com/v1/voices?languageCode=es-US
_VOICES = {
    "crispi": "es-US-Chirp3-HD-Achird",
    "carmel": "es-US-Chirp3-HD-Charon",
    "cesia": "es-US-Chirp3-HD-Autonoe",
    "cori": "es-US-Chirp3-HD-Kore",
    "cristal": "es-US-Chirp3-HD-Achernar",
    # No es un guia: la presentadora de la bienvenida al BANG (main.py).
    "bang": "es-US-Chirp3-HD-Zephyr",
}
_DEFAULT_VOICE = "es-US-Chirp3-HD-Achird"

# Pistas para el STT: sin esto "Crispi" sale "crispy", "Cris pi"... y la
# palabra clave no se reconoce. Los comandos de voz tambien, para que se
# entiendan a la primera.
_PHRASE_HINTS = [
    "Crispi", "Carmel", "Cesia", "Cori", "Cristal", "robot", "Bang",
    "saca una tarjeta", "siguiente fase", "nuevo reto",
]

_RECOGNITION_CONFIG = speech.RecognitionConfig(
    encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
    sample_rate_hertz=_SAMPLE_RATE,
    language_code=_LANGUAGE,
    enable_automatic_punctuation=True,
    speech_contexts=[speech.SpeechContext(phrases=_PHRASE_HINTS, boost=15)],
)
_STREAMING_CONFIG = speech.StreamingRecognitionConfig(
    config=_RECOGNITION_CONFIG,
    interim_results=True,
)

# Lo transcrito tiene que quedarse quieto este tiempo para dar la frase por
# terminada. Mas bajo = responde antes pero corta a quien hace pausas largas.
_ENDPOINT_S = 0.7
# Si solo se oyo "Crispi" y nada mas, cuanto se espera la pregunta.
_WAKE_ONLY_WAIT_S = 6.0
# Para los nombres que valen solos (listen_turn(name_only=...)): tras decir
# "Carmel" y callarse este tiempo, el turno vuelve con el texto vacio y el
# guia se presenta (ver main.py). Mas corto que _WAKE_ONLY_WAIT_S: la persona
# esta esperando que alguien le conteste.
_NAME_ONLY_S = 2.0
# Ventana para responderle al guia sin repetir su nombre.
FOLLOW_UP_S = 7.0

# Como transcribe Google los nombres cuando se equivoca -> clave real.
_WAKE_ALIASES = {
    "crispy": "crispi", "krispy": "crispi", "krispi": "crispi", "crispis": "crispi", "crispie": "crispi",
    "carmelo": "carmel",
    "sesia": "cesia", "zesia": "cesia", "cessia": "cesia",
    "cory": "cori", "kori": "cori", "corey": "cori",
    "crystal": "cristal", "krystal": "cristal",
    "robots": "robot",
    "bank": "bang",
}

if not _CREDENTIALS_PATH.exists():
    logger.warning(
        f"No se encontro {_CREDENTIALS_PATH}: copialo con scp a la raiz de "
        "la App (junto a app.yaml) o el microfono y la voz no van a funcionar."
    )

_credentials = None
_speech_client = None
_tts_client = None
_mic = None
_mic_device = None  # con que dispositivo se abrio _mic
_spk = None
_spk_device = None  # con que dispositivo se abrio _spk
_spk_lock = threading.Lock()  # ack() suena en otro hilo: nunca dos escrituras a la vez

# Ademas de `app logs`, cada evento importante (lo que se va transcribiendo,
# fallas de mic/TTS) se reenvia aqui, para poder mostrarlo en vivo en la web
# y confirmar que el microfono SI esta captando sonido. main.py lo conecta a
# ui.send_message() con set_debug_reporter().
_debug_reporter = None


def set_debug_reporter(fn):
    global _debug_reporter
    _debug_reporter = fn


# Mientras suena la voz, cada bloque de audio avisa aqui cuan abierta va la
# boca (0 = cerrada ... MOUTH_LEVELS - 1). main.py lo conecta al Bridge con
# set_mouth_reporter(), igual que el reporter de debug.
MOUTH_LEVELS = 5  # debe coincidir con FACE_MOUTH_LEVELS de sketch/face_sprite.h
_mouth_reporter = None


def set_mouth_reporter(fn):
    global _mouth_reporter
    _mouth_reporter = fn


def _report_mouth(level):
    if _mouth_reporter is not None:
        try:
            _mouth_reporter(level)
        except Exception:
            pass


# Visemas: ademas de cuan abierta, QUE forma tiene la boca (a, o, u, m, f...),
# estimada desde el texto de cada frase (ver _viseme_track()). Viene conectado
# de fabrica a gestures.send_viseme para no depender de que main.py lo haga;
# set_viseme_reporter(None) lo apaga y la boca vuelve a moverse solo por
# volumen (mouth_level), que es lo que entienden los sketch viejos.
_viseme_reporter = gestures.send_viseme


def set_viseme_reporter(fn):
    global _viseme_reporter
    _viseme_reporter = fn


def _report_viseme(viseme):
    if _viseme_reporter is not None:
        try:
            _viseme_reporter(viseme)
        except Exception:
            pass


def _debug(text):
    if _debug_reporter is not None:
        try:
            _debug_reporter(text)
        except Exception:
            pass


def _creds():
    global _credentials
    if _credentials is None:
        _credentials = service_account.Credentials.from_service_account_file(str(_CREDENTIALS_PATH))
    return _credentials


def _speech():
    global _speech_client
    if _speech_client is None:
        _speech_client = speech.SpeechClient(credentials=_creds())
    return _speech_client


def _tts():
    global _tts_client
    if _tts_client is None:
        _tts_client = texttospeech.TextToSpeechClient(credentials=_creds())
    return _tts_client


def warmup():
    """Abre los canales gRPC de STT y TTS mientras nadie espera: la primera
    llamada de cada cliente cuesta bastante mas que las siguientes."""
    try:
        _speech()
        _synthesize("Hola.", _DEFAULT_VOICE)
        logger.info("Voz precalentada: la primera respuesta ya sera rapida")
    except Exception as exc:
        logger.warning(f"No se pudo precalentar la voz: {exc}")
    # Los "¡Dime!" de la escucha activa (una vez: despues salen de data/tts_cache/).
    warm_cached(_CACHED_PHRASES["dime"])


# --- Dispositivos de audio: headset USB o bocina Bluetooth -------------------

_OUTPUT_POLL_S = 3.0
_bt_sink = None  # (node.name, descripcion) de la bocina Bluetooth, o None
_bt_source = None  # (node.name, descripcion) del mic de la bocina, o None
_bt_profile = None  # perfil activo de la bocina ("headset-head-unit", "a2dp-sink"...)

# Modo de la bocina Bluetooth (comando /bt_mode de la terminal):
# - "headset": mic Y voz por la bocina (perfil manos libres HFP). Suena a
#   llamada telefonica, pero no hace falta headset USB.
# - "music":   solo la voz por la bocina, en alta calidad (A2DP); el mic
#   sigue siendo el del headset USB.
# Se fuerza el perfil a mano (pw-cli) porque el autoswitch de WirePlumber
# esta desactivado en esta placa (~/.config/wireplumber, lo puso ArmonIA).
BT_MODES = ("headset", "music")
_BT_MODE_PATH = Path(__file__).resolve().parent.parent / "data" / "bt_mode.txt"
_PROFILE_RETRY_S = 15.0
_last_profile_try = {}  # (device id, modo) -> monotonic del ultimo intento


def bt_mode():
    try:
        mode = _BT_MODE_PATH.read_text().strip()
    except FileNotFoundError:
        mode = ""
    return mode if mode in BT_MODES else "headset"


def set_bt_mode(mode):
    _BT_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BT_MODE_PATH.write_text(mode)
    _last_profile_try.clear()  # que el proximo sondeo lo aplique ya


# Cuando cambia el dispositivo de audio (entra o sale la bocina Bluetooth) el
# microfono suelta ruido: clics del cambio de perfil, el "pop" del arranque.
# Vosk transcribe eso como palabras y la escucha activa se dispara sola. Se
# ignoran las interrupciones durante este rato despues del cambio.
_BARGE_DEVICE_GRACE_S = 3.0
_audio_change_at = 0.0


def _mark_audio_change():
    global _audio_change_at
    _audio_change_at = time.monotonic()


def _pw_dump():
    out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
    return json.loads(out or "[]")


def _pw_nodes(objs, media_class, prefix):
    found = []
    for obj in objs:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        name = str(props.get("node.name", ""))
        if props.get("media.class") == media_class and name.startswith(prefix):
            found.append((name, props.get("node.description") or name))
    return found


def _bt_devices(objs):
    """[(id, perfil_activo, perfiles)] de todos los equipos Bluetooth de audio."""
    found = []
    for obj in objs:
        if obj.get("type") != "PipeWire:Interface:Device":
            continue
        info = obj.get("info") or {}
        if (info.get("props") or {}).get("device.api") != "bluez5":
            continue
        params = info.get("params") or {}
        active = (params.get("Profile") or [{}])[0].get("name")
        found.append((obj["id"], active, params.get("EnumProfile") or []))
    return found


# Bocina preferida para la voz cuando hay varias conectadas (/bt_audio <n>):
# su MAC en data/bt_output.txt.
_BT_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "bt_output.txt"


def preferred_output():
    try:
        return _BT_OUTPUT_PATH.read_text().strip().upper()
    except FileNotFoundError:
        return ""


def set_preferred_output(mac):
    _BT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BT_OUTPUT_PATH.write_text(mac.upper())


def _pick_sink(sinks):
    """La bocina preferida si esta conectada; si no, la primera."""
    want = preferred_output().replace(":", "_")
    for sink in sinks:
        if want and want in sink[0].upper():
            return sink
    return sinks[0] if sinks else None


def _pick_profile(profiles, mode):
    """El mejor perfil disponible para el modo: manos libres (HFP/HSP) o
    musica (A2DP), por la prioridad que declara PipeWire."""
    want = ("headset-head-unit",) if mode == "headset" else ("a2dp-sink",)
    ok = [p for p in profiles if str(p.get("name", "")).startswith(want) and p.get("available") != "no"]
    return max(ok, key=lambda p: p.get("priority", 0)) if ok else None


def _ensure_profile(dev):
    dev_id, active, profiles = dev
    mode = bt_mode()
    target = _pick_profile(profiles, mode)
    if target is None or target.get("name") == active:
        return
    key = (dev_id, mode)
    now = time.monotonic()
    if now - _last_profile_try.get(key, -1e9) < _PROFILE_RETRY_S:
        return
    _last_profile_try[key] = now
    logger.info(f"Bocina Bluetooth: cambio el perfil {active} -> {target['name']} (modo {mode})")
    _debug(f"🔵 bocina en modo {'manos libres (mic + voz)' if mode == 'headset' else 'música (solo voz)'}")
    subprocess.run(
        ["pw-cli", "set-param", str(dev_id), "Profile", json.dumps({"index": target["index"], "save": True})],
        capture_output=True, timeout=5,
    )


def _poll_bluetooth():
    global _bt_sink, _bt_source, _bt_profile
    while True:
        try:
            objs = _pw_dump()
            devs = _bt_devices(objs)
            for dev in devs:
                _ensure_profile(dev)
            sinks = _pw_nodes(objs, "Audio/Sink", "bluez_output.")
            sources = _pw_nodes(objs, "Audio/Source", "bluez_input.")
            sink = _pick_sink(sinks)
            _bt_profile = next((d[1] for d in devs if sink and d[1]), None) if sink else None
            if sink != _bt_sink:
                _mark_audio_change()
                if sink:
                    logger.info(f"Bocina Bluetooth conectada: {sink[1]}")
                    _debug(f"🔵 bocina Bluetooth conectada: {sink[1]}")
                elif _bt_sink:
                    logger.info("Bocina Bluetooth desconectada: vuelvo al headset USB")
                    _debug("🔵 bocina Bluetooth desconectada: vuelvo al headset USB")
                _bt_sink = sink
            source = sources[0] if sources else None
            if source != _bt_source:
                _mark_audio_change()
            if source != _bt_source and source:
                _debug(f"🎙 micrófono de la bocina disponible: {source[1]}")
            _bt_source = source
        except Exception as exc:
            logger.debug(f"No pude revisar Bluetooth en PipeWire: {exc}")
        time.sleep(_OUTPUT_POLL_S)


threading.Thread(target=_poll_bluetooth, daemon=True, name="bt-poll").start()


def output_name():
    """Por donde sale la voz ahora (para la terminal del dashboard)."""
    if _bt_sink:
        return f"Bluetooth: {_bt_sink[1]}"
    return "headset USB"


def input_name():
    """De donde sale el microfono ahora."""
    if _mic_device and _mic_device.startswith("pipewire"):
        return f"Bluetooth: {_bt_source[1] if _bt_source else '?'}"
    return "headset USB" if _mic is not None else "ninguno"


def bluetooth_status():
    return {
        "sink": _bt_sink[1] if _bt_sink else None,
        "source": _bt_source[1] if _bt_source else None,
        "profile": _bt_profile,
        "mode": bt_mode(),
    }


def _wanted_output():
    return f"pipewire:NODE={_bt_sink[0]}" if _bt_sink else Speaker.USB_SPEAKER_1


def _wanted_mic():
    if _bt_source and bt_mode() == "headset":
        return f"pipewire:NODE={_bt_source[0]}"
    return Microphone.USB_MIC_1


def _open_mic(device):
    return Microphone(device=device, sample_rate=_SAMPLE_RATE, channels=Microphone.CHANNELS_MONO, format="int16")


def _microphone():
    """El mic que toca: el de la bocina en modo manos libres, si no el del
    headset USB. Si cambio desde el ultimo turno, se reabre."""
    global _mic, _mic_device
    wanted = _wanted_mic()
    if _mic is not None and _mic_device != wanted:
        try:
            _mic.stop()
        except Exception:
            pass
        _mic = None
    if _mic is None:
        try:
            _mic = _open_mic(wanted)
            _mic_device = wanted
        except Exception:
            # Sin mic USB, ultimo recurso: el de la bocina, pero solo en modo
            # manos libres. En modo musica NO: abrirlo ata la bocina a manos
            # libres, y en esta placa el mic Bluetooth llega mudo de todos
            # modos (el chip no entrega voz SCO al host: `hciconfig` RX sco:0).
            if wanted.startswith("pipewire") or not _bt_source or bt_mode() != "headset":
                raise
            logger.warning(f"Sin microfono USB: uso el de la bocina Bluetooth ({_bt_source[1]})")
            _mic_device = f"pipewire:NODE={_bt_source[0]}"
            _mic = _open_mic(_mic_device)
        logger.info(f"Microfono: {input_name()}")
        _debug(f"🎙 micrófono: {input_name()}")
    if not _mic.is_started():
        _mic.start()
    return _mic


def _speaker():
    """El parlante que toca: la bocina Bluetooth si esta conectada, si no el
    headset USB. Si cambio desde el ultimo turno, se reabre."""
    global _spk, _spk_device
    wanted = _wanted_output()
    if _spk is not None and _spk_device != wanted:
        try:
            _spk.stop()
        except Exception:
            pass
        _spk = None
    if _spk is None:
        # sample_rate debe coincidir con el que pedimos en _synthesize(): si
        # no, play_wav() tira "WAV sample rate does not match speaker
        # sample rate" (el default del Speaker es 16000, heredado del mic,
        # y el TTS de Google sintetiza a 24000).
        _spk = Speaker(device=wanted, sample_rate=_TTS_SAMPLE_RATE)
        _spk_device = wanted
    if not _spk.is_started():
        _spk.start()
    return _spk


def _reset_speaker():
    """Tras un error de reproduccion (p. ej. la bocina se apago a mitad de
    frase), se cierra para reabrirlo limpio en el proximo turno."""
    global _spk
    if _spk is not None:
        try:
            _spk.stop()
        except Exception:
            pass
    _spk = None


# Mientras el robot habla, listen_turn() no escucha: pausar el mic no alcanza
# cuando la voz sale desde otro hilo (p. ej. /bienvenida desde la terminal)
# con una sesion de escucha ya abierta, que seguia transcribiendo al propio
# robot y hasta se "activaba" con el "Bang" de su bienvenida. Cuenta cuantas
# voces suenan (say, celebrate) y, al terminar, deja _ECHO_TAIL_S de sordera
# por lo que la bocina Bluetooth todavia tiene en su buffer.
_ECHO_TAIL_S = 0.8
_DIME_TAIL_S = 0.15  # despues del "¡Dime!" por headset USB (ver say_cached)
_speaking = 0
_deaf_until = 0.0
_speaking_lock = threading.Lock()


def _is_deaf():
    return _speaking > 0 or time.monotonic() < _deaf_until


def pause_listening(keep_mic=False):
    """Silencia el microfono mientras el robot habla, para que no se
    transcriba a si mismo. Con bocina Bluetooth es imprescindible: el mic la
    escucha perfecto. Cada pause_listening() va con su resume_listening().

    keep_mic=True: listen_turn() queda igual de sordo, pero el mic sigue
    abierto para que la escucha activa (BargeIn, mas abajo) lea sus bloques."""
    global _speaking
    with _speaking_lock:
        _speaking += 1
    # Si una escucha activa esta leyendo el mic (otra voz desde la terminal o
    # la web), no se le corta: su say() lo cierra al terminar.
    if _mic is not None and not keep_mic and _active_barge is None:
        try:
            _mic.stop()
        except Exception as exc:
            logger.debug(f"No se pudo pausar el microfono: {exc}")


def resume_listening(tail=_ECHO_TAIL_S):
    global _speaking, _deaf_until
    with _speaking_lock:
        _speaking = max(0, _speaking - 1)
        _deaf_until = max(_deaf_until, time.monotonic() + tail)
    try:
        _microphone()
    except Exception as exc:
        # Igual que en listen_turn(): sin headset USB esto no puede tirar la
        # App entera (App.run() no reintenta un loop() que lanza sin atrapar).
        logger.warning(f"No se pudo reabrir el microfono ({exc}); reviso el headset USB")
        _debug(f"⚠ sin microfono: {exc}")


# --- Escucha: nombre del guia + la pregunta ---------------------------------


def _normalize(word):
    plain = unicodedata.normalize("NFD", word.lower())
    return "".join(c for c in plain if not unicodedata.combining(c))


def _find_wake_word(text, wake_keys):
    """Busca el nombre de un guia palabra por palabra y devuelve
    (clave, resto_de_la_frase), o None si no aparece ninguno. Tolera las
    transcripciones tipicas mal escritas y el nombre partido en dos
    ("Cris pi")."""
    words = text.split()
    keys = [re.sub(r"[^a-z]", "", _normalize(w)) for w in words]
    for i, key in enumerate(keys):
        for cand, skip in ((key, 1), (key + (keys[i + 1] if i + 1 < len(keys) else ""), 2)):
            cand = _WAKE_ALIASES.get(cand, cand)
            if cand in wake_keys:
                return cand, " ".join(words[i + skip :]).lstrip(" ,.")
    return None


def _canon_tokens(text, keys):
    """Palabras sin tildes ni signos, con los nombres ya llevados a su clave
    ("Krystal" -> cristal, "cris pi" -> crispi, "se sia" -> cesia). Devuelve
    (tokens, spans, words): spans[i] = (desde, hasta) en words, para poder
    devolver el resto de la frase con sus tildes."""
    words = (text or "").split()
    plain = [re.sub(r"[^a-z]", "", _normalize(w)) for w in words]
    tokens, spans = [], []
    i = 0
    while i < len(plain):
        k = plain[i]
        if not k or k == "unk":  # el [unk] de la gramatica de Vosk
            i += 1
            continue
        pair = _WAKE_ALIASES.get(k + plain[i + 1], k + plain[i + 1]) if i + 1 < len(plain) else None
        if pair in keys:
            tokens.append(pair)
            spans.append((i, i + 2))
            i += 2
            continue
        k = _WAKE_ALIASES.get(k, k) if _WAKE_ALIASES.get(k) in keys else k
        tokens.append(k)
        spans.append((i, i + 1))
        i += 1
    return tokens, spans, words


# Pedir otro guia: "quiero hablar con Cori", "pasame a Cristal", "cambiame a
# Crispi", "ahora con Carmel", "que hable Cesia", "llama a Cori", "habla
# Carmel". El nombre tiene que ir JUSTO despues de la frase (como mucho un
# "el"/"la"): "quiero hablar con mi mama" no es cambiar de guia.
_SWITCH_LEAD = (
    r"(?:(?:yo\s+)?(?:quiero|quisiera|puedo|podemos|queremos|me\s+gustaria|dejame)\s+(?:hablar|conversar|charlar|seguir)\s+con"
    r"|pasa\s?(?:me|nos)?\s+(?:con|a)"
    r"|cambia\s?(?:me|nos)?\s+(?:a|con|por)"
    r"|ahora\s+con"
    r"|que\s+(?:me\s+)?hable"
    r"|llama\s?(?:me)?\s+a"
    r"|habla(?:me)?(?:\s+con)?)"
)


def _switch_re(keys):
    names = "|".join(sorted(keys, key=len, reverse=True))
    return re.compile(r"\b(?P<lead>" + _SWITCH_LEAD + r")\s+(?:(?:el|la|a)\s+)?(?P<who>" + names + r")\b")


# "ahora con" y "habla" a secas son palabras de todos los dias ("mi mama
# habla con Cristal", "ahora con cristal hacemos ventanas"): solo cuentan al
# EMPEZAR la frase (como mucho una palabra antes) y si despues del nombre no
# sigue nada (o va una coma: "ahora con Cori, tengo una idea").
_WEAK_SWITCH = re.compile(r"^(?:ahora|habla)")
_WEAK_SWITCH_MAX_LEAD = 1

# Lo que se dice despues del nombre por cortesia: no es un turno nuevo (si
# no, "pasame a Cristal, gracias" empezaba un reto que decia "gracias").
_COURTESY = {
    "por", "favor", "porfa", "porfis", "porfavor", "gracias", "ya", "ahora", "si", "ok", "okay", "vale",
    "bueno", "please", "eh", "este", "pues", "listo", "dale",
}


def _strip_courtesy(rest):
    words = rest.split()
    plain = [re.sub(r"[^a-z]", "", _normalize(w)) for w in words]
    i = 0
    while i < len(words) and (plain[i] in _COURTESY or not plain[i]):
        i += 1
    if all(p in _COURTESY or not p for p in plain[i:]):
        return ""
    return " ".join(words[i:]).lstrip(" ,.;:")


def find_switch(text, persona_keys):
    """Si la frase pide pasar a otro guia, (clave, resto_de_la_frase); si no,
    None. Sin tildes ni mayusculas, con los alias de _WAKE_ALIASES. Al resto
    se le quita la cortesia ("por favor", "gracias")."""
    keys = tuple(persona_keys)
    if not keys:
        return None
    tokens, spans, words = _canon_tokens(text, keys)
    joined = " ".join(tokens)
    m = _switch_re(keys).search(joined)
    if not m:
        return None
    used = len(joined[: m.end()].split())
    raw_rest = " ".join(words[spans[used - 1][1] :]).lstrip(" ,.;:") if used else ""
    rest = _strip_courtesy(raw_rest)
    if _WEAK_SWITCH.match(m.group("lead")):
        name_word = words[spans[used - 1][1] - 1]
        if len(joined[: m.start()].split()) > _WEAK_SWITCH_MAX_LEAD or (rest and not re.search(r"[,.;:!?]$", name_word)):
            return None
    return m.group("who"), rest


def _audio_requests(stop):
    # OJO: el config va aparte, como argumento de streaming_recognize(), NO
    # embebido aqui como primer item (esa era la forma de APIs viejas de
    # Google Speech; en google-cloud-speech 2.x streaming_recognize() exige
    # config como parametro propio, o tira TypeError: "missing 1 required
    # positional argument: 'config'").
    for chunk in _microphone().stream():
        if stop.is_set():
            return  # cierra el stream: ya se lanzo el turno
        if chunk is None or len(chunk) == 0:
            continue
        yield speech.StreamingRecognizeRequest(audio_content=chunk.tobytes())


_last_switch = False  # el ultimo listen_turn() fue un "pasame con <guia>"


def last_switch():
    """True si lo ultimo que devolvio listen_turn() era un pedido de cambio de
    guia ("quiero hablar con Cori"): main.py le pasa el reto al nuevo guia."""
    return _last_switch


def listen_turn(wake_keys, follow_up=None, follow_up_s=FOLLOW_UP_S, name_only=(), switch_keys=None):
    """Escucha en continuo hasta oir el nombre de un guia seguido de una
    pregunta, y la devuelve apenas la persona se calla (ver _ENDPOINT_S).

    follow_up: guia al que se le puede contestar SIN decir su nombre durante
    los primeros follow_up_s segundos (justo despues de que hablo).

    name_only: claves que valen dichas solas ("Carmel" y nada mas): se
    devuelven con el texto vacio (clave, "").

    switch_keys: guias que se pueden pedir con "quiero hablar con <guia>" (por
    defecto, los de name_only). El pedido se mira ANTES de asignar la frase al
    guia del seguimiento: si no, "quiero hablar con Cori" dicho dentro de la
    ventana se le quedaba al guia anterior. last_switch() dice si fue eso.

    Devuelve (clave, texto) o (None, None) si no se capturo nada (Google
    cierra el stream a los ~5 minutos y el bucle principal vuelve a llamar).
    """
    global _last_switch
    _last_switch = False
    switch_keys = tuple(switch_keys if switch_keys is not None else name_only)
    # Si el robot esta hablando, se espera a que termine: una sesion nueva
    # arranca limpia, sin la frase del robot a medio transcribir.
    while _is_deaf():
        time.sleep(0.05)
    try:
        # El microfono se abre ANTES de tocar la red: si no hay headset
        # conectado, esto falla al toque y no gastamos una sesion gRPC
        # entera contra Google por nada (eso fue lo que satura la CPU al
        # 100% y le quitaba turno al servidor web cuando no hay headset).
        _microphone()
    except Exception as exc:
        logger.warning(f"No se pudo abrir el microfono ({exc}); reviso el headset USB y reintento")
        _debug(f"⚠ sin microfono: {exc}")
        return None, None

    # Las respuestas de Google se leen en otro hilo y llegan por una cola, para
    # poder medir el silencio aqui con timeout (el iterador de gRPC bloquea
    # hasta la proxima respuesta, que puede no llegar nunca si nadie habla).
    stop = threading.Event()
    events = queue.Queue()
    started = time.monotonic()

    def reader():
        try:
            responses = _speech().streaming_recognize(config=_STREAMING_CONFIG, requests=_audio_requests(stop))
            for response in responses:
                # Un interim puede venir partido en varios results (tramo
                # estable + tramo inestable): juntos son la frase en curso.
                pieces = [r.alternatives[0].transcript for r in response.results if r.alternatives]
                text = " ".join(p.strip() for p in pieces if p.strip())
                if text:
                    events.put(("text", text, any(r.is_final for r in response.results)))
        except Exception as exc:
            if not stop.is_set():
                events.put(("error", exc, None))
        events.put(("end", None, None))

    threading.Thread(target=reader, daemon=True, name="stt").start()

    persona = None
    finals = []  # frases ya cerradas por Google despues de la palabra clave
    interim = ""  # la frase en curso (sin la palabra clave)
    in_wake_utterance = False  # la frase en curso es la que traia el nombre
    switched = False  # la frase es un "pasame con <guia>"
    last_change = wake_at = last_heard = 0.0

    def said():
        return " ".join(finals + [interim]).strip()

    try:
        while True:
            try:
                kind, text, is_final = events.get(timeout=0.05)
            except queue.Empty:
                kind = None
            now = time.monotonic()

            if kind == "end":
                break
            if persona is None and _wanted_mic() != _mic_device:
                # Se conecto (o se fue) la bocina: se reabre con el mic que toca.
                break
            if kind == "error":
                # Un cierre a los ~5 min es el limite normal de Google Speech;
                # si pasa mucho antes es una falla real (credencial, cuota...)
                # y conviene verla ya mismo, no solo en debug.
                if now - started < 30:
                    logger.warning(f"La sesion de escucha se corto casi al toque: {text}")
                    _debug(f"⚠ error de escucha: {text}")
                else:
                    logger.debug(f"Sesion de escucha interrumpida (normal cada ~5 min): {text}")
                break

            if kind == "text" and _is_deaf():
                # El robot empezo a hablar con esta sesion abierta: lo que
                # llega es su propia voz. Se corta la sesion entera (Google
                # seguiria arrastrando esa frase en los interim siguientes).
                persona = None
                break

            if kind == "text":
                # Todo lo que el STT va entendiendo, sea o no el nombre de un
                # guia, para ver en la web que el microfono SI capta sonido.
                _debug(text if is_final else f"[{text}]")
                last_heard = now

                # Pedido de otro guia: va antes que el seguimiento, y aunque la
                # frase ya se le haya asignado al guia anterior por la ventana
                # (el "quiero..." llega antes que el "...con Cori") o por un
                # nombre al principio ("Crispi, quiero hablar con Cori",
                # "robot, pasame con Cesia"). Se mira en cada interim de la
                # primera frase: "ahora con Cristal" deja de ser pedido si
                # sigue "...hacemos ventanas".
                sw = find_switch(text, switch_keys) if not finals else None
                if sw:
                    if persona is None:
                        wake_at = now
                    persona, rest = sw
                    in_wake_utterance, switched = True, True
                elif persona is None or (switched and in_wake_utterance):
                    switched = False
                    found = _find_wake_word(text, wake_keys)
                    if found:
                        persona, rest = found
                    elif follow_up and now - started < follow_up_s:
                        persona, rest = follow_up, text
                    else:
                        persona, interim = None, ""
                        continue
                    in_wake_utterance = found is not None
                    wake_at = now
                else:
                    rest = text
                    if in_wake_utterance:
                        # Google reescribe la frase entera en cada interim: se
                        # vuelve a quitar el nombre del principio.
                        again = _find_wake_word(text, wake_keys)
                        rest = again[1] if again else text

                if rest != interim:
                    interim = rest
                    last_change = now
                if is_final:
                    if interim:
                        finals.append(interim)
                    interim = ""
                    in_wake_utterance = False
                    if finals:
                        _last_switch = switched
                        return persona, said()

            if persona is not None:
                if said() and now - last_change >= _ENDPOINT_S:
                    _last_switch = switched
                    return persona, said()
                if switched and not said() and now - last_heard >= _ENDPOINT_S:
                    _last_switch = True
                    return persona, ""  # "pasame con Cori" y nada mas
                if not said() and persona in name_only and now - wake_at > _NAME_ONLY_S:
                    return persona, ""  # solo dijo el nombre: que el guia se presente
                if not said() and now - wake_at > _WAKE_ONLY_WAIT_S:
                    # Solo dijo el nombre y se quedo callado: vuelta a esperar.
                    persona = None
                    switched = False
    finally:
        stop.set()

    if persona and (said() or switched):
        _last_switch = switched
        return persona, said()
    return None, None


# --- Voz --------------------------------------------------------------------


def _synthesize(text, voice_name):
    input_text = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(language_code="es-US", name=voice_name)
    # LINEAR16 con Google TTS trae el header WAV incluido: se puede
    # reproducir directo con Speaker.play_wav(), sin re-empaquetarlo.
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=_TTS_SAMPLE_RATE,
    )
    resp = _tts().synthesize_speech(input=input_text, voice=voice, audio_config=audio_config)
    # Speaker.play_wav() espera un np.ndarray (son los bytes crudos del WAV,
    # un byte por elemento), no el "bytes" que devuelve la API tal cual.
    return np.frombuffer(resp.audio_content, dtype=np.uint8)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_MIN_PART_CHARS = 30  # frases mas cortas se pegan a la siguiente (menos cortes)


def _split_sentences(text):
    """Parte la respuesta en frases para sintetizarlas en paralelo. La
    primera queda corta a proposito: es la que decide cuanto se espera."""
    parts = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        if parts and len(parts[-1]) < _MIN_PART_CHARS and len(parts) > 1:
            parts[-1] += " " + sentence
        elif sentence:
            parts.append(sentence)
    return parts or [text]


# Sincronia boca/voz. play() escribe al ALSA de a bloques de buffer_size
# muestras (1024 = ~43 ms a 24 kHz) y cada escritura se bloquea hasta que hay
# lugar, asi que lo que SUENA en un momento dado es lo que se escribio unos
# bloques antes. La boca tambien llega tarde: Bridge + frame del sketch + SPI
# son ~50-80 ms. Con el headset USB 2 bloques de atraso compensan; la bocina
# Bluetooth (A2DP) suma ~150-250 ms mas de buffer propio.
# Si la boca se adelanta a la voz, subirlo; si va detras, bajarlo.
_MOUTH_LAG_CHUNKS_USB = 2
_MOUTH_LAG_CHUNKS_BT = 6
_MOUTH_GATE = 0.12  # por debajo de esto (relativo al volumen tipico) = boca cerrada


def _mouth_lag():
    return _MOUTH_LAG_CHUNKS_BT if _spk_device and _spk_device.startswith("pipewire") else _MOUTH_LAG_CHUNKS_USB


def _chunk_rms(pcm, chunk):
    """Volumen (RMS) de cada bloque de audio y el volumen tipico de la frase
    (percentil 90), contra el que se normaliza."""
    n = max(1, (len(pcm) + chunk - 1) // chunk)
    padded = np.zeros(n * chunk, dtype=np.float32)
    padded[: len(pcm)] = pcm
    rms = np.sqrt(np.mean(padded.reshape(n, chunk) ** 2, axis=1))
    ref = float(np.percentile(rms, 90)) or 1.0
    return rms, ref


def _mouth_levels(pcm, chunk):
    """Nivel de boca (0..MOUTH_LEVELS-1) por bloque de audio, segun su volumen.

    Se normaliza contra el volumen tipico de ESTA frase (percentil 90), asi
    una voz mas bajita o mas fuerte abre la boca igual. Abre al toque y cierra
    de a un nivel por bloque, para que no titile entre silaba y silaba.
    """
    rms, ref = _chunk_rms(pcm, chunk)
    top = MOUTH_LEVELS - 1
    raw = np.where(rms < _MOUTH_GATE * ref, 0, np.clip(np.ceil(rms / ref * top), 1, top)).astype(int)

    levels = []
    prev = 0
    for lvl in raw:
        prev = int(lvl) if lvl >= prev else prev - 1
        levels.append(prev)
    return levels


# --- Visemas: la forma de la boca desde el texto ------------------------------
# El TTS de Google (Chirp3-HD, texto plano) no devuelve fonemas ni tiempos, asi
# que la forma de la boca se ESTIMA: cada frase se pasa de letras a visemas con
# las reglas del español, se reparte proporcionalmente sobre los bloques de
# audio que SI suenan (las vocales pesan mas que las consonantes) y los bloques
# en silencio dejan la boca en reposo. No es exacto, pero la "o" cae en la "o"
# y las "m" cierran la boca, que es lo que el ojo nota.

_V = gestures  # alias corto para las constantes VISEME_*

_VOWEL_VISEMES = {"a": _V.VISEME_AEI, "e": _V.VISEME_AEI, "i": _V.VISEME_AEI, "o": _V.VISEME_O, "u": _V.VISEME_U}
_CONSONANT_VISEMES = {
    # En español la v suena igual que la b (bilabial): cierra los labios.
    "b": _V.VISEME_BMP, "m": _V.VISEME_BMP, "p": _V.VISEME_BMP, "v": _V.VISEME_BMP,
    "f": _V.VISEME_F,
    "l": _V.VISEME_L,
    "r": _V.VISEME_R,
    "j": _V.VISEME_CHJ,
    "q": _V.VISEME_QW, "w": _V.VISEME_QW,
    "c": _V.VISEME_CDG, "d": _V.VISEME_CDG, "g": _V.VISEME_CDG, "k": _V.VISEME_CDG,
    "n": _V.VISEME_CDG, "s": _V.VISEME_CDG, "t": _V.VISEME_CDG, "x": _V.VISEME_CDG,
    "y": _V.VISEME_CDG, "z": _V.VISEME_CDG,
}
_VOWEL_WEIGHT = 2.0  # las vocales duran mas que las consonantes
_CONSONANT_WEIGHT = 1.0
_BMP_WEIGHT = 1.4  # el cierre de labios se ve mucho: que no se lo coma el redondeo
# Una cifra ("3", "10") se dice como una palabra corta: consonante + vocal.
_DIGIT_VISEMES = ((_V.VISEME_CDG, _CONSONANT_WEIGHT), (_V.VISEME_AEI, _VOWEL_WEIGHT))
_PHRASE_SPLIT = re.compile(r"[,;:.!?¡¿…()\"«»\-–—]+")

# Un visema que dura menos que esto (en bloques de ~43 ms) se funde con el
# vecino: cada cambio cuesta SPI en el sketch (1-5K px) y mas de ~12 por
# segundo le quitaria cuadros al parpadeo y a los servos.
_MIN_VISEME_CHUNKS = 2
# Silencios de al menos este largo (bloques) separan tramos de la frase: si hay
# tantos tramos como pedazos de texto entre comas, cada pedazo va a su tramo.
_PHRASE_GAP_CHUNKS = 4


def _word_visemes(text):
    """[(visema, peso)] de un pedazo de texto, con los iguales seguidos ya
    juntados. Reglas del español: ch/sh/ll/j/g(e,i) -> CHJ, qu/gu(e,i) con u
    muda, rr -> R, h muda, y final ("muy", "hoy") -> vocal, u antes de vocal
    ("cuando", "bueno") -> labios redondeados (QW)."""
    t = _normalize(text)
    out = []

    def add(viseme, weight):
        if out and out[-1][0] == viseme:
            out[-1] = (viseme, out[-1][1] + weight)
        else:
            out.append((viseme, weight))

    i, n = 0, len(t)
    while i < n:
        c = t[i]
        nxt = t[i + 1] if i + 1 < n else ""
        after = t[i + 2] if i + 2 < n else ""
        step = 1
        if c.isdigit():
            for v, w in _DIGIT_VISEMES:
                add(v, w)
        elif c in "aeio":
            add(_VOWEL_VISEMES[c], _VOWEL_WEIGHT)
        elif c == "u":
            if nxt in "aeio" and nxt:
                add(_V.VISEME_QW, _CONSONANT_WEIGHT)
            else:
                add(_V.VISEME_U, _VOWEL_WEIGHT)
        elif c == "h":
            pass  # muda (la ch y la sh se tratan con la c y la s)
        elif c in "cs" and nxt == "h":
            add(_V.VISEME_CHJ, _CONSONANT_WEIGHT)
            step = 2
        elif c == "l" and nxt == "l":
            add(_V.VISEME_CHJ, _CONSONANT_WEIGHT)
            step = 2
        elif c == "r" and nxt == "r":
            add(_V.VISEME_R, _CONSONANT_WEIGHT * 1.5)
            step = 2
        elif c == "q":
            add(_V.VISEME_QW, _CONSONANT_WEIGHT)
            step = 2 if nxt == "u" else 1  # la u de "que"/"qui" no suena
        elif c == "g" and nxt == "u" and after in ("e", "i"):
            add(_V.VISEME_CDG, _CONSONANT_WEIGHT)
            step = 2  # "guerra", "guitarra": la u no suena
        elif c == "g" and nxt in ("e", "i") and nxt:
            add(_V.VISEME_CHJ, _CONSONANT_WEIGHT)  # "gente" suena como "jente"
        elif c == "y" and not (nxt and nxt in "aeiou"):
            add(_V.VISEME_AEI, _VOWEL_WEIGHT)  # "y", "muy", "hoy": es vocal
        elif c in _CONSONANT_VISEMES:
            v = _CONSONANT_VISEMES[c]
            add(v, _BMP_WEIGHT if v == _V.VISEME_BMP else _CONSONANT_WEIGHT)
        i += step
    return out


def _text_visemes(text):
    """Los pedazos de la frase entre signos de puntuacion, cada uno como
    [(visema, peso)]. Los pedazos sin letras se descartan."""
    return [seq for seq in (_word_visemes(p) for p in _PHRASE_SPLIT.split(text or "")) if seq]


def _voiced_mask(pcm, chunk):
    """True por bloque si ahi suena voz. Un bloque mudo suelto entre dos con
    voz (la oclusion de una p o una t) cuenta como voz, para que la boca no
    salte a reposo a mitad de palabra."""
    rms, ref = _chunk_rms(pcm, chunk)
    voiced = rms >= _MOUTH_GATE * ref
    for i in range(1, len(voiced) - 1):
        if not voiced[i] and voiced[i - 1] and voiced[i + 1]:
            voiced[i] = True
    return voiced


def _voiced_runs(voiced):
    """[(inicio, fin)] de los tramos con voz, separados por silencios de al
    menos _PHRASE_GAP_CHUNKS bloques (los silencios mas cortos quedan dentro
    del tramo)."""
    runs = []
    start = last = None
    for i, on in enumerate(voiced):
        if not on:
            continue
        if start is None:
            start = i
        elif i - last > _PHRASE_GAP_CHUNKS:
            runs.append((start, last + 1))
            start = i
        last = i
    if start is not None:
        runs.append((start, last + 1))
    return runs


def _spread(seq, chunks, track):
    """Reparte la secuencia [(visema, peso)] sobre los bloques `chunks` (solo
    los que suenan) proporcionalmente al peso, y la escribe en track."""
    if not seq or not chunks:
        return
    total = sum(w for _, w in seq)
    bounds = np.cumsum([w for _, w in seq])
    for k, i in enumerate(chunks):
        pos = (k + 0.5) / len(chunks) * total
        j = min(int(np.searchsorted(bounds, pos, side="right")), len(seq) - 1)
        track[i] = seq[j][0]


def _merge_short(track, min_len):
    """Ningun visema dura menos de min_len bloques: si un vecino con voz le
    sobra largo, le presta bloques (asi la "f" de "dificil" no desaparece);
    si no, se funde con el de antes (o el de despues, si es el primero del
    tramo). Los silencios no se tocan."""
    out = list(track)
    rest = _V.VISEME_REST

    def runs():
        found, i = [], 0
        while i < len(out):
            j = i
            while j < len(out) and out[j] == out[i]:
                j += 1
            found.append([i, j])
            i = j
        return found

    spans = runs()
    for k, (i, j) in enumerate(spans):
        if out[i] == rest or j - i >= min_len:
            continue
        need = min_len - (j - i)
        prev = spans[k - 1] if k > 0 and out[spans[k - 1][0]] != rest else None
        nxt = spans[k + 1] if k + 1 < len(spans) and out[spans[k + 1][0]] != rest else None
        if prev and prev[1] - prev[0] - need >= min_len:
            out[prev[1] - need : prev[1]] = [out[i]] * need
            prev[1] -= need
            spans[k][0] -= need
        elif nxt and nxt[1] - nxt[0] - need >= min_len:
            out[nxt[0] : nxt[0] + need] = [out[i]] * need
            nxt[0] += need
            spans[k][1] += need
        else:
            fill = out[prev[0]] if prev else (out[nxt[0]] if nxt else rest)
            if fill != rest:
                out[i:j] = [fill] * (j - i)
    return out


def _viseme_track(text, pcm, chunk):
    """Visema (VISEME_*) por bloque de audio de una frase cuyo texto se conoce.

    Si la voz hace tantas pausas largas como pedazos tiene el texto entre
    comas y puntos, cada pedazo se reparte en su tramo (asi una pausa no
    desfasa el resto de la frase); si no, todo el texto sobre toda la voz.
    """
    voiced = _voiced_mask(pcm, chunk)
    track = [_V.VISEME_REST] * len(voiced)
    phrases = _text_visemes(text)
    if not phrases:
        return track
    runs = _voiced_runs(voiced)
    if len(runs) == len(phrases):
        for seq, (a, b) in zip(phrases, runs):
            _spread(seq, [i for i in range(a, b) if voiced[i]], track)
    else:
        whole = [vw for seq in phrases for vw in seq]
        _spread(whole, [i for i, on in enumerate(voiced) if on], track)
    return _merge_short(track, _MIN_VISEME_CHUNKS)


def _drop_speaker(spk):
    """Corta en seco lo que el parlante tiene en su buffer (barge-in). Despues
    de un drop() el PCM de ALSA queda sin preparar, asi que se cierra y el
    proximo turno lo reabre limpio."""
    try:
        pcm = getattr(spk, "_pcm", None)
        if pcm is not None:
            pcm.drop()
    except Exception as exc:
        logger.debug(f"No pude vaciar el buffer del parlante: {exc}")
    _reset_speaker()


def _play_synced(spk, pcm, on_audible, stop=None):
    """Reproduce PCM int16 de a bloques y llama on_audible(i) cuando el bloque
    i empieza a SONAR (con el atraso de _mouth_lag(), ver arriba).

    Hace lo mismo que Speaker.play_pcm(), que no deja meter nada entre
    bloque y bloque.

    stop: threading.Event que se mira entre bloque y bloque (~43 ms). Si se
    prende, se corta la voz y se vacia el buffer: devuelve False. True si
    sono entera.
    """
    lag = _mouth_lag()
    chunk = spk.buffer_size
    n = (len(pcm) + chunk - 1) // chunk
    for i in range(n):
        if stop is not None and stop.is_set():
            _drop_speaker(spk)
            return False
        if i >= lag:
            on_audible(i - lag)
        spk.play(pcm[i * chunk : (i + 1) * chunk])
    # Los ultimos bloques ya estan escritos pero todavia no sonaron.
    for i in range(max(0, n - lag), n):
        if stop is not None and stop.is_set():
            _drop_speaker(spk)
            return False
        on_audible(i)
        time.sleep(chunk / spk.sample_rate)
    return True


def _wav_to_pcm(wav, spk):
    """PCM int16 de un WAV del TTS, o None si no viene en el formato esperado."""
    with wave.open(io.BytesIO(wav.tobytes()), "rb") as w:
        ok = (w.getsampwidth() == 2 and w.getnchannels() == 1 and w.getframerate() == spk.sample_rate and spk.format == np.int16)
        frames = w.readframes(w.getnframes())
    if not ok:
        return None
    return np.frombuffer(frames, dtype="<i2").astype(np.int16)


def _play_pcm_with_mouth(spk, pcm, pcm_boca=None, text=None, stop=None):
    """Reproduce PCM moviendo la boca al ritmo de cada bloque.

    Con el texto de lo que suena (y alguien a quien mandarle visemas) la boca
    toma la forma de cada letra (_viseme_track()); si no, solo se abre mas o
    menos segun el volumen (mouth_level), como antes.

    pcm_boca permite calcular la boca con un audio DISTINTO del que suena: en
    el aviso con musica de fondo, la boca sigue solo a la voz, para que no se
    mueva durante los tramos en que solo suena la musica.
    """
    voz = pcm_boca if pcm_boca is not None else pcm
    if text and _viseme_reporter is not None:
        track, rest, send = _viseme_track(text, voz, spk.buffer_size), gestures.VISEME_REST, _report_viseme
    else:
        track, rest, send = _mouth_levels(voz, spk.buffer_size), 0, _report_mouth
    sent = None

    def report(value):
        nonlocal sent
        if value != sent:  # solo los cambios: el Bridge no tiene por que saturarse
            send(value)
            sent = value

    try:
        return _play_synced(spk, pcm, lambda i: report(track[min(i, len(track) - 1)]), stop=stop)
    finally:
        report(rest)  # cortada o entera, la boca termina cerrada


def _play_with_mouth(spk, wav, text=None, stop=None):
    """Reproduce el WAV moviendo la boca (ver _play_pcm_with_mouth()).
    Devuelve False si `stop` la corto a mitad.

    Si el audio no es el formato esperado, se reproduce igual, pero sin
    mover la boca (ni se puede cortar).
    """
    pcm = _wav_to_pcm(wav, spk)
    if pcm is None:
        logger.warning("Audio del TTS en un formato inesperado: suena sin mover la boca")
        spk.play_wav(wav)
        return True
    return _play_pcm_with_mouth(spk, pcm, text=text, stop=stop)


# --- Musica de fondo del aviso de seguridad ----------------------------------
# El parlante toca UNA sola cosa a la vez (ver _spk_lock), asi que la musica no
# se reproduce en paralelo: se MEZCLA con la voz en el mismo PCM antes de
# sonar. Son acordes suaves en bucle, bien por debajo de la voz.

_BED_CHORDS = (
    (261.63, 329.63, 392.00),  # Do mayor
    (220.00, 261.63, 329.63),  # La menor
    (174.61, 220.00, 261.63),  # Fa mayor
    (196.00, 246.94, 293.66),  # Sol mayor
)
_BED_CHORD_S = 2.4   # cuanto dura cada acorde
_BED_GAIN = 0.20     # respecto a la voz: se oye, pero no la tapa


def _music_bed(n, sample_rate):
    """Colchon de acordes en bucle, de n muestras."""
    largo = max(1, int(_BED_CHORD_S * sample_rate))
    trozos = []
    for acorde in _BED_CHORDS:
        t = np.arange(largo) / sample_rate
        # Entrada y salida suaves: que no se oiga el corte entre acordes.
        env = np.clip(np.minimum(t / 0.45, (_BED_CHORD_S - t) / 0.55), 0, 1)
        onda = sum(np.sin(2 * np.pi * f * t) for f in acorde) / len(acorde)
        trozos.append(onda * env)
    bucle = np.concatenate(trozos)
    repes = int(n / len(bucle)) + 1
    return np.tile(bucle, repes)[:n] * 7000.0


def say_with_music(persona, text):
    """Como say(), pero con musica de fondo. Se usa en el aviso de seguridad.

    A diferencia de say(), aqui se arma TODO el audio antes de sonar: hace
    falta conocer la duracion total para generar el colchon musical y mezclarlo.
    Se pierde el arranque temprano de say() (que habla con la primera frase
    lista), pero el aviso no es una conversacion: no urge.
    """
    pause_listening()
    voice_name = _VOICES.get(persona, _DEFAULT_VOICE)
    partes = _split_sentences(text)
    futures = [_tts_pool.submit(_synthesize, p, voice_name) for p in partes]
    try:
        with _spk_lock:
            spk = _speaker()
            pausa = np.zeros(int(0.22 * spk.sample_rate), dtype=np.int16)
            trozos = []
            for fut in futures:
                pcm = _wav_to_pcm(fut.result(), spk)
                if pcm is None:
                    raise ValueError("el TTS devolvio un formato inesperado")
                trozos.append(pcm)
                trozos.append(pausa)
            # Una colita para que la musica cierre sola y no se corte en seco.
            trozos.append(np.zeros(int(1.6 * spk.sample_rate), dtype=np.int16))
            voz = np.concatenate(trozos)

            bed = _music_bed(len(voz), spk.sample_rate) * _BED_GAIN
            mezcla = np.clip(voz.astype(np.int32) + bed.astype(np.int32), -32768, 32767).astype(np.int16)

            # La boca sigue a la VOZ, no a la mezcla: si no, se movería con la
            # musica aunque el robot no este hablando.
            _play_pcm_with_mouth(spk, mezcla, pcm_boca=voz, text=" . ".join(partes))
    except Exception as exc:
        logger.warning(f"No pude decir el aviso con musica ({exc}); lo digo sin musica")
        _reset_speaker()
        resume_listening()
        say(persona, text)  # respaldo: al menos que se oiga el aviso
        return
    finally:
        for fut in futures:
            fut.cancel()
    time.sleep(0.4)
    resume_listening()


# --- Pitido de "te escuche" (como el de Alexa) --------------------------------


def _ack_pcm(sample_rate):
    """Dos notas cortas ascendentes (~160 ms), suaves para no asustar."""
    parts = []
    for freq, dur in ((880, 0.07), (1320, 0.09)):
        t = np.arange(int(dur * sample_rate)) / sample_rate
        env = np.clip(np.minimum(t / 0.005, (dur - t) / 0.02), 0, 1)
        parts.append((np.sin(2 * np.pi * freq * t) * env * 6000).astype(np.int16))
    return np.concatenate(parts)


# --- Sonidos de navegacion del menu de personajes ----------------------------
# Cada vez que el menu de la pantalla cambia de guia resaltado suena un tono
# corto: ascendente al bajar por la lista y descendente al subir, para que el
# niño note el movimiento sin tener que leer.

_NAV_DOWN = (523, 784)  # do -> sol : "bajando"
_NAV_UP = (784, 523)    # sol -> do : "subiendo"


def _nav_pcm(sample_rate, freqs):
    """Dos notas muy cortas (~90 ms en total), suaves."""
    parts = []
    for freq in freqs:
        dur = 0.045
        t = np.arange(int(dur * sample_rate)) / sample_rate
        env = np.clip(np.minimum(t / 0.004, (dur - t) / 0.012), 0, 1)
        parts.append((np.sin(2 * np.pi * freq * t) * env * 5000).astype(np.int16))
    return np.concatenate(parts)


def nav_tone(subiendo=False):
    """Tono de navegacion del menu, sin bloquear el turno."""

    def play():
        try:
            with _spk_lock:
                spk = _speaker()
                pcm = _nav_pcm(spk.sample_rate, _NAV_UP if subiendo else _NAV_DOWN)
                chunk = spk.buffer_size
                for i in range(0, len(pcm), chunk):
                    spk.play(pcm[i : i + chunk])
        except Exception as exc:
            logger.debug(f"No sono el tono del menu: {exc}")
            _reset_speaker()

    threading.Thread(target=play, daemon=True, name="nav-tone").start()


# --- Sonido de atencion del aviso de seguridad -------------------------------
# Tres notas ascendentes, mas presentes que el pitido de "te escuche": marcan
# que empieza algo importante. Suena con el aviso de seguridad del arranque.

_ATTENTION = ((660, 0.12), (880, 0.12), (1175, 0.22))


def _attention_pcm(sample_rate):
    parts = []
    for freq, dur in _ATTENTION:
        t = np.arange(int(dur * sample_rate)) / sample_rate
        env = np.clip(np.minimum(t / 0.008, (dur - t) / 0.05), 0, 1)
        # Un armonico suave para que no suene a pitido de microondas.
        onda = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(4 * np.pi * freq * t)
        parts.append((onda * env * 7000).astype(np.int16))
    return np.concatenate(parts)


def attention(block=True):
    """Campanilla de atencion. Por defecto espera a que termine, para que no se
    pise con la voz que viene detras."""

    def play():
        try:
            with _spk_lock:
                spk = _speaker()
                pcm = _attention_pcm(spk.sample_rate)
                chunk = spk.buffer_size
                for i in range(0, len(pcm), chunk):
                    spk.play(pcm[i : i + chunk])
        except Exception as exc:
            logger.debug(f"No sono la campanilla del aviso: {exc}")
            _reset_speaker()

    if block:
        pause_listening()
        try:
            play()
        finally:
            resume_listening()
    else:
        threading.Thread(target=play, daemon=True, name="attention").start()


def ack():
    """Pitido de confirmacion, sin bloquear: suena mientras el LLM piensa."""

    def play():
        try:
            with _spk_lock:
                spk = _speaker()
                pcm = _ack_pcm(spk.sample_rate)
                chunk = spk.buffer_size
                for i in range(0, len(pcm), chunk):
                    spk.play(pcm[i : i + chunk])
        except Exception as exc:
            logger.debug(f"No sono el pitido: {exc}")
            _reset_speaker()

    threading.Thread(target=play, daemon=True, name="ack").start()


# --- Celebracion: la "cancioncita + baile" de Diome-chan ----------------------
#
# Viene del tutorial de Diome-chan (Drive "Tutorial diome-chan", partes 2 y 3):
# un Arduino toca una melodia con un buzzer, nota por nota (arreglo de
# nota + duracion, al estilo de github.com/robsoncouto/arduino-songs), y en
# CADA nota alterna la pose de los dos brazos (servos). Aqui no hay buzzer:
# la melodia sale por el parlante, sintetizada con numpy, y cada nota avisa
# al sketch con Bridge.notify("arm_step", n) justo cuando suena.
# Se usa al pasar de fase BANG (ver main.py).

_NOTES = {"C5": 523, "D5": 587, "E5": 659, "F5": 698, "G5": 784, "A5": 880, "B5": 988, "C6": 1047, "REST": 0}
_CELEBRATION_TEMPO = 180
# Nota y divisor: 8 = corchea, 4 = negra, 2 = blanca; negativo = con puntillo.
_CELEBRATION = [
    ("C5", 8), ("E5", 8), ("G5", 8), ("C6", 4), ("REST", 8), ("G5", 8), ("C6", 2),
]
_arm_reporter = None


def set_arm_reporter(fn):
    global _arm_reporter
    _arm_reporter = fn


def _melody_pcm(melody, tempo, sample_rate):
    """PCM int16 de la melodia y la muestra donde empieza cada nota."""
    wholenote = 60.0 * 4 / tempo
    parts, starts, pos = [], [], 0
    for name, divider in melody:
        dur = wholenote / abs(divider) * (1.5 if divider < 0 else 1.0)
        n = int(dur * sample_rate)
        t = np.arange(n) / sample_rate
        freq = _NOTES[name]
        # 90% sonando y 10% de silencio, como el tone(..., noteDuration * 0.9)
        # del tutorial; envolvente corta para que no haga "clic".
        env = np.clip(np.minimum(t / 0.01, (dur * 0.9 - t) / 0.02), 0, 1)
        wave_ = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t) if freq else np.zeros(n)
        parts.append((wave_ * env * 9000).astype(np.int16))
        starts.append(pos)
        pos += n
    return np.concatenate(parts), starts


def celebrate():
    """Toca la cancioncita moviendo los brazos en cada nota. Nunca tira error."""
    pause_listening()
    try:
        with _spk_lock:
            spk = _speaker()
            pcm, starts = _melody_pcm(_CELEBRATION, _CELEBRATION_TEMPO, spk.sample_rate)
            chunk = spk.buffer_size
            note_chunks = {s // chunk: k for k, s in enumerate(starts)}

            def on_audible(i):
                if i in note_chunks and _arm_reporter is not None:
                    try:
                        _arm_reporter(note_chunks[i])
                    except Exception:
                        pass

            _play_synced(spk, pcm, on_audible)
    except Exception as exc:
        logger.warning(f"No pude tocar la celebracion: {exc}")
        _reset_speaker()
    finally:
        resume_listening()


# Sin audio propio no hay volumen que medir: con espeak la boca recorre los
# visemas del texto a este ritmo estimado (segundos por unidad de peso; espeak
# habla a ~175 palabras por minuto) mientras el proceso siga hablando.
_BLIND_S_PER_WEIGHT = 0.05
_BLIND_PAUSE_S = 0.15  # entre pedazos de la frase (comas, puntos)


def _blind_visemes(text, proc):
    """Mueve la boca con los visemas del texto, sin audio con que alinearlos,
    hasta que termine `proc`. Nunca tira error."""
    if _viseme_reporter is None:
        return
    min_hold = _MIN_VISEME_CHUNKS * 1024 / _TTS_SAMPLE_RATE
    try:
        for seq in _text_visemes(text):
            for viseme, weight in seq:
                if proc.poll() is not None:
                    return
                _report_viseme(viseme)
                time.sleep(max(min_hold, weight * _BLIND_S_PER_WEIGHT))
            _report_viseme(gestures.VISEME_REST)
            time.sleep(_BLIND_PAUSE_S)
    finally:
        _report_viseme(gestures.VISEME_REST)


def _say_fallback(text):
    """Si Google TTS no responde (sin internet, credencial invalida...) el
    robot igual habla, aunque suene robotico, en vez de quedarse mudo. Solo
    funciona si `espeak` esta instalado en el contenedor de la App (hoy no
    lo esta: viene en el sistema host, pero no en esta imagen)."""
    if shutil.which("espeak") is None:
        logger.warning("No hay espeak en este contenedor: se salta la voz de este turno")
        return
    try:
        proc = subprocess.Popen(["espeak", "-v", "es-la", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        logger.warning(f"Tampoco funciono el respaldo espeak: {exc}")
        return
    try:
        _blind_visemes(text, proc)
        proc.wait(timeout=30)
    except Exception as exc:
        logger.warning(f"Tampoco funciono el respaldo espeak: {exc}")
        proc.kill()


_tts_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tts")


# --- Escucha activa (barge-in) ------------------------------------------------
# NATIVA: no se apaga ni se configura. El robot nunca habla "encima" del niño:
# si el niño habla, se calla y escucha, como Alexa.
#
# Mientras un guia habla, el robot sigue oyendo, pero LOCALMENTE: Vosk (modelo
# pequeno en español, models/vosk-es/) transcribe libre lo que entra por el
# mic. No cuesta nada de API: el STT de Google (pagado) solo se abre despues,
# en el turno normal, si de verdad lo interrumpieron.
#
# Hay dos formas de cortar la voz, y las dos valen siempre:
# - PALABRA CLAVE: el nombre del guia, "espera", "se me ocurrio algo",
#   "pasame con <guia>"... Ademas de callarlo dicen QUE hacer despues (seguir
#   con otro guia, anotar un aporte).
# - VOZ SUELTA (kind "speech"): cualquier frase del niño que no sea el eco del
#   propio robot. Bastan _BARGE_MIN_NOVEL palabras nuevas. Aqui el robot NO
#   contesta "¡Dime!": se calla y deja hablar (ver main._on_barge).
#
# Contra el auto-disparo (el robot oyendose a si mismo, sobre todo con bocina
# Bluetooth):
# - lo que se parece a lo que suena, o a lo ya dicho en esta respuesta, no cuenta;
# - un parcial de Vosk tiene que repetirse 2 bloques seguidos (o ser final), y
#   las claves de una sola palabra ("oye", "espera") solo valen en final;
# - compuerta de energia: tiene que haber voz de verdad en el mic;
# - _BARGE_GRACE_S de gracia al empezar cada frase;
# - con bocina Bluetooth se pide una palabra nueva mas (_BARGE_MIN_NOVEL_BT).
#
# Sin el paquete vosk o sin el modelo se apaga sola, con una linea en el log
# (y queda /interrumpir para probar sin hardware).

_VOSK_INSTALLER = Path(__file__).resolve().parent.parent / "tools" / "install_vosk_model.py"
_BARGE_GRACE_S = 0.3
_BARGE_MIN_RMS = 300  # int16; por debajo es silencio o el eco bajito del headset
_BARGE_NOISE_X = 3.0  # ... y ademas tiene que pasar 3 veces el ruido de fondo
_BARGE_VOICED_CHUNKS = 3  # bloques con voz en la ultima _BARGE_VOICED_WINDOW
_BARGE_VOICED_WINDOW = 1.5
# Un resultado FINAL de Vosk llega cuando el niño ya se callo (necesita el
# silencio para cerrar la frase): ahi la voz quedo un poco mas atras. Sin esta
# cola, un "¡Cori!" o un "¡Espera!" sueltos se perdian por la compuerta.
_BARGE_VOICED_TAIL = 3.0
_BARGE_MIN_CONF = 0.5  # confianza de Vosk (en finales) de las palabras clave
_BARGE_MIN_CONF_SPEECH = 0.5  # ... y de una frase cualquiera (Vosk libre da valores mas bajos)
_BARGE_ECHO_RATIO = 0.75  # parecido (difflib) con lo que dice el robot para contarlo como eco
# ... y el mismo parecido, pero de la FRASE entera, para la voz suelta: cuando
# Vosk entiende mal la voz del propio robot ("Cesia me contó tu reto" ->
# "se seame concedido") las palabras sueltas ya no se parecen a nada, pero la
# frase completa si. Medido con los clips de tools/test_barge_in.py --vosk:
# eco 0.62-1.00, voz de un niño 0.14-0.43.
_BARGE_ECHO_PHRASE = 0.55
_BARGE_MAX_LEAD = 2  # la frase clave tiene que empezar entre las 3 primeras palabras
_BARGE_WEAK_MAX_TOKENS = 3  # "¡Cori!", "¡Oye, Cori!": lo debil solo en frases cortas
# Palabras NUEVAS y DISTINTAS (que no son eco del robot ni muletillas) para
# cortar la voz sin palabra clave. Dos bastan: "mi colegio", "los arboles"...
_BARGE_MIN_NOVEL = 2
# Vosk transcribe el ruido del microfono como una palabra repetida ("tic tic
# tic tic..."): visto en la placa al conectarse la bocina Bluetooth. Un niño
# no repite la misma palabra tantas veces seguidas en una frase.
_BARGE_MAX_REPEAT = 3
_BARGE_MIN_NOVEL_BT = 3  # con bocina Bluetooth el mic oye al robot: una mas

# Muletillas y palabras sueltas que no cuentan como "voz nueva": son las que
# Vosk inventa con cualquier ruido. Las claves de verdad ("espera", "oye") van
# por su propio camino, no por aqui.
_BARGE_NOISE_WORDS = set(
    "a ah ahi al algo asi aqui bueno como con de del e eh el ella ellos en es esa ese eso esta este esto hay "
    "la las le lo los mas me mi mm mmm mu muy ni no nos o oh para pero por pues que se si sin su sus tan te "
    "tu un una uno uy y ya yo".split()
)

# Como suena cada guia para Vosk: "crispi" y "cesia" no estan en su
# vocabulario, pero "cris pi" y "se sia" si (y _canon_tokens() los junta).
_VOSK_NAMES = {
    "crispi": ("cris pi",),
    "carmel": ("carmel", "carmelo"),
    "cesia": ("se sia",),
    "cori": ("cori",),
    "cristal": ("cristal",),
}
# Como oye Vosk los nombres con reconocimiento libre ("¡Carmel!" -> "carmen",
# "¡Cori!" -> "cory"). Son alias SOLO de la escucha activa: en el turno normal
# manda Google STT, y ahi "Carmen" tiene que poder ser una amiga del niño.
# "concesion" vale por dos palabras: asi es como Vosk junta "con Cesia", y sin
# partirlo el pedido de cambio de guia se perderia.
_BARGE_ALIASES = {
    "carmen": "carmel", "carmelo": "carmel", "carmela": "carmel", "crispin": "crispi", "cristina": "cristal",
    "cory": "cori", "corey": "cori", "kori": "cori", "crystal": "cristal",
    "concesion": "con cesia", "concesiones": "con cesia",
}


def _barge_alias(word):
    """La palabra como la entiende la escucha activa (solo ella)."""
    return _BARGE_ALIASES.get(re.sub(r"[^a-z]", "", _normalize(word)), word)
# Las palabras van con tildes: asi estan en el vocabulario del modelo.
_INTERRUPT_VOSK = ("se me ocurrió algo", "se me ocurrió", "tengo una idea", "espera", "oye", "un momento")
_INTERRUPT_CANON = tuple(" ".join(_normalize(w) for w in p.split()) for p in _INTERRUPT_VOSK)


def _has_phrase(haystack, phrase):
    return re.search(r"(?:^|\s)" + re.escape(phrase) + r"(?:\s|$)", haystack) is not None


def _echoes(piece, spoken_tokens):
    """True si `piece` (palabras canonicas) se parece a algun tramo de lo que
    el robot esta diciendo: Vosk oye "se me ocurrio" donde el guia dijo "se te
    ocurre", asi que no alcanza con buscarlo tal cual."""
    words = piece.split()
    n = len(words)
    for i in range(max(0, len(spoken_tokens) - n) + 1):
        window = " ".join(spoken_tokens[i : i + n])
        if window and difflib.SequenceMatcher(None, window, piece).ratio() >= _BARGE_ECHO_RATIO:
            return True
    return False


def _barge_trigger(text, current_sentence, persona_keys, bt, previous_sentence="", speaker=None):
    """Decide si lo que oyo Vosk mientras hablaba `speaker` es una interrupcion.

    Pura (sin audio ni estado), para poder probarla. Devuelve None o
    {"kind": "switch"|"interrupt"|"speech", "persona": clave, "strong": bool, "words", "text"}:
    - "switch": pidio otro guia ("quiero hablar con Cori", o el nombre de OTRO
      guia a secas);
    - "interrupt": el nombre del que habla o una frase de interrupcion
      ("espera", "se me ocurrio algo");
    - "speech": no dijo ninguna clave, simplemente esta hablando. El guia se
      calla y escucha, sin contestar nada (es lo que lo hace nativo).
    strong = vale ya en un parcial de Vosk: nombre + frase ("Cori, espera"),
    "pasame con <guia>" o una frase de varias palabras ("se me ocurrio"). Un
    nombre solo o "oye"/"espera" solos son debiles: solo valen en un resultado
    final con buena confianza (ver BargeIn), porque Vosk los "encuentra" en
    palabras parecidas ("necesito" -> "se sia", "corrimos" -> "cori").
    words = las palabras canonicas que hicieron disparar (para la confianza).

    Lo que se parece a la frase que suena (o a lo ya dicho en esta respuesta)
    se ignora: es el eco del propio robot. Con bocina Bluetooth (bt) la voz
    suelta pide una palabra nueva mas.
    """
    keys = tuple(persona_keys)
    text = " ".join(_barge_alias(w) for w in (text or "").split())
    tokens, _, _ = _canon_tokens(text, keys)
    if not tokens:
        return None
    joined = " ".join(tokens)
    spoken = _canon_tokens(f"{current_sentence or ''} {previous_sentence or ''}", keys)[0]

    def live(piece):
        return not _echoes(piece, spoken)

    names = [t for t in dict.fromkeys(tokens) if t in keys and live(t)]
    phrases = [p for p in _INTERRUPT_CANON if _has_phrase(joined, p) and live(p)]
    phrase_words = [w for p in phrases for w in p.split()]

    # Una frase clave que no cuenta por ser eco igual "ocupa" palabras: "Cori,
    # se me ocurrio" es corta aunque el guia este diciendo "se te ocurre".
    covered = max((len(p.split()) for p in _INTERRUPT_CANON if _has_phrase(joined, p)), default=0)

    def pos(piece):
        m = re.search(r"(?:^|\s)" + re.escape(piece) + r"(?:\s|$)", joined)
        return len(joined[: m.start()].split()) if m else len(tokens)

    def out(kind, who, strong, words, start):
        # Quien interrumpe con una CLAVE la dice al empezar ("Cori, ...",
        # "Espera...", "Quiero hablar con..."): una clave en mitad de otra cosa
        # es Vosk forzandola ("construir un cohete" -> "un momento"). Si no
        # cumple no se pierde la interrupcion: cae en "speech" mas abajo.
        if start > _BARGE_MAX_LEAD or (not strong and len(tokens) - covered > _BARGE_WEAK_MAX_TOKENS):
            return None
        return {"kind": kind, "persona": who, "strong": strong, "words": words, "text": joined}

    m = _switch_re(keys).search(joined)
    if m:
        who, lead = m.group("who"), " ".join(m.group("lead").split())
        if who != speaker and (who in names or live(lead)):
            t = out("switch", who, True, lead.split() + [who], len(joined[: m.start()].split()))
            if t:
                return t
    others = [n for n in names if n != speaker]
    if others:
        t = out("switch", others[0], False, [others[0]], pos(others[0]))
        if t:
            return t
    first_phrase = min((pos(p) for p in phrases), default=len(tokens))
    if speaker in names:
        t = out("interrupt", speaker, bool(phrases), [speaker] + phrase_words, min(pos(speaker), first_phrase))
        if t:
            return t
    elif phrases:
        t = out("interrupt", speaker, any(" " in p for p in phrases), phrase_words, first_phrase)
        if t:
            return t
    return _speech_trigger(tokens, joined, spoken, speaker, bt)


# Palabras con las que se queda a medias un pedido de cambio de guia: si el
# parcial termina en una de ellas, lo que viene es el nombre.
_SWITCH_TAIL = {"quiero", "quisiera", "puedo", "podemos", "queremos", "gustaria", "dejame", "hablar",
                "conversar", "charlar", "seguir", "con", "a", "pasame", "pasanos", "cambiame", "cambia",
                "ahora", "hable", "llama", "habla", "hablame", "el", "la"}


def _echo_phrase(tokens, spoken):
    """Cuanto se parece TODO lo oido a algun tramo de lo que el robot esta
    diciendo (0..1). Ver _BARGE_ECHO_PHRASE."""
    if not tokens or not spoken:
        return 0.0
    oido = " ".join(tokens)
    n = len(tokens)
    mejor = 0.0
    for largo in {max(1, n - 1), n, n + 1}:
        for i in range(max(0, len(spoken) - largo) + 1):
            tramo = " ".join(spoken[i : i + largo])
            mejor = max(mejor, difflib.SequenceMatcher(None, tramo, oido).ratio())
    return mejor


def _speech_trigger(tokens, joined, spoken, speaker, bt):
    """Sin palabra clave: el niño simplemente esta hablando. Se cuenta cuanto
    de lo oido es NUEVO (no es eco del robot ni muletilla); con suficientes
    palabras nuevas el guia se calla."""
    if tokens[-1] in _SWITCH_TAIL:
        # "quiero hablar con...", "pásame a...": el nombre llega en el proximo
        # parcial. Si se cortara aqui, el pedido de cambio de guia se perderia
        # (seria una interrupcion a secas). Se espera un parcial mas.
        return None
    novel = [t for t in tokens if t not in _BARGE_NOISE_WORDS and not _echoes(t, spoken)]
    distintas = list(dict.fromkeys(novel))
    if max((novel.count(t) for t in distintas), default=0) >= _BARGE_MAX_REPEAT:
        return None  # ruido del mic: la misma palabra una y otra vez
    minimo = _BARGE_MIN_NOVEL_BT if bt else _BARGE_MIN_NOVEL
    if len(distintas) < minimo:
        return None
    if _echo_phrase(tokens, spoken) >= _BARGE_ECHO_PHRASE:
        return None  # es el robot oyendose a si mismo, mal entendido
    return {"kind": "speech", "persona": speaker, "strong": True, "words": distintas, "text": joined}


_vosk_model = None
_vosk_rec = None  # un solo reconocedor con la gramatica, reusado (Reset) en cada say()
_vosk_state = "cargando"
_vosk_lock = threading.Lock()


def _load_vosk():
    """En segundo plano al arrancar: importa vosk, baja el modelo si falta
    (tools/install_vosk_model.py) y arma el reconocedor. Nunca tira error."""
    global _vosk_model, _vosk_rec, _vosk_state
    try:
        import vosk
    except Exception as exc:
        _vosk_state = "sin vosk"
        logger.warning(f"Escucha activa desactivada: falta el paquete vosk ({exc})")
        return
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("install_vosk_model", _VOSK_INSTALLER)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        path = installer.ensure_model(log=logger.info)
        if path is None:
            _vosk_state = "sin modelo"
            logger.warning("Escucha activa desactivada: no hay modelo Vosk (ver tools/install_vosk_model.py)")
            return
        vosk.SetLogLevel(-1)
        t0 = time.monotonic()
        model = vosk.Model(str(path))
        # Sin gramatica: reconocimiento libre. Con una gramatica cerrada solo
        # se oian las palabras clave; la escucha activa nativa necesita saber
        # que el niño esta hablando aunque diga cualquier otra cosa.
        rec = vosk.KaldiRecognizer(model, _SAMPLE_RATE)
        rec.SetWords(True)
        _vosk_model, _vosk_rec, _vosk_state = model, rec, "listo"
        logger.info(f"Escucha activa lista (Vosk cargado en {time.monotonic() - t0:.1f}s)")
    except Exception as exc:
        _vosk_state = "error"
        logger.warning(f"Escucha activa desactivada: no pude cargar Vosk ({exc})")


threading.Thread(target=_load_vosk, daemon=True, name="vosk-load").start()


def barge_status():
    """Para /barge y /status. La escucha activa es nativa: solo puede estar
    lista o no (segun Vosk), nunca apagada a mano."""
    return {"vosk": _vosk_state, "lista": _vosk_rec is not None,
            "minimo": _BARGE_MIN_NOVEL_BT if _bt_sink is not None else _BARGE_MIN_NOVEL}


_active_barge = None  # el BargeIn de la voz que suena ahora (para /interrumpir)
_pending_inject = None  # /interrumpir sin nadie hablando: se dispara en la proxima voz


class BargeIn:
    """Escucha activa de UNA respuesta: lee el mic en un hilo mientras say()
    habla, y prende `event` si oye una interrupcion. `trigger` dice cual."""

    def __init__(self, persona, persona_keys):
        self.persona = persona
        self.keys = tuple(persona_keys)
        self.event = threading.Event()
        self.trigger = None
        self.current = ""
        self.previous = ""
        self.history = []  # todas las frases que ya sonaron en este say()
        self.sentence_at = time.monotonic()
        self._stop = threading.Event()
        self._thread = None
        self._inject_text = None  # /interrumpir armado
        self._inject_lock = threading.Lock()
        self.cpu_s = 0.0  # tiempo de Vosk, para el diagnostico
        self.audio_s = 0.0

    def set_sentence(self, text):
        """say() avisa cada frase que empieza a sonar (filtro de eco + gracia).
        El parcial de Vosk se va acumulando frase tras frase: el eco se mira
        contra TODO lo dicho en esta respuesta, no solo la frase anterior."""
        if self.current:
            self.history.append(self.current)
        self.current = text
        self.previous = " ".join(self.history)
        self.sentence_at = time.monotonic()

    def fired(self):
        return self.event.is_set()

    def _fire(self, trig):
        if self.event.is_set():
            return
        self.trigger = trig
        self.event.set()
        logger.info(f"Barge-in: {trig}")
        _debug(f"✋ interrupcion ({trig['kind']} → {trig['persona']}): «{trig['text']}»")

    def inject(self, text):
        """/interrumpir <texto>: como si Vosk lo hubiera oido (sin eco ni gracia)."""
        trig = _barge_trigger(text, "", self.keys, False, speaker=self.persona)
        if trig:
            self._fire(trig)
        return trig

    def _take_inject(self):
        with self._inject_lock:
            text, self._inject_text = self._inject_text, None
        return text

    def _armed_inject(self):
        text = self._take_inject()
        if text and not self._stop.is_set():
            self.inject(text)

    def reads_mic(self):
        return self._thread is not None

    def start(self):
        global _active_barge, _pending_inject
        armed = False
        if _pending_inject:
            # /interrumpir armado: se dispara 1 s despues de empezar a hablar
            # (o al terminar, si la respuesta dura menos: ver stop()).
            self._inject_text, _pending_inject = _pending_inject, None
            _active_barge = self
            threading.Timer(1.0, self._armed_inject).start()
            armed = True
        if _vosk_rec is None:
            return armed
        try:
            mic = _microphone()
        except Exception as exc:
            logger.debug(f"Escucha activa sin mic: {exc}")
            return armed
        _active_barge = self
        self._thread = threading.Thread(target=self._run, args=(mic,), daemon=True, name="barge")
        self._thread.start()
        return True

    def stop(self):
        global _active_barge
        self._stop.set()
        text = self._take_inject()
        if text:
            self.inject(text)  # la respuesta fue mas corta que 1 s
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if _active_barge is self:
            _active_barge = None
        if self.audio_s > 0:
            logger.debug(f"Vosk: {self.cpu_s:.2f}s de CPU por {self.audio_s:.1f}s de audio")

    def _run(self, mic):
        if not _vosk_lock.acquire(blocking=False):
            return  # otra voz ya lo usa (no deberia pasar: say() va con _spk_lock)
        try:
            rec = _vosk_rec
            rec.Reset()
            bt = _bt_sink is not None
            noise = 100.0
            voiced = []  # monotonic de los bloques con voz
            last_partial = None
            while not self._stop.is_set() and not self.event.is_set():
                try:
                    chunk = mic.capture()
                except Exception as exc:
                    logger.debug(f"Escucha activa: se corto el mic ({exc})")
                    return
                if chunk is None or len(chunk) == 0:
                    time.sleep(0.005)
                    continue
                now = time.monotonic()
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                if rms > max(_BARGE_MIN_RMS, _BARGE_NOISE_X * noise):
                    voiced.append(now)
                else:
                    noise = 0.95 * noise + 0.05 * rms
                voiced = [t for t in voiced if now - t < _BARGE_VOICED_TAIL]
                reciente = [t for t in voiced if now - t < _BARGE_VOICED_WINDOW]

                c0 = time.process_time()
                is_final = rec.AcceptWaveform(chunk.tobytes())
                res = json.loads(rec.Result() if is_final else rec.PartialResult())
                self.cpu_s += time.process_time() - c0
                self.audio_s += len(chunk) / _SAMPLE_RATE

                text = res.get("text") if is_final else res.get("partial")
                if not text:
                    last_partial = None
                    continue
                trig = _barge_trigger(text, self.current, self.keys, bt, self.previous, self.persona)
                # En un final la voz ya paso (Vosk cierra la frase con el
                # silencio): vale la cola larga. En un parcial, voz AHORA.
                con_voz = len(reciente if not is_final else voiced) >= _BARGE_VOICED_CHUNKS
                if trig is not None and now - _audio_change_at < _BARGE_DEVICE_GRACE_S:
                    logger.debug(f"Barge-in descartado: acaba de cambiar el audio ({trig['text']})")
                    continue
                if trig is None or not con_voz or now - self.sentence_at < _BARGE_GRACE_S:
                    if trig is None:
                        last_partial = None
                    continue
                key = (trig["kind"], trig["persona"])
                if is_final:
                    conf = _trigger_conf(res.get("result", []), trig, self.keys)
                    minima = _BARGE_MIN_CONF_SPEECH if trig["kind"] == "speech" else _BARGE_MIN_CONF
                    if conf >= minima:
                        self._fire(trig)
                    else:
                        logger.debug(f"Barge-in descartado por confianza {conf:.2f} (<{minima}): {trig['text']}")
                    last_partial = None
                elif trig["strong"] and last_partial == key:
                    self._fire(trig)
                else:
                    last_partial = key
        finally:
            _vosk_lock.release()


def _trigger_conf(result_words, trig, keys):
    """Confianza de Vosk (la peor) de las palabras que hicieron disparar, en un
    resultado final con SetWords(True). "se"+"sia" cuentan como "cesia".

    Los alias se aplican igual que en _barge_trigger(): si no, la palabra que
    disparo ("carmel", via "carmen") no se encontraba aqui y la confianza
    salia 0, que descartaba la interrupcion.
    """
    confs_in, palabras = [], []
    for w in result_words:
        if not w.get("word"):
            continue
        for pieza in _barge_alias(w["word"]).split():
            palabras.append(pieza)
            confs_in.append(w.get("conf", 1.0))
    tokens, spans, _ = _canon_tokens(" ".join(palabras), keys)
    want = set(trig.get("words") or [])
    confs = [min(confs_in[a:b]) for tok, (a, b) in zip(tokens, spans) if tok in want]
    return min(confs) if confs else 0.0


def make_barge(persona, persona_keys):
    """El BargeIn para la proxima respuesta de `persona`. Siempre se crea: la
    escucha activa es nativa. Solo devuelve None si no hay Vosk ni modelo (y
    entonces se avisa una vez en el log, porque el robot queda sin poder
    callarse cuando el niño habla)."""
    if _vosk_rec is None and not _pending_inject:
        _warn_sin_escucha()
        return None
    return BargeIn(persona, persona_keys)


_sin_escucha_avisado = False


def _warn_sin_escucha():
    global _sin_escucha_avisado
    if _sin_escucha_avisado:
        return
    _sin_escucha_avisado = True
    logger.warning(f"Sin escucha activa ({_vosk_state}): el guia no se va a poder interrumpir")
    _debug(f"⚠ sin escucha activa ({_vosk_state}): no se puede interrumpir al guía")


def inject_barge(text):
    """/interrumpir <texto>. Si un guia esta hablando con escucha activa, lo
    interrumpe ya; si no, queda armado para la proxima respuesta."""
    global _pending_inject
    b = _active_barge
    if b is not None:
        trig = b.inject(text)
        return ("now", trig) if trig else ("none", None)
    if _barge_trigger(text, "", tuple(_VOSK_NAMES), False) is None:
        return "none", None
    _pending_inject = text
    return "armed", None


# Frases cortas que se dicen igual siempre ("¡Dime!" al interrumpir): se
# sintetizan una vez y se guardan en data/tts_cache/, asi suenan al instante y
# no se paga el TTS cada vez.
_CACHED_PHRASES = {
    "dime": {
        "crispi": "¡Dime! Te escucho.",
        "carmel": "¡Dime! Soy todo oídos.",
        "cesia": "¡Dime! Te escucho.",
        "cori": "¡Dime, dime! Te escucho.",
        "cristal": "¡Dime! Te escucho.",
    },
}
_TTS_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "tts_cache"
_cached_wavs = {}


def _cached_text(persona, key):
    table = _CACHED_PHRASES.get(key) or {}
    return table.get(persona) or next(iter(table.values()), "")


def _cached_wav(persona, key):
    wav = _cached_wavs.get((persona, key))
    if wav is not None:
        return wav
    text = _cached_text(persona, key)
    # El hash del texto va en el nombre: si se cambia la frase, se vuelve a sintetizar.
    path = _TTS_CACHE_DIR / f"{persona}_{key}_{hashlib.md5(text.encode()).hexdigest()[:8]}.wav"
    if path.exists():
        wav = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    else:
        wav = _synthesize(text, _VOICES.get(persona, _DEFAULT_VOICE))
        try:
            _TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(wav.tobytes())
        except Exception as exc:
            logger.debug(f"No pude guardar {path}: {exc}")
    _cached_wavs[(persona, key)] = wav
    return wav


def warm_cached(personas):
    """Deja listos los "¡Dime!" de todos los guias (en segundo plano)."""
    for persona in personas:
        for key in _CACHED_PHRASES:
            try:
                _cached_wav(persona, key)
            except Exception as exc:
                logger.debug(f"No pude precalentar '{key}' de {persona}: {exc}")
                return


def say_cached(persona, key):
    """Dice una frase de _CACHED_PHRASES sin esperar al TTS (si ya esta en cache)."""
    text = _cached_text(persona, key)
    try:
        wav = _cached_wav(persona, key)
    except Exception as exc:
        logger.warning(f"No tengo '{key}' en cache ({exc}): lo digo normal")
        return say(persona, text)
    pause_listening()
    try:
        with _spk_lock:
            _play_with_mouth(_speaker(), wav, text)
    except Exception as exc:
        logger.warning(f"No pude reproducir '{key}': {exc}")
        _reset_speaker()
    finally:
        # Tras el "¡Dime!" el niño arranca a hablar YA: con el headset USB
        # casi no hay eco, asi que la sordera es corta (la de siempre solo con
        # la bocina Bluetooth, que sigue sonando su buffer).
        resume_listening(tail=_ECHO_TAIL_S if _bt_sink is not None else _DIME_TAIL_S)
    return {"interrupted": False, "spoken": text, "trigger": None}


def _wait_tts(fut, stop):
    """fut.result(), pero suelta antes si la escucha activa ya disparo."""
    while True:
        try:
            return fut.result(timeout=0.05)
        except TimeoutError:
            if stop is not None and stop.is_set():
                return None


def say(persona, text, barge=None):
    """Sintetiza y reproduce la respuesta, con el microfono silenciado
    mientras tanto. Las frases se piden a Google todas a la vez y suenan en
    orden: la primera arranca apenas esta lista.

    barge: un BargeIn (make_barge()) para escuchar interrupciones mientras
    habla. Devuelve {"interrupted", "spoken", "trigger"}: si lo cortaron,
    hasta donde alcanzo a decir y que frase lo corto."""
    out = {"interrupted": False, "spoken": "", "trigger": None}
    pause_listening(keep_mic=barge is not None)
    if barge is not None and not barge.start():
        barge = None
    if (barge is None or not barge.reads_mic()) and _mic is not None and _active_barge in (None, barge):
        # Nadie lo va a leer (sin Vosk, o /interrumpir armado sin Vosk): se
        # pausa como siempre.
        try:
            _mic.stop()
        except Exception:
            pass
    stop = barge.event if barge is not None else None
    t0 = time.monotonic()
    voice_name = _VOICES.get(persona, _DEFAULT_VOICE)
    parts = _split_sentences(text)
    futures = [_tts_pool.submit(_synthesize, p, voice_name) for p in parts]
    spoken = []
    try:
        with _spk_lock:
            for i, (part, fut) in enumerate(zip(parts, futures)):
                try:
                    wav = _wait_tts(fut, stop)
                except Exception as exc:
                    logger.warning(f"Google TTS fallo ({exc}); uso espeak de respaldo")
                    _debug(f"⚠ Google TTS fallo, uso espeak de respaldo: {exc}")
                    _say_fallback(" ".join(parts[i:]))
                    return out
                if wav is None:
                    break  # interrumpido mientras esperaba el audio
                if i == 0:
                    _debug(f"⏱ voz lista en {time.monotonic() - t0:.1f}s · sale por {output_name()}")
                if barge is not None:
                    barge.set_sentence(part)
                spoken.append(part)
                try:
                    if not _play_with_mouth(_speaker(), wav, part, stop=stop):
                        break
                except Exception as exc:
                    logger.warning(f"No pude reproducir por {output_name()}: {exc}")
                    _debug(f"⚠ fallo el parlante ({output_name()}): {exc}")
                    _reset_speaker()
                    return out
    finally:
        for fut in futures:
            fut.cancel()
        if barge is not None:
            barge.stop()
            if barge.fired():
                out.update(interrupted=True, trigger=barge.trigger)
        out["spoken"] = " ".join(spoken)
        # Pequeno respiro para que no quede eco residual del parlante en el
        # mic (con bocina Bluetooth, ademas, su propio buffer sigue sonando).
        # Si lo interrumpieron, el buffer ya se vacio: se contesta enseguida.
        if out["interrupted"]:
            time.sleep(0.1)
        else:
            time.sleep(0.4 if not (_spk_device or "").startswith("pipewire") else 0.6)
        resume_listening()
    return out
