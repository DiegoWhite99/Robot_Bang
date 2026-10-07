# Descarga una voz de Piper a models/piper/ de la App. Es la voz del modo
# Essentials (python/localvoice.py): neuronal, gratis y sin internet una vez
# bajada.
#
# Va aparte del repositorio por lo mismo que el modelo de Vosk: son decenas o
# cientos de MB (models/ esta en .gitignore).
#
# Idempotente: si ya estan el .onnx y su .onnx.json, no hace nada. Descarga a
# un archivo temporal y renombra al final, asi una descarga cortada nunca deja
# un .onnx a medias que onnxruntime intente cargar.
#
# localvoice.py lo importa y llama a ensure_voice() la primera vez que hay que
# hablar; tambien se puede correr a mano:
#   docker exec robot-bang-stable-main-1 python3 /app/tools/install_piper_voice.py
#   python3 tools/install_piper_voice.py es_AR-daniela-high
#
# Las voces en español que hay, medidas en esta placa (ver localvoice.py; RTF
# = segundos de CPU por segundo de audio, por encima de 1 no sirve):
#   es_MX-claude-high      mujer, mexicana     RTF 0,77   <- la de fabrica
#   es_ES-sharvard-medium  mujer y hombre      RTF 0,67
#   es_MX-ald-medium       hombre, mexicano    RTF 0,66
#   es_ES-mls_9972-low     España              RTF 0,50
#   es_ES-mls_10246-low    España              RTF 0,56
#   es_AR-daniela-high     mujer, argentina    RTF 4,97   (suena muy bien, pero no da)
#   es_ES-davefx-medium / es_ES-carlfm-x_low / es_MX-ald-x_low

import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

VOICE_DIR = Path(__file__).resolve().parent.parent / "models" / "piper"
DEFAULT_VOICE = "es_MX-claude-high"

# El repositorio oficial de voces de Piper (Hugging Face). La ruta se arma con
# las partes del nombre: es_ES-sharvard-medium -> es/es_ES/sharvard/medium/...
_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _urls(name):
    """(url del .onnx, url del .onnx.json) de una voz 'es_ES-sharvard-medium'."""
    try:
        locale, resto = name.split("-", 1)
        dataset, quality = resto.rsplit("-", 1)
    except ValueError:
        raise ValueError(f"nombre de voz Piper mal formado: '{name}'")
    idioma = locale.split("_")[0]
    base = f"{_BASE}/{idioma}/{locale}/{dataset}/{quality}/{name}"
    return f"{base}.onnx?download=true", f"{base}.onnx.json?download=true"


def voice_ready(name=DEFAULT_VOICE, path=VOICE_DIR):
    path = Path(path)
    onnx = path / f"{name}.onnx"
    # Un .onnx de pocos KB es una descarga cortada o una pagina de error.
    return onnx.exists() and onnx.stat().st_size > 1_000_000 and (path / f"{name}.onnx.json").exists()


def _download(url, destino, log):
    tmp = Path(tempfile.mkstemp(prefix="piper-", dir=destino.parent)[1])
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out, 1 << 20)
        tmp.replace(destino)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def ensure_voice(name=DEFAULT_VOICE, path=VOICE_DIR, log=print):
    """Deja la voz en `path` y devuelve la ruta del .onnx, o None si no se pudo."""
    path = Path(path)
    if voice_ready(name, path):
        return path / f"{name}.onnx"
    path.mkdir(parents=True, exist_ok=True)
    try:
        url_onnx, url_json = _urls(name)
        log(f"Descargando la voz Piper '{name}' (puede tardar: son decenas de MB) ...")
        _download(url_json, path / f"{name}.onnx.json", log)
        _download(url_onnx, path / f"{name}.onnx", log)
        if not voice_ready(name, path):
            raise RuntimeError("la descarga quedo incompleta")
        log(f"Voz Piper lista en {path / (name + '.onnx')}")
        return path / f"{name}.onnx"
    except Exception as exc:
        log(f"No pude descargar la voz Piper '{name}': {exc}")
        (path / f"{name}.onnx").unlink(missing_ok=True)
        (path / f"{name}.onnx.json").unlink(missing_ok=True)
        return None


if __name__ == "__main__":
    nombre = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VOICE
    sys.exit(0 if ensure_voice(nombre) else 1)
