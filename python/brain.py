# Cerebro del chatbot: las personalidades BANG y la llamada al LLM en la nube.
#
# Vive aparte de main.py para poder probarlo sin arrancar la App entera.

from arduino.app_bricks.cloud_llm import CloudLLM
from arduino.app_utils import Logger

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
# bang.py (y Gemini cargado) varios turnos daban 504 DEADLINE_EXCEEDED.
TIMEOUT = 20
# El cliente de Gemini (langchain) reintenta por su cuenta 6 veces, con
# espera creciente, ante un 503 "high demand": una sola respuesta llego a
# tardar 96 s. Con 1 reintento se falla rapido y chat() pasa al modelo
# siguiente, que suele contestar al toque.
MAX_RETRIES = 1

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
SPOKEN_RULES = (
    "Responde en espanol, en 2 a 4 frases cortas: tu respuesta la dice el robot "
    "en voz alta, asi que debe sonar natural al hablarla. "
    "No uses markdown, listas, vinetas ni emojis: solo texto hablado natural."
)


def _get_llm(cache_key, model, system_prompt, temperature):
    ck = (cache_key, model)
    if ck not in _llms:
        _llms[ck] = CloudLLM(model=model, system_prompt=system_prompt, temperature=temperature, timeout=TIMEOUT, max_retries=MAX_RETRIES).with_memory(max_messages=12)
    return _llms[ck]


def chat(cache_key, system_prompt, text, temperature=None, memory=True):
    """Pregunta al LLM y devuelve su respuesta (o None si ningun modelo contesta).

    cache_key identifica la conversacion (p. ej. ("crispi", "solida")): cada
    una guarda su propia memoria. Con memory=False es una llamada suelta
    (clasificador, reescritor...), sin historial.

    Gemini devuelve 503/429 de forma intermitente, asi que si un modelo no
    responde pasamos al siguiente en vez de reintentar el mismo: un modelo
    alterno suele contestar en un par de segundos.
    """
    for model in MODELS:
        try:
            if memory:
                return _get_llm(cache_key, model, system_prompt, temperature).chat(text)
            return CloudLLM(model=model, system_prompt=system_prompt, temperature=temperature, timeout=TIMEOUT, max_retries=MAX_RETRIES).chat(text)
        except Exception as exc:
            logger.warning(f"{model} no respondio ({exc}); pruebo el siguiente")
    return None


FALLBACK_REPLY = "Se me cruzaron los cables un segundo. Puedes repetirlo?"


def clear(persona):
    """Borra la memoria de todas las conversaciones de un guia."""
    for ck in list(_llms):
        key = ck[0]
        if key == persona or (isinstance(key, tuple) and key[0] == persona):
            _llms.pop(ck).clear_memory()


def warmup():
    """Hace la primera llamada a Gemini mientras nadie espera.

    El primer chat del proceso cuesta ~25 s (importar langchain, abrir el
    canal y autenticar) y los siguientes bajan a ~1 s. Se usa un CloudLLM
    de usar y tirar, fuera de la cache, para no ensuciar la memoria de
    ningun guia con esta frase de prueba.
    """
    try:
        CloudLLM(model=MODELS[0], system_prompt="Responde solo: ok", timeout=TIMEOUT).chat("ok")
        logger.info("Modelo precalentado: la primera respuesta ya sera rapida")
    except Exception as exc:
        logger.warning(f"No se pudo precalentar el modelo: {exc}")
