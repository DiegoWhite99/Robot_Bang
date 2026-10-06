# La canción: "¡A despegar!" (assets/audio/) con los brazos al ritmo.
#
# Es distinto del baile de voice.dance(): aquel se SINTETIZA con notas, así que
# los golpes se saben de antemano. Aquí hay un MP3 de verdad, y hay que:
#
#   1. DECODIFICARLO a PCM mono al sample rate del parlante. En el contenedor
#      no hay ffmpeg del sistema ni libsndfile, así que se usa el ffmpeg que
#      viene DENTRO del wheel `imageio-ffmpeg` (un binario estático: no hace
#      falta apt, igual que espeak-ng en localvoice.py). miniaudio sería más
#      directo pero no trae rueda para aarch64 y falla al compilar.
#   2. ENCONTRARLE EL PULSO, para mover los brazos en el golpe y no a destiempo.
#      Flujo espectral + autocorrelación, en numpy puro (no hay librosa, ni
#      falta: la canción es pop infantil, con un pulso clarísimo).
#   3. GUARDARLO EN CACHÉ, porque los dos pasos juntos tardan ~3 s y el niño
#      pidió la canción ahora. La caché va a data/song_cache/ y se rehace sola
#      si cambia el MP3 (la clave lleva el tamaño y la fecha del archivo).
#
# Prueba rápida, sin arrancar la App:
#     python3 python/song.py            # decodifica, analiza y deja la caché
#     python3 python/song.py --info     # solo lo que ya hay en caché

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from arduino.app_utils import Logger

logger = Logger("chat-bang")

_ROOT = Path(__file__).resolve().parent.parent
_AUDIO_DIR = _ROOT / "assets" / "audio"
_CACHE_DIR = _ROOT / "data" / "song_cache"

# La canción se busca por nombre, no por ruta fija: el archivo se llama
# "¡A_despegar_.mp3" (con el signo de apertura), y dar por buena una ruta con
# ese carácter es pedir problemas. Cualquier audio cuyo nombre contenga
# "despegar" sirve, así que renombrarlo no rompe nada.
SONG_MATCH = "despegar"
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")

# Pulso: el rango donde puede estar el tempo de una canción infantil.
MIN_BPM, MAX_BPM = 60.0, 190.0
# La autocorrelación no distingue un tempo de su mitad ni de su doble: en esta
# canción daba picos parecidos en 59,8 / 80,4 / 122,3 BPM y se quedaba con el
# más lento, que mueve los brazos a medio tiempo y se ve dormido. Se resuelve
# como en librosa, con una preferencia por los tempos que una persona siente
# como "el pulso": una campana (en escala logarítmica) centrada en PREF_BPM.
PREF_BPM = 120.0
PREF_WIDTH = 0.9  # en octavas: a 60 o a 240 BPM la preferencia ya pesa poco
_HOP = 512  # muestras entre frames del análisis
_WIN = 1024


# --- Como se pide la cancion --------------------------------------------------
# Una sola fuente de verdad para los dos modos (bang.py y curioso.py), porque
# tener dos copias ya costo un bug: "canta" funcionaba y "quiero que cantes"
# —que es como lo pide un niño de verdad— no.
#
# Se compara sobre el texto SIN TILDES (bang._plain / curioso._plain).
#
# La forma es: [cortesia o nombre del guia] [formula de peticion] VERBO
#
# El anclaje al principio NO es un capricho: sin el, un reto normal se
# convertia en concierto ("mi reto es que nadie CANTA en el coro"). Lo que
# distingue pedir de contar es justamente que la peticion va al principio y
# no lleva sujeto delante: "nadie canta" y "mi hermana cante" quedan fuera
# porque "nadie" y "mi" no son palabras de cortesia ni formulas de peticion.
_LEAD = (r"(?:(?:crispi|carmel|cesia|cori|cristal|robot|bang|oye|mira|hola|bueno|pues|eh+|este|"
         r"y|ya|ahora|porfa|porfis|por\s+favor|dale|ok|vale|a\s+ver)[\s,.!¡]*)*")
# "quiero que...", "puedes...", "me gustaria que...", "me cantas...". El "que"
# es opcional porque Vosk a veces se lo come.
_PETICION = (r"(?:(?:yo\s+)?(?:quiero|quisiera|queremos|necesito|me\s+gustaria|puedes|podrias|podes|"
             r"puede|podemos|vamos\s+a|me|nos|te\s+pido)\s+(?:que\s+)?)?")
# Todas las formas en que se conjuga el verbo al pedirlo, con o sin pronombre
# pegado: canta, cantas, cante, cantes, canten, cantemos, cantar, cantame,
# cantanos, cantale. Ojo: "cantes" no casaba con \bcante\b (la s rompe el
# limite de palabra) y era justo la forma de "quiero que cantes".
_VERBO = (r"(?:cant(?:a|as|e|es|en|emos|amos|ar)(?:me|nos|le)?"
          r"|(?:pon|ponme|ponnos|pone|poneme)\s+(?:una\s+|la\s+|esa\s+|esta\s+)?(?:cancion|musica|tema)"
          r"|(?:toca|tocame|tocanos|tocar|dame)\s+(?:una\s+|la\s+|esa\s+|esta\s+)?(?:cancion|musica|tema)"
          r"|(?:quiero|dame)\s+(?:una\s+|la\s+|esa\s+)?(?:cancion|musica)"
          r"|echa(?:te)?\s+una\s+cancion"
          r"|a\s+despegar)")

SING_RE = re.compile(r"^" + _LEAD + _PETICION + _VERBO + r"\b")


def pide_cancion(texto_plano):
    """True si `texto_plano` (ya sin tildes) es pedirle al robot que cante."""
    return SING_RE.search(texto_plano or "") is not None


def song_path():
    """El archivo de la canción, o None si no está."""
    if not _AUDIO_DIR.is_dir():
        return None
    for path in sorted(_AUDIO_DIR.iterdir()):
        if path.suffix.lower() in AUDIO_EXTS and SONG_MATCH in path.stem.lower():
            return path
    # Si no hay ninguna que se llame así, vale cualquier audio que haya.
    for path in sorted(_AUDIO_DIR.iterdir()):
        if path.suffix.lower() in AUDIO_EXTS:
            return path
    return None


def available():
    return song_path() is not None


# --- Decodificar ----------------------------------------------------------------


def _ffmpeg():
    """La ruta del ffmpeg empaquetado, o None si falta el wheel."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        logger.warning(f"No puedo decodificar la canción: falta imageio-ffmpeg ({exc})")
        return None


def _decode(path, sample_rate):
    """PCM int16 mono al sample_rate pedido, o None si no se pudo."""
    exe = _ffmpeg()
    if exe is None:
        return None
    cmd = [exe, "-v", "error", "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le",
           "-ac", "1", "-ar", str(sample_rate), "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception as exc:
        logger.warning(f"ffmpeg no pudo con {path.name}: {exc}")
        return None
    if res.returncode != 0 or not res.stdout:
        logger.warning(f"ffmpeg falló con {path.name}: {res.stderr.decode('utf-8', 'replace')[:200]}")
        return None
    return np.frombuffer(res.stdout, dtype="<i2").astype(np.int16)


# --- Encontrar el pulso ----------------------------------------------------------


def _onset_envelope(pcm, sample_rate):
    """Cuánta ENERGÍA NUEVA entra en cada frame (flujo espectral).

    Un golpe de batería mete energía de golpe en muchas frecuencias a la vez,
    así que la suma de las subidas del espectro marca los golpes bastante mejor
    que el volumen a secas (que sube y baja también con la voz sostenida).
    """
    n = 1 + max(0, (len(pcm) - _WIN) // _HOP)
    if n < 4:
        return np.zeros(0, dtype=np.float32), sample_rate / _HOP
    x = pcm.astype(np.float32) / 32768.0
    frames = np.lib.stride_tricks.sliding_window_view(x, _WIN)[::_HOP][:n]
    mag = np.abs(np.fft.rfft(frames * np.hanning(_WIN).astype(np.float32), axis=1))
    mag = np.log1p(mag * 10.0)  # comprimir: que un bombo fuerte no tape todo lo demás
    flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    # Quitarle la tendencia lenta: lo que importa es el pico sobre su entorno.
    k = 16
    suave = np.convolve(flux, np.ones(k, dtype=np.float32) / k, mode="same")
    env = np.maximum(0.0, flux - suave)
    pico = env.max()
    return (env / pico if pico > 0 else env), sample_rate / _HOP


def _tempo_period(env, fps):
    """El periodo del pulso, en frames, por autocorrelación de la envolvente."""
    lo = int(round(fps * 60.0 / MAX_BPM))
    hi = int(round(fps * 60.0 / MIN_BPM))
    if len(env) < 2 * hi or hi <= lo:
        return None
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lags = np.arange(lo, hi + 1)
    ventana = ac[lo:hi + 1].astype(np.float64)
    # La autocorrelación cruda decae con el lag porque cada vez se solapan
    # menos muestras: se normaliza para poder comparar lags lejanos entre sí.
    ventana /= np.maximum(1.0, len(e) - lags)
    # ...y se pondera por la preferencia de tempo, que es lo que desempata
    # entre un tempo y su mitad.
    bpm = 60.0 * fps / lags
    ventana *= np.exp(-0.5 * (np.log2(bpm / PREF_BPM) / PREF_WIDTH) ** 2)
    if not len(ventana) or ventana.max() <= 0:
        return None
    return int(lags[int(np.argmax(ventana))])


def _beat_grid(env, periodo):
    """Los frames donde cae el golpe: la rejilla de periodo `periodo` que más
    energía de ataque recoge (busca la FASE que mejor encaja)."""
    mejor_fase, mejor = 0, -1.0
    for fase in range(periodo):
        idx = np.arange(fase, len(env), periodo)
        s = float(env[idx].sum())
        if s > mejor:
            mejor_fase, mejor = fase, s
    return np.arange(mejor_fase, len(env), periodo)


def beats_of(pcm, sample_rate):
    """Las muestras donde cae cada golpe. Si no se le encuentra el pulso, una
    rejilla fija a 120 BPM: mover los brazos a tiempo constante se ve mucho
    mejor que no moverlos."""
    env, fps = _onset_envelope(pcm, sample_rate)
    periodo = _tempo_period(env, fps) if len(env) else None
    if periodo:
        bpm = 60.0 * fps / periodo
        logger.info(f"Canción: pulso a {bpm:.0f} BPM")
        return (_beat_grid(env, periodo) * _HOP).astype(np.int64)
    logger.info("Canción: no le encontré el pulso, uso 120 BPM fijos")
    return np.arange(0, len(pcm), int(sample_rate * 0.5), dtype=np.int64)


# --- Caché ------------------------------------------------------------------------


def _cache_path(path, sample_rate):
    st = path.stat()
    clave = hashlib.md5(f"{path.name}|{st.st_size}|{int(st.st_mtime)}|{sample_rate}".encode()).hexdigest()[:12]
    return _CACHE_DIR / f"{clave}.npz"


def load(sample_rate, use_cache=True):
    """(pcm, golpes) de la canción, o (None, None) si no se puede.

    La primera vez decodifica y analiza (~3 s) y lo guarda; después sale de la
    caché al instante, que es lo que hace que el robot arranque a cantar sin
    que se note la espera.
    """
    path = song_path()
    if path is None:
        logger.warning(f"No hay ninguna canción en {_AUDIO_DIR}")
        return None, None
    cache = _cache_path(path, sample_rate)
    if use_cache and cache.exists():
        try:
            with np.load(cache) as z:
                return z["pcm"], z["beats"]
        except Exception as exc:
            logger.debug(f"Caché de la canción ilegible ({exc}): la rehago")
    pcm = _decode(path, sample_rate)
    if pcm is None or not len(pcm):
        return None, None
    beats = beats_of(pcm, sample_rate)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.savez(cache, pcm=pcm, beats=beats)
    except Exception as exc:
        logger.debug(f"No pude guardar la caché de la canción: {exc}")
    logger.info(f"Canción lista: {path.name}, {len(pcm)/sample_rate:.0f}s, {len(beats)} golpes")
    return pcm, beats


def warmup(sample_rate):
    """Deja la caché hecha en segundo plano, al arrancar la App."""
    try:
        load(sample_rate)
    except Exception as exc:
        logger.warning(f"No pude preparar la canción: {exc}")


if __name__ == "__main__":
    sr = 24000
    path = song_path()
    print("canción:", path)
    if path is None:
        sys.exit(1)
    cache = _cache_path(path, sr)
    print("caché:", cache, "(existe)" if cache.exists() else "(se va a crear)")
    if "--info" in sys.argv and not cache.exists():
        sys.exit(0)
    pcm, beats = load(sr, use_cache="--forzar" not in sys.argv)
    if pcm is None:
        print("no se pudo preparar")
        sys.exit(1)
    dur = len(pcm) / sr
    print(f"{dur:.1f}s, {len(beats)} golpes, {60*len(beats)/dur:.0f} BPM aprox., pico {int(np.abs(pcm).max())}")
