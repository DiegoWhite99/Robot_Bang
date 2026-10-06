# Selector de "cerebro": PLUS (Gemini en la nube) o ESSENTIALS (modelo local).
#
#   plus        lo de siempre: Gemini via arduino:cloud_llm (ver brain.py).
#               Si Gemini falla (sin API_KEY, cuota, 503, None), ESE turno lo
#               contesta el modelo local y se avisa en el panel de diagnostico.
#   essentials  Qwen3.5-0.8B en la propia placa (arduino:llm, servido por
#               llamacpp-models-runner). Mas lento y mas simple, sin Gemini.
#               Desde 1.1.1 la voz tampoco sale a internet: se oye con Vosk y
#               se habla con espeak-ng (ver localvoice.py). Y hay una sola
#               guia, Cristal (ver guides.ESSENTIALS_GUIDE).
#
# El modo lo elige un adulto desde el dashboard (boton ESSENTIALS / PLUS) o la
# terminal (/modo) y se guarda en data/llm_mode.txt, igual que el modo de la
# bocina (voice.bt_mode()).
#
# Cifras de esta placa (INFORME-MODELOS-LOCALES.md): el modelo local lee el
# prompt a ~9 tok/s y genera a ~5 tok/s, y cargarlo cuesta ~22 s. De ahi:
# - instancias de LargeLanguageModel construidas UNA vez y reusadas (el
#   constructor hace un list_models() por HTTP);
# - system prompt corto y FIJO por guia (llama.cpp reusa el prefijo en cache)
#   y lo variable (fase, reto, pista del RAG) en el mensaje del usuario;
# - memoria corta (LOCAL_MEMORY mensajes) y respuestas de LOCAL_MAX_TOKENS;
# - un candado alrededor de la generacion: chat_stream() del brick lanza
#   AlreadyGenerating si dos hilos usan la misma instancia, y aunque sean
#   instancias distintas el runner tiene 4 hilos de CPU para todo.

import threading
import time
from pathlib import Path

from arduino.app_utils import Logger

logger = Logger("chat-bang")

MODES = ("plus", "essentials")
DEFAULT_MODE = "plus"
MODE_LABELS = {"plus": "PLUS (Gemini y voz de Google)", "essentials": "ESSENTIALS (todo en la placa, sin internet)"}
_MODE_PATH = Path(__file__).resolve().parent.parent / "data" / "llm_mode.txt"

LOCAL_MAX_TOKENS = 70  # ~14 s de generacion como mucho; 1-2 frases + 1 pregunta
LOCAL_MEMORY = 4  # 2 intercambios: mas memoria = mas prompt que leer cada turno
LOCAL_TEMPERATURE = 0.5
LOCAL_TIMEOUT = 60  # una respuesta con el modelo ya cargado (lee ~300 tokens y genera 70)
# La carga del modelo (~22 s) se paga aparte, con warmup_local() y su propio
# plazo: si el modelo no se uso hace rato, chat_local() lo precalienta antes
# de preguntar, asi la primera respuesta no se come el LOCAL_TIMEOUT cargando.
LOCAL_COLD_TIMEOUT = 120
LOCAL_WARM_TTL = 600  # s: pasado esto sin usarlo, se asume que hay que volver a cargarlo
# Cortacircuitos de Gemini en Plus: tras un fallo, durante GEMINI_COOLDOWN s
# no se le pregunta (cada intento fallido cuesta hasta 3 modelos x 20 s x 2) y
# los turnos van directo al modelo local.
GEMINI_COOLDOWN = 90

_mode_lock = threading.Lock()
_gen_lock = threading.Lock()  # una generacion local a la vez
_build_lock = threading.Lock()
_local = {}  # (cache_key, system_prompt) -> LargeLanguageModel
_reporter = None
_last = threading.local()
_local_ok_at = None  # time.monotonic() de la ultima respuesta local (None: frio)
_gemini_down_until = 0.0


def set_reporter(fn):
    """main.py pasa _broadcast_debug: los respaldos se ven en el dashboard."""
    global _reporter
    _reporter = fn


def _report(text):
    logger.info(text)
    if _reporter:
        try:
            _reporter(text)
        except Exception:
            pass


# --- Modo ----------------------------------------------------------------------


def _read_mode():
    try:
        m = _MODE_PATH.read_text().strip().lower()
    except FileNotFoundError:
        m = ""
    except Exception as exc:
        logger.warning(f"No pude leer {_MODE_PATH} ({exc}): uso {DEFAULT_MODE}")
        m = ""
    return m if m in MODES else DEFAULT_MODE


_mode = _read_mode()


def mode():
    return _mode


def is_local():
    return _mode == "essentials"


def set_mode(m):
    """Cambia y guarda el modo. Devuelve el modo final (o lanza ValueError).

    Al pasar a Essentials se precalienta el modelo local en segundo plano,
    para que el primer turno no pague la carga (~22 s).
    """
    global _mode
    m = (m or "").strip().lower()
    aliases = {"local": "essentials", "essential": "essentials", "basico": "essentials", "nube": "plus", "gemini": "plus", "cloud": "plus"}
    m = aliases.get(m, m)
    if m not in MODES:
        raise ValueError(f"Modo desconocido '{m}'. Opciones: {', '.join(MODES)}")
    with _mode_lock:
        changed = m != _mode
        _mode = m
        try:
            _MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _MODE_PATH.write_text(m)
        except Exception as exc:
            logger.warning(f"No pude guardar {_MODE_PATH}: {exc}")
    if changed:
        logger.info(f"Modo LLM -> {m}")
        if m == "essentials":
            threading.Thread(target=warmup_local, daemon=True, name="llm-warmup").start()
    return m


def reset_source():
    """bang.turn() lo llama al empezar: asi un turno sin LLM no hereda el anterior."""
    _last.source = None


def last_source():
    """Quien contesto la ultima llamada principal de este hilo: 'gemini',
    'local' o None. Las llamadas secundarias (fallback=False) que fallan no lo
    tocan: un reescritor caido no convierte en 'plantilla' lo que dijo Gemini."""
    return getattr(_last, "source", None)


def gemini_down():
    """True mientras el cortacircuitos tiene a Gemini apartado (ver chat())."""
    return not is_local() and time.monotonic() < _gemini_down_until


# --- Modelo local ----------------------------------------------------------------


def _local_llm(cache_key, system_prompt, temperature, memory):
    """Instancia local reutilizada. Con memoria, una por conversacion; sin
    memoria, una por system prompt (se le borra el historial en cada uso)."""
    from arduino.app_bricks.llm import LargeLanguageModel

    ck = (cache_key if memory else "_suelta", system_prompt, temperature)
    with _build_lock:
        llm = _local.get(ck)
        if llm is None:
            llm = LargeLanguageModel(
                system_prompt=system_prompt,
                temperature=LOCAL_TEMPERATURE if temperature is None else temperature,
                max_tokens=LOCAL_MAX_TOKENS,
                timeout=LOCAL_TIMEOUT,
                max_retries=0,  # si el runner no responde, se falla rapido
            ).with_memory(max_messages=LOCAL_MEMORY if memory else 2)
            _local[ck] = llm
        return llm


def chat_local(cache_key, system_prompt, text, temperature=None, memory=True):
    """Pregunta al modelo local. Devuelve el texto o None si falla."""
    global _local_ok_at
    if _local_ok_at is None or time.monotonic() - _local_ok_at > LOCAL_WARM_TTL:
        if warmup_local(system_prompt) is False:  # None: habia otra generacion, ya esta cargado
            _last.source = None
            return None  # el runner no carga: no se esperan otros LOCAL_TIMEOUT s
    t0 = time.monotonic()
    try:
        llm = _local_llm(cache_key, system_prompt, temperature, memory)
        with _gen_lock:
            if not memory:
                llm.clear_memory()
            reply = llm.chat(text)
        logger.info(f"Modelo local: {time.monotonic() - t0:.1f}s")
        _local_ok_at = time.monotonic()
        _last.source = "local"
        return (reply or "").strip() or None
    except Exception as exc:
        logger.warning(f"El modelo local no respondio ({exc})")
        _report(f"⚠ modelo local sin respuesta: {str(exc)[:80]}")
        _last.source = None
        return None


def clear(persona):
    """Borra la memoria local de las conversaciones de un guia (o de todos con None)."""
    with _build_lock:
        for ck, llm in list(_local.items()):
            key = ck[0]
            if persona is None or key == persona or (isinstance(key, tuple) and key[0] == persona):
                llm.clear_memory()


def warmup_local(system_prompt=None):
    """Carga el modelo en el runner (y, si se pasa, cachea el prefijo de un guia).

    Usa una instancia suelta con max_tokens=1: no ensucia ninguna memoria.
    Devuelve True si cargo, False si fallo y None si habia otra generacion.
    """
    global _local_ok_at
    if not _gen_lock.acquire(timeout=1):
        return None  # ya hay una generacion en curso: el modelo ya esta cargado
    t0 = time.monotonic()
    try:
        from arduino.app_bricks.llm import LargeLanguageModel

        LargeLanguageModel(system_prompt=system_prompt or "Responde: ok", max_tokens=1, timeout=LOCAL_COLD_TIMEOUT, max_retries=0).chat("ok")
        _local_ok_at = time.monotonic()
        logger.info(f"Modelo local precalentado en {time.monotonic() - t0:.1f}s")
        return True
    except Exception as exc:
        logger.warning(f"No se pudo precalentar el modelo local: {exc}")
        _report("⚠ el modelo local no responde: ¿está arduino:llm en app.yaml?")
        return False
    finally:
        _gen_lock.release()


# --- Punto de entrada -----------------------------------------------------------


def chat(cache_key, system_prompt, text, temperature=None, memory=True, local=None, fallback=True):
    """Lo que usa brain.chat(): elige cerebro segun el modo.

    local:    (system, texto) cortos para el modelo local. Si no se pasa, se
              usan los mismos de Gemini (sirve, pero tarda mas en leerlos).
    fallback: en Plus, si Gemini no contesta, probar el modelo local. Los
              llamadores secundarios (clasificador, resumen, tarjeta) lo
              apagan: tienen plantillas y no vale la pena esperar 15 s mas.
    """
    import brain

    loc_system, loc_text = local or (system_prompt, text)
    if is_local():
        return chat_local(cache_key, loc_system, loc_text, temperature, memory)

    global _gemini_down_until
    if gemini_down():
        # Gemini fallo hace poco: no se vuelven a pagar sus plazos en cada turno.
        if not fallback:
            return None
        _report("☁️✗ Gemini sigue caído: este turno lo contesta el modelo local")
        return chat_local(cache_key, loc_system, loc_text, temperature, memory)
    reply = brain.chat_gemini(cache_key, system_prompt, text, temperature, memory)
    if reply is not None:
        _gemini_down_until = 0.0
        _last.source = "gemini"
        return reply
    _gemini_down_until = time.monotonic() + GEMINI_COOLDOWN
    if not fallback:
        return None  # llamada secundaria: no toca last_source()
    _last.source = None
    _report(f"☁️✗ Gemini no respondió: este turno (y los de los próximos {GEMINI_COOLDOWN} s) los contesta el modelo local")
    return chat_local(cache_key, loc_system, loc_text, temperature, memory)
