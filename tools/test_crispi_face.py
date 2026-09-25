# Prueba de la cara de Crispi SIN headset: sintetiza una frase real con el
# TTS, calcula los niveles de boca y los manda al sketch al ritmo del audio
# (no suena nada; solo se mueve la cara). Correr con la App andando:
#   docker exec robot-bang-3-main-1 /app/.cache/.venv/bin/python /app/tools/test_crispi_face.py
import io, sys, time, wave
sys.path.insert(0, "/app/python")
import numpy as np
from arduino.app_utils import Bridge
import voice, gestures

text = "Hola, soy Crispi! Arranca ya con tu prototipo y lo ajustamos despues."
wav = voice._synthesize(text, voice._VOICES["crispi"])
with wave.open(io.BytesIO(wav.tobytes()), "rb") as w:
    sr = w.getframerate(); pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
chunk = 1024
levels = voice._mouth_levels(pcm, chunk)
print(f"audio {len(pcm)/sr:.2f}s a {sr} Hz, {len(levels)} bloques")
print("niveles:", "".join(str(l) for l in levels))

gestures.send(gestures.HAPPY, "crispi")
time.sleep(0.3)
sent, t0 = None, time.monotonic()
for i, lvl in enumerate(levels):
    if lvl != sent:
        gestures.send_mouth(lvl); sent = lvl
    time.sleep(max(0, t0 + (i + 1) * chunk / sr - time.monotonic()))
gestures.send_mouth(0)
gestures.send(gestures.REST, "crispi")
print("listo")
