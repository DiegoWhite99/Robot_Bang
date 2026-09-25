# Guias desbloqueados. Por ahora solo Crispi arranca activo; los otros 4 se
# desbloquean desde la terminal del dashboard (/unlock_carmel, /unlock_cesia,
# ...). El estado se guarda en data/unlocks.json (raiz de la App) para que
# sobreviva a un reinicio.

import json
import threading
from pathlib import Path

from arduino.app_utils import Logger

import brain

logger = Logger("chat-bang")

ALWAYS_UNLOCKED = "crispi"

_PATH = Path(__file__).resolve().parent.parent / "data" / "unlocks.json"
_lock = threading.Lock()


def _load():
    try:
        keys = json.loads(_PATH.read_text()).get("unlocked", [])
    except FileNotFoundError:
        keys = []
    except Exception as exc:
        logger.warning(f"No pude leer {_PATH} ({exc}): solo queda Crispi")
        keys = []
    return {k for k in keys if k in brain.PERSONAS} | {ALWAYS_UNLOCKED}


_unlocked = _load()


def _save():
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps({"unlocked": sorted(_unlocked)}))
    except Exception as exc:
        logger.warning(f"No pude guardar {_PATH}: {exc}")


def unlocked():
    """Claves desbloqueadas, en el orden de brain.PERSONAS."""
    with _lock:
        return [k for k in brain.PERSONAS if k in _unlocked]


def is_unlocked(key):
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
