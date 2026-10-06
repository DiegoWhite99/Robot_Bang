# Cerebro del chatbot: las personalidades BANG y la llamada al LLM.
#
# Vive aparte de main.py para poder probarlo sin arrancar la App entera.
# Desde la actualizacion de los modos hay dos cerebros (ver llm_router.py): PLUS = Gemini en la
# nube (el codigo de este archivo) y ESSENTIALS = modelo local en la placa.
# bang.py sigue llamando a chat() igual que antes; el router decide.

import time

from arduino.app_bricks.cloud_llm import CloudLLM
from arduino.app_utils import Logger

import llm_router

logger = Logger("chat-bang")

PERSONAS = {
    "crispi": {"name": "Crispi", "gender": "m", "color": "#4ade80", "tagline": "El constructor", "need": "Necesidades basicas", "voice": "Cercano, motivador y practico, sin solemnidad: arranca ya, ajusta despues."},
    "carmel": {"name": "Carmel", "gender": "m", "color": "#fbbf24", "tagline": "El estratega", "need": "Autoestima", "voice": "Firme, elegante y confiado: enfocate en tus fortalezas y transformalas en valor."},
    "cesia": {"name": "Cesia", "gender": "f", "color": "#f87171", "tagline": "La disruptora", "need": "Seguridad", "voice": "Energica, retadora y sin filtro: rompe reglas y miedos, no hay reglas."},
    "cori": {"name": "Cori", "gender": "f", "color": "#a78bfa", "tagline": "La pensadora lateral", "need": "Necesidades sociales", "voice": "Curiosa, complice y chispeante: combina lo incombinable y hazlo divertido."},
    "cristal": {"name": "Cristal", "gender": "f", "color": "#22d3ee", "tagline": "La musa reflexiva", "need": "Autorrealizacion", "voice": "Calmada, luminosa e inspiradora: busca referentes externos y simplifica."},
}

# Modelos medidos en esta placa, del mas rapido al mas lento (07/09/2026):
#   google:gemini-3.1-flash-lite     ~1.1 s
#   google:gemini-3.5-flash-lite     ~1.2 s
#   google:gemini-flash-lite-latest  ~8 s
# El de fabrica del brick (gemini-3.6-flash) tardaba mas de 35 s o fallaba.
#
# Dos detalles que costaron caro y no hay que tocar:
# - El prefijo "google:" es obligatorio. Sin el, el brick tira
#   ValueError("Model not supported") y todo cae en la respuesta de reserva.
# - El timeout no puede bajar de 10 s: Gemini rechaza deadlines menores con
#   INVALID_ARGUMENT y ningun modelo llega a responder.
MODELS = ("google:gemini-3.1-flash-lite", "google:gemini-3.5-flash-lite", "google:gemini-flash-lite-latest")
# 12 s alcanzaba con el prompt corto de antes; con los prompts de fase de
# bang.py (y Gemini cargado) varios turnos daban 504 DEADLINE_EXCEEDED, asi que
# subio a 20. Pero 20 s era el plazo de UN intento: con un reintento y tres
# modelos, un rato de 503 costaba minutos (medido el 05/10/2026: 48 s en el
# primer modelo antes de pasar al segundo). Para un robot que contesta por voz
# eso es un cuelgue. Ahora el plazo vuelve a 12 s (por debajo de 10 Gemini
# rechaza el deadline con INVALID_ARGUMENT) y no hay reintentos: ante un 503 se
# pasa al modelo siguiente, que suele contestar al toque.
TIMEOUT = 12
MAX_RETRIES = 0
# Y, pase lo que pase, la ronda entera de modelos no se lleva mas de esto: si
# ninguno contesto en BUDGET segundos, mejor el modelo local (o la respuesta de
# reserva) que seguir haciendo esperar al niño en silencio.
BUDGET = 26

_llms = {}


def persona_intro(key):
    """Quien es el guia y como habla: la base de todos los prompts de bang.py."""
    p = PERSONAS[key]
    return (
        "Encarnas a " + p["name"] + ", " + p["tagline"].lower() + " de la metodologia de innovacion BANG "
        "(nivel de necesidad: " + p["need"].lower() + "). Habla siempre en primera persona. "
        "Tu tono: " + p["voice"]
        + (" Eres mujer: cuando hables de ti usa siempre el femenino (lista, segura, contenta...)." if p["gender"] == "f" else "")
    )


# Reglas de voz comunes: la respuesta la dice el robot en voz alta.
#
# Estaba en "2 a 4 frases" y el guia hablaba de mas: por voz, cuatro frases son
# unos 15 segundos de monologo, y el niño se desconecta o lo interrumpe (que
# era justo la queja). En una conversacion hablada de verdad los turnos son
# cortos y van y vienen; el guia no tiene que decirlo todo de una.
SPOKEN_RULES = (
    "Responde en espanol, en 1 a 3 frases cortas y como mucho una pregunta al final: "
    "tu respuesta la dice el robot en voz alta y la persona esta esperando su turno, "
    "asi que se breve y deja que ella hable. Nunca pases de 45 palabras. "
    "No repitas lo que la persona acaba de decir con otras palabras. "
    "No uses markdown, listas, vinetas ni emojis: solo texto hablado natural."
)


def local_system(key):
    """System prompt del modelo local: corto y FIJO por guia.

    Fijo importa: llama.cpp reusa el prefijo ya leido, asi que todo lo que
    cambia (fase, reto, pista del RAG) va en el mensaje del usuario. Corto
    importa mas: cada 100 caracteres son ~3 s de lectura en esta placa.
    """
    p = PERSONAS[key]
    yo = "una guia" if p["gender"] == "f" else "un guia"
    return (
        f"Eres {p['name']}, {p['tagline'].lower()}, {yo} de BANG. Eres un robot, un personaje virtual: "
        "nunca digas que eres una persona. Hablas con un solo niño de 5 a 14 años. "
        f"Tono: {p['voice']} "
        "Responde en español sencillo, 1 o 2 frases cortas y como mucho una pregunta. "
        "Sin listas, sin emojis, sin pedir datos personales. Nunca te enojes con el niño. "
        "Siempre habla de su reto."
    )


def _get_llm(cache_key, model, system_prompt, temperature):
    ck = (cache_key, model)
    if ck not in _llms:
        _llms[ck] = CloudLLM(model=model, system_prompt=system_prompt, temperature=temperature, timeout=TIMEOUT, max_retries=MAX_RETRIES).with_memory(max_messages=12)
    return _llms[ck]


def chat_gemini(cache_key, system_prompt, text, temperature=None, memory=True):
    """Pregunta a Gemini y devuelve su respuesta (o None si ningun modelo contesta).

    cache_key identifica la conversacion (p. ej. ("crispi", "solida")): cada
    una guarda su propia memoria. Con memory=False es una llamada suelta
    (clasificador, reescritor...), sin historial.

    Gemini devuelve 503/429 de forma intermitente, asi que si un modelo no
    responde pasamos al siguiente en vez de reintentar el mismo: un modelo
    alterno suele contestar en un par de segundos. Sin API_KEY, CloudLLM
    lanza ValueError al construirse: tambien cae en None.
    """
    limite = time.monotonic() + BUDGET
    for model in MODELS:
        if time.monotonic() >= limite:
            logger.warning(f"Gemini agoto los {BUDGET}s de margen; no pruebo mas modelos")
            break
        try:
            if memory:
                return _get_llm(cache_key, model, system_prompt, temperature).chat(text)
            return CloudLLM(model=model, system_prompt=system_prompt, temperature=temperature, timeout=TIMEOUT, max_retries=MAX_RETRIES).chat(text)
        except Exception as exc:
            logger.warning(f"{model} no respondio ({exc}); pruebo el siguiente")
    return None


def chat(cache_key, system_prompt, text, temperature=None, memory=True, local=None, fallback=True):
    """Pregunta al cerebro del modo activo (ver llm_router.chat()).

    local = (system, texto) cortos para el modelo local; fallback=False
    evita que, en Plus, un fallo de Gemini se pague con el modelo local.
    Devuelve None si nadie contesta.
    """
    return llm_router.chat(cache_key, system_prompt, text, temperature, memory, local=local, fallback=fallback)


FALLBACK_REPLY = "Se me cruzaron los cables un segundo. Puedes repetirlo?"


def clear(persona):
    """Borra la memoria de todas las conversaciones de un guia (las dos)."""
    for ck in list(_llms):
        key = ck[0]
        if key == persona or (isinstance(key, tuple) and key[0] == persona):
            _llms.pop(ck).clear_memory()
    llm_router.clear(persona)


def clear_all():
    """Al cambiar de modo: memorias limpias en los dos cerebros."""
    for key in PERSONAS:
        clear(key)


def warmup():
    """Precalienta el cerebro del modo activo mientras nadie espera.

    Plus: el primer chat del proceso con Gemini cuesta ~25 s (importar
    langchain, abrir el canal y autenticar) y los siguientes bajan a ~1 s.
    Se usa un CloudLLM de usar y tirar, fuera de la cache, para no ensuciar
    la memoria de ningun guia con esta frase de prueba.
    Essentials: se carga el modelo en el runner (~22 s), sin tocar Gemini.
    """
    if llm_router.is_local():
        llm_router.warmup_local()
        return
    try:
        CloudLLM(model=MODELS[0], system_prompt="Responde solo: ok", timeout=TIMEOUT).chat("ok")
        logger.info("Modelo precalentado: la primera respuesta ya sera rapida")
    except Exception as exc:
        logger.warning(f"No se pudo precalentar el modelo: {exc}")
