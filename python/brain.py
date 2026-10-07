# Cerebro del chatbot: las personalidades BANG y la llamada al LLM.
#
# Vive aparte de main.py para poder probarlo sin arrancar la App entera.
# Desde la actualizacion de los modos hay dos cerebros (ver llm_router.py): PLUS = Gemini en la
# nube (el codigo de este archivo) y ESSENTIALS = modelo local en la placa.
# bang.py sigue llamando a chat() igual que antes; el router decide.

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

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

# --- Que modelo de Gemini, y como se le pregunta ------------------------------
#
# LA LECCION: el orden de los modelos NO se puede dejar fijo. El 07/09/2026 se
# midio en esta placa y el mas rapido era gemini-3.1-flash-lite (~1,1 s) con
# flash-lite-latest en ~8 s. Un mes despues, el 07/10/2026, era AL REVES:
#
#     modelo                          mediana   fallos de 5
#     google:gemini-flash-lite-latest  0,90 s     0
#     google:gemini-3.5-flash-lite     1,13 s     0   (un pico de 9,3 s)
#     google:gemini-3.1-flash-lite    11,7 s      3   <- iba PRIMERO en la lista
#
# Con el orden fijo, cada turno de Plus esperaba hasta TIMEOUT (12 s) al modelo
# caido antes de probar el siguiente: esa era la "conversacion que no fluye".
#
# Ahora hay dos mecanismos, y entre los dos el turno tarda lo que tarde el
# modelo mas rapido de ESE momento:
#
#   1. RANKING ADAPTATIVO. Cada llamada mide cuanto tardo cada modelo (media
#      movil) y un fallo lo aparta un rato (castigo que crece si repite). La
#      siguiente pregunta empieza siempre por el que mejor va AHORA.
#   2. PETICION ESCALONADA ("hedging"). Se pregunta al mejor; si en HEDGE_S no
#      contesto, se lanza TAMBIEN al segundo, en paralelo, y gana el primero
#      que llegue. Es lo que mata los picos sueltos (el 9,3 s de arriba) sin
#      pagar el doble de llamadas en el caso normal, que contesta en ~1 s.
#
# Detalles que costaron caro y no hay que tocar:
# - El prefijo "google:" es obligatorio. Sin el, el brick tira
#   ValueError("Model not supported") y todo cae en la respuesta de reserva.
# - El timeout no puede bajar de 10 s: Gemini rechaza deadlines menores con
#   INVALID_ARGUMENT y ningun modelo llega a responder.
# - reasoning_effort=0 (apagar el razonamiento) hace FALLAR a estos modelos:
#   medido, ni uno contesto. No se usa.
MODELS = ("google:gemini-flash-lite-latest", "google:gemini-3.5-flash-lite", "google:gemini-3.1-flash-lite")
TIMEOUT = 12
MAX_RETRIES = 0
# Cuanto se espera al mejor modelo antes de lanzar tambien el siguiente. El
# caso normal contesta en ~0,9 s; pasado 1,8 s es que ese intento se atasco.
HEDGE_S = 1.8
# Y, pase lo que pase, la pregunta entera no se lleva mas de esto: si nadie
# contesto, mejor el modelo local (o la respuesta de reserva) que seguir
# haciendo esperar al niño en silencio.
BUDGET = 14

_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="gemini")
_stats_lock = threading.Lock()
# Punto de partida del ranking: el orden de MODELS. warmup() lo corrige con
# medidas reales al arrancar, y cada llamada despues.
_stats = {m: {"ewma": 1.0 + 0.5 * k, "fails": 0, "down_until": 0.0} for k, m in enumerate(MODELS)}


def _ranked():
    """Los modelos en el orden en que conviene preguntarles AHORA."""
    ahora = time.monotonic()
    with _stats_lock:
        return sorted(MODELS, key=lambda m: (ahora < _stats[m]["down_until"], _stats[m]["ewma"]))


def _record(model, ok, dt):
    with _stats_lock:
        st = _stats[model]
        if ok:
            st["ewma"] = 0.6 * st["ewma"] + 0.4 * dt
            st["fails"] = 0
            st["down_until"] = 0.0
        else:
            st["fails"] += 1
            st["ewma"] = max(st["ewma"], float(TIMEOUT))
            # 30 s, 60 s, 120 s... hasta 5 minutos: un modelo caido no se vuelve
            # a probar en cada turno, pero tampoco se olvida para siempre.
            st["down_until"] = time.monotonic() + min(300.0, 30.0 * 2 ** (st["fails"] - 1))


def model_stats():
    """Para /status: como va cada modelo ahora mismo."""
    ahora = time.monotonic()
    with _stats_lock:
        return [
            {"model": m.split(":", 1)[-1], "ms": int(_stats[m]["ewma"] * 1000),
             "apartado_s": max(0, int(_stats[m]["down_until"] - ahora))}
            for m in _ranked_unlocked(ahora)
        ]


def _ranked_unlocked(ahora):
    return sorted(MODELS, key=lambda m: (ahora < _stats[m]["down_until"], _stats[m]["ewma"]))


# --- Memoria de la conversacion ----------------------------------------------
# La memoria la lleva ESTE modulo, no el brick. Antes cada modelo tenia la suya
# (CloudLLM.with_memory), asi que cuando un turno lo contestaba otro modelo —que
# con Gemini pasa a menudo— ese no sabia nada de lo hablado: el guia "se
# olvidaba" del reto a media charla. Con una sola memoria por conversacion,
# cualquier modelo puede contestar cualquier turno, que es lo que permite el
# ranking y la peticion escalonada de arriba.
HIST_TURNS = 6  # intercambios que se recuerdan (= los 12 mensajes de antes)
_HIST_USER_CHARS = 400  # del mensaje del usuario se guarda la COLA: ahi esta lo que dijo el niño
_hist = {}
_hist_lock = threading.Lock()
_clients = {}
_clients_lock = threading.Lock()


def _client(model, system_prompt, temperature):
    """Un CloudLLM SIN memoria por (modelo, prompt, temperatura), reutilizado:
    construirlo cuesta, y la memoria ya va aparte."""
    ck = (model, system_prompt, temperature)
    with _clients_lock:
        c = _clients.get(ck)
        if c is None:
            c = CloudLLM(model=model, system_prompt=system_prompt, temperature=temperature, timeout=TIMEOUT, max_retries=MAX_RETRIES)
            _clients[ck] = c
        return c


def _with_history(cache_key, text):
    with _hist_lock:
        hist = list(_hist.get(cache_key) or ())
    if not hist:
        return text
    lineas = ["Conversación hasta ahora (lo más reciente al final):"]
    for usuario, respuesta in hist:
        lineas.append(f"[Persona] {usuario}")
        lineas.append(f"[Tú] {respuesta}")
    lineas += ["", "Ahora:", text]
    return "\n".join(lineas)


def _remember(cache_key, text, reply):
    with _hist_lock:
        h = _hist.setdefault(cache_key, [])
        h.append((text[-_HIST_USER_CHARS:], reply))
        del h[:-HIST_TURNS]


def _call(model, system_prompt, prompt, temperature):
    t0 = time.monotonic()
    try:
        reply = (_client(model, system_prompt, temperature).chat(prompt) or "").strip()
    except Exception as exc:
        _record(model, False, time.monotonic() - t0)
        logger.warning(f"{model} no respondio en {time.monotonic() - t0:.1f}s ({str(exc)[:80]})")
        return None
    dt = time.monotonic() - t0
    _record(model, bool(reply), dt)
    return reply or None


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
    "No uses markdown, listas, vinetas ni emojis: solo texto hablado natural. "
    # Gemini se iba al español de España ("¡eso mola!", "tío", "guay") y esto es
    # un producto de la CUN para niños colombianos.
    "Habla en español latinoamericano neutro, como en Colombia: nunca uses expresiones "
    "de España como mola, guay, tío, vale, flipar, currar ni vosotros."
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


def chat_gemini(cache_key, system_prompt, text, temperature=None, memory=True):
    """Pregunta a Gemini y devuelve su respuesta (o None si ningun modelo contesta).

    cache_key identifica la conversacion (p. ej. ("crispi", "solida")): cada
    una guarda su propia memoria (_hist). Con memory=False es una llamada suelta
    (clasificador, reescritor...), sin historial.

    Ver arriba el porque: se empieza por el modelo que mejor va AHORA y, si no
    contesta en HEDGE_S, se lanza tambien el siguiente; gana el primero.
    """
    prompt = _with_history(cache_key, text) if memory else text
    orden = _ranked()
    t0 = time.monotonic()
    limite = t0 + BUDGET
    vivos = {}
    siguiente = 0

    def lanzar():
        nonlocal siguiente
        m = orden[siguiente]
        siguiente += 1
        vivos[_pool.submit(_call, m, system_prompt, prompt, temperature)] = m

    lanzar()
    while vivos:
        queda = limite - time.monotonic()
        if queda <= 0:
            logger.warning(f"Gemini agoto los {BUDGET}s de margen")
            break
        espera = min(HEDGE_S, queda) if siguiente < len(orden) else queda
        hechos, _ = wait(list(vivos), timeout=espera, return_when=FIRST_COMPLETED)
        if not hechos:
            if siguiente < len(orden):
                logger.info(f"{vivos[next(iter(vivos))]} tarda mas de {HEDGE_S}s: pregunto tambien a {orden[siguiente]}")
                lanzar()
            continue
        for f in hechos:
            m = vivos.pop(f)
            reply = f.result()
            if reply:
                if memory:
                    _remember(cache_key, text, reply)
                logger.info(f"Gemini: {m.split(':', 1)[-1]} en {time.monotonic() - t0:.2f}s")
                return reply
        # El que termino fallo: el siguiente entra YA, sin esperar el escalon.
        if siguiente < len(orden):
            lanzar()
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
    with _hist_lock:
        for key in list(_hist):
            if key == persona or (isinstance(key, tuple) and key and key[0] == persona):
                _hist.pop(key, None)
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
    # Los TRES a la vez: asi se paga el arranque de cada cliente ahora y, de
    # paso, el ranking sale de una medida real y no del orden de MODELS.
    #
    # DOS rondas: la primera incluye crear el cliente (~4-5 s, una sola vez por
    # proceso) y no dice nada de lo rapido que contesta el modelo. Lo que vale
    # para el ranking es la segunda, que es la que se parece a un turno.
    futs = [_pool.submit(_call, m, "Responde solo: ok", "ok", None) for m in MODELS]
    wait(futs, timeout=40)
    with _stats_lock:
        primera = {m: _stats[m]["ewma"] for m in MODELS}
    futs = [_pool.submit(_call, m, "Responde solo: ok", "ok", None) for m in MODELS]
    wait(futs, timeout=20)
    with _stats_lock:
        for m in MODELS:
            # Si la segunda contesto, manda ella sola (sin arrastrar el arranque).
            if _stats[m]["ewma"] != primera[m] and not _stats[m]["fails"]:
                _stats[m]["ewma"] = (_stats[m]["ewma"] - 0.6 * primera[m]) / 0.4
    logger.info("Modelos precalentados: " + ", ".join(f"{x['model']} {x['ms']} ms" for x in model_stats()))
