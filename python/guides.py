# Guias desbloqueados. Por ahora solo Crispi arranca activo; los otros 4 se
# desbloquean desde la terminal del dashboard (/unlock_carmel, /unlock_cesia,
# ...). El estado se guarda en data/unlocks.json (raiz de la App) para que
# sobreviva a un reinicio.

import json
import threading
from pathlib import Path

from arduino.app_utils import Logger

import brain
import llm_router

logger = Logger("chat-bang")

ALWAYS_UNLOCKED = "crispi"

# El unico guia del modo ESSENTIALS.
#
# Essentials corre entero en la placa: Qwen de 0.8B para pensar, Vosk para oir
# y espeak-ng para hablar. Con cinco guias eso no da: cada uno tiene su propio
# system prompt, y llama.cpp solo reusa el prefijo en cache mientras el prompt
# no cambie — cambiar de guia tira la cache y el siguiente turno vuelve a
# costar los ~22 s de releer todo. Con uno solo el prefijo se queda caliente y
# el turno baja a unos 8 s, que es la diferencia entre conversar y esperar.
#
# Y es Cristal, no Crispi (el de Plus), por dos razones: es la musa reflexiva,
# que es la que mejor le sienta a un modelo pequeño —calmada, frases cortas,
# preguntas en vez de recetas— y es mujer, que es la voz que se pidio para el
# modo local (espeak-ng "es-419+f3", ver localvoice.py).
ESSENTIALS_GUIDE = "cristal"


def essentials_only():
    """True si ahora mismo hay un solo guia porque el modo es Essentials."""
    return llm_router.is_local()


def default_guide():
    """A quien va todo cuando no hay nadie mas: Cristal en Essentials, Crispi
    en Plus. Usar esto en vez de ALWAYS_UNLOCKED en cualquier respaldo, o en
    Essentials se acabaria llamando a un guia que no esta disponible."""
    return ESSENTIALS_GUIDE if essentials_only() else ALWAYS_UNLOCKED


_PATH = Path(__file__).resolve().parent.parent / "data" / "unlocks.json"
_lock = threading.Lock()


def _load():
    """Los 5 guias arrancan desbloqueados.

    Antes solo estaba Crispi y los demas se abrian con /unlock_<guia> desde la
    terminal. Eso no sirve para el menu por voz: el niño tiene que poder decir
    cualquiera de los cinco nombres y que le conteste. El bloqueo sigue
    existiendo para quien lo quiera usar (/lock_cesia), pero ya no es el
    estado de partida.
    """
    try:
        keys = json.loads(_PATH.read_text()).get("unlocked", [])
    except FileNotFoundError:
        keys = list(brain.PERSONAS)  # instalacion nueva: los 5 disponibles
    except Exception as exc:
        logger.warning(f"No pude leer {_PATH} ({exc}): dejo los 5 disponibles")
        keys = list(brain.PERSONAS)
    return {k for k in keys if k in brain.PERSONAS} | {ALWAYS_UNLOCKED}


_unlocked = _load()


def _save():
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps({"unlocked": sorted(_unlocked)}))
    except Exception as exc:
        logger.warning(f"No pude guardar {_PATH}: {exc}")


def unlocked():
    """Claves disponibles ahora mismo, en el orden de brain.PERSONAS.

    En Essentials es siempre una sola (ver ESSENTIALS_GUIDE); los candados de
    /lock_* y /unlock_* siguen ahi y vuelven a mandar al pasar a Plus.
    """
    if essentials_only():
        return [ESSENTIALS_GUIDE]
    with _lock:
        return [k for k in brain.PERSONAS if k in _unlocked]


def is_unlocked(key):
    if essentials_only():
        return key == ESSENTIALS_GUIDE
    with _lock:
        return key in _unlocked


def unlock(key):
    """Devuelve True si cambio algo."""
    with _lock:
        if key in _unlocked:
            return False
        _unlocked.add(key)
        _save()
        return True


def lock(key):
    """Crispi no se puede bloquear. Devuelve True si cambio algo."""
    with _lock:
        if key == ALWAYS_UNLOCKED or key not in _unlocked:
            return False
        _unlocked.discard(key)
        _save()
        return True
