# Voz LOCAL: el modo Essentials sin pasar por internet.
#
# En Plus la voz es de Google (STT por streaming + TTS Chirp3-HD). Eso suena
# muy bien pero necesita red, credencial y cuota, y las tres fallan: los logs
# del 06/10 estan llenos de 503 y 504. Essentials no toca la red para nada: el
# modelo (Qwen en la placa), la escucha y la voz son todos de aqui.
#
#   ESCUCHAR  Vosk, el mismo modelo pequeño en español (models/vosk-es/) que
#             ya se usa para la escucha activa. Aqui se usa para el turno
#             entero, con los mismos parciales/finales que da Google, para
#             que la maquina de estados de voice.listen_turn() sea una sola.
#   HABLAR    Piper (voz neuronal, gratis y offline), con espeak-ng de
#             respaldo. Ver abajo.
#
# Por que Piper y no espeak-ng a secas (que es lo que habia hasta 1.1.1):
# espeak-ng es un sintetizador por formantes de los años 90; se entiende, pero
# suena a robot de dibujos animados y para un niño de 5 años es cansador. Piper
# es una red neuronal (VITS) que corre en CPU con onnxruntime: suena a persona,
# sigue siendo gratis y sigue sin tocar internet. El precio es tiempo de CPU,
# y de ahi sale la eleccion de modelo (ver PIPER_VOICE).
#
# espeak-ng NO se quita: queda de respaldo. Si falta el modelo .onnx (son
# decenas de MB y van fuera de git, como el de Vosk) o falla onnxruntime, el
# robot habla igual, feo pero habla, en vez de quedarse mudo.
#
# Piper sintetiza al sample rate de su modelo (22050 Hz en las voces en
# español) y el parlante de la App esta abierto a 24000 (voice._TTS_SAMPLE_RATE),
# asi que aqui se remuestrea y se envuelve en un WAV: de esa forma devuelve
# EXACTAMENTE lo mismo que voice._synthesize() (un np.uint8 con un WAV adentro)
# y todo lo que viene despues —la boca, los visemas, el corte por barge-in—
# sigue funcionando sin tocar una linea.
#
# Prueba rapida, sin arrancar la App:
#     python3 python/localvoice.py "hola, soy Cristal" /tmp/out.wav

import ctypes
import io
import json
import os
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

# --- TTS: Piper (voz neuronal, offline) ----------------------------------------
# Los modelos viven en models/piper/ (fuera de git, como el de Vosk) y los baja
# tools/install_piper_voice.py, que este modulo llama solo la primera vez.

PIPER_DIR = _ROOT / "models" / "piper"
_VOICE_FILE = _ROOT / "data" / "piper_voice.txt"  # para cambiarla sin tocar codigo

# LA voz del modo Essentials. Una sola, a proposito: en este modo hay UNA sola
# guia, Cristal (ver guides.ESSENTIALS_GUIDE), asi que tener cinco voces aqui
# seria prometer cinco guias que no existen.
#
# POR QUE ESTA Y NO es_AR-daniela-high, que es la que se pidio:
# medido en esta placa (UNO Q, 4 nucleos, el 07/10/2026, con el runner del LLM
# encendido), sobre la misma frase, con el modelo ya cargado:
#
#     VOZ                     RTF    SEXO    ACENTO
#     es_AR-daniela-high      4,97   mujer   argentino     INVIABLE
#     es_MX-claude-high       0,77   mujer   mexicano      <- la de fabrica
#     es_ES-sharvard-medium   0,67   mujer   España (hablante F)
#     es_MX-ald-medium        0,66   hombre  mexicano
#     es_ES-mls_9972-low      0,50   -       España
#     es_ES-mls_10246-low     0,56   -       España
#
# "RTF" (real time factor) es segundos de CPU por segundo de audio: por encima
# de 1 el robot tarda mas en PREPARAR la frase que en decirla. daniela esta en
# 5, o sea que una respuesta de 10 s se hace esperar casi un minuto — y el modo
# Essentials ya es el lento de los dos. No es cuestion de hilos: se midieron
# 91,8 s de CPU para 27,5 s de reloj, o sea que ya usa ~3,3 de los 4 nucleos.
#
# De las que SI dan, es_MX-claude-high es la que mas se parece a lo que se
# pidio: mujer, latinoamericana (que para la CUN, en Colombia, suena mucho mas
# natural que el acento de España) y de calidad "high".
#
# daniela sigue disponible, y cambiarla no necesita tocar codigo: se escribe el
# nombre en data/piper_voice.txt (o en la variable de entorno
# BANG_PIPER_VOICE) y el robot la baja y la usa.
PIPER_DEFAULT = "es_MX-claude-high"

# Las voces de varios hablantes traen un mapa {'M': 0, 'F': 1}. Como la unica
# guia del modo es Cristal (mujer), se elige sola la femenina; esto solo hace
# falta cuando el modelo no trae el mapa.
PIPER_SPEAKER_FALLBACK = 0

# >1 habla mas despacio, <1 mas rapido. 1.0 es el ritmo natural del modelo, y
# para niños de 5 a 14 años se deja un pelin mas lento.
PIPER_LENGTH_SCALE = 1.05


def piper_voice_name():
    """Que voz Piper toca usar: BANG_PIPER_VOICE, data/piper_voice.txt o la de fabrica."""
    env = (os.environ.get("BANG_PIPER_VOICE") or "").strip()
    if env:
        return env
    try:
        elegida = _VOICE_FILE.read_text().strip()
        if elegida:
            return elegida
    except Exception:
        pass
    return PIPER_DEFAULT


# voice._voice_name() busca aqui por guia. Solo esta Cristal: si en este modo
# llegara cualquier otro nombre (no deberia, guides no lo deja), cae en
# DEFAULT_VOICE, que es la misma — en Essentials siempre habla Cristal.
CRISTAL_VOICE = "cristal"
DEFAULT_VOICE = CRISTAL_VOICE
VOICES = {"cristal": CRISTAL_VOICE}

_piper = None  # (PiperVoice, speaker_id, sample_rate)
_piper_lock = threading.Lock()
_piper_state = "sin cargar"


def _piper_model_path():
    """El .onnx de la voz elegida, bajandolo si hace falta. None si no se pudo."""
    nombre = piper_voice_name()
    onnx = PIPER_DIR / f"{nombre}.onnx"
    if onnx.exists() and (PIPER_DIR / f"{nombre}.onnx.json").exists():
        return onnx
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "install_piper_voice", _ROOT / "tools" / "install_piper_voice.py"
        )
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        return installer.ensure_voice(nombre, log=logger.info)
    except Exception as exc:
        logger.warning(f"No pude preparar la voz Piper '{nombre}': {exc}")
        return None


def _piper_voice():
    """(PiperVoice, speaker_id, sample_rate) ya cargado, o None. Nunca lanza."""
    global _piper, _piper_state
    if _piper is not None:
        return _piper
    with _piper_lock:
        if _piper is not None:
            return _piper
        try:
            from piper import PiperVoice
        except Exception as exc:
            _piper_state = "sin piper-tts"
            logger.warning(f"Voz Piper no disponible: falta el paquete piper-tts ({exc})")
            return None
        onnx = _piper_model_path()
        if onnx is None:
            _piper_state = "sin modelo"
            return None
        try:
            t0 = time.monotonic()
            v = PiperVoice.load(str(onnx))
            # Voz de varios hablantes: se queda con la femenina (Cristal).
            mapa = (v.config.speaker_id_map or {}) if hasattr(v.config, "speaker_id_map") else {}
            spk = None
            if mapa:
                spk = next((i for k, i in mapa.items() if str(k).upper().startswith("F")), PIPER_SPEAKER_FALLBACK)
            _piper = (v, spk, v.config.sample_rate)
            _piper_state = f"listo ({onnx.stem})"
            logger.info(f"Voz local lista: Piper {onnx.stem} cargado en {time.monotonic() - t0:.1f}s")
        except Exception as exc:
            _piper_state = "error"
            logger.warning(f"Voz Piper no disponible: no pude cargar {onnx.name} ({exc})")
    return _piper


def _piper_pcm(text):
    """PCM int16 de `text` dicho por Piper, con su sample rate. None si no se pudo."""
    cargado = _piper_voice()
    if cargado is None:
        return None, None
    v, spk, rate = cargado
    try:
        from piper import SynthesisConfig

        cfg = SynthesisConfig(length_scale=PIPER_LENGTH_SCALE, speaker_id=spk)
        with _piper_lock:  # onnxruntime no gana nada con dos sintesis a la vez
            trozos = [c.audio_int16_bytes for c in v.synthesize(text, syn_config=cfg)]
    except Exception as exc:
        logger.warning(f"Piper no pudo decir la frase ({exc}): uso espeak-ng")
        return None, None
    if not trozos:
        return None, None
    return np.frombuffer(b"".join(trozos), dtype="<i2"), rate


# --- TTS de respaldo: espeak-ng por ctypes --------------------------------------
# Si Piper no esta (falta el wheel, falta el .onnx, falla onnxruntime) el robot
# habla igual. Suena a robot de los 90, pero habla, que es mucho mejor que
# quedarse mudo en mitad de una feria.

ESPEAK_RATE = 22050  # lo que entrega espeak-ng; se remuestrea al salir

# "es-419+f3" = español latinoamericano con la variante femenina 3 de espeak-ng.
ESPEAK_VOICE = "es-419+f3"

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
        logger.warning(f"Respaldo de voz no disponible: falta espeakng-loader ({exc})")
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
        logger.info(f"Respaldo de voz listo (espeak-ng a {rate} Hz)")
    except Exception as exc:
        _state = "error"
        logger.warning(f"Respaldo de voz no disponible: no pude cargar espeak-ng ({exc})")
    return _espeak


def _espeak_pcm(text):
    """PCM int16 de `text` por espeak-ng, a ESPEAK_RATE. None si no se pudo."""
    lib = _lib()
    if lib is None:
        return None
    raw = text.encode("utf-8")
    with _espeak_lock:
        _buffer.clear()
        if lib.espeak_SetVoiceByName(ESPEAK_VOICE.encode()) != 0:
            logger.warning(f"espeak-ng no conoce la voz '{ESPEAK_VOICE}'")
        rc = lib.espeak_Synth(raw, len(raw) + 1, 0, 0, 0, _CHARS_UTF8, None, None)
        lib.espeak_Synchronize()
        trozos = list(_buffer)
        _buffer.clear()
    if rc != 0:
        logger.warning(f"espeak_Synth devolvio {rc}")
        return None
    return np.concatenate(trozos) if trozos else None


# --- Lo que ve el resto de la App ------------------------------------------------


def tts_state():
    """Para /status y el panel: 'Piper es_ES-sharvard-medium', 'espeak-ng'..."""
    if _piper is None and _piper_state == "sin cargar":
        _piper_voice()
    if _piper is not None:
        return _piper_state
    if _espeak is None and _state == "sin cargar":
        _lib()
    return f"espeak-ng ({_piper_state})" if _espeak is not None else f"sin voz: piper {_piper_state}, espeak {_state}"


def available():
    """True si hay ALGUNA voz local (Piper o, en su defecto, espeak-ng)."""
    return _piper_voice() is not None or _lib() is not None


def _resample(pcm, desde, hasta):
    """Remuestreo lineal. Para esto alcanza y sobra, y no arrastra scipy (que
    no esta en el contenedor)."""
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
    """Un WAV (np.uint8) con `text` dicho por la voz local, al sample_rate pedido.

    Primero Piper; si no esta, espeak-ng. Misma firma de salida que
    voice._synthesize(): asi el modo local entra por el mismo sitio y no hay
    dos caminos de reproduccion que mantener.
    Lanza RuntimeError solo si NINGUNO de los dos esta disponible.
    """
    data = (text or "").strip()
    if not data:
        return _wav_bytes(np.zeros(0, dtype=np.int16), sample_rate)

    pcm, rate = _piper_pcm(data)
    if pcm is None:
        pcm, rate = _espeak_pcm(data), ESPEAK_RATE
    if pcm is None:
        raise RuntimeError(f"no hay voz local (piper: {_piper_state}, espeak: {_state})")
    return _wav_bytes(_resample(pcm, rate, sample_rate), sample_rate)


def warmup():
    """Carga el modelo (~11 s) mientras nadie espera. voice.warmup() lo llama."""
    _piper_voice()


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
    import time as _t

    texto = sys.argv[1] if len(sys.argv) > 1 else "Hola, soy Cristal. Vamos a pensar juntos tu reto."
    destino = sys.argv[2] if len(sys.argv) > 2 else "/tmp/localvoice.wav"
    print("voz elegida:", piper_voice_name())
    print("tts:", tts_state())
    t0 = _t.monotonic()
    wav = synthesize(texto, DEFAULT_VOICE)
    tardo = _t.monotonic() - t0
    Path(destino).write_bytes(wav.tobytes())
    audio = max(0.001, (len(wav) - 44) / 2 / 24000)
    print(f"{len(wav)} bytes -> {destino}")
    print(f"{tardo:.2f}s para {audio:.2f}s de audio (RTF {tardo / audio:.2f})")
    print("vosk:", "listo" if stt_ready() else "no disponible")
