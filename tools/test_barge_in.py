# Pruebas de la escucha activa (barge-in) y del cambio de guia, sin hardware:
# - voice.find_switch(): "quiero hablar con Cori", "pásame a Cristal"... y
#   los casos que NO son cambio ("Cori dijo que quiero hablar con mi mamá");
# - voice._barge_trigger(): eco del propio robot, voz suelta del niño (kind
#   "speech", la interrupcion nativa de 1.1.0), cambio vs interrupcion;
# - bang.contribute() en cada fase, con y sin LLM (cerebro falso);
# - bang.handover(): el reto pasa entero al otro guia.
#
#   docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/test_barge_in.py
#
# Con --vosk ademas prueba el reconocedor REAL: sintetiza frases cortas con
# Google TTS (cuesta muy poco; se guardan en models/test_clips/ para no
# pagarlas otra vez), se las pasa a voice.BargeIn con un mic falso y mide el
# tiempo de CPU de Vosk por segundo de audio.
import hashlib
import io
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import bang  # noqa: E402
import brain  # noqa: E402
import llm_router  # noqa: E402
import voice  # noqa: E402

KEYS = tuple(brain.PERSONAS)
fallos = 0


def check(ok, label):
    global fallos
    fallos += not ok
    print(f"{'ok ' if ok else 'MAL'} {label}")


# --- Cambio de guia ------------------------------------------------------------

SWITCH = [
    ("quiero hablar con cori", ("cori", "")),
    ("Quiero hablar con Cori", ("cori", "")),
    ("pásame a Cristal", ("cristal", "")),
    ("pasame con krystal por favor", ("cristal", "")),
    ("ahora con Carmel porfa", ("carmel", "")),
    ("quiero hablar con Cori por favor", ("cori", "")),
    ("pásame a Cristal, gracias", ("cristal", "")),
    ("pásame con Cori, por favor, tengo una idea", ("cori", "tengo una idea")),
    ("Crispi quiero hablar con Cori", ("cori", "")),
    ("robot pásame con Cesia", ("cesia", "")),
    ("ahora con Cori, tengo una idea", ("cori", "tengo una idea")),
    ("cámbiame a crispi", ("crispi", "")),
    ("cambia me a Cris pi", ("crispi", "")),
    ("que hable Cesia", ("cesia", "")),
    ("llama a Cori", ("cori", "")),
    ("habla Carmel", ("carmel", "")),
    ("oye, quisiera hablar con la Cori sobre mi reto", ("cori", "sobre mi reto")),
    ("pásame con Sesia, se me ocurrió algo", ("cesia", "se me ocurrió algo")),
    ("Crispi, pásame a Cory", ("cori", "")),
    ("bueno ya, quiero hablar con Cristal", ("cristal", "")),
]
NO_SWITCH = [
    "Cori dijo que quiero hablar con mi mamá",
    "quiero hablar con mi profesor del colegio",
    "mi perro se llama Cori",
    "Cori, mi reto es que la gente recicle",
    "Carmel me ayudó con el reto",
    "quiero que mis amigos hablen más",
    "pásame la sal",
    "mi mamá habla con cristal",
    "ahora con cristal hacemos ventanas",
    "mi hermano habla con Cori todos los dias",
]

print("== find_switch ==")
for text, want in SWITCH:
    got = voice.find_switch(text, KEYS)
    check(got == want, f"{text!r} -> {got}")
for text in NO_SWITCH:
    got = voice.find_switch(text, KEYS)
    check(got is None, f"{text!r} -> {got} (no es cambio)")


# listen_turn() con un STT falso: el pedido de cambio tiene que ganarle a la
# ventana de seguimiento aunque el nombre llegue al final de la frase.
print("\n== listen_turn (STT falso) ==")
from types import SimpleNamespace as _NS  # noqa: E402


def _fake_stt(interims):
    def recognize(config=None, requests=None):
        for i, text in enumerate(interims):
            time.sleep(0.05)
            final = i == len(interims) - 1
            yield _NS(results=[_NS(alternatives=[_NS(transcript=text)], is_final=final)])
        time.sleep(1.0)  # Google tarda en cerrar
    return _NS(streaming_recognize=recognize)


# El modo se FIJA en Plus mientras dura este bloque. Desde 1.1.1 listen_turn()
# elige con que motor escucha segun el modo activo (Google en Plus, Vosk en
# Essentials), y aqui lo que se falsea es el de Google: si la placa se quedo
# en Essentials —algo normal, el modo se guarda en data/llm_mode.txt y
# sobrevive a los reinicios— el STT falso no se usaba y los diez casos daban
# (None, None). La prueba no puede depender de en que modo quedo el robot.
_modo_real = llm_router.mode()
llm_router._mode = "plus"
_real = (voice._speech, voice._microphone, voice._wanted_mic)
voice._microphone = lambda: None
voice._wanted_mic = lambda: voice._mic_device
LT = [
    (["quiero", "quiero hablar", "quiero hablar con", "quiero hablar con Cori"], "crispi", ("cori", ""), True),
    (["pásame", "pásame a Cristal", "pásame a Cristal, se me ocurrió un cohete"], "crispi", ("cristal", "se me ocurrió un cohete"), True),
    (["me gustaría", "me gustaría hablar con mi mamá"], "crispi", ("crispi", "me gustaría hablar con mi mamá"), False),
    (["Cori", "Cori, mi reto es reciclar"], None, ("cori", "mi reto es reciclar"), False),
    (["ahora con", "ahora con Carmel porfa"], None, ("carmel", ""), True),
    (["Crispi", "Crispi quiero", "Crispi quiero hablar con Cori"], None, ("cori", ""), True),
    (["Crispi", "Crispi quiero hablar con Cori"], "crispi", ("cori", ""), True),
    (["robot", "robot pásame con Cesia"], None, ("cesia", ""), True),
    # Fue pedido un momento ("ahora con cristal") y dejo de serlo: sin traspaso.
    (["ahora con", "ahora con cristal", "ahora con cristal hacemos ventanas"], "carmel", ("cristal", "hacemos ventanas"), False),
    (["mi hermano", "mi hermano habla con Cori todos los dias"], "carmel", ("carmel", "mi hermano habla con Cori todos los dias"), False),
]
try:
    for interims, follow, want, want_sw in LT:
        voice._speech = lambda interims=interims: _fake_stt(interims)
        got = voice.listen_turn(list(KEYS) + ["robot", "bang"], follow_up=follow, name_only=KEYS)
        check(got == want and voice.last_switch() == want_sw, f"{interims[-1]!r} (seguimiento: {follow}) -> {got} switch={voice.last_switch()}")
finally:
    voice._speech, voice._microphone, voice._wanted_mic = _real
    llm_router._mode = _modo_real


# --- Disparo de la escucha activa ------------------------------------------------

print("\n== _barge_trigger ==")


def trig(text, current="", speaker="cori", bt=False, previous=""):
    t = voice._barge_trigger(text, current, KEYS, bt, previous, speaker)
    return (t["kind"], t["persona"]) if t else None


CASES = [
    # (lo que oyo Vosk, frase que suena, guia que habla, bt, esperado)
    # 1. Palabras clave: ademas de callar al guia, dicen que hacer despues.
    ("cori se me ocurrió algo", "Pensemos en tu reto un momento.", "cori", False, ("interrupt", "cori")),
    ("se me ocurrió algo", "Pensemos en tu reto.", "cori", False, ("interrupt", "cori")),
    ("tengo una idea", "Cuéntame más.", "carmel", False, ("interrupt", "carmel")),
    ("quiero hablar con se sia", "Cuéntame más.", "cori", False, ("switch", "cesia")),
    ("pásame a cris pi", "Cuéntame más.", "cori", False, ("switch", "crispi")),
    ("carmel", "Cuéntame más.", "cori", False, ("switch", "carmel")),  # nombre de OTRO guia
    ("cori", "Cuéntame más.", "cori", False, ("interrupt", "cori")),
    ("[unk]", "Cuéntame más.", "cori", False, None),
    ("", "Cuéntame más.", "cori", False, None),
    # 2. Voz suelta: el niño habla de cualquier cosa y el guia se calla igual.
    ("mi colegio tiene mucha basura", "Cuéntame más.", "cori", False, ("speech", "cori")),
    ("hola podemos construir un cohete", "Cuéntame.", "cori", False, ("speech", "cori")),
    ("los árboles del parque", "Cuéntame.", "carmel", False, ("speech", "carmel")),
    # ... pero no con muletillas ni con una sola palabra nueva.
    ("eh mmm", "Cuéntame más.", "cori", False, None),
    # Ruido del mic: Vosk lo transcribe como la misma palabra una y otra vez
    # (visto en la placa al conectarse la bocina Bluetooth).
    ("tic tic tic tic tic tic", "Cuéntame más.", "cori", False, None),
    ("xix tic tic tic tic tic tic tic tic", "Cuéntame más.", "cori", False, None),
    ("casa casa casa casa", "Cuéntame más.", "cori", False, None),
    ("tic tic", "Cuéntame más.", "cori", False, None),  # 1 palabra distinta
    ("y la de", "Cuéntame más.", "cori", False, None),
    ("basura", "Cuéntame más.", "cori", False, None),
    # 3. Eco: lo que el robot esta diciendo no cuenta, ni como clave ni como voz.
    ("cori", "¡Hola! Soy Cori, la pensadora lateral.", "cori", False, None),
    ("espera", "Espera, eso me encanta.", "cori", False, None),
    ("un momento", "Dame un momento para pensarlo.", "crispi", False, None),
    ("carmel", "Carmel me contó tu reto.", "cori", False, None),
    ("pensemos en tu reto", "Pensemos en tu reto.", "cori", False, None),
    ("vamos afinando tu pregunta problema", "Vamos afinando tu pregunta problema.", "cesia", False, None),
    # Vosk oye "se me ocurrio" donde el guia dice "se te ocurre": eso es eco.
    ("se me ocurrió", "¿Qué otra idea se te ocurre?", "cori", False, None),
    ("cori se me ocurrió", "¿Qué otra idea se te ocurre?", "cori", False, ("interrupt", "cori")),
    ("espera", "¡Esperar vale la pena!", "cori", False, None),
    ("se me ocurrió algo", "A mí se me ocurrió algo parecido.", "cori", False, None),
    ("cori se me ocurrió algo", "Soy Cori.", "cori", False, ("interrupt", "cori")),  # el nombre es eco, la frase no
    # 4. Bluetooth: el mic oye al robot, asi que la voz suelta pide palabras
    # nuevas de mas. Las claves valen igual (la interrupcion es nativa en los dos).
    # En 1.1.1 el minimo con bocina paso de 3 a 4: TODOS los falsos disparos de
    # los logs (el robot callandose al oirse a si mismo) eran con bocina
    # Bluetooth. Por eso esta frase, que tiene 3 palabras nuevas, ya no dispara.
    ("mi colegio tiene basura", "Cuéntame más.", "cori", True, None),
    ("mi colegio tiene mucha basura", "Cuéntame más.", "cori", True, ("speech", "cori")),  # 4: si
    ("colegio sucio", "Cuéntame más.", "cori", True, None),  # 2 palabras nuevas: con BT no alcanza
    ("colegio sucio", "Cuéntame más.", "cori", False, ("speech", "cori")),  # con headset si
    ("mi colegio", "Cuéntame más.", "cori", False, None),  # "mi" es muletilla: queda 1 palabra nueva
    ("se me ocurrió algo", "Cuéntame más.", "cori", True, ("interrupt", "cori")),
    ("cori espera", "Cuéntame más.", "cori", True, ("interrupt", "cori")),
    ("quiero hablar con cristal", "Cuéntame más.", "cori", True, ("switch", "cristal")),
]
for text, current, speaker, bt, want in CASES:
    got = trig(text, current, speaker, bt)
    check(got == want, f"{speaker} dice {current!r} / oye {text!r} bt={bt} -> {got}")
check(trig("cori", "Cuéntame.", previous="Soy Cori.") is None, "eco de la frase ANTERIOR tambien cuenta")
# El parcial de Vosk se acumula frase tras frase: el eco se mira contra TODO
# lo dicho en la respuesta, no solo la frase anterior.
_b = voice.BargeIn("cori", KEYS)
for _s in ("Tengo una idea genial.", "Mira los árboles del parque.", "¿Qué podemos hacer hoy?"):
    _b.set_sentence(_s)
check(voice._barge_trigger("tengo una idea mira los árboles del parque", _b.current, KEYS, False, _b.previous, "cori") is None,
      "eco de la frase de hace 3 frases: no dispara")
_b = voice.BargeIn("cesia", KEYS)
for _s in ("¡Hola, soy Cesia!", "Crispi me contó tu reto.", "Ya llevas 2 ideas.", "¿Seguimos?"):
    _b.set_sentence(_s)
check(voice._barge_trigger("se sia", _b.current, KEYS, False, _b.previous, "cesia") is None, "su propio nombre dicho 3 frases atras: no dispara")
check(voice._barge_trigger("cori espera", _b.current, KEYS, False, _b.previous, "cesia") is not None, "con historia larga, lo nuevo si dispara")
t = voice._barge_trigger("oye", "", KEYS, False, speaker="cori")
check(t and t["kind"] == "interrupt" and not t["strong"], "'oye' solo es debil (solo vale en final)")
t = voice._barge_trigger("cori oye", "", KEYS, False, speaker="cori")
check(t and t["strong"], "'cori oye' es fuerte")
t = voice._barge_trigger("cori", "", KEYS, False, speaker="cori")
check(t and not t["strong"], "'cori' solo es debil (solo final con confianza)")
t = voice._barge_trigger("me se sia", "", KEYS, False, speaker="crispi")
check(t and t["kind"] == "switch" and not t["strong"], "nombre de otro guia solo: debil")
check(voice._barge_trigger("quiero hablar con se sia", "", KEYS, False, speaker="cori")["strong"], "'quiero hablar con cesia' es fuerte")
# Una clave en mitad de la frase ya no se ignora: sigue siendo el niño hablando.
check(trig("hola podemos construir un momento", "Cuéntame.") == ("speech", "cori"), "clave en mitad de otra cosa: vale como voz suelta")
check(trig("cori nos por el correr hasta la cocina", "Cuéntame.") == ("speech", "cori"), "nombre dentro de una frase larga: vale como voz suelta")
check(trig("quiero hablar con", "Cuéntame.") is None, "pedido a medias: espera el nombre, no corta como voz suelta")
check(trig("quiero hablar con se sia", "Cuéntame.") == ("switch", "cesia"), "... y con el nombre, cambia de guía")
check(trig("oye cori", "Cuéntame.") == ("interrupt", "cori"), "'oye cori' corto: si")
check(trig("bueno quiero hablar con cristal", "Cuéntame.") == ("switch", "cristal"), "una palabra antes del pedido: si")
res = [{"word": "me", "conf": 0.69}, {"word": "se", "conf": 0.78}, {"word": "sia", "conf": 0.73}]
check(abs(voice._trigger_conf(res, t, KEYS) - 0.73) < 1e-9, "confianza de 'se sia' = la peor de sus dos palabras")


# --- bang.contribute -------------------------------------------------------------

print("\n== bang.contribute ==")
calls = []


def fake_chat(cache_key, system, text, temperature=None, memory=True, local=None, fallback=True):
    calls.append(text)
    return "¡Qué buena! Ya quedó en tu reto: un mural que cuente historias. ¿Lo pintamos en el patio? ¿O adentro?"


real_chat, real_is_local = brain.chat, llm_router.is_local
brain.chat = fake_chat
try:
    for phase in bang.PHASES:
        for use_llm in (False, True):
            bang.reset("cori")
            s = bang._sessions["cori"] = bang.Session(persona="cori", phase=phase, reto="que mi barrio sea más alegre", ideas=["una fiesta"] if phase != "solida" else [])
            calls.clear()
            r = bang.contribute("cori", "Cori, se me ocurrió que hagamos un mural", "Pensemos en tu barrio. Imagina", use_llm=use_llm)
            ok = s.aportes and s.aportes[-1]["text"] == "hagamos un mural" and s.interrumpido.startswith("Pensemos")
            ok = ok and (("hagamos un mural" in s.ideas) == (phase == "gaseosa"))
            ok = ok and len(calls) == (1 if use_llm else 0)
            if use_llm:
                ok = ok and r.source == "gemini" and r.reply.count("?") <= 1 and "[Aportes" in calls[0] and "se corto" in calls[0]
            else:
                ok = ok and r.source == "plantilla" and r.reply.startswith("¡Listo, agregado! «hagamos un mural» ya queda en tu reto")
            check(ok, f"{phase:8s} use_llm={use_llm!s:5s} -> {r.reply!r}")
            check("hagamos un mural" in s.state()["aportes"] and s.state()["interrumpido"], f"{phase:8s} state() trae aportes e interrumpido")
    # Plus con Gemini caido: plantilla.
    brain.chat = lambda *a, **k: None
    bang._sessions["cori"] = s = bang.Session(persona="cori", phase="gaseosa", reto="x", ideas=[])
    r = bang.contribute("cori", "tengo una idea: un club de lectura", use_llm=True)
    check(r.source == "plantilla" and "idea número 1" in r.reply, f"Gemini caido -> plantilla: {r.reply!r}")
    # Essentials por defecto: sin LLM.
    brain.chat = fake_chat
    llm_router.is_local = lambda: True
    calls.clear()
    r = bang.contribute("cori", "y si lo hacemos en la noche", "")
    check(not calls and r.source == "plantilla", f"Essentials por defecto sin LLM: {r.reply!r}")
    # La nota de interrupcion vale un turno: turn() la borra.
    llm_router.is_local = real_is_local
    s.interrumpido = "algo que se corto"
    bang.turn("cori", "otra idea mas: un festival")
    check(s.interrumpido == "", "turn() borra la nota de 'te cortaron'")
    # Seguridad primero, tambien en un aporte.
    r = bang.contribute("cori", "mi teléfono es 3001234567", "", use_llm=False)
    check(r.source == "fijo", f"dato privado en un aporte -> respuesta fija: {r.reply!r}")
    # Tema delicado: recordatorio de adulto (una vez por reto) y sin repetir lo que dijo.
    for phase in ("solida", "gaseosa"):
        bang._sessions["cori"] = s = bang.Session(persona="cori", phase=phase, reto="que el recreo sea mejor", ideas=[])
        calls.clear()
        text = "mi problema es que me pegan en el recreo"
        aviso = bang.guardrails.aviso_adulto(text)
        r = bang.contribute("cori", text, "", use_llm=True)
        ok = aviso is not None and r.reply.startswith(aviso[1]) and "pegan" not in r.reply and not calls and len(s.aportes) == 1
        check(ok, f"{phase:8s} aporte delicado -> aviso de adulto, sin citarlo: {r.reply!r}")
        r = bang.contribute("cori", text, "", use_llm=False)
        check(aviso[1] not in r.reply and "pegan" not in r.reply, f"{phase:8s} el aviso va una sola vez por reto: {r.reply!r}")
    # Aportes vacios: no se guardan.
    for junk in ("no, nada", "eh, se me ocurrió algo", "Cori, se me ocurrió", "ya no, olvídalo"):
        bang._sessions["cori"] = s = bang.Session(persona="cori", phase="gaseosa", reto="x", ideas=["una"])
        calls.clear()
        r = bang.contribute("cori", junk, "Pensemos", use_llm=True)
        check(not s.aportes and s.ideas == ["una"] and not calls and r.reply.startswith("¡Vale!"), f"{junk!r} no es aporte -> {r.reply!r}")
finally:
    brain.chat, llm_router.is_local = real_chat, real_is_local


# --- bang.handover ---------------------------------------------------------------

print("\n== bang.handover ==")
for phase in bang.PHASES:
    for k in KEYS:
        bang.reset(k)
    old = bang._sessions["cesia"] = bang.Session(
        persona="cesia", phase=phase, reto="que en el recreo nadie se quede solo", pregunta="¿Cómo podríamos...?" if phase != "solida" else "",
        turns=2, ideas=["un buzón de amigos", "juegos mezclados"] if phase != "solida" else [],
        aportes=[{"text": "un mural", "phase": phase}], cards=bang._deal("cesia") if phase == "gaseosa" else [],
    )
    new = bang.handover("cesia", "cori")
    ok = new is not None and bang.session("cesia") is None and bang.session("cori") is new
    ok = ok and (new.phase, new.reto, new.pregunta, new.turns, new.ideas, new.aportes) == (old.phase, old.reto, old.pregunta, old.turns, old.ideas, old.aportes)
    ok = ok and new.persona == "cori" and new.relevo == "cesia"
    ok = ok and (all(c["image"].startswith("img/tarjetas/Cori/") for c in new.cards) and len(new.cards) == 3 if phase == "gaseosa" else not new.cards)
    greet = bang.handover_greeting("cori", new)
    ok = ok and greet.startswith("¡Hola, soy Cori! Cesia me contó tu reto")
    check(ok, f"{phase:8s} -> {greet!r}")
    check("[Nota: recibes este reto de Cesia" in bang._context(new), f"{phase:8s} el prompt sabe que viene de Cesia")
check(bang.handover("crispi", "cori") is None, "sin reto no hay nada que pasar")
check(bang.handover_greeting("carmel", None).startswith("¡Hola, soy Carmel, el estratega!"), "sin reto: saludo normal")


# --- Corte de la voz -----------------------------------------------------------------

print("\n== corte de la voz (parlante falso) ==")


class _FakePcm:
    dropped = False

    def drop(self):
        _FakePcm.dropped = True


class _FakeSpk:
    """Como el Speaker: play() bloquea lo que dura el bloque (1024 a 24 kHz)."""

    buffer_size = 1024
    sample_rate = 24000

    def __init__(self):
        self._pcm = _FakePcm()

    def play(self, chunk):
        time.sleep(len(chunk) / self.sample_rate)

    def stop(self):
        pass


stop = threading.Event()
pcm = np.zeros(24000 * 3, dtype=np.int16)  # 3 s de "voz"
threading.Timer(0.5, stop.set).start()
t0 = time.monotonic()
done = voice._play_synced(_FakeSpk(), pcm, lambda i: None, stop=stop)
dt = time.monotonic() - t0
check(done is False and _FakePcm.dropped and dt < 0.5 + 0.1, f"se corta {dt - 0.5:.3f}s despues del disparo (buffer vaciado: {_FakePcm.dropped})")
check(voice._play_synced(_FakeSpk(), pcm[:4096], lambda i: None, stop=threading.Event()) is True, "sin disparo suena entera")


# --- Vosk real (opcional) ----------------------------------------------------------


def _clip(text, persona, cache):
    """PCM int16 a 16 kHz de `text` dicho con la voz de `persona` (Google TTS, en cache)."""
    path = cache / f"{persona}_{hashlib.md5(text.encode()).hexdigest()[:10]}.wav"
    if not path.exists():
        path.write_bytes(voice._synthesize(text, voice._VOICES[persona]).tobytes())
    with wave.open(io.BytesIO(path.read_bytes())) as w:
        rate, pcm = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
    n = int(len(pcm) * voice._SAMPLE_RATE / rate)
    return np.interp(np.arange(n) * rate / voice._SAMPLE_RATE, np.arange(len(pcm)), pcm).astype(np.int16)


class _FakeMic:
    """Entrega el clip de a bloques de 1024 (64 ms) y luego 2.5 s de silencio.

    EN TIEMPO REAL (un sleep por bloque): la compuerta de energia de BargeIn
    mide ventanas de reloj (_BARGE_VOICED_WINDOW), asi que entregar el audio
    mas rapido que la vida real cambia el resultado y la prueba se vuelve
    irrepetible.
    """

    def __init__(self, pcm):
        # 2.5 s: lo que Vosk necesita de silencio para cerrar la frase y dar un
        # resultado FINAL. Con menos, lo "debil" (un nombre o un "¡espera!"
        # sueltos, que solo valen en final) no llegaba a dispararse nunca.
        pad = np.zeros(int(2.5 * voice._SAMPLE_RATE), dtype=np.int16)
        self.pcm = np.concatenate([np.zeros(4096, dtype=np.int16), pcm, pad])
        self.pos = 0

    def capture(self):
        if self.pos >= len(self.pcm):
            raise EOFError("fin del clip")
        chunk = self.pcm[self.pos : self.pos + 1024]
        self.pos += 1024
        time.sleep(len(chunk) / voice._SAMPLE_RATE)
        return chunk


def vosk_test():
    print("\n== Vosk real ==")
    t0 = time.monotonic()
    while voice._vosk_rec is None and voice._vosk_state == "cargando" and time.monotonic() - t0 < 120:
        time.sleep(0.2)
    print(f"estado de Vosk: {voice._vosk_state} ({time.monotonic() - t0:.1f}s esperando)")
    if voice._vosk_rec is None:
        check(False, "Vosk no cargo")
        return
    cache = Path(__file__).resolve().parent.parent / "models" / "test_clips"
    cache.mkdir(parents=True, exist_ok=True)
    # (texto, voz del clip, guia que habla, frase que suena, esperado)
    clips = [
        ("Cori, se me ocurrió algo", "cristal", "cori", "Pensemos en tu reto.", ("interrupt", "cori")),
        ("Quiero hablar con Cesia", "crispi", "cori", "Pensemos en tu reto.", ("switch", "cesia")),
        ("Pásame a Cristal", "cesia", "carmel", "Pensemos en tu reto.", ("switch", "cristal")),
        ("Crispi, tengo una idea", "cori", "crispi", "Pensemos en tu reto.", ("interrupt", "crispi")),
        ("Carmel, espera", "cesia", "carmel", "Pensemos en tu reto.", ("interrupt", "carmel")),
        ("Ahora con Carmel", "cristal", "cori", "Pensemos en tu reto.", ("switch", "carmel")),
        ("Oye Cristal, un momento", "crispi", "cristal", "Pensemos en tu reto.", ("interrupt", "cristal")),
        ("Un momento, se me ocurrió algo", "cori", "carmel", "Pensemos en tu reto.", ("interrupt", "carmel")),
        # Un nombre a secas: Vosk con reconocimiento libre lo puede entender
        # mal ("¡Cori!" -> "amen gloria"). Lo que se exige es que el guia SE
        # CALLE ("corta"); afinar quien habla despues es cosa del STT de Google.
        ("¡Cori!", "crispi", "cori", "Pensemos en tu reto.", "corta"),
        ("¡Carmel!", "cristal", "cori", "Pensemos en tu reto.", ("switch", "carmel")),
        ("¡Espera!", "cesia", "crispi", "Pensemos en tu reto.", ("interrupt", "crispi")),
        # Voz suelta: habla normal de un niño, sin frase clave. Antes no
        # disparaba nada; desde 1.1.0 el guia se calla igual (kind "speech").
        ("Hoy fuimos al parque y jugamos fútbol con mis amigos del colegio", "cristal", "cori", "Pensemos en tu reto.", ("speech", "cori")),
        ("Me gustaría que en mi barrio hubiera más árboles y menos basura", "crispi", "cori", "Pensemos en tu reto.", ("speech", "cori")),
        ("Quisiera inventar una máquina que riegue las macetas automáticamente", "cesia", "cori", "Pensemos en tu reto.", ("speech", "cori")),
        ("El dinosaurio de mi primo tiene escamas verdes y come espinacas", "cristal", "carmel", "Pensemos en tu reto.", ("speech", "carmel")),
        ("Necesito terminar la tarea de matemáticas antes del viernes", "carmel", "crispi", "Pensemos en tu reto.", ("speech", "crispi")),
        ("Ojalá podamos construir un cohete con botellas recicladas", "cori", "cesia", "Pensemos en tu reto.", ("speech", "cesia")),
        ("Corrimos por el corredor hasta la cocina", "crispi", "cori", "Pensemos en tu reto.", ("speech", "cori")),
        # Eco: el guia diciendo su propia frase, que trae su nombre.
        ("¡Hola! Soy Cori, la pensadora lateral.", "cori", "cori", "¡Hola! Soy Cori, la pensadora lateral.", None),
        ("Espera, eso que dijiste me encanta. ¿Qué otra idea se te ocurre?", "cesia", "cesia", "Espera, eso que dijiste me encanta. ¿Qué otra idea se te ocurre?", None),
        ("Cesia me contó tu reto. Vamos afinando tu pregunta problema.", "cori", "cori", "Cesia me contó tu reto. Vamos afinando tu pregunta problema.", None),
    ]
    total_cpu = total_audio = 0.0
    for text, voz, speaker, current, want in clips:
        pcm = _clip(text, voz, cache)
        b = voice.BargeIn(speaker, KEYS)
        b.set_sentence(current)
        b.sentence_at -= 10  # sin la gracia de inicio de frase: el clip arranca ya
        t_start = time.monotonic()
        b._run(_FakeMic(pcm))
        got = (b.trigger["kind"], b.trigger["persona"]) if b.trigger else None
        if want == "corta":  # basta con que corte la voz, sea del tipo que sea
            want = got if got else ("algo", "algo")
        total_cpu += b.cpu_s
        total_audio += b.audio_s
        heard = b.trigger["text"] if b.trigger else "-"
        # Cuanto audio hizo falta para disparar (sin los 0.256 s de silencio inicial).
        at = f" · disparo a {b.audio_s - 0.256:.2f}s de un clip de {len(pcm) / voice._SAMPLE_RATE:.2f}s" if b.trigger else ""
        check(got == want, f"{text!r} ({voz}) mientras habla {speaker} -> {got} oyo {heard!r}{at} · CPU {b.cpu_s / max(b.audio_s, 1e-6):.3f} s/s · {time.monotonic() - t_start:.2f}s")
    print(f"CPU de Vosk: {total_cpu:.2f}s para {total_audio:.1f}s de audio = {total_cpu / total_audio:.3f} s de CPU por s de audio")


# --- main.py: cambio pedido a un guia bloqueado -------------------------------------


def main_locked_test():
    """Cori habla, le dicen "pasame con Cesia" y Cesia esta bloqueada: contesta
    CORI (no Crispi) y el reto sigue con ella. main.py se importa con la WebUI,
    App.run, la voz y los gestos falsos (no toca data/ ni el parlante)."""
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
    real_run, au.App.run = au.App.run, lambda *a, **k: None
    import gestures
    import guides

    saved = {
        (brain, "warmup"): brain.warmup, (voice, "warmup"): voice.warmup, (voice, "say"): voice.say,
        (voice, "say_cached"): voice.say_cached, (voice, "make_barge"): voice.make_barge, (voice, "ack"): voice.ack,
        (guides, "is_unlocked"): guides.is_unlocked, (guides, "unlocked"): guides.unlocked,
        (threading, "Thread"): threading.Thread,
    }
    saved.update({(gestures, n): getattr(gestures, n) for n in ("send", "send_card", "send_menu")})
    real_thread = threading.Thread

    class QuietThread(real_thread):
        def start(self):
            return None  # nada de wifi, bienvenida ni warmups en segundo plano

    log = []
    try:
        brain.warmup = voice.warmup = lambda *a, **k: None
        threading.Thread = QuietThread
        import main
        threading.Thread = real_thread
        for n in ("send", "send_card", "send_menu"):
            setattr(gestures, n, lambda *a, n=n, **k: log.append(("gesto", n) + a))
        guides.is_unlocked = lambda k: k != "cesia"
        guides.unlocked = lambda: [k for k in KEYS if k != "cesia"]
        says = [{"kind": "switch", "persona": "cesia", "text": "pasame con se sia"}]

        def fake_say(persona, text, barge=None):
            log.append(("say", persona, text))
            if says and barge is not None:
                return {"interrupted": True, "spoken": text[:20], "trigger": says.pop(0)}
            return {"interrupted": False, "spoken": text, "trigger": None}

        voice.say, voice.say_cached, voice.ack = fake_say, lambda *a: log.append(("cached",) + a), lambda: None
        voice.make_barge = lambda p, keys: object()
        for k in KEYS:
            bang.reset(k)
        bang._sessions["cori"] = bang.Session(persona="cori", phase="solida", reto="que el parque esté limpio")
        main._current_persona, main._follow_up = "cori", None
        main._speak("cori", "Cuéntame más de tu parque. ¿Qué pasa ahí?")
        spoken = [e for e in log if e[0] == "say"]
        last = spoken[-1]
        ok = last[1] == "cori" and "Cesia todavía está bloqueada" in last[2] and "Crispi" not in last[2]
        ok = ok and ("gesto", "send", gestures.REST, "cori") == [e for e in log if e[0] == "gesto"][-1]
        ok = ok and main._follow_up == "cori" and main._current_persona == "cori" and bang.has_reto("cori")
        check(ok, f"barge 'pasame con Cesia' (bloqueada) -> lo dice Cori: {last[2]!r}")
    finally:
        threading.Thread = real_thread
        for (mod, name), val in saved.items():
            setattr(mod, name, val)
        au.App.run = real_run
        if real_web is not None:
            sys.modules["arduino.app_bricks.web_ui"] = real_web


def main_barge_kinds_test():
    """La interrupcion nativa vista desde main.py:
    - kind "speech" (el niño simplemente habla): el guia se calla y NO dice
      nada encima; lo que oyo Vosk queda guardado por si el STT no lo capta;
    - kind "interrupt" (una clave corta: "¡Cori!", "espera"): ahi si contesta
      "¡Dime!", porque el niño esta esperando turno.
    """
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
    real_run, au.App.run = au.App.run, lambda *a, **k: None
    import gestures

    saved = {(brain, "warmup"): brain.warmup, (voice, "warmup"): voice.warmup, (voice, "say"): voice.say,
             (voice, "say_cached"): voice.say_cached, (voice, "make_barge"): voice.make_barge}
    saved.update({(gestures, n): getattr(gestures, n) for n in ("send", "send_card", "send_menu")})
    real_thread = threading.Thread

    class QuietThread(real_thread):
        def start(self):
            return None

    log = []
    try:
        brain.warmup = voice.warmup = lambda *a, **k: None
        threading.Thread = QuietThread
        import main
        threading.Thread = real_thread
        for n in ("send", "send_card", "send_menu"):
            setattr(gestures, n, lambda *a, n=n, **k: log.append(("gesto", n) + a))
        voice.say_cached = lambda *a: log.append(("cached",) + a)
        voice.make_barge = lambda p, keys: object()

        for kind, oido, dice_dime in (("speech", "mi colegio tiene basura", False), ("interrupt", "", True)):
            log.clear()
            main._awaiting_aporte = main._follow_up = None
            res = {"interrupted": True, "spoken": "Cuéntame más de tu reto.",
                   "trigger": {"kind": kind, "persona": "cori", "text": oido or "cori espera"}}
            main._on_barge("cori", res, 0)
            dijo = [e for e in log if e[0] == "cached"]
            esperado = main._awaiting_aporte or {}
            ok = bool(dijo) == dice_dime and main._follow_up == "cori" and esperado.get("persona") == "cori"
            ok = ok and esperado.get("oido") == (oido if kind == "speech" else "")
            check(ok, f"interrupcion '{kind}': {'dice ¡Dime!' if dijo else 'se calla y escucha'}, "
                      f"guarda lo oído = {esperado.get('oido')!r}")
    finally:
        threading.Thread = real_thread
        for (mod, name), val in saved.items():
            setattr(mod, name, val)
        au.App.run = real_run
        if real_web is not None:
            sys.modules["arduino.app_bricks.web_ui"] = real_web


print("\n== main.py: guia bloqueado ==")
main_locked_test()

print("\n== main.py: tipos de interrupción ==")
main_barge_kinds_test()

if "--vosk" in sys.argv:
    vosk_test()

print("\ntodo bien" if not fallos else f"\n{fallos} fallos")
raise SystemExit(1 if fallos else 0)
