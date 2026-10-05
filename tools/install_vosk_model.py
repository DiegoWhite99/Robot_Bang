# Descarga el modelo pequeno de Vosk en español (vosk-model-small-es-0.42,
# ~40 MB) a models/vosk-es/ de la App. Lo usa la escucha activa (barge-in) de
# python/voice.py: mientras un guia habla, el robot escucha LOCALMENTE con una
# gramatica cerrada (nombres de los guias + "se me ocurrio algo", "espera"...),
# sin pagar el STT de Google.
#
# Idempotente: si el modelo ya esta (archivo .ok), no hace nada. Se descarga a
# una carpeta temporal y se renombra al final, asi una descarga cortada nunca
# deja un modelo a medias que Vosk intente cargar.
#
# voice.py lo importa y llama a ensure_model() en un hilo al arrancar; tambien
# se puede correr a mano:
#   docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/install_vosk_model.py

import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

MODEL_NAME = "vosk-model-small-es-0.42"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
# tools/install_vosk_model.py -> parent (tools/) -> parent (raiz de la App).
MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "vosk-es"
_MARKER = ".ok"


def model_ready(path=MODEL_DIR):
    return (Path(path) / _MARKER).exists() and (Path(path) / "am").is_dir()


def ensure_model(path=MODEL_DIR, url=MODEL_URL, log=print):
    """Deja el modelo en `path` y devuelve la ruta, o None si no se pudo."""
    path = Path(path)
    if model_ready(path):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="vosk-", dir=path.parent))
    try:
        zpath = tmp / "model.zip"
        log(f"Descargando el modelo Vosk de {url} ...")
        with urllib.request.urlopen(url, timeout=60) as resp, open(zpath, "wb") as out:
            shutil.copyfileobj(resp, out, 1 << 20)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp / "x")
        zpath.unlink()
        # El zip trae una carpeta raiz (vosk-model-small-es-0.42/): esa es el modelo.
        inner = [p for p in (tmp / "x").iterdir() if p.is_dir()]
        src = inner[0] if len(inner) == 1 else tmp / "x"
        if not (src / "am").is_dir():
            raise RuntimeError("el zip no trae un modelo Vosk (falta am/)")
        (src / _MARKER).write_text(MODEL_NAME)
        if path.exists():
            shutil.rmtree(path)
        src.rename(path)
        log(f"Modelo Vosk listo en {path}")
        return path
    except Exception as exc:
        log(f"No pude descargar el modelo Vosk: {exc}")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(0 if ensure_model() else 1)
