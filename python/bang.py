# Metodologia BANG: el guia no es un chatbot suelto, es un FACILITADOR que
# acompana un reto por las tres fases de BangLite.
#
#   1. SOLIDA   formular bien la pregunta problema (Mom Test + "¿Como
#               podriamos...?"). Termina cuando el LLM marca faseCompleta.
#   2. GASEOSA  idear en cantidad: se reparten 3 tarjetas del mazo del guia
#               ("saca una tarjeta") y cada cosa que dice la persona cuenta
#               como una idea; a las 7 se ordenan y se proponen la liquida.
#   3. LIQUIDA  prototipo y validacion minima: hipotesis, senal, version
#               minima, con quien probarlo y criterio de decision.
#
# Todo esto viene de bang-lite-ai (repo PROYECTOS-IA-CUN-2026, carpetas
# "1. Bang", "4. Bang tarjetas" y "5. Bang unificado"): los prompts de fase,
# los guardarrailes del facilitador (sin soluciones, max. 2 preguntas), el
# clasificador de personaje y la mecanica de tarjetas e ideas. Lo que cambia
# es que aqui todo pasa por voz: no hay botones, las transiciones se piden
# hablando ("siguiente fase", "nuevo reto", "saca una tarjeta").

import json
import random
import re
import unicodedata
from dataclasses import dataclass, field

import brain
from arduino.app_utils import Logger

logger = Logger("chat-bang")

PHASES = ("solida", "gaseosa", "liquida")
PHASE_LABELS = {"solida": "Fase sólida", "gaseosa": "Fase gaseosa", "liquida": "Fase líquida"}

CARDS_PER_ROUND = 3  # como en bang-lite-ai: 3 tarjetas al azar al entrar a la gaseosa
IDEAS_FOR_LIST = 7  # a las 7 ideas se ordena el listado y se propone pasar a la liquida
MIN_SOLID_TURNS = 2  # la solida no se da por cerrada antes de 2 respuestas de la persona

# Texto de las 10 cartas de cada mazo, EN EL ORDEN de sus imagenes
# (assets/img/tarjetas/<Guia>/<guia>-N.png), copiado de cardConfig en
# bang-lite-ai (5. Bang unificado/src/app/reto/page.js). Ojo: los reportes de
# identty/ traen otra version de algunas cartas; estas son las que coinciden
# con lo que dice cada imagen.
CARDS = {
    "carmel": [
        "Haz una lista de cuatro cosas que podrías hacer y haz lo último en la lista",
        "Elimina", "Cambia de roles", "Una sencilla solución, dos difíciles soluciones", "Déjà vu",
        "Toma un descanso", "Nado submarino largas distancias", "Comparte valor",
        "Si tú te lo crees, los demás lo comprarán",
        "Todo bombero no desea otra cosa en la vida más que ser bombero",
    ],
    "cesia": [
        "Quita las partes importantes", "Cuantas manos y cabezas sean necesarias", "Primero lo primero",
        "Cambia de roles", "Aporte valor de verdad", "Modifica", "Duro como una roca, flexible como",
        "Sé aburrido", "¡Rómpelo!", "No hay reglas",
    ],
    "cori": [
        "Combina lo incombinable", "Piensa como un...", "Elimina, reduce, incrementa y crea",
        "Observa de cerca los detalles más vergonzosos y amplíalos", "Adapta",
        "Mira los dos lados, el oscuro y el claro", "Desordénalo todo", "¿Qué haría tu mejor amigo?",
        "La idea no es un motivo", "Ni a Dios lo que es de Dios, ni al César lo que es del César",
    ],
    "crispi": [
        "El cielo es un vecindario", "Empieza por un garaje", "Pon otros usos",
        "Sin planos no se hacen rascacielos", "Dale un vuelco a tu rutina", "Piensa cosas imposibles de hacer",
        "Haz trocitos y después júntalos", "Corta una conexión", "Haz cosas imperfectas", "Devuélvete",
    ],
    "cristal": [
        "Conviértelo en tu APP favorita", "Busca un libro y lee su primer párrafo", "Actúa con pasión",
        "Juega el juego", "¿Qué pasaría si...?", "Nunca pongas los huevos en una sola canasta", "Como en casa",
        "Como lo haría la persona que más admiras", "Los detalles marcan la diferencia", "Qué aprendiste ayer",
    ],
}


def card_image(persona, number):
    """Ruta web de la imagen de la carta (la sirve web_ui desde assets/)."""
    name = brain.PERSONAS[persona]["name"]
    return f"img/tarjetas/{name}/{persona}-{number}.png"


# --- Estado de cada reto ----------------------------------------------------


@dataclass
class Session:
    persona: str
    phase: str = "solida"
    reto: str = ""
    pregunta: str = ""  # la pregunta problema final de la solida ("¿Como podriamos...?")
    turns: int = 0  # respuestas de la persona en la fase actual
    cards: list = field(default_factory=list)  # [{"number", "text", "image", "unlocked"}]
    ideas: list = field(default_factory=list)
    ideas_listed: bool = False

    def state(self):
        """Lo que la web necesita para mostrar el reto."""
        return {
            "persona": self.persona,
            "phase": self.phase,
            "phase_label": PHASE_LABELS[self.phase],
            "reto": self.reto,
            "pregunta": self.pregunta,
            "cards": self.cards,
            "ideas": self.ideas,
        }


_sessions = {}


def session(persona):
    return _sessions.get(persona)


def reset(persona):
    """Borra el reto en curso del guia (comando /reset de la terminal)."""
    _sessions.pop(persona, None)
    brain.clear(persona)


@dataclass
class Turn:
    reply: str
    phase_changed: bool = False  # para celebrarlo (baile + melodia, ver voice.celebrate())
    new_persona: str = None  # el clasificador eligio guia (cuando se llamo al "robot")
    card: tuple = None  # (guia, numero) de la tarjeta recien volteada, para la pantalla


# --- Guardarrailes del facilitador (openai.js de bang-lite-ai) ---------------

# Verbos de "dar la solucion", recetas y consejo directo. Si aparecen en la
# solida o la liquida, la respuesta se reescribe en modo facilitador. En la
# gaseosa no se aplican: ahi el guia SI da detonantes y ejemplos.
_BANNED = (
    re.compile(r"\b(usa|utiliza|instala|implementa|configura|descarga|aplica|compra|contrata)\b", re.I),
    re.compile(r"\b(paso a paso|instrucciones|tutorial)\b", re.I),
    re.compile(r"\b(deberias|deberías|te sugiero|recomiendo|mi consejo|mi recomendacion|mi recomendación)\b", re.I),
)
_INTERPRETATIONS = re.compile(r"(^|(?<=[.!?]\s))(Parece que|Me imagino que|Si no me equivoco)", re.I)


def _violates_facilitator(text):
    return any(rx.search(text or "") for rx in _BANNED)


def _limit_questions(text, max_q=2):
    """Corta despues de la segunda pregunta: por voz, mas de 2 abruma."""
    count = 0
    for i, ch in enumerate(text):
        if ch == "?":
            count += 1
            if count == max_q:
                return text[: i + 1].strip()
    return text.strip()


def _neutralize(text):
    return _INTERPRETATIONS.sub(lambda m: m.group(1) + "Entiendo que", text or "")


def _rephrase_as_facilitator(persona, text):
    system = (
        "Eres un auditor de estilo BANG. Reescribe el texto que te paso para cumplir: "
        "maximo 2 preguntas cortas; neutralidad total, sin soluciones, herramientas ni pasos; "
        "se permite una validacion breve ('Tiene sentido que...'). Mantén la voz de "
        + brain.PERSONAS[persona]["name"] + ". " + brain.SPOKEN_RULES + " Responde SOLO con el texto final."
    )
    return brain.chat(None, system, text, temperature=0.2, memory=False) or text


def _polish(persona, phase, text):
    if phase != "gaseosa" and _violates_facilitator(text):
        logger.info("La respuesta daba soluciones: la reescribo en modo facilitador")
        text = _rephrase_as_facilitator(persona, text)
    return _limit_questions(_neutralize(text).strip())


# --- Prompts por fase ---------------------------------------------------------


def _system(persona, phase):
    intro = brain.persona_intro(persona)
    rules = brain.SPOKEN_RULES
    if phase == "solida":
        return (
            intro + " Actuas como facilitador BANG en la FASE SOLIDA: ayudas a formular bien la pregunta problema. "
            "Mision: hacer facil la innovacion; neutralidad; tono cercano, dinamico y motivador. "
            "No des soluciones, herramientas ni pasos: solo preguntas. Maximo 2 preguntas por turno, "
            "con una breve validacion emocional al inicio ('Tiene sentido que...'). "
            "Verifica si ya tienes: 1) contexto claro (donde y cuando ocurre), 2) la situacion especifica, "
            "3) el impacto personal, 4) intentos previos o la dificultad, 5) que se pueda reformular como "
            "'¿Como podriamos...?'. Si falta algo, pregunta por eso; una buena pregunta es de comportamiento "
            "pasado (Mom Test): 'Cuentame sobre la ultima vez que...'. "
            "Si ya esta todo, reformula la pregunta problema final como '¿Como podriamos...?'. "
            + rules + " "
            'Responde SIEMPRE y SOLO con este JSON: {"respuesta": "lo que dices en voz alta", '
            '"faseCompleta": true o false, "pregunta": "la pregunta problema final, o vacio si aun falta"}'
        )
    if phase == "gaseosa":
        return (
            intro + " Actuas como facilitador BANG en la FASE GASEOSA (ideacion divergente). "
            "Mision: activar la creatividad; cantidad sobre calidad; no juzgues ni evalues las ideas; "
            "provoca multiples interpretaciones. Cada cosa que te dice la persona es una idea: celebrala "
            "en pocas palabras y lanza UN detonante breve para la siguiente (una palanca SCAMPER: eliminar, "
            "reducir, aumentar o crear; un rol extremo o analogia; una restriccion fuerte como resolverlo en "
            "24 horas; o un pequeno experimento). Maximo 2 preguntas. " + rules
        )
    return (
        intro + " Actuas como facilitador BANG en la FASE LIQUIDA (prototipo y validacion). "
        "Mision: simple y rapido; construir para pensar; validar en pocos dias; celebrar avances. "
        "No des el como hacerlo ni herramientas concretas: guia con preguntas, maximo 2 por turno, "
        "recorriendo en la conversacion: la hipotesis a validar en una frase; la senal que observaria en "
        "3 a 7 dias; la version minima y barata que simula el valor; con quien y cuando lo pone a prueba; "
        "y el criterio para seguir, ajustar o detener. Evalua tambien factibilidad tecnica, deseabilidad "
        "y viabilidad economica de la idea elegida. " + rules
    )


def _context(s):
    parts = [f"[Fase: {PHASE_LABELS[s.phase]}]", f"[Reto: {s.reto}]"]
    if s.pregunta:
        parts.append(f"[Pregunta problema: {s.pregunta}]")
    if s.ideas and s.phase != "solida":
        parts.append("[Ideas hasta ahora: " + "; ".join(s.ideas) + "]")
    return " ".join(parts)


def _ask(s, text, temperature):
    raw = brain.chat((s.persona, s.phase), _system(s.persona, s.phase), f"{_context(s)}\nLa persona dice: {text}", temperature=temperature)
    return raw if raw is not None else brain.FALLBACK_REPLY


def _parse_solid(raw):
    """El JSON de la solida, tolerante a que venga envuelto en ```json o con texto."""
    match = re.search(r"\{.*\}", raw or "", re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            return str(data.get("respuesta") or "").strip(), bool(data.get("faseCompleta")), str(data.get("pregunta") or "").strip()
        except (ValueError, AttributeError):
            pass
    return (raw or "").strip(), False, ""


# --- Clasificador de personaje ------------------------------------------------

_CLASSIFIER = (
    "Eres un clasificador de personajes BANG. Segun el reto, elige UNO: "
    "crispi (necesidades basicas: practico, inmediato, simplificar, construir); "
    "cesia (seguridad: privacidad, estabilidad, riesgos; o romper miedos y reglas); "
    "cori (necesidades sociales: relaciones, comunidad, comunicacion, pertenencia); "
    "carmel (autoestima: motivacion, progreso, reconocimiento, fortalezas); "
    "cristal (autorrealizacion: vision, crecimiento, proposito, emprendimiento). "
    'Devuelve SOLO JSON estricto: {"nombre": "crispi|cesia|cori|carmel|cristal", "confidence": 0.0}'
)


def classify(reto):
    """Que guia acompana mejor este reto. Si algo falla, Crispi (como bang-lite-ai)."""
    raw = brain.chat(None, _CLASSIFIER, reto, temperature=0.15, memory=False) or ""
    match = re.search(r"\{.*\}", raw, re.S)
    try:
        name = _plain(json.loads(match.group(0)).get("nombre", "")) if match else ""
    except ValueError:
        name = ""
    return name if name in brain.PERSONAS else "crispi"


# --- Comandos de voz ----------------------------------------------------------


def _plain(text):
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


_CMD_NEW = re.compile(r"\b(nuevo reto|otro reto|empe[cz]\w* de nuevo|reinicia\w*|desde cero)\b")
_CMD_NEXT = re.compile(r"\b(siguiente fase|proxima fase|otra fase|cambia\w* de fase|pas\w* a la (fase )?(gaseosa|liquida)|fase (gaseosa|liquida))\b")
_CMD_CARD = re.compile(r"\b(tarjeta|carta|inspirame)\b")


# --- Turno -------------------------------------------------------------------


def turn(persona, text):
    """Procesa lo que dijo la persona y devuelve lo que responde el guia."""
    t = _plain(text)
    s = _sessions.get(persona)

    if s is None or not s.reto or _CMD_NEW.search(t):
        return _start(persona, text, restarted=s is not None and bool(s.reto))

    if _CMD_NEXT.search(t):
        return _advance(s, announce_only=False)

    if s.phase == "gaseosa" and _CMD_CARD.search(t):
        return _draw_card(s)

    s.turns += 1
    if s.phase == "solida":
        return _solid_turn(s, text)
    if s.phase == "gaseosa":
        return _gaseous_turn(s, text)
    return Turn(_polish(persona, "liquida", _ask(s, text, 0.35)))


def _start(persona, text, restarted):
    brain.clear(persona)
    # Si la persona solo dijo "nuevo reto", el reto llega en el proximo turno.
    reto = _CMD_NEW.sub("", _plain(text)).strip(" ,.")
    s = _sessions[persona] = Session(persona=persona, reto=text.strip() if reto else "")
    s.turns = 1
    name = brain.PERSONAS[persona]["name"]
    if not reto:
        return Turn(f"¡Dale! Empezamos un reto nuevo conmigo, {name}. Cuéntame en una frase cuál es tu reto.")
    respuesta, _, _ = _parse_solid(_ask(s, text, 0.35))
    intro = "Empezamos de cero. " if restarted else ""
    return Turn(intro + _polish(persona, "solida", respuesta or brain.FALLBACK_REPLY))


def _solid_turn(s, text):
    respuesta, complete, pregunta = _parse_solid(_ask(s, text, 0.35))
    respuesta = _polish(s.persona, "solida", respuesta or brain.FALLBACK_REPLY)
    if complete and s.turns >= MIN_SOLID_TURNS:
        s.pregunta = pregunta or respuesta
        return _advance(s, announce_only=True)
    return Turn(respuesta)


def _gaseous_turn(s, text):
    s.ideas.append(text.strip())
    if len(s.ideas) >= IDEAS_FOR_LIST and not s.ideas_listed:
        s.ideas_listed = True
        return Turn(_ideas_summary(s))
    return Turn(_polish(s.persona, "gaseosa", _ask(s, text, 0.6)))


def _ideas_summary(s):
    system = (
        brain.persona_intro(s.persona) + " La persona genero estas ideas en la fase gaseosa de BANG. "
        "Agrupalas y quita las repetidas, y nombra en voz alta las 3 a 5 mas distintas, de forma breve y "
        "natural (sin listas ni vinetas, como lo dirias hablando). Cierra invitando a pasar a la fase "
        "liquida para evaluar la mejor, diciendo 'siguiente fase'. " + brain.SPOKEN_RULES
    )
    ideas = "\n".join(f"- {i}" for i in s.ideas)
    return brain.chat(None, system, f"{_context(s)}\nIdeas:\n{ideas}", temperature=0.4, memory=False) or (
        "¡Ya tenemos un montón de ideas! Cuando quieras, dime 'siguiente fase' y evaluamos la mejor."
    )


def _draw_card(s):
    card = next((c for c in s.cards if not c["unlocked"]), None)
    if card is None:
        return Turn("Ya volteamos las tres tarjetas de esta ronda. Sigue contándome ideas, o dime 'siguiente fase'.")
    card["unlocked"] = True
    system = (
        brain.persona_intro(s.persona) + " La persona esta en la fase gaseosa de BANG y acaba de voltear una "
        "tarjeta de tu mazo. La tarjeta no es una respuesta, es una lente. Explica en una frase como usarla "
        "para generar ideas, da 2 ejemplos concretos aplicados a su reto y termina con UNA pregunta que la "
        "obligue a pensar diferente. Tono creativo, inspirador y breve. No repitas el texto de la tarjeta. "
        + brain.SPOKEN_RULES
    )
    reply = brain.chat(None, system, f"{_context(s)}\nTarjeta: {card['text']}", temperature=0.6, memory=False)
    return Turn(
        f"Tarjeta: «{card['text']}». " + _limit_questions(reply or "¿Qué idea te aparece para tu reto si la tomas en serio?"),
        card=(s.persona, card["number"]),
    )


def _advance(s, announce_only):
    """Pasa a la siguiente fase. announce_only: la cerro el LLM (no la persona)."""
    idx = PHASES.index(s.phase)
    if idx == len(PHASES) - 1:
        return Turn("Ya estamos en la fase líquida, la última. Cuéntame cómo te fue con tu prueba, o dime 'nuevo reto'.")

    s.phase = PHASES[idx + 1]
    s.turns = 0
    if s.phase == "gaseosa":
        if not s.pregunta:
            s.pregunta = s.reto
        deck = CARDS.get(s.persona) or []
        picks = random.sample(range(len(deck)), min(CARDS_PER_ROUND, len(deck)))
        s.cards = [{"number": n + 1, "text": deck[n], "image": card_image(s.persona, n + 1), "unlocked": False} for n in picks]
        s.ideas, s.ideas_listed = [], False
        lead = f"¡Excelente! Ya tenemos tu pregunta problema: {s.pregunta} " if announce_only else "¡Vamos! "
        return Turn(
            lead + "Pasamos a la fase gaseosa: ahora a generar ideas, muchas, sin juzgarlas. "
            "Tengo tres tarjetas boca abajo para inspirarte: dime 'saca una tarjeta' cuando quieras, "
            "o cuéntame tu primera idea.",
            phase_changed=True,
        )
    return Turn(
        "¡Genial! Entramos en la fase líquida: vamos a elegir la idea con más potencial y a probarla rápido "
        "y barato. ¿Cuál de tus ideas te entusiasma más para ponerla a prueba?",
        phase_changed=True,
    )
