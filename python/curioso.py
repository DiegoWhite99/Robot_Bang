# Modo CURIOSO: el guia fuera de la metodologia BANG.
#
# El robot tiene dos formas de conversar (el niño elige al empezar, ver
# main._elegir_modo()):
#
#   BANG     el de siempre: el guia lleva el reto por las fases solida,
#            gaseosa y liquida, con tarjetas e ideas (bang.py).
#   CURIOSO  libre, como un asistente de voz: responde lo que le pregunten,
#            obedece ordenes cortas ("ponte feliz", "baila") y conversa, pero
#            SIEMPRE con la personalidad del guia que este activo.
#
# Lo que NO cambia entre modos: los guias y sus voces, la escucha activa
# (voice.BargeIn), los guardarrailes de seguridad (guardrails.py) y el cambio
# de guia. Aqui solo cambia QUE se hace con lo que dijo el niño.

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from arduino.app_utils import Logger

import brain
import gestures
import guardrails
import llm_router
import song
import websearch

logger = Logger("chat-bang")

MODES = ("bang", "curioso")
DEFAULT_MODE = "bang"
MODE_LABELS = {
    "bang": "BANG: el guía te acompaña a convertir tu reto en ideas (fases sólida, gaseosa y líquida)",
    "curioso": "CURIOSO: el guía conversa libre, responde lo que le preguntes y obedece órdenes como 'ponte feliz'",
}
MODE_NAMES = {"bang": "BANG", "curioso": "Curioso"}

# El modo se guarda igual que el del cerebro (llm_router.py): asi sobrevive a
# un reinicio de la App y el dashboard lo ve al conectarse.
_MODE_PATH = Path(__file__).resolve().parent.parent / "data" / "chat_mode.txt"


def _read_mode():
    try:
        m = _MODE_PATH.read_text().strip().lower()
    except FileNotFoundError:
        m = ""
    except Exception as exc:
        logger.warning(f"No pude leer {_MODE_PATH} ({exc}): uso {DEFAULT_MODE}")
        m = ""
    return m if m in MODES else DEFAULT_MODE


_mode = _read_mode()


def mode():
    return _mode


def is_curioso():
    return _mode == "curioso"


def set_mode(m):
    """Cambia el modo de conversacion. Devuelve el modo que quedo."""
    global _mode
    m = (m or "").strip().lower()
    alias = {"edu": "curioso", "libre": "curioso", "curiosa": "curioso", "curios": "curioso",
             "bang": "bang", "reto": "bang", "retos": "bang"}
    m = alias.get(m, m)
    if m not in MODES:
        raise ValueError(f"Modo desconocido: {m}. Usa {' o '.join(MODES)}")
    antes, _mode = _mode, m
    try:
        _MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MODE_PATH.write_text(m)
    except Exception as exc:
        logger.warning(f"No pude guardar el modo en {_MODE_PATH}: {exc}")
    if m != antes:
        # Las memorias no se mezclan entre modos: lo hablado en BANG no le
        # sirve al Curioso y al reves. (Si el modo no cambia no se tocan: al
        # arrancar se confirma el modo que ya habia.)
        brain.clear_all()
    return m


def _plain(text):
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# --- Elegir modo por voz ------------------------------------------------------
# "modo curioso", "quiero el modo bang", o, cuando se esta preguntando cual de
# los dos, un "curioso" / "bang" a secas.

_PIDE_CURIOSO = re.compile(r"\b(modo\s+)?(curioso|curiosa|edu|libre)\b")
_PIDE_BANG = re.compile(r"\b(modo\s+)?(bang|band|retos?)\b")
_PIDE_MODO = re.compile(r"\b(modo|cambia\w*|pasa\w*|quiero|ponte?|vamos a|juguemos)\b")


def find_mode(text, bare=False):
    """Que modo esta pidiendo esta frase, o None.

    bare=True (justo cuando se le pregunto al niño) acepta la palabra sola:
    "curioso", "bang". Si no, hace falta que la frase suene a pedido ("modo
    curioso", "cambia a curioso"): asi "bang" sigue siendo palabra de
    activacion y "que curioso" no cambia nada.
    """
    t = _plain(text)
    if not t:
        return None
    curioso, bang_ = _PIDE_CURIOSO.search(t), _PIDE_BANG.search(t)
    if not curioso and not bang_:
        return None
    if not bare and not _PIDE_MODO.search(t):
        return None
    if curioso and bang_:
        # "cambia de bang a curioso": manda el que va al final.
        return "curioso" if curioso.start() > bang_.start() else "bang"
    return "curioso" if curioso else "bang"


# --- Ordenes cortas ("ponte feliz") -------------------------------------------
# Esto es lo que hace que el modo se sienta un asistente de voz: la orden la
# resuelve Python, sin LLM, asi que la cara cambia al instante.

_ORDENES = (
    # (regex, gesto, celebrar, que responde)
    #
    # Cada orden acepta las formas en que un niño la dice de verdad: imperativo
    # ("saluda"), con pronombre ("saludame"), subjuntivo tras "quiero que"
    # ("saludes") e INFINITIVO ("saludar"). El infinitivo faltaba y era el caso
    # mas comun: "saludar" no casaba con \bsaluda\b (la r rompe el limite de
    # palabra), asi que el turno se iba al LLM y los brazos hacian el vaiven de
    # hablar — los dos a la vez, lo mismo para todo. Por eso "todos los gestos
    # se veian iguales".
    (r"\b(ponte|pon[ée]s|est[áa]s?|haz(te)?|qu[ée]date|s[ée])\s+(muy\s+)?(feliz|alegre|content[oa]|chever[ea])\b|\bs[oó]nr[íi]e\b|\bre[íi]te\b|"
     r"\b(alza|levanta|sube|arriba)\s+(los\s+|las\s+)?(manos|brazos|bracitos)\b|\b(manos|brazos)\s+arriba\b|"
     r"\b(alzar|levantar|subir)\s+(los\s+|las\s+)?(manos|brazos|bracitos)\b",
     gestures.HAPPY, False, ("¡Listo! Ya estoy feliz. {exclama}", "¡Hecho! Mírame: así me pongo cuando estoy content{a}.")),
    (r"\b(ponte|pon[ée]s|haz(te)?|s[ée])\s+(muy\s+)?(triste|melanc[óo]lic[oa])\b|\bentristecete\b",
     gestures.SAD, False, ("Bueno... ya estoy triste. Pero si me cuentas algo lindo se me pasa.",
                           "Mira mi carita triste. ¿Me cuentas algo para animarme?")),
    (r"\b(ponte|pon[ée]s|haz(te)?|s[ée])\s+(muy\s+)?(enojad[oa]|brav[oa]|furios[oa]|rabios[oa])\b|\bhaz\s+grr+\b|\benoj(ate|arte)\b",
     gestures.ANGRY, False, ("¡Grrr! Ya estoy enojad{a}... pero solo de mentiritas, nunca contigo.",
                             "¡Grrr! Mira mi cara de enojad{a}. Es puro juego.")),
    (r"\b(ponte|pon[ée]s|haz(te)?|s[ée])\s+(muy\s+)?(sorprendid[oa]|asombrad[oa])\b|\bsorpr[ée]nd(ete|erte)\b",
     gestures.SURPRISE, False, ("¡Wow! ¿Así de sorprendid{a}?", "¡Uy! Mira mi cara de sorpresa.")),
    (r"\b(ponte|pon[ée]s|haz(te)?|s[ée]|qu[ée]date)\s+(normal|tranquil[oa]|seri[oa]|en\s+reposo)\b|\bdescansa(r)?\b|\bc[áa]lmate\b",
     gestures.REST, False, ("Listo, vuelvo a mi cara de siempre.", "Ya está, me puse tranquil{a}.")),
    (r"\b(baila|bailar|baile(s|mos)?|bailemos|celebra(r|mos|emos)?|haz\s+una\s+fiesta|fiesta)\b",
     gestures.DANCE, "baila", ("¡Vamos con esa fiesta! Pon atención a mis brazos.",
                               "¡Dale! Mira cómo bailo. Háblame cuando quieras que pare.")),
    # Gestos de brazos agregados en 1.1.0 (ver gestures.py y sketch.ino).
    (r"\b(saluda|saludar|saluda(me|nos)|saludes|salude(n)?|haz(me)?\s+(un\s+)?saludo|"
     r"di(le|me)?\s+hola|haz\s+hola|hola\s+con\s+la\s+mano|mueve\s+la\s+mano)\b",
     gestures.WAVE, False, ("¡Hola, hola! Mira cómo saludo.", "¡Holaaa! Te saludo con mi brazo.")),
    (r"\b(aplaude|aplaudir|aplaude(me|nos)|aplaudan|aplaudas|dame\s+un\s+aplauso|"
     r"haz\s+aplausos|palmas|bate\s+palmas)\b",
     gestures.CLAP, False, ("¡Clap, clap, clap! Para ti.", "¡Un aplauso! Te lo mereces.")),
    (r"\b(piensa|pensar|pienses|ponte\s+a\s+pensar|haz\s+(como\s+)?que\s+piensas|cara\s+de\s+pensar)\b",
     gestures.THINK, False, ("Mmm... déjame pensarlo.", "A ver, a ver... estoy pensando.")),
    (r"\b(di\s+que\s+si|asiente|asentir|asientas|haz\s+que\s+si|di\s+si)\b",
     gestures.YES, False, ("¡Sí, sí, sí!", "Sí, claro que sí.")),
    (r"\b(di\s+que\s+no|niega|negar|niegues|haz\s+que\s+no|di\s+no)\b",
     gestures.NO, False, ("No, no y no.", "Nop, así digo que no.")),
    (r"\b(mu[ée]vete|moverte|mueve\s+(los\s+brazos|las\s+manos|los\s+bracitos))\b",
     gestures.DANCE, False, ("¡Mírame mover los brazos!", "¡Así me muevo yo!")),
    (r"\b(abraza|abrazar|abraza(me|nos)|abraces|dame\s+un\s+abrazo)\b",
     gestures.HUG, False, ("¡Un abrazo grandote para ti!", "Ven, te doy un abrazo de robot.")),
    (r"\b(duerme|dormir|du[ée]rmete|duermas|ponte\s+a\s+dormir|a\s+dormir|"
     r"haz\s+(como\s+)?que\s+duermes)\b",
     gestures.SLEEP, False, ("Shhh... me estoy durmiendo. Zzz.", "Me duermo un ratico. Zzz...")),
    (r"\b(estira|estirar(te|se)?|est[íi]rate|estires|estira\s+(los\s+brazos|las\s+manos)|"
     r"bosteza|bostezar|haz\s+un\s+bostezo)\b",
     gestures.STRETCH, False, ("¡Aaah, qué buen estirón!", "Me estiro... ¡y ya estoy despiert{a}!")),
)
# El tercer campo dice que musica acompaña a la orden: False (ninguna),
# "celebra" (la cancioncita de 3 s) o "baila" (la cancion larga con
# coreografia, ver voice.dance()).
_ORDENES = tuple((re.compile(rx), gesto, musica, frases) for rx, gesto, musica, frases in _ORDENES)

# Una orden se DA al empezar la frase ("ponte feliz", "Cori, salúdame", "oye
# Cori, por favor aplaude"), nunca en mitad de otra cosa: "¿por qué la gente
# aplaude en los conciertos?" es una pregunta, y hay que contestarla, no
# aplaudir. Antes de la orden solo pueden ir estas palabras (el nombre del
# guía, una llamada de atención o una cortesía); cualquier otra cosa quiere
# decir que la orden está metida dentro de una frase.
_ORDEN_LEAD = set(brain.PERSONAS) | {
    "robot", "bang", "oye", "oiga", "hey", "mira", "por", "favor", "porfa", "porfis",
    "ya", "ahora", "y", "bueno", "dale", "vamos", "a", "ver", "quiero", "que", "puedes",
    "podrias", "me", "te", "nos", "haz", "hazme", "hola",
}
_ES_PREGUNTA = re.compile(r"\b(por\s*que|porque|como\s+(es|se|hace)|cuando|cuanto|quien|cual|"
                          r"que\s+(es|son|significa|pasa)|sabes|crees)\b")

# Preguntas de siempre que no vale la pena pagarle al modelo.
_QUIEN_ERES = re.compile(r"\b(c[óo]mo te llamas|qui[ée]n eres|c[óo]mo se llama|tu nombre|qui[ée]n sos)\b")
_QUE_HACES = re.compile(r"\b(qu[ée] (puedes|sabes) hacer|qu[ée] haces|para qu[ée] sirves|ay[úu]dame|ayuda)\b")
_MODO_ACTUAL = re.compile(r"\b(en qu[ée] modo|qu[ée] modo)\b")


# Los nombres de los guias no son parte de la pregunta: "oye Cori, ¿que paso
# ayer?" se busca sin "Cori".
_NOMBRES = tuple(p["name"] for p in brain.PERSONAS.values())


@dataclass
class Reply:
    """Lo que devuelve un turno del modo Curioso. Mismos campos que bang.Turn
    (main.py los trata igual) mas el gesto y la celebracion de las ordenes."""

    reply: str
    source: str = None  # gemini | local | fijo | plantilla
    phase_changed: bool = False  # nunca: en Curioso no hay fases
    card: tuple = None  # nunca: las tarjetas son de BANG
    gesture: int = None  # gesto pedido a mano ("ponte feliz"); None = el del texto
    celebrate: bool = False  # cancioncita corta (~3 s) + brazos
    dance: bool = False  # cancion larga (~30 s) con coreografia: voice.dance()
    sing: bool = False  # "¡A despegar!" de assets/audio/: voice.sing()


def _a(persona):
    """Terminacion de genero del guia: Cesia, Cori y Cristal son mujeres."""
    return "a" if brain.PERSONAS[persona]["gender"] == "f" else "o"


def _formatea(frase, persona):
    return frase.format(a=_a(persona), exclama="¡Qué bueno verte!")


_orden_turno = 0


_CANTA_FRASES = ("¡A despegar! Canto y bailo, mírame los brazos.",
                 "¡Esta me encanta! A despegar. Háblame cuando quieras que pare.")


def _orden(persona, t):
    """Si la frase es una orden corta, la Reply que la cumple; si no, None."""
    global _orden_turno
    if _ES_PREGUNTA.search(t):
        return None
    # Cantar va aparte de _ORDENES y ANTES que todo: _ORDENES exige que lo que
    # va delante del verbo sea cortesia o el nombre del guia, y eso dejaba
    # fuera "quiero que cantes" — justo como lo pide un niño. El patron es el
    # mismo que usa el modo BANG (song.SING_RE): una sola fuente de verdad.
    if song.pide_cancion(t):
        _orden_turno += 1
        return Reply(_formatea(_CANTA_FRASES[_orden_turno % len(_CANTA_FRASES)], persona),
                     source="plantilla", gesture=gestures.DANCE, sing=True)
    for rx, gesto, musica, frases in _ORDENES:
        m = rx.search(t)
        if m is None or any(w.strip(",.;:¡!¿?") not in _ORDEN_LEAD for w in t[: m.start()].split()):
            continue
        _orden_turno += 1
        frase = frases[_orden_turno % len(frases)]
        return Reply(_formatea(frase, persona), source="plantilla", gesture=gesto,
                     celebrate=musica == "celebra", dance=musica == "baila", sing=musica == "canta")
    return None


def _sin_reto(texto):
    """Las respuestas fijas de seguridad terminan preguntando por el reto
    (guardrails.py vive pensando en BANG). En Curioso no hay reto en curso, asi
    que esa colita se cambia por una que si tiene sentido aqui."""
    return (
        texto.replace("¿Seguimos con tu reto?", "¿De qué quieres que hablemos?")
        .replace("Cuéntame mejor en qué estás pensando para tu reto.", "Mejor cuéntame qué quieres saber.")
        .replace("no los necesito para ayudarte", "no los necesito para conversar contigo")
    )


# --- El prompt del guia libre -------------------------------------------------

_REGLAS = (
    "Hablas con un solo niño de 5 a 14 años por voz. Esta es una charla libre: responde lo que te "
    "pregunte, sea de lo que sea, con informacion correcta y en palabras sencillas. No estas guiando "
    "ninguna metodologia: no hables de retos, fases ni ideas si el niño no los menciona. "
    "Responde en español, en 1 a 3 frases cortas, naturales al decirlas en voz alta. "
    "Nada de markdown, listas, vinetas ni emojis. Como mucho una pregunta al final, y no en todos los turnos. "
    "Si no sabes algo, dilo con sencillez. Nunca pidas datos personales. Nunca te enojes con el niño. "
    "Eres un robot, un personaje virtual: nunca digas que eres una persona."
)


def _system(persona):
    return brain.persona_intro(persona) + " " + _REGLAS


def _local_system(persona):
    """Corto y FIJO por guia: el modelo local lee ~3 s por cada 100 caracteres
    (ver brain.local_system)."""
    p = brain.PERSONAS[persona]
    yo = "una guia" if p["gender"] == "f" else "un guia"
    return (
        f"Eres {p['name']}, {p['tagline'].lower()}, {yo} de BANG. Eres un robot, un personaje virtual: "
        "nunca digas que eres una persona. Charlas libre con un niño de 5 a 14 años y respondes lo que te "
        f"pregunte, en palabras sencillas. Tono: {p['voice']} "
        "Responde en español, 1 o 2 frases cortas y como mucho una pregunta. "
        "Sin listas, sin emojis, sin pedir datos personales. Nunca te enojes con el niño."
    )


def greeting(persona):
    """Lo que dice el guia al entrar al modo (o al presentarse en el)."""
    p = brain.PERSONAS[persona]
    return (
        f"¡Hola! Soy {p['name']}, {p['tagline'].lower()}. Estamos en modo Curioso: "
        "pregúntame lo que quieras, o pídeme cosas como ponte feliz, salúdame o baila. ¿Qué quieres saber?"
    )


def _corto(text, words):
    partes = (text or "").split()
    return " ".join(partes[:words]) + ("..." if len(partes) > words else "")


def slow_turn(persona, text):
    """True si este turno va a esperar al modelo LOCAL (para la frase de
    relleno de main.py). Las ordenes y las respuestas fijas salen al instante."""
    if not llm_router.is_local():
        return False
    t = _plain(text)
    if guardrails.respuesta_fija(text, "") or _orden(persona, t) or _QUIEN_ERES.search(t) or _QUE_HACES.search(t) or _MODO_ACTUAL.search(t):
        return False
    return True


def turn(persona, text, interrumpido=""):
    """Un turno del modo Curioso. Devuelve Reply.

    interrumpido: lo que el guia alcanzo a decir antes de que el niño lo
    cortara. En Curioso no hay reto donde guardarlo (eso es cosa de bang.py),
    asi que se le recuerda al modelo en ESTE turno: retoma donde iba y lo
    engancha con lo nuevo, en vez de arrancar de cero como si no hubiera
    estado hablando.
    """
    llm_router.reset_source()
    name = brain.PERSONAS[persona]["name"]

    # 1. Seguridad, antes que nada y en los dos modos.
    fijo = guardrails.respuesta_fija(text, name)
    if fijo:
        logger.info("Respuesta fija de seguridad (sin LLM)")
        return Reply(_sin_reto(fijo), source="fijo")

    t = _plain(text)

    # 2. Ordenes cortas: las cumple Python, sin pagar modelo ni esperar.
    orden = _orden(persona, t)
    if orden:
        return orden

    # 3. Preguntas de siempre.
    if _QUIEN_ERES.search(t):
        p = brain.PERSONAS[persona]
        return Reply(f"Soy {p['name']}, {p['tagline'].lower()} de BANG. Soy un robot que te acompaña a pensar "
                     "y a descubrir cosas. ¿Qué quieres saber?", source="plantilla")
    if _QUE_HACES.search(t):
        return Reply("Puedo contarte de lo que quieras y responder tus preguntas. También hago cosas: "
                     "dime ponte feliz, ponte triste, salúdame, aplaude, abrázame, baila o duérmete. "
                     "Y si quieres trabajar un reto, dime modo BANG.", source="plantilla")
    if _MODO_ACTUAL.search(t):
        return Reply("Estamos en modo Curioso: charlamos de lo que quieras. Si prefieres trabajar un reto, "
                     "dime 'modo BANG'.", source="plantilla")

    # 4. Hechos de verdad: si la pregunta pide un dato que cambia con el tiempo
    #    ("¿quien gano ayer?") se busca en internet ANTES de preguntarle al
    #    modelo. Gemini no tiene buscador (ver websearch.py): sin esto o dice
    #    que no sabe o se inventa el dato, que con un niño es peor.
    #    Solo en Plus: el contexto son ~900 caracteres y el modelo local los
    #    leeria a ~9 tokens/s, unos 30 s. En Essentials se pregunta sin buscar,
    #    y en el respaldo local de Plus tambien (el prompt corto de `local=`).
    pregunta, con_web = text, False
    if not llm_router.is_local() and websearch.necesita(text):
        ctx = websearch.contexto(text, personas=_NOMBRES)
        if ctx:
            pregunta, con_web = websearch.prompt_con_contexto(text, ctx), True

    if interrumpido:
        nota = (
            f"[Te interrumpieron mientras decias: \"{_corto(interrumpido, 20)}\". "
            "Enlaza con lo que dice ahora y sigue desde ahi; no lo repitas ni empieces de cero]\n"
        )
        pregunta = nota + pregunta

    raw = llm_router.chat(
        (persona, "curioso"), _system(persona), pregunta, temperature=0.6,
        local=(_local_system(persona), text),
    )
    if not raw:
        return Reply(brain.FALLBACK_REPLY, source=llm_router.last_source() or "plantilla")
    limpio = guardrails.limpiar(raw, max_frases=4) or brain.FALLBACK_REPLY
    fuente = llm_router.last_source() or "plantilla"
    if con_web and fuente == "gemini":
        fuente = "gemini_web"  # el panel lo muestra como "Gemini + internet"
    reply = Reply(limpio, source=fuente)

    # 5. Tema delicado: el aviso de hablarlo con un adulto vale aqui igual.
    aviso = guardrails.aviso_adulto(text)
    if aviso:
        reply.reply = f"{aviso[1]} {reply.reply}"
    return reply
