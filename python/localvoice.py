# Voz LOCAL: el modo Essentials sin pasar por internet.
#
# En Plus la voz es de Google (STT por streaming + TTS Chirp3-HD). Eso suena
# muy bien pero necesita red, credencial y cuota, y las tres fallan: los logs
# del 06/10 estan llenos de 503 y 504. Essentials ahora no toca la red para
# nada: el modelo (Qwen en la placa), la escucha y la voz son todos de aqui.
#
#   ESCUCHAR  Vosk, el mismo modelo pequeño en español (models/vosk-es/) que
#             ya se usa para la escucha activa. Aqui se usa para el turno
#             entero, con los mismos parciales/finales que da Google, para
#             que la maquina de estados de voice.listen_turn() sea una sola.
#   HABLAR    espeak-ng, que viene como wheel de pip (espeakng-loader: trae
#             libespeak-ng.so y espeak-ng-data con el es_dict adentro, sin
#             apt ni nada que instalar en el contenedor).
#
# La voz: "es-419+f3", español latinoamericano con la variante femenina 3.
# Suena robotica, y eso esta aceptado: lo que se pidio fue una voz de mujer
# gratis y sin internet. Es la UNICA voz del modo, porque el modo tiene una
# UNICA guia: Cristal (ver guides.ESSENTIALS_GUIDE). Aqui no hay que añadir
# voces de los otros guias: en Essentials no existen.
#
# espeak-ng sintetiza a 22050 Hz y el parlante de la App esta abierto a
# 24000 (voice._TTS_SAMPLE_RATE), asi que aqui se remuestrea y se envuelve en
# un WAV: de esa forma devuelve EXACTAMENTE lo mismo que voice._synthesize()
# (un np.uint8 con un WAV adentro) y todo lo que viene despues —la boca, los
# visemas, el corte por barge-in— sigue funcionando sin tocar una linea.
#
# Prueba rapida, sin arrancar la App:
#     python3 python/localvoice.py "hola, soy Cristal" /tmp/out.wav

import ctypes
import io
import json
import re
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

from arduino.app_utils import Logger

logger = Logger("chat-bang")

_ROOT = Path(__file__).resolve().parent.parent

# --- TTS: espeak-ng por ctypes -------------------------------------------------

ESPEAK_RATE = 22050  # lo que entrega espeak-ng; se remuestrea al salir

# LA voz del modo Essentials. Una sola, a proposito: en este modo hay UNA sola
# guia, Cristal (ver guides.ESSENTIALS_GUIDE), asi que tener cinco voces aqui
# seria prometer cinco guias que no existen.
#
# "es-419+f3" = español latinoamericano con la variante femenina 3 de
# espeak-ng. Suena robotica, y esta aceptado: lo que se pidio fue una voz de
# mujer gratis y sin internet.
CRISTAL_VOICE = "es-419+f3"
DEFAULT_VOICE = CRISTAL_VOICE

# voice._voice_name() busca aqui por guia. Solo esta Cristal: si en este modo
# llegara cualquier otro nombre (no deberia, guides no lo deja), cae en
# DEFAULT_VOICE, que es la misma — en Essentials siempre habla Cristal.
VOICES = {"cristal": CRISTAL_VOICE}

# Palabras por minuto. espeak-ng viene en 175, que para un niño de 5 a 14 años
# leyendo una voz ya de por si robotica es demasiado rapido: a 150 se entiende
# bastante mejor y no se hace lento.
WPM = 150
PITCH = 60  # 0..99; por encima de 50 suena mas agudo, que ayuda a la voz femenina

_AUDIO_OUTPUT_SYNCHRONOUS = 0x02
_CHARS_UTF8 = 1
_espeak = None
_espeak_lock = threading.Lock()  # la libreria tiene UN solo callback global
_buffer = []
_state = "sin cargar"


def _synth_callback(wav, numsamples, events):
    if numsamples > 0 and wav:
        _buffer.append(np.ctypeslib.as_array(wav, (numsamples,)).copy())
    return 0  # 0 = sigue sintetizando


_CB_TYPE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)
_CB = _CB_TYPE(_synth_callback)


def _lib():
    """La libreria ya inicializada, o None si no se pudo (nunca lanza)."""
    global _espeak, _state
    if _espeak is not None:
        return _espeak
    try:
        import espeakng_loader
    except Exception as exc:
        _state = "sin espeakng-loader"
        logger.warning(f"Voz local no disponible: falta el paquete espeakng-loader ({exc})")
        return None
    try:
        lib = ctypes.CDLL(espeakng_loader.get_library_path())
        # AUDIO_OUTPUT_SYNCHRONOUS: espeak no abre ningun dispositivo de audio,
        # nos entrega el PCM por el callback y lo reproducimos nosotros (que es
        # lo que permite moverle la boca y cortarlo a media frase).
        rate = lib.espeak_Initialize(_AUDIO_OUTPUT_SYNCHRONOUS, 0, espeakng_loader.get_data_path().encode(), 0)
        if rate <= 0:
            raise RuntimeError(f"espeak_Initialize devolvio {rate}")
        lib.espeak_SetSynthCallback(_CB)
        lib.espeak_SetParameter(1, WPM, 0)  # 1 = espeakRATE
        lib.espeak_SetParameter(3, PITCH, 0)  # 3 = espeakPITCH
        _espeak, _state = lib, "listo"
        logger.info(f"Voz local lista (espeak-ng a {rate} Hz)")
    except Exception as exc:
        _state = "error"
        logger.warning(f"Voz local no disponible: no pude cargar espeak-ng ({exc})")
    return _espeak


def tts_state():
    """Para /status y el panel: 'listo', 'sin espeakng-loader', 'error'..."""
    if _espeak is None and _state == "sin cargar":
        _lib()
    return _state


def available():
    return _lib() is not None


def _resample(pcm, desde, hasta):
    """Remuestreo lineal. Para una voz robotica alcanza y sobra, y no arrastra
    scipy (que no esta en el contenedor)."""
    if desde == hasta or len(pcm) == 0:
        return pcm
    n = int(round(len(pcm) * hasta / desde))
    x = np.linspace(0.0, len(pcm) - 1, n, dtype=np.float64)
    return np.interp(x, np.arange(len(pcm), dtype=np.float64), pcm.astype(np.float64)).astype(np.int16)


def _wav_bytes(pcm, sample_rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return np.frombuffer(buf.getvalue(), dtype=np.uint8)


def synthesize(text, voice=None, sample_rate=24000):
    """Un WAV (np.uint8) con `text` dicho por espeak-ng, al sample_rate pedido.

    Misma firma de salida que voice._synthesize(): asi el modo local entra por
    el mismo sitio y no hay dos caminos de reproduccion que mantener.
    Lanza RuntimeError si espeak-ng no esta disponible.
    """
    lib = _lib()
    if lib is None:
        raise RuntimeError(f"espeak-ng no disponible ({_state})")
    data = (text or "").strip()
    if not data:
        return _wav_bytes(np.zeros(0, dtype=np.int16), sample_rate)
    raw = data.encode("utf-8")
    with _espeak_lock:
        _buffer.clear()
        if lib.espeak_SetVoiceByName((voice or DEFAULT_VOICE).encode()) != 0:
            logger.warning(f"espeak-ng no conoce la voz '{voice}': uso {DEFAULT_VOICE}")
            lib.espeak_SetVoiceByName(DEFAULT_VOICE.encode())
        rc = lib.espeak_Synth(raw, len(raw) + 1, 0, 0, 0, _CHARS_UTF8, None, None)
        lib.espeak_Synchronize()
        trozos = list(_buffer)
        _buffer.clear()
    if rc != 0:
        raise RuntimeError(f"espeak_Synth devolvio {rc}")
    pcm = np.concatenate(trozos) if trozos else np.zeros(0, dtype=np.int16)
    return _wav_bytes(_resample(pcm, ESPEAK_RATE, sample_rate), sample_rate)


# --- STT: Vosk para el turno entero --------------------------------------------
# La escucha activa (voice.BargeIn) ya usa Vosk mientras el guia habla. Aqui se
# usa para ESCUCHAR EL TURNO, que es lo que en Plus hace Google. Se arma un
# reconocedor propio: el de la escucha activa se resetea en cada frase que
# suena y los dos nunca corren a la vez, pero compartirlo seria pedir problemas.

_rec = None
_rec_lock = threading.Lock()


def _recognizer(sample_rate):
    """El KaldiRecognizer del turno, o None si no hay Vosk/modelo."""
    global _rec
    if _rec is not None:
        return _rec
    try:
        import vosk

        spec_path = _ROOT / "tools" / "install_vosk_model.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("install_vosk_model", spec_path)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        path = installer.ensure_model(log=logger.info)
        if path is None:
            logger.warning("Escucha local sin modelo Vosk: el modo Essentials no va a poder oir")
            return None
        vosk.SetLogLevel(-1)
        rec = vosk.KaldiRecognizer(vosk.Model(str(path)), sample_rate)
        rec.SetWords(True)
        _rec = rec
        logger.info("Escucha local lista (Vosk para el turno completo)")
    except Exception as exc:
        logger.warning(f"Escucha local no disponible ({exc})")
    return _rec


def stt_ready(sample_rate=16000):
    return _recognizer(sample_rate) is not None


def listen_events(mic, stop, events, sample_rate=16000, voice_at=None, rms_gate=420):
    """Lee el microfono con Vosk y va dejando en `events` lo mismo que pone el
    lector de Google: ("text", frase, es_final), y ("end", None, None) al
    terminar. Asi voice.listen_turn() no se entera de con que motor escucha.

    Los parciales de Vosk SON el texto completo de la frase en curso (igual
    que los interim de Google), asi que la maquina de estados —palabra clave,
    cambio de guia, fin de frase adaptativo— vale tal cual.
    """
    rec = _recognizer(sample_rate)
    if rec is None:
        events.put(("error", RuntimeError("sin Vosk: el modo Essentials no puede escuchar"), None))
        events.put(("end", None, None))
        return
    with _rec_lock:
        try:
            rec.Reset()
            ultimo = ""
            while not stop.is_set():
                try:
                    chunk = mic.capture()
                except Exception as exc:
                    events.put(("error", exc, None))
                    break
                if chunk is None or len(chunk) == 0:
                    time.sleep(0.005)
                    continue
                if voice_at is not None:
                    try:
                        if float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) > rms_gate:
                            voice_at[0] = time.monotonic()
                    except Exception:
                        pass
                final = rec.AcceptWaveform(chunk.tobytes())
                res = json.loads(rec.Result() if final else rec.PartialResult())
                texto = (res.get("text") if final else res.get("partial")) or ""
                if final:
                    if texto.strip():
                        events.put(("text", texto.strip(), True))
                    ultimo = ""
                elif texto.strip() and texto.strip() != ultimo:
                    ultimo = texto.strip()
                    events.put(("text", ultimo, False))
        finally:
            events.put(("end", None, None))


# --- Como oye Vosk los nombres de los guias ------------------------------------
# En el turno de Plus manda Google, que escribe "Crispi" bien (va en
# _PHRASE_HINTS). Vosk no tiene esos nombres en su vocabulario y los parte o
# los cambia por palabras que si tiene. voice._WAKE_ALIASES ya trae los fallos
# de Google; estos son los de Vosk, y voice.py los suma cuando escucha local.
VOSK_WAKE_ALIASES = {
    "crispin": "crispi", "cristina": "cristal", "crispa": "crispi",
    "carmen": "carmel", "carmelo": "carmel", "carmela": "carmel",
    "cory": "cori", "corey": "cori", "kori": "cori", "corre": "cori",
    "crystal": "cristal", "cristal": "cristal",
    "sesia": "cesia", "sesion": "cesia", "cecilia": "cesia",
    # Partidos en dos: _canon_tokens() prueba tambien el par de palabras.
    "crispi": "crispi", "sesia2": "cesia",
}


if __name__ == "__main__":
    texto = sys.argv[1] if len(sys.argv) > 1 else "Hola, soy Cristal. Vamos a pensar juntos tu reto."
    destino = sys.argv[2] if len(sys.argv) > 2 else "/tmp/localvoice.wav"
    print("espeak-ng:", tts_state())
    wav = synthesize(texto, DEFAULT_VOICE)
    Path(destino).write_bytes(wav.tobytes())
    print(f"{len(wav)} bytes -> {destino}")
    print("vosk:", "listo" if stt_ready() else "no disponible")
