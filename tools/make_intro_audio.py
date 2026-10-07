# Graba las locuciones FIJAS del arranque como MP3 en assets/audio/.
#
# El arranque dice siempre lo mismo (que es BANG, que lo hizo la CUN, el menu
# PLUS/ESSENTIAL, la presentacion de los guias...). Sintetizarlo en vivo en
# cada encendido cuesta red, cuota y ~2 s de espera, y en Essentials ni
# siquiera habia red: lo leia espeak-ng y sonaba a robot de los 90.
#
# Grabado una vez queda SIEMPRE igual y SIEMPRE bien, en los dos modos: el MP3
# se decodifica en la placa (song._decode(), ffmpeg del wheel) y suena sin
# tocar internet. Ver voice.say_clip() y main._boot_sequence().
#
# La voz es la de la presentadora del BANG (voice._VOICES["bang"],
# es-US-Chirp3-HD-Zephyr), la misma que ya daba la bienvenida.
#
# Uso (hace falta red y google-credentials.json):
#     python3 tools/make_intro_audio.py              # solo los que falten
#     python3 tools/make_intro_audio.py --forzar     # rehace todos
#     python3 tools/make_intro_audio.py intro_bang   # solo uno

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_AUDIO = _ROOT / "assets" / "audio"
_CREDS = _ROOT / "google-credentials.json"

VOZ = "es-US-Chirp3-HD-Zephyr"  # la presentadora, = voice._VOICES["bang"]
IDIOMA = "es-US"

# Los TEXTOS no viven aqui: viven en python/intro.py, que es lo que lee tambien
# la App (para la pantalla, para la boca y como respaldo si falta el MP3).
# Tener dos copias era garantizar que un dia el robot dijera una cosa y el
# dashboard mostrara otra.
sys.path.insert(0, str(_ROOT / "python"))
import intro  # noqa: E402

CLIPS = intro.CLIPS


def main():
    from google.cloud import texttospeech
    from google.oauth2 import service_account

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    forzar = "--forzar" in sys.argv
    pedidos = args or list(CLIPS)
    desconocidos = [k for k in pedidos if k not in CLIPS]
    if desconocidos:
        sys.exit(f"No conozco {desconocidos}. Hay: {', '.join(CLIPS)}")

    _AUDIO.mkdir(parents=True, exist_ok=True)
    cliente = texttospeech.TextToSpeechClient(
        credentials=service_account.Credentials.from_service_account_file(str(_CREDS))
    )
    for key in pedidos:
        destino = _AUDIO / f"{key}.mp3"
        if destino.exists() and not forzar:
            print(f"  = {destino.name} (ya está; --forzar lo rehace)")
            continue
        res = cliente.synthesize_speech(
            input=texttospeech.SynthesisInput(text=CLIPS[key]),
            voice=texttospeech.VoiceSelectionParams(language_code=IDIOMA, name=VOZ),
            audio_config=texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3),
        )
        destino.write_bytes(res.audio_content)
        print(f"  ✓ {destino.name} ({len(res.audio_content)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
