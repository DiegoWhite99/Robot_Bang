# Pruebas del modo CURIOSO (python/curioso.py), sin hardware ni llamadas al LLM:
# - curioso.find_mode(): elegir modo por voz ("modo curioso", "bang" a secas)
#   y lo que NO debe cambiarlo ("qué curioso", "bang" como palabra de activacion);
# - ordenes cortas ("ponte feliz", "baila"): gesto correcto y sin pasar por el modelo;
# - guardarrailes: las respuestas fijas de seguridad valen igual, sin hablar de retos;
# - el turno normal: prompt del guia, limpieza de la salida y aviso de adulto;
# - curioso.slow_turn(): que sepa cuando va a esperar al modelo local.
#
#   docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/test_curioso.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import brain  # noqa: E402
import curioso  # noqa: E402
import gestures  # noqa: E402
import llm_router  # noqa: E402

fallos = 0


def check(ok, label):
    global fallos
    fallos += not ok
    print(f"{'ok ' if ok else 'MAL'} {label}")


# --- Elegir modo por voz -----------------------------------------------------

print("== find_mode ==")
CASES = [
    # (frase, bare, esperado)
    ("modo curioso", False, "curioso"),
    ("quiero el modo curioso", False, "curioso"),
    ("cambia a curioso", False, "curioso"),
    ("ponte en modo bang", False, "bang"),
    ("modo bang", False, "bang"),
    ("vamos al modo edu", False, "curioso"),
    ("quiero hacer un reto", False, "bang"),
    ("cambia de bang a curioso", False, "curioso"),
    # Sin pedido explicito no se cambia nada: "bang" es palabra de activacion y
    # "curioso" es una palabra normal.
    ("bang cuéntame un cuento", False, None),
    ("qué curioso lo que dices", False, None),
    ("mi gato es muy curioso", False, None),
    ("hola", False, None),
    ("", False, None),
    # Justo cuando se le pregunta, la palabra sola basta.
    ("curioso", True, "curioso"),
    ("bang", True, "bang"),
    ("el curioso", True, "curioso"),
    ("quiero bang", True, "bang"),
    ("no sé", True, None),
]
for text, bare, want in CASES:
    got = curioso.find_mode(text, bare=bare)
    check(got == want, f"{text!r} bare={bare} -> {got}")


# --- Ordenes cortas ----------------------------------------------------------

print("\n== órdenes ==")


def no_llm(*a, **k):
    raise AssertionError("una orden corta NO debe llamar al modelo")


_chat_real = llm_router.chat
llm_router.chat = no_llm

ORDENES = [
    ("ponte feliz", gestures.HAPPY, False),
    ("cori ponte muy contenta", gestures.HAPPY, False),
    ("sonríe", gestures.HAPPY, False),
    ("ponte triste", gestures.SAD, False),
    ("ponte enojado", gestures.ANGRY, False),
    ("sorpréndete", gestures.SURPRISE, False),
    ("ponte normal", gestures.REST, False),
    ("descansa", gestures.REST, False),
    ("baila", gestures.HAPPY, True),
    ("canta una canción", gestures.HAPPY, True),
    ("celebra", gestures.HAPPY, True),
    # Gestos de brazos agregados en 1.1.0
    ("salúdame", gestures.WAVE, False),
    ("di hola", gestures.WAVE, False),
    ("aplaude", gestures.CLAP, False),
    ("dame un aplauso", gestures.CLAP, False),
    ("ponte a pensar", gestures.THINK, False),
    ("di que sí", gestures.YES, False),
    ("di que no", gestures.NO, False),
    ("muévete", gestures.DANCE, False),
    ("mueve los brazos", gestures.DANCE, False),
    ("abrázame", gestures.HUG, False),
    ("dame un abrazo", gestures.HUG, False),
    ("duérmete", gestures.SLEEP, False),
    ("estírate", gestures.STRETCH, False),
    ("haz un bostezo", gestures.STRETCH, False),
    # La orden también vale tras el nombre del guía o una cortesía.
    ("cori, salúdame", gestures.WAVE, False),
    ("oye cori, por favor aplaude", gestures.CLAP, False),
]
for text, gesto, celebra in ORDENES:
    r = curioso.turn("cori", text)
    ok = r.gesture == gesto and r.celebrate == celebra and r.source == "plantilla" and r.reply
    check(ok, f"{text!r} -> gesto={r.gesture} celebra={r.celebrate} · {r.reply!r}")

# El genero del guia se respeta en las frases de las ordenes.
check("enojada" in curioso.turn("cesia", "ponte enojada").reply, "guía mujer: 'enojada'")
check("enojado" in curioso.turn("carmel", "ponte enojado").reply, "guía hombre: 'enojado'")

# Una orden nueva no le roba el turno a una pregunta normal (se mira el
# detector a secas: un turno de verdad iría al modelo, que aquí está apagado).
for texto in ("¿por qué no puedo dormir cuando hay ruido?", "¿los robots piensan?",
              "mi perro se mueve mucho", "¿por qué la gente aplaude en los conciertos?",
              "ayer me abrazó mi abuela"):
    r = curioso._orden("cori", curioso._plain(texto))
    check(r is None, f"no es una orden: {texto!r} -> {r and r.gesture}")

# Preguntas de siempre: tampoco cuestan modelo.
for text, pista in (("¿cómo te llamas?", "Cori"), ("¿qué puedes hacer?", "ponte feliz"), ("¿en qué modo estamos?", "Curioso")):
    r = curioso.turn("cori", text)
    check(pista.lower() in r.reply.lower() and r.source == "plantilla", f"{text!r} -> {r.reply!r}")

# Seguridad: manda antes que todo, y sin hablar de retos.
r = curioso.turn("cori", "me duele la barriga y me siento mal")
check(r.source == "fijo" and "adulta" in r.reply, f"malestar -> {r.reply!r}")
r = curioso.turn("cori", "¿eres una persona de verdad?")
check(r.source == "fijo" and "reto" not in r.reply.lower(), f"identidad, sin hablar de retos -> {r.reply!r}")
r = curioso.turn("cori", "mi teléfono es 3001234567")
check(r.source == "fijo" and "privados" in r.reply, f"dato privado -> {r.reply!r}")

llm_router.chat = _chat_real


# --- Turno normal (cerebro falso) --------------------------------------------

print("\n== turno con el modelo ==")
llamadas = []


def fake_chat(cache_key, system, text, temperature=None, memory=True, local=None, fallback=True):
    llamadas.append({"cache_key": cache_key, "system": system, "text": text, "local": local})
    llm_router._last.source = "gemini"
    return "**¡Claro!** Los volcanes son montañas que echan lava 🌋. ¿Quieres saber más?"


llm_router.chat = fake_chat
r = curioso.turn("cori", "¿por qué hacen erupción los volcanes?")
check(r.source == "gemini", f"la respuesta viene del cerebro: {r.source}")
check("**" not in r.reply and "🌋" not in r.reply, f"salida limpia (sin markdown ni emojis): {r.reply!r}")
check(llamadas[-1]["cache_key"] == ("cori", "curioso"), "memoria propia del modo Curioso")
check("Cori" in llamadas[-1]["system"] and "no estas guiando" in llamadas[-1]["system"].lower(),
      "el prompt lleva la personalidad del guía y le dice que no guíe la metodología")
check(llamadas[-1]["local"] and len(llamadas[-1]["local"][0]) < len(llamadas[-1]["system"]),
      "el prompt del modelo local es más corto")
r = curioso.turn("cori", "mi reto es que en el colegio me pegan")
check("adulta" in r.reply and "volcanes" in r.reply, f"tema delicado: aviso de adulto + respuesta: {r.reply!r}")


def sin_respuesta(*a, **k):
    return None


llm_router.chat = sin_respuesta
r = curioso.turn("cori", "cuéntame algo")
check(r.reply == brain.FALLBACK_REPLY, f"si nadie contesta, frase de reserva: {r.reply!r}")
llm_router.chat = _chat_real


# --- slow_turn y greeting ----------------------------------------------------

print("\n== slow_turn / greeting ==")
_is_local = llm_router.is_local
llm_router.is_local = lambda: False
check(curioso.slow_turn("cori", "¿por qué llueve?") is False, "en Plus nunca se espera al modelo local")
llm_router.is_local = lambda: True
check(curioso.slow_turn("cori", "¿por qué llueve?") is True, "en Essentials una pregunta sí espera al modelo local")
check(curioso.slow_turn("cori", "ponte feliz") is False, "una orden corta no espera a nadie")
check(curioso.slow_turn("cori", "me siento mal") is False, "una respuesta fija tampoco")
llm_router.is_local = _is_local

g = curioso.greeting("cori")
check("Cori" in g and "Curioso" in g, f"saludo del modo: {g!r}")

# --- main.py: la pregunta de modo del arranque -------------------------------


def main_elegir_modo_test():
    """_elegir_modo() corre en el ARRANQUE: si falla ahí, no arranca nada.

    Se prueba con la WebUI, App.run, la voz y los gestos falsos: elegir
    Curioso hablando, elegir un guía en vez del modo (no se pierde el turno)
    y no contestar nada (se queda el modo que había).
    """
    import threading
    import types

    try:
        import arduino.app_utils as au
    except Exception as exc:
        print(f"(sin arduino.app_utils: salto la prueba de main.py: {exc})")
        return
    fake_web = types.ModuleType("arduino.app_bricks.web_ui")

    class FakeUI:
        def __init__(self, **k):
            self.sent = []

        def send_message(self, name, data=None, sid=None):
            self.sent.append((name, data))

        def __getattr__(self, name):
            return lambda *a, **k: None

    fake_web.WebUI = FakeUI
    real_web = sys.modules.get("arduino.app_bricks.web_ui")
    sys.modules["arduino.app_bricks.web_ui"] = fake_web
    import voice

    real_run, au.App.run = au.App.run, lambda *a, **k: None
    saved = {(brain, "warmup"): brain.warmup, (voice, "warmup"): voice.warmup, (voice, "say"): voice.say,
             (voice, "make_barge"): voice.make_barge, (voice, "listen_turn"): voice.listen_turn,
             (curioso, "_MODE_PATH"): curioso._MODE_PATH}
    saved.update({(gestures, n): getattr(gestures, n) for n in ("send", "send_card", "send_menu")})
    real_thread = threading.Thread

    class QuietThread(real_thread):
        def start(self):
            return None

    dicho = []
    try:
        brain.warmup = voice.warmup = lambda *a, **k: None
        threading.Thread = QuietThread
        import main
        threading.Thread = real_thread
        for n in ("send", "send_card", "send_menu"):
            setattr(gestures, n, lambda *a, **k: None)
        # El modo NO se guarda en data/ durante la prueba.
        curioso._MODE_PATH = Path("/tmp/test_chat_mode.txt")
        voice.make_barge = lambda p, keys: None
        voice.say = lambda persona, text, barge=None: (dicho.append(text), {"interrupted": False, "spoken": text, "trigger": None})[1]

        def oye(respuestas):
            it = iter(respuestas)
            return lambda *a, **k: next(it, (None, None))

        curioso.set_mode("bang")

        # 1. Contesta "curioso" hablando (sin decir ningún nombre de guía).
        dicho.clear()
        voice.listen_turn = oye([("__modo__", "quiero el modo curioso")])
        salto = main._elegir_modo()
        check(salto is False and curioso.mode() == "curioso",
              f"dice 'quiero el modo curioso' -> modo {curioso.mode()}")
        check(any("BANG o Curioso" in t for t in dicho) and any("Curioso" in t for t in dicho[1:]),
              "pregunta en voz alta y confirma el modo elegido")

        # 2. Contesta con el nombre de un guía: se queda el modo y no se pierde el turno.
        curioso.set_mode("bang")
        main._pending_turn = None
        voice.listen_turn = oye([("cori", "mi reto es el recreo")])
        salto = main._elegir_modo()
        check(salto is True and curioso.mode() == "bang" and main._pending_turn == ("cori", "mi reto es el recreo"),
              f"dice 'Cori, mi reto...' -> modo {curioso.mode()}, turno guardado {main._pending_turn}")

        # 3. "bang" a secas (es palabra de activación: llega como wake sin texto).
        curioso.set_mode("curioso")
        voice.listen_turn = oye([("bang", "")])
        main._elegir_modo()
        check(curioso.mode() == "bang", f"dice 'bang' a secas -> modo {curioso.mode()}")

        # 4. No contesta nada: pregunta 2 veces y se queda el modo que había.
        curioso.set_mode("curioso")
        dicho.clear()
        voice.listen_turn = oye([])
        main._elegir_modo()
        check(curioso.mode() == "curioso" and len(dicho) == 3,
              f"sin respuesta: pregunta {len(dicho) - 1} veces y sigue en {curioso.mode()}")
    finally:
        threading.Thread = real_thread
        for (mod, name), val in saved.items():
            setattr(mod, name, val)
        au.App.run = real_run
        if real_web is not None:
            sys.modules["arduino.app_bricks.web_ui"] = real_web
        Path("/tmp/test_chat_mode.txt").unlink(missing_ok=True)


print("\n== main.py: elegir modo al arrancar ==")
main_elegir_modo_test()

print("\n" + ("todo bien" if not fallos else f"{fallos} fallos"))
sys.exit(1 if fallos else 0)
