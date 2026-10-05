# Prueba de la cara de Crispi SIN headset: sintetiza una frase real con el
# TTS, calcula los visemas (y los niveles de boca de respaldo) y los manda al
# sketch al ritmo del audio (no suena nada; solo se mueve la cara). Correr con
# la App andando:
#   docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/test_crispi_face.py
# Con --niveles manda mouth_level (0..4) en vez de visemas, como un sketch viejo.
import io, sys, time, wave
sys.path.insert(0, "/app/python")
import numpy as np
from arduino.app_utils import Bridge
import voice, gestures

text = "¡Uff, qué difícil! Pero mira: si probamos con otra pieza, seguro que funciona."
wav = voice._synthesize(text, voice._VOICES["crispi"])
with wave.open(io.BytesIO(wav.tobytes()), "rb") as w:
    sr = w.getframerate(); pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
chunk = 1024
if "--niveles" in sys.argv:
    track, send, rest = voice._mouth_levels(pcm, chunk), gestures.send_mouth, 0
else:
    track, send, rest = voice._viseme_track(text, pcm, chunk), gestures.send_viseme, gestures.VISEME_REST
print(f"audio {len(pcm)/sr:.2f}s a {sr} Hz, {len(track)} bloques")
print("boca:", "".join("0123456789X"[v] for v in track))

gesture = gestures.emotion_of(text)
gestures.send(gesture, "crispi")
time.sleep(0.3)
sent, t0 = None, time.monotonic()
for i, v in enumerate(track):
    if v != sent:
        send(v); sent = v
    time.sleep(max(0, t0 + (i + 1) * chunk / sr - time.monotonic()))
send(rest)
gestures.send(gestures.REST, "crispi")
print("listo")
