# Recorre un reto BANG completo por texto (sin microfono ni parlante), con
# el LLM real: sirve para revisar los prompts de bang.py sin hablarle al robot.
#   docker exec robot-bang-3-main-1 /app/.cache/.venv/bin/python /app/tools/test_bang_flow.py
import sys
import time

sys.path.insert(0, "/app/python")
import bang  # noqa: E402

TURNS = [
    ("robot", "mi reto es que los estudiantes del laboratorio no muestran sus proyectos a nadie"),
    (None, "pasa en la CUN, al final de cada semestre, los proyectos quedan guardados en un cajón"),
    (None, "me frustra porque hay proyectos muy buenos; ya probamos una feria pero casi nadie fue"),
    (None, "sí, esa pregunta está bien"),
    (None, "siguiente fase"),
    (None, "saca una tarjeta"),
    (None, "podríamos hacer una vitrina en el pasillo con los prototipos"),
    (None, "un canal de videos cortos donde cada estudiante muestra su proyecto en un minuto"),
    (None, "siguiente fase"),
    (None, "creo que la de los videos cortos"),
]

persona = None
for wake, text in TURNS:
    t0 = time.monotonic()
    if wake == "robot":
        persona = bang.classify(text)
        print(f"\n[clasificador] -> {persona}")
    r = bang.turn(persona, text)
    s = bang.session(persona)
    print(f"\nTÚ: {text}\n{persona.upper()} ({s.phase}, {time.monotonic() - t0:.1f}s{', CAMBIO DE FASE' if r.phase_changed else ''}): {r.reply}")
print("\nESTADO FINAL:", bang.session(persona).state())
