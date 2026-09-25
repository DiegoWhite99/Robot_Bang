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
#
# Salida de audio: si hay una bocina Bluetooth conectada en el host (nodo
# bluez_output.* de PipeWire) se usa esa; si no, el headset USB. Se revisa en
# segundo plano cada _OUTPUT_POLL_S, asi que prender/apagar la bocina cambia
# la salida sola, sin reiniciar la App.

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
                if sink:
                    logger.info(f"Bocina Bluetooth conectada: {sink[1]}")
                    _debug(f"🔵 bocina Bluetooth conectada: {sink[1]}")
                elif _bt_sink:
                    logger.info("Bocina Bluetooth desconectada: vuelvo al headset USB")
                    _debug("🔵 bocina Bluetooth desconectada: vuelvo al headset USB")
                _bt_sink = sink
            source = sources[0] if sources else None
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
_speaking = 0
_deaf_until = 0.0
_speaking_lock = threading.Lock()


def _is_deaf():
    return _speaking > 0 or time.monotonic() < _deaf_until


def pause_listening():
    """Silencia el microfono mientras el robot habla, para que no se
    transcriba a si mismo. Con bocina Bluetooth es imprescindible: el mic la
    escucha perfecto. Cada pause_listening() va con su resume_listening()."""
    global _speaking
    with _speaking_lock:
        _speaking += 1
    if _mic is not None:
        try:
            _mic.stop()
        except Exception as exc:
            logger.debug(f"No se pudo pausar el microfono: {exc}")


def resume_listening():
    global _speaking, _deaf_until
    with _speaking_lock:
        _speaking = max(0, _speaking - 1)
        _deaf_until = max(_deaf_until, time.monotonic() + _ECHO_TAIL_S)
    _microphone()


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


def listen_turn(wake_keys, follow_up=None, follow_up_s=FOLLOW_UP_S, name_only=()):
    """Escucha en continuo hasta oir el nombre de un guia seguido de una
    pregunta, y la devuelve apenas la persona se calla (ver _ENDPOINT_S).

    follow_up: guia al que se le puede contestar SIN decir su nombre durante
    los primeros follow_up_s segundos (justo despues de que hablo).

    name_only: claves que valen dichas solas ("Carmel" y nada mas): se
    devuelven con el texto vacio (clave, "").

    Devuelve (clave, texto) o (None, None) si no se capturo nada (Google
    cierra el stream a los ~5 minutos y el bucle principal vuelve a llamar).
    """
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
    last_change = wake_at = 0.0

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

                if persona is None:
                    found = _find_wake_word(text, wake_keys)
                    if found:
                        persona, rest = found
                    elif follow_up and now - started < follow_up_s:
                        persona, rest = follow_up, text
                    else:
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
                        return persona, said()

            if persona is not None:
                if said() and now - last_change >= _ENDPOINT_S:
                    return persona, said()
                if not said() and persona in name_only and now - wake_at > _NAME_ONLY_S:
                    return persona, ""  # solo dijo el nombre: que el guia se presente
                if not said() and now - wake_at > _WAKE_ONLY_WAIT_S:
                    # Solo dijo el nombre y se quedo callado: vuelta a esperar.
                    persona = None
    finally:
        stop.set()

    if persona and said():
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


def _mouth_levels(pcm, chunk):
    """Nivel de boca (0..MOUTH_LEVELS-1) por bloque de audio, segun su volumen.

    Se normaliza contra el volumen tipico de ESTA frase (percentil 90), asi
    una voz mas bajita o mas fuerte abre la boca igual. Abre al toque y cierra
    de a un nivel por bloque, para que no titile entre silaba y silaba.
    """
    n = (len(pcm) + chunk - 1) // chunk
    padded = np.zeros(n * chunk, dtype=np.float32)
    padded[: len(pcm)] = pcm
    rms = np.sqrt(np.mean(padded.reshape(n, chunk) ** 2, axis=1))
    ref = float(np.percentile(rms, 90)) or 1.0
    top = MOUTH_LEVELS - 1
    raw = np.where(rms < _MOUTH_GATE * ref, 0, np.clip(np.ceil(rms / ref * top), 1, top)).astype(int)

    levels = []
    prev = 0
    for lvl in raw:
        prev = int(lvl) if lvl >= prev else prev - 1
        levels.append(prev)
    return levels


def _play_synced(spk, pcm, on_audible):
    """Reproduce PCM int16 de a bloques y llama on_audible(i) cuando el bloque
    i empieza a SONAR (con el atraso de _mouth_lag(), ver arriba).

    Hace lo mismo que Speaker.play_pcm(), que no deja meter nada entre
    bloque y bloque.
    """
    lag = _mouth_lag()
    chunk = spk.buffer_size
    n = (len(pcm) + chunk - 1) // chunk
    for i in range(n):
        if i >= lag:
            on_audible(i - lag)
        spk.play(pcm[i * chunk : (i + 1) * chunk])
    # Los ultimos bloques ya estan escritos pero todavia no sonaron.
    for i in range(max(0, n - lag), n):
        on_audible(i)
        time.sleep(chunk / spk.sample_rate)


def _play_with_mouth(spk, wav):
    """Reproduce el WAV avisando a la boca el nivel de cada bloque.

    Si el audio no es el formato esperado, se reproduce igual, pero sin
    mover la boca.
    """
    with wave.open(io.BytesIO(wav.tobytes()), "rb") as w:
        ok = (w.getsampwidth() == 2 and w.getnchannels() == 1 and w.getframerate() == spk.sample_rate and spk.format == np.int16)
        frames = w.readframes(w.getnframes())
    if not ok:
        logger.warning("Audio del TTS en un formato inesperado: suena sin mover la boca")
        spk.play_wav(wav)
        return

    pcm = np.frombuffer(frames, dtype="<i2").astype(np.int16)
    levels = _mouth_levels(pcm, spk.buffer_size)
    sent = None

    def report(level):
        nonlocal sent
        if level != sent:  # solo los cambios: el Bridge no tiene por que saturarse
            _report_mouth(level)
            sent = level

    try:
        _play_synced(spk, pcm, lambda i: report(levels[i]))
    finally:
        report(0)


# --- Pitido de "te escuche" (como el de Alexa) --------------------------------


def _ack_pcm(sample_rate):
    """Dos notas cortas ascendentes (~160 ms), suaves para no asustar."""
    parts = []
    for freq, dur in ((880, 0.07), (1320, 0.09)):
        t = np.arange(int(dur * sample_rate)) / sample_rate
        env = np.clip(np.minimum(t / 0.005, (dur - t) / 0.02), 0, 1)
        parts.append((np.sin(2 * np.pi * freq * t) * env * 6000).astype(np.int16))
    return np.concatenate(parts)


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


def _say_fallback(text):
    """Si Google TTS no responde (sin internet, credencial invalida...) el
    robot igual habla, aunque suene robotico, en vez de quedarse mudo. Solo
    funciona si `espeak` esta instalado en el contenedor de la App (hoy no
    lo esta: viene en el sistema host, pero no en esta imagen)."""
    if shutil.which("espeak") is None:
        logger.warning("No hay espeak en este contenedor: se salta la voz de este turno")
        return
    try:
        subprocess.run(["espeak", "-v", "es-la", text], timeout=30, capture_output=True)
    except Exception as exc:
        logger.warning(f"Tampoco funciono el respaldo espeak: {exc}")


_tts_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tts")


def say(persona, text):
    """Sintetiza y reproduce la respuesta, con el microfono silenciado
    mientras tanto. Las frases se piden a Google todas a la vez y suenan en
    orden: la primera arranca apenas esta lista."""
    pause_listening()
    t0 = time.monotonic()
    voice_name = _VOICES.get(persona, _DEFAULT_VOICE)
    parts = _split_sentences(text)
    futures = [_tts_pool.submit(_synthesize, p, voice_name) for p in parts]
    try:
        with _spk_lock:
            for i, (part, fut) in enumerate(zip(parts, futures)):
                try:
                    wav = fut.result()
                except Exception as exc:
                    logger.warning(f"Google TTS fallo ({exc}); uso espeak de respaldo")
                    _debug(f"⚠ Google TTS fallo, uso espeak de respaldo: {exc}")
                    _say_fallback(" ".join(parts[i:]))
                    return
                if i == 0:
                    _debug(f"⏱ voz lista en {time.monotonic() - t0:.1f}s · sale por {output_name()}")
                try:
                    _play_with_mouth(_speaker(), wav)
                except Exception as exc:
                    logger.warning(f"No pude reproducir por {output_name()}: {exc}")
                    _debug(f"⚠ fallo el parlante ({output_name()}): {exc}")
                    _reset_speaker()
                    return
    finally:
        for fut in futures:
            fut.cancel()
        # Pequeno respiro para que no quede eco residual del parlante en el
        # mic (con bocina Bluetooth, ademas, su propio buffer sigue sonando).
        time.sleep(0.4 if not (_spk_device or "").startswith("pipewire") else 0.6)
        resume_listening()
