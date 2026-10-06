# Pruebas del DIÁLOGO (versión 1.1.1), sin hardware, sin red y sin LLM.
#
# Esta es la parte crítica del robot: si el guía corta al niño a media idea,
# todo lo demás da igual. Se prueba en cuatro capas, de la más barata a la más
# cara:
#
#   1. endpoint_wait()      cuánto silencio hace falta para dar por terminada
#                           una frase. Función pura: decenas de casos al vuelo.
#   2. listen_turn()        la máquina de estados COMPLETA, con un guion de
#                           transcripciones y sus tiempos reales. Aquí es donde
#                           se ve si el robot deja terminar la idea.
#   3. barge-in             que el guía se calle cuando habla el niño y NO se
#                           calle cuando se oye a sí mismo.
#   4. voz local            espeak-ng (modo Essentials): que sintetice de
#                           verdad, en femenino y al sample rate del parlante.
#
#   docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/test_dialogo.py
#
# Con --rapido se salta la capa 2 (la que tarda, porque corre en tiempo real).
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import brain  # noqa: E402
import guides  # noqa: E402
import llm_router  # noqa: E402
import voice  # noqa: E402

KEYS = tuple(brain.PERSONAS)
RAPIDO = "--rapido" in sys.argv
fallos = 0
pruebas = 0


def check(ok, label):
    global fallos, pruebas
    pruebas += 1
    fallos += not ok
    print(f"{'ok ' if ok else 'MAL'} {label}")


# --- 1. Fin de frase: la decisión que arregla el corte a media idea -----------
#
# El caso que motivó todo: un niño de 8 años contando un problema hace pausas
# de 1,5-2 s en mitad de la frase. Con el 0,7 s fijo de antes, CUALQUIERA de
# esas pausas cerraba el turno. Lo que se mide aquí es que la espera sea larga
# justo cuando la frase quedó colgando, y corta cuando de verdad terminó.

print("== endpoint_wait: cuánto esperar antes de contestar ==")

COLGANDO = [
    # Frases que NO terminaron: el niño está a media idea.
    "mi reto es que",
    "en mi salón nadie recicla y",
    "yo creo que podríamos hacer un",
    "lo que pasa es que mi hermano siempre me",
    "quiero hacer algo para",
    "es como cuando uno está en el parque y de pronto",
    "la idea sería ponerle una",
    "porque",
    "entonces yo pensé que si",
    "me gustaría que el colegio",
    "sería bueno hacerlo con mi",
    "y después nosotros",
]
for frase in COLGANDO:
    espera = voice.endpoint_wait(frase)
    check(espera == voice._ENDPOINT_HANGING_S, f"espera larga ({espera}s) tras «{frase}»")

TERMINADAS = [
    # Frases cerradas y con cuerpo: contestarle rápido es lo correcto.
    "en mi salón nadie recicla la basura.",
    "mi reto es que se desperdicia mucha agua en el colegio.",
    "¿cómo podríamos hacer que todos reciclen?",
    "quiero hacer un buzón para el salón de clases!",
]
for frase in TERMINADAS:
    espera = voice.endpoint_wait(frase)
    check(espera == voice._ENDPOINT_DONE_S, f"espera corta ({espera}s) tras «{frase}»")

CORTAS = [
    # Una o dos palabras no son una idea: casi siempre viene más. Algunas caen
    # además en la regla de palabra colgante ("no sé" acaba en "se"), y esperar
    # todavía más tampoco está mal: lo que no puede es esperar MENOS.
    "hola",
    "mi reto",
    "no sé",
    "el parque",
]
for frase in CORTAS:
    espera = voice.endpoint_wait(frase)
    check(espera >= voice._ENDPOINT_SHORT_S, f"espera media o más ({espera}s) tras «{frase}»")

NORMALES = [
    # Con cuerpo pero sin puntuación de cierre: el plazo de siempre.
    "en mi salón nadie recicla la basura",
    "mi hermano me daña las cosas todo el tiempo",
    "quiero hacer un buzón para el salón",
]
for frase in NORMALES:
    espera = voice.endpoint_wait(frase)
    check(espera == voice._ENDPOINT_S, f"espera normal ({espera}s) tras «{frase}»")

# La espera larga tiene que ser de verdad larga: si no, no sirve de nada.
check(voice._ENDPOINT_HANGING_S >= 2.0, f"la espera de frase colgando es >= 2 s ({voice._ENDPOINT_HANGING_S})")
check(voice._ENDPOINT_S >= 1.0, f"la espera normal es >= 1 s ({voice._ENDPOINT_S})")
check(voice.endpoint_wait("") == voice._ENDPOINT_S, "sin texto, la espera normal")


# --- 2. listen_turn() entero, con un guion de tiempos ------------------------
#
# Un mic falso y un lector falso que va soltando transcripciones a los tiempos
# del guion. De ahí para abajo corre el código REAL: palabra clave, cambio de
# guía, acumulación de finales y cierre por silencio.

class MicFalso:
    """Lo único que listen_turn() le pide al mic es existir."""

    def capture(self):
        time.sleep(0.01)
        return None

    def stream(self):
        while True:
            time.sleep(0.01)
            yield None

    def stop(self):
        pass


def simular(guion, follow_up=None, name_only=KEYS, limite=25.0):
    """Corre listen_turn() contra un guion y devuelve (persona, texto, segundos).

    guion: [(t, texto, es_final), ...] con t en segundos desde que empieza el
    turno. En cada evento se marca que hubo voz en el mic (voice_at), que es
    lo que hace el robot de verdad.
    """
    original_stt, original_mic, original_deaf = voice.local_stt, voice._microphone, voice._is_deaf
    original_wanted, original_dev = voice._wanted_mic, voice._mic_device
    original_events = voice.localvoice.listen_events

    def replay(mic, stop, events, sample_rate=16000, voice_at=None, rms_gate=0):
        t0 = time.monotonic()
        for t, texto, final in guion:
            while time.monotonic() - t0 < t:
                if stop.is_set():
                    events.put(("end", None, None))
                    return
                time.sleep(0.01)
            if stop.is_set():
                break
            if voice_at is not None:
                voice_at[0] = time.monotonic()  # acaba de sonar su voz
            events.put(("text", texto, final))
        # El guion se acabó pero el niño sigue ahí callado: el stream sigue
        # abierto, como el de verdad. Quien cierra el turno es el silencio.
        while not stop.is_set() and time.monotonic() - t0 < limite:
            time.sleep(0.01)
        events.put(("end", None, None))

    voice.local_stt = lambda: True
    voice._microphone = lambda: MicFalso()
    voice._is_deaf = lambda: False
    voice._mic_device = "test"
    voice._wanted_mic = lambda: "test"
    voice.localvoice.listen_events = replay
    t0 = time.monotonic()
    try:
        persona, texto = voice.listen_turn(list(KEYS) + ["robot", "bang"], follow_up=follow_up, name_only=name_only)
    finally:
        voice.local_stt, voice._microphone, voice._is_deaf = original_stt, original_mic, original_deaf
        voice._wanted_mic, voice._mic_device = original_wanted, original_dev
        voice.localvoice.listen_events = original_events
    return persona, texto, time.monotonic() - t0


if not RAPIDO:
    print("\n== listen_turn: dejar terminar la idea (tiempo real, tarda un poco) ==")

    # EL CASO QUE ROMPÍA TODO. El niño habla despacio y hace una pausa en
    # mitad de la idea; Google cierra la frase ahí (is_final) porque oyó
    # silencio. Antes el turno se devolvía en ese mismo instante y el guía
    # contestaba a media frase. Ahora el final se guarda y se sigue oyendo.
    persona, texto, _ = simular([
        (0.0, "Cristal", False),
        (0.8, "Cristal mi reto es que en el salón", False),
        (1.6, "Cristal mi reto es que en el salón", True),   # Google cierra la frase
        (3.2, "nadie recicla la basura", False),             # ...pero el niño seguía
        (4.0, "nadie recicla la basura.", True),
    ])
    check(persona == "cristal", f"el guía es Cristal (salió {persona})")
    check("salón" in texto and "recicla" in texto, f"no se cortó la idea: «{texto}»")

    # Pausa larguísima en mitad de una frase colgando ("y..."): tampoco corta.
    persona, texto, _ = simular([
        (0.0, "Crispi en mi colegio no hay canecas y", False),
        (0.9, "Crispi en mi colegio no hay canecas y", True),
        (2.8, "y la basura se queda en el piso", False),
        (3.6, "y la basura se queda en el piso.", True),
    ])
    check(persona == "crispi" and "piso" in texto, f"aguanta la pausa tras «y»: «{texto}»")

    # Tres trozos seguidos, como habla de verdad un niño pensando en voz alta.
    persona, texto, _ = simular([
        (0.0, "Cori", False),
        (0.6, "Cori yo quería", False),
        (1.3, "Cori yo quería", True),
        (2.9, "hacer algo con mi hermana para", False),
        (3.7, "hacer algo con mi hermana para", True),
        (5.4, "que no peleemos tanto.", False),
        (6.1, "que no peleemos tanto.", True),
    ])
    check(persona == "cori", f"el guía es Cori (salió {persona})")
    check("hermana" in texto and "peleemos" in texto, f"junta los tres trozos: «{texto}»")

    # Y al revés: cuando la frase SÍ terminó, no se queda esperando de más.
    persona, texto, tardo = simular([
        (0.0, "Carmel quiero mejorar mi equipo de fútbol.", False),
        (0.5, "Carmel quiero mejorar mi equipo de fútbol.", True),
    ])
    check(persona == "carmel", f"el guía es Carmel (salió {persona})")
    check(tardo < 2.2, f"contesta rápido cuando la frase está cerrada ({tardo:.1f}s)")

    # Solo el nombre y nada más: el guía se presenta (texto vacío).
    persona, texto, _ = simular([(0.0, "Cesia", False), (0.4, "Cesia", True)])
    check(persona == "cesia" and texto == "", f"solo el nombre -> se presenta (texto={texto!r})")

    # Cambio de guía con lo que viene detrás.
    persona, texto, _ = simular([
        (0.0, "quiero hablar con Cristal", False),
        (0.6, "quiero hablar con Cristal se me ocurrió un mural", False),
        (1.4, "quiero hablar con Cristal se me ocurrió un mural.", True),
    ])
    check(persona == "cristal", f"pide cambio a Cristal (salió {persona})")
    check("mural" in texto, f"lo que venía detrás no se pierde: «{texto}»")

    # Respuesta de seguimiento, sin repetir el nombre, también con pausa.
    persona, texto, _ = simular([
        (0.0, "pues yo creo que", False),
        (0.7, "pues yo creo que", True),
        (2.4, "podríamos poner una caneca grande.", False),
        (3.1, "podríamos poner una caneca grande.", True),
    ], follow_up="cori")
    check(persona == "cori" and "caneca" in texto, f"seguimiento sin cortar: «{texto}»")

    # El tope duro existe: una frase que nunca para igual se contesta.
    guion = [(i * 0.6, " ".join(["y más cosas"] * (i + 1)), False) for i in range(60)]
    persona, texto, tardo = simular([(0.0, "Crispi mi reto es largo y", False)] + [(0.8 + t, "Crispi mi reto es largo y " + x, f) for t, x, f in guion], limite=45.0)
    check(tardo <= voice._ENDPOINT_MAX_S + 4, f"el tope duro corta a los {tardo:.0f}s (max {voice._ENDPOINT_MAX_S})")


# --- 3. Barge-in: callarse cuando habla el niño, NO cuando se oye a sí mismo --

print("\n== barge-in: el guía se calla solo cuando hay alguien hablando ==")

# Voz de verdad del niño: el guía se calla (kind "speech").
NIÑO = [
    ("mi colegio no tiene canecas", "cristal"),
    ("yo quiero hacer un mural grande", "crispi"),
    ("se me ocurrió poner música en el recreo", "cori"),
]
for frase, guia in NIÑO:
    trig = voice._barge_trigger(frase, "vamos a pensarlo con calma", KEYS, False, "", guia)
    check(trig is not None, f"se calla con «{frase}»")

# Ojo al contar: las muletillas ("mi", "no", "la", "que"...) NO cuentan como
# palabra nueva, así que "mi colegio" es UNA, no dos.
trig = voice._barge_trigger("mi colegio", "vamos a pensarlo con calma", KEYS, False, "", "cristal")
check(trig is None, "«mi colegio» es 1 palabra nueva («mi» es muletilla): no basta")
# Con headset USB bastan 2 palabras nuevas: el guía tiene que callarse rápido.
trig = voice._barge_trigger("colegio sucio", "vamos a pensarlo con calma", KEYS, False, "", "cristal")
check(trig is not None, "con 2 palabras nuevas y headset USB ya se calla")
# Con bocina Bluetooth el micrófono oye al robot (de ahí venían TODOS los
# falsos disparos de los logs), así que ahí se piden 4.
trig = voice._barge_trigger("colegio sucio", "vamos a pensarlo con calma", KEYS, True, "", "cristal")
check(trig is None, "con bocina Bluetooth 2 palabras no bastan (por ahí entraba el eco)")
trig = voice._barge_trigger("mi colegio tiene mucha basura", "vamos a pensarlo con calma", KEYS, True, "", "cristal")
check(trig is not None, "con bocina Bluetooth y 4 palabras nuevas sí se calla")

# El robot oyéndose a sí mismo: no puede callarse.
ECO = [
    ("que necesitas mejor seria conveniente", "¿Qué necesitas? Sería mejor que lo pensemos juntos"),
    ("vamos a pensarlo con calma", "vamos a pensarlo con calma"),
    ("cuentame mas de tu reto", "Cuéntame más de tu reto"),
]
for oido, sonando in ECO:
    trig = voice._barge_trigger(oido, sonando, KEYS, False, "", "cristal")
    check(trig is None, f"NO se calla con su propio eco: «{oido}»")

# Un parcial que cambia de palabras no vale; uno que crece, sí.
check(not voice._same_partial(("speech", "cori", frozenset({"mejor", "seria"})),
                              ("speech", "cori", frozenset({"necesitas", "conveniente"}))),
      "parciales con palabras distintas no disparan (Vosk tanteando el eco)")
check(voice._same_partial(("speech", "cori", frozenset({"mi", "colegio"})),
                          ("speech", "cori", frozenset({"mi", "colegio", "recicla"}))),
      "un parcial que crece sí dispara (voz de verdad)")
check(not voice._same_partial(None, ("speech", "cori", frozenset({"mi"}))), "el primer parcial nunca dispara solo")

# El nombre y "espera" siguen valiendo con una palabra: ahí el niño pide turno.
trig = voice._barge_trigger("cori", "vamos a pensarlo con calma", KEYS, False, "", "cori")
check(trig is not None and trig["kind"] == "interrupt", "su nombre solo sigue interrumpiendo")
trig = voice._barge_trigger("espera", "vamos a pensarlo con calma", KEYS, False, "", "cori")
check(trig is not None and trig["kind"] == "interrupt", "«espera» sigue interrumpiendo")

# Pedir otro guía mientras habla: eso es cambio, no interrupción.
trig = voice._barge_trigger("quiero hablar con cristal", "vamos a pensarlo", KEYS, False, "", "cori")
check(trig is not None and trig["kind"] == "switch" and trig["persona"] == "cristal", "«quiero hablar con Cristal» es cambio de guía")


# --- 3-bis. Interrumpir, recordar y seguir -----------------------------------
#
# Lo que se pidió: "cuando el usuario hable con el nombre del agente, se calla
# y escucha, almacena la memoria, prosigue con la memoria y con la nueva idea".
# El callarse es la capa 3. Aquí se prueba lo otro: que lo que el guía alcanzó
# a decir NO se pierda, y que el siguiente turno lo lleve puesto.

print("\n== interrumpir: el guía recuerda dónde iba ==")

import bang  # noqa: E402
import curioso  # noqa: E402

visto = {}


def cerebro_falso(cache_key, system, text, temperature=None, memory=True, local=None, fallback=True):
    """Guarda el prompt que le llegó al modelo, para poder mirarlo."""
    visto["system"], visto["text"] = system, text
    return "Perfecto, sigamos por ahí. ¿Qué más se te ocurre?"


# --- Modo BANG: la memoria vive en la sesión del reto ---
bang.reset("cristal")
brain_chat_antes, llm_chat_antes = brain.chat, llm_router.chat
brain.chat = cerebro_falso
try:
    # Un reto a medias, como lo tendría el robot en mitad de una sesión.
    s = bang.Session(persona="cristal", phase="gaseosa", reto="en mi salón nadie recicla")
    bang._sessions["cristal"] = s

    # El guía iba hablando y lo cortaron a media frase.
    bang.mark_interrupted("cristal", "Vamos a pensar entre las dos qué podría")
    check(s.interrumpido == "Vamos a pensar entre las dos qué podría", "se guarda lo que alcanzó a decir")

    # ...y el niño le dice su idea. Tiene que quedar en el reto Y llegarle al
    # modelo junto con lo que el guía venía diciendo.
    turno = bang.contribute("cristal", "se me ocurrió poner una caneca de colores", use_llm=True)
    check(any("caneca de colores" in a["text"] for a in s.aportes), "la idea nueva queda guardada en el reto")
    check("caneca de colores" in visto.get("text", ""), "la idea nueva le llega al modelo")
    check("interrump" in visto.get("system", "").lower(), "al modelo se le dice que lo interrumpieron")
    check(bool(turno.reply), "el guía contesta algo")

    # Y en el turno siguiente el contexto del reto sigue trayendo las dos cosas.
    ctx = bang._context(s)
    check("caneca de colores" in ctx, "el aporte sigue en el contexto del reto")
    check("Vamos a pensar" in ctx, "lo que decía antes sigue en el contexto del reto")
    check("No la repitas entera" in ctx, "se le pide que no repita lo ya dicho")
finally:
    brain.chat = brain_chat_antes
    bang.reset("cristal")

# --- Modo Curioso: no hay reto, así que la memoria se pasa a mano ---
visto.clear()
llm_router.chat = cerebro_falso
try:
    r = curioso.turn("cristal", "oye pero y los perros también reciclan",
                     interrumpido="Las canecas de colores sirven para separar")
    check("Las canecas de colores" in visto.get("text", ""), "en Curioso también se le recuerda dónde iba")
    check("perros" in visto.get("text", ""), "...y lo nuevo que dijo el niño va en el mismo turno")
    check(bool(r.reply), "el guía contesta en Curioso")

    # Sin interrupción, el prompt va limpio: la nota no se cuela siempre.
    visto.clear()
    curioso.turn("cristal", "cuántas patas tiene una araña")
    check("[Te interrumpieron" not in visto.get("text", ""), "sin interrupción no se mete la nota")
finally:
    llm_router.chat = llm_chat_antes


# --- 3-ter. Un solo lector del micrófono -------------------------------------
#
# El objeto Microphone entrega cada bloque UNA vez. Si dos hilos lo leen a la
# vez, cada uno se queda con la mitad y los dos transcriben audio con agujeros:
# es lo que se vio en la placa el 06/10 ("quiero que" -> "quiero checar", y
# luego palabras sueltas: "pero", "art", "la"). El candado lo impide.

print("\n== micrófono: un solo lector a la vez ==")

_orden_lectores = []
_a_dentro = threading.Event()


def _lector(nombre, espera):
    with voice._mic_reader(nombre):
        _orden_lectores.append(f"entra {nombre}")
        if nombre == "A":
            _a_dentro.set()
        time.sleep(espera)
        _orden_lectores.append(f"sale {nombre}")


ta = threading.Thread(target=_lector, args=("A", 0.4))
ta.start()
_a_dentro.wait(2.0)
tb = threading.Thread(target=_lector, args=("B", 0.05))
tb.start()
ta.join(5.0)
tb.join(5.0)
check(_orden_lectores == ["entra A", "sale A", "entra B", "sale B"],
      f"el segundo lector espera al primero: {_orden_lectores}")

# Y si el primero no suelta, el segundo no se queda colgado para siempre: avisa
# y sigue (mejor oír regular que quedarse sordo).
check(voice._MIC_READ_WAIT_S <= 3.0, f"la espera por el micrófono está acotada ({voice._MIC_READ_WAIT_S}s)")
check(not voice._mic_read_lock.locked(), "el candado del micrófono queda libre al terminar")


# --- 4. Modo Essentials: un solo guía y voz local ----------------------------

print("\n== Essentials: una sola guía (Cristal) y voz local ==")

modo_antes = llm_router.mode()
try:
    llm_router._mode = "essentials"  # sin tocar el archivo de data/
    check(guides.unlocked() == ["cristal"], f"en Essentials solo está Cristal (salió {guides.unlocked()})")
    check(guides.is_unlocked("cristal"), "Cristal está disponible")
    check(not guides.is_unlocked("crispi"), "Crispi no está disponible en Essentials")
    check(guides.default_guide() == "cristal", "el guía por defecto es Cristal")
    check(guides.essentials_only(), "essentials_only() es True")
    check(voice._voice_name("cristal") == "es-419+f3", f"la voz de Cristal es femenina local ({voice._voice_name('cristal')})")

    llm_router._mode = "plus"
    check(guides.default_guide() == "crispi", "en Plus el guía por defecto vuelve a ser Crispi")
    check(guides.is_unlocked("crispi"), "en Plus Crispi vuelve")
    check(voice._voice_name("cristal").startswith("es-US-Chirp3"), "en Plus la voz vuelve a ser la de Google")
finally:
    llm_router._mode = modo_antes

print("\n== voz local (espeak-ng) ==")
import localvoice  # noqa: E402

if not localvoice.available():
    check(False, f"espeak-ng disponible (estado: {localvoice.tts_state()})")
else:
    import wave as _wave
    import io as _io

    wav = localvoice.synthesize("Hola, soy Cristal. Vamos a pensar juntos tu reto.", localvoice.DEFAULT_VOICE, 24000)
    with _wave.open(_io.BytesIO(wav.tobytes()), "rb") as w:
        rate, canales, ancho, marcos = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    check(rate == 24000, f"sale al sample rate del parlante ({rate} Hz)")
    check(canales == 1 and ancho == 2, f"mono 16 bits ({canales} canal, {ancho*8} bits)")
    check(marcos > 24000, f"dura más de un segundo ({marcos/rate:.1f}s)")
    check(localvoice.DEFAULT_VOICE.endswith("+f3"), "la voz por defecto es femenina (+f3)")
    # Y que el camino de voice.py la use de verdad.
    wav2 = voice._synthesize("Prueba.", localvoice.DEFAULT_VOICE)
    check(len(wav2) > 1000, f"voice._synthesize() enruta a espeak-ng ({len(wav2)} bytes)")


# --- 4-bis. La canción "¡A despegar!" ----------------------------------------
#
# Lo pedido: que en LOS DOS MODOS el niño pueda pedir que cante, suene la
# canción de assets/audio/ y los brazos vayan con ella.

print("\n== la canción: se pide igual en BANG y en Curioso ==")

import song  # noqa: E402

check(song.available(), f"hay canción en assets/audio/ ({song.song_path()})")

# Las formas en que un niño pide de verdad la canción. "quiero que cantes" es
# LA forma más común y durante un rato no funcionó: el patrón pedía que todo lo
# anterior al verbo fuera cortesía, y "quiero que" no lo es. Además "cantes" no
# casaba con \bcante\b, porque la "s" rompe el límite de palabra.
PEDIDOS = ["canta", "cantes", "cantar", "cantemos",
           "quiero que cantes", "quiero que cantes una canción",
           "puedes cantar", "podrías cantar", "vamos a cantar",
           "me cantas una canción", "cántame una canción", "cántanos algo",
           "canta una canción", "pon música", "ponme la canción",
           "quiero una canción", "tócame esa canción", "a despegar"]
# Lo que NO es pedir la canción. El cuarto y el quinto son retos de verdad, y
# sin anclar el comando al principio de la frase el robot se ponía a cantar en
# vez de escuchar el reto del niño.
NO_PEDIDOS = ["me gusta esa canción", "el cantante cantó ayer", "qué encantador",
              "mi reto es que nadie canta en el coro",
              "en mi colegio nadie pone música en el recreo",
              "quiero que mi hermana cante conmigo",
              "mi hermana canta muy bonito", "nadie canta en el recreo",
              "mi reto es la música del colegio",
              "quiero hablar con Cristal", "modo curioso"]
# ...y estas sí lo son, aunque lleven el nombre del guía o cortesía delante.
PEDIDOS_CON_LEAD = ["cristal, cántame una canción", "oye robot por favor canta"]

# --- Modo Curioso ---
for frase in PEDIDOS:
    r = curioso._orden("cristal", curioso._plain(frase))
    check(r is not None and r.sing and not r.dance, f"Curioso canta con «{frase}»")
for frase in PEDIDOS_CON_LEAD:
    r = curioso._orden("cristal", curioso._plain(frase))
    check(r is not None and r.sing, f"Curioso canta con «{frase}»")
for frase in NO_PEDIDOS:
    r = curioso._orden("cristal", curioso._plain(frase))
    check(r is None or not r.sing, f"Curioso NO canta con «{frase}»")
# Bailar sigue siendo otra cosa.
r = curioso._orden("cristal", "baila")
check(r is not None and r.dance and not r.sing, "«baila» sigue siendo el baile corto, no la canción")

# --- Modo BANG ---
for frase in PEDIDOS + PEDIDOS_CON_LEAD:
    check(song.pide_cancion(bang._plain(frase)), f"BANG canta con «{frase}»")
for frase in NO_PEDIDOS:
    check(not song.pide_cancion(bang._plain(frase)), f"BANG NO canta con «{frase}»")

# Los dos modos tienen que decidir SIEMPRE igual: es el mismo patrón.
for frase in PEDIDOS + PEDIDOS_CON_LEAD + NO_PEDIDOS:
    t = curioso._plain(frase)
    r = curioso._orden("cristal", t)
    check(bool(r and r.sing) == song.pide_cancion(bang._plain(frase)),
          f"BANG y Curioso deciden igual con «{frase}»")

# Pedir la canción SIN reto en curso no puede guardarse como reto: es un
# recreo, no una respuesta. (Era el error fácil al añadirlo.)
bang.reset("cristal")
t = bang.turn("cristal", "quiero que cantes")
check(getattr(t, "sing", False), "sin reto en curso, «cántame una canción» canta")
check(bang.session("cristal") is None or not bang.session("cristal").reto,
      "...y NO se guarda como reto")

# Con un reto en curso, canta y el reto sigue intacto donde estaba.
s_bang = bang.Session(persona="cristal", phase="gaseosa", reto="en mi salón nadie recicla")
s_bang.ideas = ["un buzón"]
bang._sessions["cristal"] = s_bang
t = bang.turn("cristal", "pon música")
check(getattr(t, "sing", False), "con reto en curso también canta")
check(s_bang.reto == "en mi salón nadie recicla" and s_bang.phase == "gaseosa" and s_bang.ideas == ["un buzón"],
      "la canción no toca el reto: misma fase, mismo reto, mismas ideas")
check(s_bang.turns == 0, "la canción no gasta un turno de la fase")
check(not bang.slow_turn("cristal", "canta"), "cantar no espera al modelo local")
bang.reset("cristal")

# --- El audio y su pulso ---
pcm, beats = song.load(24000)
check(pcm is not None and len(pcm) > 24000 * 10, f"la canción se decodifica ({len(pcm)/24000:.0f}s)" if pcm is not None else "la canción se decodifica")
if pcm is not None:
    dur = len(pcm) / 24000
    bpm = 60.0 * len(beats) / dur
    check(len(beats) > 10, f"se le encontró el pulso: {len(beats)} golpes")
    check(80 <= bpm <= 190, f"el tempo es de canción bailable ({bpm:.0f} BPM)")
    check(beats[0] >= 0 and beats[-1] < len(pcm), "los golpes caen dentro de la canción")
    sep = np.diff(beats) if len(beats) > 1 else np.array([0])
    check(sep.std() < 2.0, f"los golpes van parejos (desviación {sep.std():.1f} muestras)")


# --- 5. RAG: que las pistas nuevas se encuentren ------------------------------

print("\n== RAG (modo Essentials) ==")
import rag  # noqa: E402

check(len(rag.CHUNKS) >= 300, f"el conocimiento creció: {len(rag.CHUNKS)} fragmentos (antes 198)")
CONSULTAS = [
    ("en mi salón nadie recicla", "cristal", "solida"),
    ("me peleo con mi hermano", "cristal", "solida"),
    ("se me ocurrió hacer un buzón", "cristal", "gaseosa"),
    ("quiero probar mi idea rápido", "cristal", "liquida"),
    ("no sé qué hacer", "cristal", "solida"),
]
for consulta, guia, fase in CONSULTAS:
    pista = rag.retrieve(consulta, guia, fase)
    check(bool(pista.strip()) and len(pista) <= 220, f"pista para «{consulta}» ({len(pista)} car.)")

check("cristal" in rag.PERSONA_KEYS, "Cristal sigue en el índice por guía")


# --- Resumen -----------------------------------------------------------------

exito = 100.0 * (pruebas - fallos) / pruebas if pruebas else 0.0
print(f"\n{pruebas - fallos}/{pruebas} pruebas pasan ({exito:.1f}%)")
if RAPIDO:
    print("(--rapido: no se corrió la capa 2, listen_turn en tiempo real)")
sys.exit(1 if fallos else 0)
