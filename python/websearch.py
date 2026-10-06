# Busqueda web para el modo Curioso: hechos de verdad, no lo que el modelo recuerde.
#
# POR QUE EXISTE ESTE ARCHIVO
#
# El brick arduino:cloud_llm habla con Gemini, pero le manda un chat pelado: sin
# buscador. Gemini contesta con lo que aprendio hasta su fecha de corte, asi que
# una pregunta de hoy ("¿quien gano ayer?") sale mal de dos maneras, las dos
# comprobadas contra la API con la API_KEY de esta App (05/10/2026):
#
#   gemini-3.5-flash-lite  "no tengo acceso a informacion en tiempo real"
#   gemini-3.1-flash-lite  se invento resultados de la Liga BetPlay, con marcador
#
# La segunda es la peligrosa: un niño se lo cree. Y el grounding propio de Google
# (tools=[{"google_search": {}}]) no es salida: devuelve 429 RESOURCE_EXHAUSTED
# con esta clave, porque la cuota gratuita no incluye busqueda.
#
# Asi que buscamos NOSOTROS antes de preguntarle al modelo y le pasamos lo
# encontrado dentro del prompt. Una sola llamada al LLM: dejarle la busqueda como
# herramienta (CloudLLM(tools=...)) costaria dos viajes mas de ida y vuelta, y
# este robot contesta por voz, donde cada segundo se nota.
#
# Tres fuentes, ninguna pide clave ni dependencias nuevas (todo con urllib):
#   Google Noticias RSS  hechos frescos y fechados ("quien gano ayer")
#   DuckDuckGo lite      busqueda general
#   Wikipedia ES         definiciones ("quien es...", "que es...")
#
# Se piden EN PARALELO con plazos cortos: la busqueda entera cuesta ~2 s, no la
# suma de las tres. Si ninguna contesta se devuelve None y el turno sigue como
# siempre (el modelo contesta de memoria, avisando que puede no estar al dia).

import html
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

from arduino.app_utils import Logger

logger = Logger("chat-bang")

# Plazos. Son cortos a proposito: esto se paga ANTES de que hable el modelo, asi
# que es tiempo que el niño espera callado. Una fuente que no llega, se pierde.
TIMEOUT = 4.0  # s por fuente
TOTAL_TIMEOUT = 6.0  # s para las tres juntas (van en paralelo)
MAX_CHARS = 900  # lo que se le mete al prompt: mas contexto = mas lento el modelo
CACHE_TTL = 300  # s: la misma pregunta dos veces seguidas no se vuelve a buscar

_UA = "Mozilla/5.0 (X11; Linux aarch64) RobotBANG/1.2"
_cache = {}
_cache_lock = threading.Lock()


def _plain(text):
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# --- Cuando hace falta buscar ---------------------------------------------------
#
# Buscar en todo turno seria regalar 2 s a preguntas que no lo necesitan: "¿por
# que el cielo es azul?" la contesta el modelo igual de bien y al instante. Se
# busca solo cuando la pregunta pide un dato que cambia con el tiempo o un hecho
# concreto y verificable.

# Cosas que pasaron (o estan pasando) en una fecha.
_ACTUAL = re.compile(
    r"\b(hoy|ayer|anoche|anteayer|antier|anteanoche|esta\s+(semana|manana|tarde|noche)|"
    r"este\s+(ano|mes|fin\s+de\s+semana)|ahora|ahorita|actual(mente|es)?|"
    r"ultim[oa]s?|reciente(s|mente)?|todavia|aun\s+(sigue|esta)|"
    r"en\s+(2026|2025)|del?\s+2026)\b"
)
# Hechos concretos que conviene no inventar aunque no lleven fecha.
_HECHO = re.compile(
    r"\b(partido|partidos|jugo|jugaron|juega|gano|ganaron|ganador|perdio|perdieron|"
    r"empat[oó]|resultado|resultados|marcador|puntaje|goles?|campeon|campeona|"
    r"mundial|liga|torneo|eliminatorias|selecci[oó]n|"
    r"noticia|noticias|paso|pasaron|ocurrio|sucedio|murio|fallecio|gan[oó]\s+el\s+premio|"
    r"presidente|elecciones|alcalde|gobierno|"
    r"precio|cuesta|cuanto\s+vale|dolar|salario\s+minimo|"
    r"clima|temperatura|llueve|va\s+a\s+llover|"
    r"estreno|estrena|sali[oó]\s+(la|el)\s+(pelicula|cancion|juego)|"
    r"cuantos\s+anos\s+tiene|que\s+edad\s+tiene)\b"
)
# Definiciones: Wikipedia las cubre muy bien y evitan que el modelo se adorne.
_DEFINICION = re.compile(r"\b(quien\s+(es|fue|son|eran)|que\s+(es|son|fue|significa)|donde\s+(queda|esta))\b")

# Lo que NO se busca: charla, opiniones, ordenes y lo del propio robot.
_NO_BUSCAR = re.compile(
    r"\b(te\s+gusta|te\s+parece|crees\s+que|opinas|piensas\s+de|imagina|"
    r"invent[ae]|cuentame\s+un\s+(chiste|cuento|secreto)|adivina|juguemos|"
    r"como\s+estas|como\s+te\s+sientes|quien\s+eres|como\s+te\s+llamas)\b"
)

# Muletillas que no aportan nada a la consulta (y si ruido al buscador).
_RUIDO = re.compile(
    r"\b(oye|oiga|hey|mira|porfa|porfis|por\s+favor|dime|dime\s+una\s+cosa|sabes|"
    r"me\s+puedes\s+decir|puedes\s+decirme|quiero\s+saber|me\s+gustaria\s+saber|"
    r"una\s+pregunta|tengo\s+una\s+pregunta|robot|bang)\b"
)


def necesita(text, personas=()):
    """True si a esta pregunta hay que buscarle los hechos antes de responder."""
    t = _plain(text)
    if not t or len(t) < 8:
        return False
    if _NO_BUSCAR.search(t):
        return False
    return bool(_ACTUAL.search(t) or _HECHO.search(t) or _DEFINICION.search(t))


def consulta(text, personas=()):
    """La frase del niño convertida en algo que se le puede dar a un buscador."""
    t = _plain(text)
    for nombre in personas:
        t = re.sub(r"\b" + re.escape(_plain(nombre)) + r"\b", " ", t)
    t = _RUIDO.sub(" ", t)
    t = re.sub(r"[¿?¡!.,;:]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # "ayer"/"hoy" no le dicen nada al buscador, pero la fecha si.
    if re.search(r"\b(ayer|anoche|anteayer|antier)\b", t):
        t = re.sub(r"\b(ayer|anoche|anteayer|antier)\b", " ", t).strip()
    return t[:160] or _plain(text)[:160]


# --- Las fuentes ----------------------------------------------------------------


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "es-CO,es;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _limpia_html(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).replace("\xa0", " ").strip()


def _hace(pub):
    """'hoy' / 'ayer' / 'hace 3 dias' a partir del pubDate del RSS, o '' si no se
    puede leer. La fecha es justo lo que el modelo necesita para no decir 'ayer'
    sobre algo de hace un mes."""
    try:
        d = parsedate_to_datetime(pub)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        dias = (datetime.now(timezone.utc) - d).days
    except Exception:
        return ""
    if dias <= 0:
        return "hoy"
    if dias == 1:
        return "ayer"
    if dias < 30:
        return f"hace {dias} dias"
    return d.strftime("%d/%m/%Y")


def _news(q):
    """Google Noticias RSS: titulares reales y fechados. La fuente que de verdad
    contesta '¿quien gano ayer?'."""
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=es-419&gl=CO&ceid=CO:es-419"
    xml = _get(url)
    items = re.findall(r"<item>(.*?)</item>", xml, re.S)[:5]
    out = []
    for it in items:
        tit = re.search(r"<title>(.*?)</title>", it, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        if not tit:
            continue
        cuando = _hace(_limpia_html(pub.group(1))) if pub else ""
        titulo = _limpia_html(tit.group(1))
        out.append(f"[noticia{', ' + cuando if cuando else ''}] {titulo}")
    return out


def _ddg(q):
    """DuckDuckGo lite: busqueda general, sin clave ni cuota."""
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(q)
    h = _get(url)
    out = []
    for s in re.findall(r"result-snippet[^>]*>(.*?)</td>", h, re.S)[:4]:
        txt = _limpia_html(s)
        txt = re.sub(r"\s+", " ", txt)
        if len(txt) > 40:
            out.append(f"[web] {txt[:260]}")
    return out


def _wiki(q):
    """Wikipedia en español: la mejor para '¿quien es...?' y '¿que es...?'."""
    url = ("https://es.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=1&srsearch="
           + urllib.parse.quote(q))
    import json

    data = json.loads(_get(url))
    hits = data.get("query", {}).get("search", [])
    if not hits:
        return []
    titulo = hits[0]["title"]
    resumen = json.loads(_get("https://es.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(titulo.replace(" ", "_"))))
    extracto = (resumen.get("extract") or "").strip()
    return [f"[wikipedia: {titulo}] {extracto[:400]}"] if extracto else []


def _fuentes_para(t):
    """Que fuentes vale la pena consultar para esta pregunta. Pedir las tres
    siempre no cuesta tiempo (van en paralelo) pero si llena el prompt de ruido,
    y un prompt mas largo es una respuesta mas lenta."""
    if _ACTUAL.search(t) or _HECHO.search(t):
        return (_news, _ddg) if not _DEFINICION.search(t) else (_news, _ddg, _wiki)
    return (_wiki, _ddg)


def contexto(text, personas=()):
    """Los hechos encontrados, listos para meter en el prompt. None si no hay.

    Nunca lanza: si no hay internet o una fuente cambia de formato, el turno
    sigue sin contexto (el modelo responde de memoria, con su aviso).
    """
    q = consulta(text, personas)
    if not q:
        return None
    ahora = time.monotonic()
    with _cache_lock:
        hit = _cache.get(q)
        if hit and ahora - hit[0] < CACHE_TTL:
            logger.info(f"Busqueda web (en cache): «{q}»")
            return hit[1]

    t0 = time.monotonic()
    fuentes = _fuentes_para(_plain(text))
    lineas = []
    try:
        with ThreadPoolExecutor(max_workers=len(fuentes)) as pool:
            futuros = [(f.__name__, pool.submit(f, q)) for f in fuentes]
            limite = time.monotonic() + TOTAL_TIMEOUT
            for nombre, fut in futuros:
                try:
                    lineas += fut.result(timeout=max(0.1, limite - time.monotonic())) or []
                except Exception as exc:
                    logger.warning(f"Fuente {nombre} sin resultados ({type(exc).__name__}: {str(exc)[:80]})")
    except Exception as exc:  # el pool mismo fallando: no debe tumbar el turno
        logger.warning(f"La busqueda web fallo entera ({exc})")

    if not lineas:
        logger.info(f"Busqueda web sin resultados para «{q}» ({time.monotonic() - t0:.1f}s)")
        return None

    bloque = ""
    for linea in lineas:
        if len(bloque) + len(linea) + 1 > MAX_CHARS:
            break
        bloque += linea + "\n"
    bloque = bloque.strip() or None
    logger.info(f"Busqueda web: «{q}» -> {len(lineas)} resultados en {time.monotonic() - t0:.1f}s")
    with _cache_lock:
        _cache[q] = (ahora, bloque)
        if len(_cache) > 64:  # no crece sin fin en una jornada larga
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:32]:
                _cache.pop(k, None)
    return bloque


# Lo que se le dice al modelo junto con los resultados. Dos ordenes, y las dos
# importan: usar ESTOS datos (no su memoria) y, si no estan, decir que no sabe en
# vez de inventarlos.
def prompt_con_contexto(text, ctx):
    hoy = datetime.now().strftime("%d/%m/%Y")
    return (
        f"Hoy es {hoy}. Busque esto en internet ahora mismo para responderte:\n"
        f"{ctx}\n\n"
        "Responde la pregunta del niño usando SOLO estos datos para los hechos, fechas, nombres y "
        "resultados. No inventes ningun dato que no este arriba: si la respuesta no esta, di con "
        "sencillez que no lo encontraste. Si la pregunta no dice de que equipo, persona o tema habla "
        "y los datos de arriba no coinciden entre si, preguntale a cual se referia en vez de adivinar. "
        "No leas las etiquetas ni los enlaces en voz alta, y no "
        "digas que buscaste: solo cuentale lo que averiguaste, con tu tono de siempre.\n\n"
        f"Pregunta del niño: {text}"
    )
