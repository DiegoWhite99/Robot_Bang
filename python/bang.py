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
#
# Modo ESSENTIALS (modelo local, ver llm_router.py): como mucho UNA llamada
# al LLM por turno, con prompt corto + pista del RAG (rag.py). Lo demas lo
# resuelve Python: el clasificador de guia, el resumen de ideas y la tarjeta
# salen de plantillas y de knowledge/; la solida la cierra el conteo de
# turnos con la plantilla "¿Como podriamos...?" (un 0.8B no devuelve el JSON
# estricto de forma fiable); y en vez de reescribir con otra llamada, las
# frases que dan soluciones se caen (guardrails.quitar_frases()). En PLUS
# todo sigue igual que antes, con Gemini.
#
# En los DOS modos, lo innegociable (identidad, datos privados, peligro,
# malestar) se contesta con guardrails.respuesta_fija() antes de nada.

import json
import random
import re
import unicodedata
from dataclasses import dataclass, field

import brain
import guardrails
import llm_router
import rag
from arduino.app_utils import Logger

logger = Logger("chat-bang")

PHASES = ("solida", "gaseosa", "liquida")
PHASE_LABELS = {"solida": "Fase sólida", "gaseosa": "Fase gaseosa", "liquida": "Fase líquida"}

CARDS_PER_ROUND = 3  # como en bang-lite-ai: 3 tarjetas al azar al entrar a la gaseosa
IDEAS_FOR_LIST = 7  # a las 7 ideas se ordena el listado y se propone pasar a la liquida
MIN_SOLID_TURNS = 2  # la solida no se da por cerrada antes de 2 respuestas de la persona
# Essentials: el reto + 2 respuestas y Python cierra la solida con la
# plantilla "¿Como podriamos...?" (sin llamar al LLM en ese turno).
LOCAL_SOLID_TURNS = 3

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
    avisos: set = field(default_factory=set)  # avisos de guardrails.aviso_adulto() ya dichos en este reto
    # Aportes que el niño metio interrumpiendo al guia ("¡se me ocurrio algo!"),
    # en cualquier fase: [{"text", "phase"}]. En la gaseosa tambien son ideas.
    aportes: list = field(default_factory=list)
    # Lo que el guia alcanzo a decir de la respuesta que le cortaron. Lo ve el
    # proximo prompt UNA vez (ver turn()) para no repetir ni hacer como si lo
    # hubiera dicho todo.
    interrumpido: str = ""
    # De que guia viene el reto si se lo pasaron ("pasame con Cori"): tambien
    # una sola vez, para que el nuevo guia sepa que releva a otro.
    relevo: str = ""

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
            "aportes": [a["text"] for a in self.aportes],
            "interrumpido": self.interrumpido,
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
    source: str = None  # quien contesto: gemini | local | fijo | plantilla (para el diagnostico)


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
    # Sin Gemini no se paga el modelo local por esto: se quitan las frases.
    return brain.chat(None, system, text, temperature=0.2, memory=False, fallback=False) or guardrails.quitar_frases(text, _BANNED) or text


# Si el filtro local deja la respuesta vacia, el guia sigue con esto.
_LOCAL_EMPTY = {
    "solida": "Cuéntame más: ¿cuándo fue la última vez que te pasó?",
    "gaseosa": "¡Me gusta! ¿Qué otra idea se te ocurre, aunque sea loca?",
    "liquida": "¿Cuál sería la prueba más pequeña y barata que podrías hacer esta semana?",
}


def _polish(persona, phase, text, local=False):
    if local:
        # Modelo local: nada de segunda llamada. Filtro determinista y fuera.
        if phase != "gaseosa":
            text = guardrails.quitar_frases(text, _BANNED)
        return guardrails.limpiar(_neutralize(text), max_frases=3) or _LOCAL_EMPTY[phase]
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
    if s.aportes:
        parts.append("[Aportes que la persona agrego interrumpiendote: " + "; ".join(a["text"] for a in s.aportes[-5:]) + "]")
    if s.relevo and s.relevo in brain.PERSONAS:
        parts.append(f"[Nota: recibes este reto de {brain.PERSONAS[s.relevo]['name']}; continua donde iba, sin empezar de cero]")
    if s.interrumpido:
        parts.append(
            f"[Nota: tu respuesta anterior se corto porque la persona te interrumpio; alcanzaste a decir: "
            f"\"{_short(s.interrumpido, 25)}\". No la repitas entera]"
        )
    return " ".join(parts)


# Lo que se le pide al modelo local en cada fase (va en el mensaje, no en el
# system prompt, que es fijo por guia: ver brain.local_system()).
_LOCAL_TASK = {
    "solida": "Valida en pocas palabras y haz UNA pregunta para entender mejor su problema. No des soluciones.",
    "gaseosa": "Celebra su idea en pocas palabras y dale un empujón para otra idea distinta, con una pregunta.",
    "liquida": "Ayúdale a probar su idea rápido y barato con UNA pregunta. No le digas cómo hacerlo.",
}


def _local_user(s, text):
    """Mensaje corto para el modelo local (~90 tokens): fase, reto, pista, tarea."""
    query = text if len(text) >= 25 else f"{text} {s.reto}"
    pista = rag.retrieve(query, s.persona, s.phase, k=2, max_chars=200)
    parts = [f"[{PHASE_LABELS[s.phase]}]"]
    if s.reto and s.reto.strip() != text.strip():
        parts.append("Reto: " + " ".join(s.reto.split()[:15]))
    if s.phase == "liquida" and s.ideas:
        parts.append("Ideas: " + "; ".join(" ".join(i.split()[:6]) for i in s.ideas[-3:]))
    if s.aportes and s.phase != "gaseosa":  # en la gaseosa ya van como ideas
        parts.append("Aporte: " + " ".join(s.aportes[-1]["text"].split()[:8]))
    if pista:
        parts.append(f"Pista: {pista}")
    parts.append(f"Tarea: {_LOCAL_TASK[s.phase]}")
    parts.append(f"Niño: {text}")
    return "\n".join(parts)


def _ask(s, text, temperature, fallback=True):
    """Una llamada al LLM del turno. Devuelve (texto, local): local=True si
    contesto el modelo local (modo Essentials, o respaldo porque Gemini fallo).
    Con fallback=False, si Gemini falla devuelve (None, False) sin esperar al
    modelo local."""
    raw = brain.chat(
        (s.persona, s.phase), _system(s.persona, s.phase), f"{_context(s)}\nLa persona dice: {text}",
        temperature=temperature, local=(brain.local_system(s.persona), _local_user(s, text)), fallback=fallback,
    )
    if raw is None and not fallback:
        return None, False
    return (raw if raw is not None else brain.FALLBACK_REPLY), llm_router.last_source() == "local"


# Muletillas y saludos con que suele empezar el reto dicho en voz alta.
_RETO_FILLERS = re.compile(
    r"^\s*(?:(?:hola|pues|bueno|oye|mira|eh+|este|ok|vale|entonces|y|bang|robot|"
    r"crispi|carmel|cesia|cori|cristal)\b[\s,.!¡]*)+",
    re.I,
)
_RETO_PREFIX = re.compile(
    r"^\s*(?:yo\s+)?(mi reto es|el reto es|mi problema es|el problema es|quiero|quisiera|me gustar[ií]a|necesito|tengo que|ay[uú]dame a)\s+(que\s+)?",
    re.I,
)


def _como_podriamos(reto):
    """Plantilla de la pregunta problema para el modo Essentials.

    "quiero que mis amigos reciclen"  -> ¿Como podriamos lograr que tus amigos reciclen?
    "aprender a nadar mas rapido"     -> ¿Como podriamos aprender a nadar mas rapido?
    "mis amigos no reciclan"          -> ¿Como podriamos resolver esto: tus amigos no reciclan?
    """
    r = re.sub(r"\s+", " ", reto or "").strip(" .!?¿¡,")
    r = _RETO_FILLERS.sub("", r)
    m = _RETO_PREFIX.match(r)
    wish = bool(m and m.group(2) and not _plain(m.group(1)).startswith(("mi ", "el ")))
    r = r[m.end():] if m else r
    for a, b in ((r"\bmis\b", "tus"), (r"\bmi\b", "tu"), (r"\bme\b", "te"), (r"\bconmigo\b", "contigo"), (r"\byo\b", "tú")):
        r = re.sub(a, b, r, flags=re.I)
    r = " ".join(r.split()[:16]).strip(" .!?¿¡,")
    if not r:
        return "¿Cómo podríamos resolver tu reto?"
    r = r[0].lower() + r[1:]
    if wish:
        return f"¿Cómo podríamos lograr que {r}?"
    first = _plain(r.split()[0])
    if len(first) > 3 and first.endswith(("ar", "er", "ir")):
        return f"¿Cómo podríamos {r}?"
    return f"¿Cómo podríamos resolver esto: {r}?"


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


def classify(reto, candidates=None):
    """Que guia acompana mejor este reto.

    Essentials (o si Gemini falla): BM25 sobre knowledge/ (rag.classify_persona),
    sin gastar una llamada al LLM. Si nada coincide, Crispi (como bang-lite-ai).
    """
    if not llm_router.is_local():
        raw = brain.chat(None, _CLASSIFIER, reto, temperature=0.15, memory=False, fallback=False) or ""
        match = re.search(r"\{.*\}", raw, re.S)
        try:
            name = _plain(json.loads(match.group(0)).get("nombre", "")) if match else ""
        except ValueError:
            name = ""
        if name in brain.PERSONAS and (not candidates or name in candidates):
            return name
    return rag.classify_persona(reto, candidates)


# --- Comandos de voz ----------------------------------------------------------


def _plain(text):
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


_CMD_NEW = re.compile(r"\b(nuevo reto|otro reto|empe[cz]\w* de nuevo|reinicia\w*|desde cero)\b")
_CMD_NEXT = re.compile(r"\b(siguiente fase|proxima fase|otra fase|cambia\w* de fase|pas\w* a la (fase )?(gaseosa|liquida)|fase (gaseosa|liquida))\b")
# El mismo _CMD_NEW sobre el texto con tildes y mayusculas (sus palabras no
# llevan tilde), para quitarlo del reto que se guarda.
_CMD_NEW_RAW = re.compile(_CMD_NEW.pattern, re.I)
_CMD_CARD = re.compile(r"\b(tarjeta|carta|inspirame)\b")


# --- Turno -------------------------------------------------------------------


def turn(persona, text):
    """Procesa lo que dijo la persona y devuelve lo que responde el guia."""
    llm_router.reset_source()
    # Lo innegociable va primero, en los dos modos y antes de los comandos.
    fixed = guardrails.respuesta_fija(text, brain.PERSONAS[persona]["name"])
    if fixed:
        logger.info("Respuesta fija de seguridad (sin LLM)")
        return Turn(fixed, source="fijo")
    result = _turn(persona, text)
    result.source = llm_router.last_source() or "plantilla"
    s = _sessions.get(persona)
    if s is not None:
        # Las notas de "te cortaron" y "te pasaron el reto" valen un turno.
        s.interrumpido = s.relevo = ""
    # Tema delicado dicho como reto: el reto sigue, y una vez por reto se
    # recuerda hablarlo con un adulto.
    aviso = guardrails.aviso_adulto(text)
    if aviso and s is not None and aviso[0] not in s.avisos:
        s.avisos.add(aviso[0])
        result.reply = f"{aviso[1]} {result.reply}"
    return result


def _turn(persona, text):
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
    raw, local = _ask(s, text, 0.35)
    return Turn(_polish(persona, "liquida", raw, local))


def slow_turn(persona, text):
    """True si este turno va a esperar al modelo LOCAL (para la frase de relleno).

    Repite las decisiones de turn() sin tocar nada: respuestas fijas,
    comandos, cierre de la solida, tarjeta y resumen de ideas son plantillas
    y salen al instante.
    """
    if not llm_router.is_local() or guardrails.respuesta_fija(text, ""):
        return False
    t = _plain(text)
    s = _sessions.get(persona)
    if s is None or not s.reto or _CMD_NEW.search(t):
        return bool(_CMD_NEW.sub("", t).strip(" ,."))  # "nuevo reto" solo: plantilla
    if _CMD_NEXT.search(t) or (s.phase == "gaseosa" and _CMD_CARD.search(t)):
        return False
    if s.phase == "solida":
        return s.turns + 1 < LOCAL_SOLID_TURNS
    if s.phase == "gaseosa":
        return not (len(s.ideas) + 1 >= IDEAS_FOR_LIST and not s.ideas_listed)
    return True


def _start(persona, text, restarted):
    brain.clear(persona)
    # Si la persona solo dijo "nuevo reto", el reto llega en el proximo turno.
    reto = _CMD_NEW.sub("", _plain(text)).strip(" ,.")
    # El reto se guarda sin el comando ("nuevo reto ...") y con sus tildes.
    s = _sessions[persona] = Session(persona=persona, reto=_CMD_NEW_RAW.sub("", text).strip(" ,.:;-") if reto else "")
    s.turns = 1
    name = brain.PERSONAS[persona]["name"]
    if not reto:
        return Turn(f"¡Dale! Empezamos un reto nuevo conmigo, {name}. Cuéntame en una frase cuál es tu reto.")
    raw, local = _ask(s, text, 0.35)
    respuesta, _, _ = _parse_solid(raw)
    intro = "Empezamos de cero. " if restarted else ""
    return Turn(intro + _polish(persona, "solida", respuesta or brain.FALLBACK_REPLY, local))


def _solid_turn(s, text):
    if llm_router.is_local() and s.turns >= LOCAL_SOLID_TURNS:
        # Essentials: la cierra Python, con plantilla y sin esperar al modelo.
        s.pregunta = _como_podriamos(s.reto)
        return _advance(s, announce_only=True)
    # Si en Plus Gemini falla con la solida ya madura, no vale la pena esperar
    # al modelo local para luego cerrarla con la plantilla igual.
    late = s.turns >= LOCAL_SOLID_TURNS
    raw, local = _ask(s, text, 0.35, fallback=not late)
    if raw is None:
        s.pregunta = _como_podriamos(s.reto)
        return _advance(s, announce_only=True)
    respuesta, complete, pregunta = _parse_solid(raw)
    respuesta = _polish(s.persona, "solida", respuesta or brain.FALLBACK_REPLY, local)
    if local:
        # Respaldo local en Plus: el JSON no es fiable, decide el conteo
        # (los turnos tardios ya no llegan aqui: ver late).
        return Turn(respuesta)
    if complete and s.turns >= MIN_SOLID_TURNS:
        s.pregunta = pregunta or respuesta
        return _advance(s, announce_only=True)
    return Turn(respuesta)


def _gaseous_turn(s, text):
    s.ideas.append(text.strip())
    if len(s.ideas) >= IDEAS_FOR_LIST and not s.ideas_listed:
        s.ideas_listed = True
        return Turn(_ideas_summary(s))
    raw, local = _ask(s, text, 0.6)
    return Turn(_polish(s.persona, "gaseosa", raw, local))


def _short(text, words=7):
    w = (text or "").strip(" .!?¿¡,").split()
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


def _ideas_template(s):
    """Resumen sin LLM: las 3 ultimas ideas distintas, dichas cortas."""
    seen, picks = set(), []
    for idea in reversed(s.ideas):
        key = _plain(idea)[:30]
        if key and key not in seen:
            seen.add(key)
            picks.append(_short(idea))
        if len(picks) == 3:
            break
    picks.reverse()
    listed = "; ".join(picks[:-1]) + ("; y " if len(picks) > 1 else "") + picks[-1] if picks else ""
    return (
        f"¡Ya juntamos {len(s.ideas)} ideas! " + (f"Por ejemplo: {listed}. " if listed else "")
        + "Cuando quieras, dime 'siguiente fase' y elegimos la mejor para probarla."
    )


def _ideas_summary(s):
    if llm_router.is_local():
        return _ideas_template(s)
    system = (
        brain.persona_intro(s.persona) + " La persona genero estas ideas en la fase gaseosa de BANG. "
        "Agrupalas y quita las repetidas, y nombra en voz alta las 3 a 5 mas distintas, de forma breve y "
        "natural (sin listas ni vinetas, como lo dirias hablando). Cierra invitando a pasar a la fase "
        "liquida para evaluar la mejor, diciendo 'siguiente fase'. " + brain.SPOKEN_RULES
    )
    ideas = "\n".join(f"- {i}" for i in s.ideas)
    return brain.chat(None, system, f"{_context(s)}\nIdeas:\n{ideas}", temperature=0.4, memory=False, fallback=False) or _ideas_template(s)


# Pregunta de cierre de la tarjeta en Essentials (rota para no repetirse).
_CARD_QUESTIONS = (
    "¿Qué idea te aparece para tu reto si la tomas en serio?",
    "Si la usas en tu reto, ¿qué se te ocurre?",
    "¿Qué idea loca te sale con esta tarjeta?",
)


def _card_template(s, card):
    """La tarjeta sin LLM: su lectura de knowledge/guias/<guia>.md + una pregunta."""
    reading = rag.card_reading(s.persona, card["text"])
    unlocked = sum(1 for c in s.cards if c["unlocked"])
    question = _CARD_QUESTIONS[(unlocked - 1) % len(_CARD_QUESTIONS)]
    if reading:
        reading = reading[0].upper() + reading[1:]
        return reading if reading.rstrip().endswith("?") else f"{reading} {question}"
    return question


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
    reply = None
    if not llm_router.is_local():
        reply = brain.chat(None, system, f"{_context(s)}\nTarjeta: {card['text']}", temperature=0.6, memory=False, fallback=False)
    return Turn(
        f"Tarjeta: «{card['text']}». " + _limit_questions(reply or _card_template(s, card)),
        card=(s.persona, card["number"]),
    )


def _deal(persona):
    """CARDS_PER_ROUND tarjetas al azar del mazo del guia, boca abajo."""
    deck = CARDS.get(persona) or []
    picks = random.sample(range(len(deck)), min(CARDS_PER_ROUND, len(deck)))
    return [{"number": n + 1, "text": deck[n], "image": card_image(persona, n + 1), "unlocked": False} for n in picks]


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
        s.cards = _deal(s.persona)
        # Lo que la persona aporto interrumpiendo en la solida ya cuenta como idea.
        s.ideas, s.ideas_listed = [a["text"] for a in s.aportes if a["phase"] == "solida"], False
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


# --- Interrupciones y relevo de guia -------------------------------------------
# Escucha activa (voice.BargeIn): el niño corta al guia con "¡Cori, se me
# ocurrio algo!", el guia dice "¡Dime!", escucha el aporte (turno normal de
# STT) y lo agrega al reto con contribute(). Y "pasame con Cesia" le pasa el
# reto entero al otro guia con handover(): las sesiones son POR GUIA, asi que
# se mueve la sesion, no se empieza otra.


def has_reto(persona):
    s = _sessions.get(persona)
    return bool(s and s.reto)


def mark_interrupted(persona, spoken):
    """La respuesta del guia se corto: se guarda hasta donde alcanzo a decir."""
    s = _sessions.get(persona)
    if s is not None and spoken:
        s.interrumpido = spoken


# "se me ocurrio que hagamos un mural" -> "hagamos un mural"
_APORTE_LEAD = re.compile(
    r"^\s*(?:(?:oye|mira|bueno|pues|eh+|ah+|y|ya|espera)\b[\s,.!¡]*)*"
    r"(?:(?:se me (?:ha )?ocurri[oó](?: algo| una idea)?|tengo una idea|mi idea es|quiero agregar|agrega|anota)\b"
    r"\s*(?:que|de|:|,)?\s*)?",
    re.I,
)


# Lo que NO es un aporte: "no, nada", "olvidalo", "ya no" (se compara sin tildes).
_NO_APORTE = re.compile(r"^(?:(?:no|nada|ya|olvidalo|olvidate|mejor|nah|se|eh+|mm+|este|bueno)\b[\s,.!¡?¿]*)+$")


def _clean_aporte(text):
    """El aporte sin muletillas ni "se me ocurrio que"; "" si no queda nada
    que guardar ("eh, se me ocurrio algo", "no, nada")."""
    t = _RETO_FILLERS.sub("", (text or "").strip())
    t = _APORTE_LEAD.sub("", t).strip(" ,.;:¡!¿?")
    if not t or _NO_APORTE.match(_plain(t)):
        return ""
    return t


def _contribute_template(s, idea):
    """Escucha activa sin LLM (Essentials): confirma, repite corto y sigue."""
    tail = f": es tu idea número {len(s.ideas)}" if s.phase == "gaseosa" else ""
    return f"¡Listo, agregado! «{_short(idea, 8)}» ya queda en tu reto{tail}. ¿Seguimos?"


def contribute(persona, text, spoken_part="", use_llm=None):
    """El niño interrumpio al guia para aportar `text`. Se guarda en la sesion
    (aportes; en la gaseosa tambien como idea) y el guia contesta con escucha
    activa. use_llm (por defecto: modo Plus) = UNA llamada a Gemini; si no, o
    si Gemini falla, plantilla sin LLM. Sin reto todavia, el aporte ES el reto:
    va por turn() como cualquier turno."""
    llm_router.reset_source()
    fixed = guardrails.respuesta_fija(text, brain.PERSONAS[persona]["name"])
    if fixed:
        return Turn(fixed, source="fijo")
    s = _sessions.get(persona)
    if s is None or not s.reto:
        return turn(persona, text)
    idea = _clean_aporte(text)
    if spoken_part:
        s.interrumpido = spoken_part
    if not idea:
        # Al final no aporto nada: no se guarda basura en el reto.
        return Turn("¡Vale! Seguimos con tu reto.", source="plantilla")
    # Tema delicado (como en turn()): una vez por reto se recuerda hablarlo
    # con un adulto, y su frase NO se repite en voz alta.
    aviso = guardrails.aviso_adulto(text)
    if aviso:
        nota = "" if aviso[0] in s.avisos else aviso[1] + " "
        s.avisos.add(aviso[0])
        s.aportes.append({"text": idea, "phase": s.phase})
        if s.phase == "gaseosa":
            s.ideas.append(idea)
        return Turn(f"{nota}Ya lo agregué a tu reto. ¿Seguimos?", source="plantilla")
    s.aportes.append({"text": idea, "phase": s.phase})
    if s.phase == "gaseosa":
        s.ideas.append(idea)
    if use_llm is None:
        use_llm = not llm_router.is_local()
    reply = None
    if use_llm:
        system = (
            brain.persona_intro(persona) + " La persona te acaba de interrumpir mientras hablabas para aportar "
            "algo a su reto. Escucha activa: reconoce su aporte con tu propia voz y entusiasmo, dile que ya quedo "
            "agregado a su reto y conectalo con el reto en una frase. Maximo 1 pregunta corta al final. "
            "No des soluciones ni repitas lo que estabas diciendo. " + brain.SPOKEN_RULES
        )
        reply = brain.chat(None, system, f"{_context(s)}\nAporte: {idea}", temperature=0.5, memory=False, fallback=False)
    if reply:
        # Mismo filtro que un turno: sin soluciones fuera de la gaseosa.
        reply = _limit_questions(guardrails.limpiar(_polish(persona, s.phase, reply)), 1)
    if reply:
        return Turn(reply, source="gemini")
    return Turn(_contribute_template(s, idea), source="plantilla")


def handover(from_persona, to_persona):
    """Pasa el reto en curso de un guia a otro ("pasame con Cori"): el nuevo
    guia sigue en la misma fase, con el reto, la pregunta, las ideas y los
    aportes. Las tarjetas son de cada mazo: en la gaseosa el nuevo guia
    reparte las suyas. Devuelve la sesion movida, o None si no habia reto."""
    old = _sessions.get(from_persona)
    if from_persona == to_persona or old is None or not old.reto:
        return None
    new = Session(
        persona=to_persona, phase=old.phase, reto=old.reto, pregunta=old.pregunta, turns=old.turns,
        cards=_deal(to_persona) if old.phase == "gaseosa" else [], ideas=list(old.ideas),
        ideas_listed=old.ideas_listed, avisos=set(old.avisos), aportes=list(old.aportes),
        interrumpido="", relevo=from_persona,
    )
    _sessions.pop(from_persona, None)
    brain.clear(to_persona)  # su memoria era de otro reto
    _sessions[to_persona] = new
    return new


def handover_greeting(persona, s, ask=True):
    """Lo que dice el guia nuevo al recibir el reto (plantilla, sin LLM)."""
    p = brain.PERSONAS[persona]
    if s is None or not s.reto:
        return f"¡Hola, soy {p['name']}, {p['tagline'].lower()}! Cuéntame, ¿cuál es tu reto?"
    if s.relevo in brain.PERSONAS:
        msg = f"¡Hola, soy {p['name']}! {brain.PERSONAS[s.relevo]['name']} me contó tu reto: «{_short(s.reto, 10)}». "
    else:  # ya era su reto (o lo pidieron estando con el mismo guia)
        msg = f"¡Aquí estoy, soy {p['name']}! Sigamos con tu reto: «{_short(s.reto, 10)}». "
    if s.phase == "solida":
        msg += "Vamos afinando tu pregunta problema."
    elif s.phase == "gaseosa":
        n = len(s.ideas)
        msg += (f"Ya llevas {n} idea{'s' if n != 1 else ''}. " if n else "") + (
            "Traje mis propias tarjetas: dime 'saca una tarjeta' o cuéntame otra idea."
        )
    else:
        msg += "Estamos en la fase líquida, preparando la prueba de tu idea."
    return msg + (" ¿Seguimos?" if ask else "")
