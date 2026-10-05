# Gestos de la carita: traduce lo que dice el guia en animacion (ojos y boca).
#
# La emocion se detecta por texto y no preguntandole al modelo, para no
# anadir otra llamada (ni latencia) ni depender de que el LLM se acuerde de
# etiquetar su propia emocion.

import re
import threading
import unicodedata

from arduino.app_utils import Bridge, Logger

logger = Logger("chat-bang")

# Deben coincidir con las constantes G_* de sketch/sketch.ino.
REST = 0
TALK = 1
HAPPY = 2       # Cara feliz + manos arriba
SURPRISE = 3    # Cara sorpresa + manos arriba que bajan un poco vibrando
ANGRY = 4       # Cara enojada (un "grrr" jugueton contra el obstaculo) + sube/baja rapido
FRUSTRATED = 5  # ojos de Cara 3 + boca de Cara F ("uff") + sube/baja lento con pausa
SAD = 6         # Cara triste + brazos que caen despacio
# Gestos de 1.1.0: solo mueven los BRAZOS y reusan una cara que ya existe (el
# flash del sketch esta al 90 %, no entran sprites nuevos).
WAVE = 7        # Saluda con un brazo (cara feliz)
CLAP = 8        # Aplaude: los dos brazos arriba y abajo (cara feliz)
THINK = 9       # Un brazo arriba, quieto: "dejame pensarlo" (ojos del "uff")
YES = 10        # Asiente: dos cabezadas cortas (cara feliz)
NO = 11         # Niega: los brazos en espejo (cara neutra)
DANCE = 12      # Baile propio, sin la cancioncita de celebracion (cara feliz)
HUG = 13        # Abrazo: los dos brazos suben despacio y se quedan (cara feliz)
SLEEP = 14      # Dormido: brazos caidos, respiracion lenta (cara triste)
STRETCH = 15    # Se estira (bostezo): hasta arriba, se queda y baja (cara feliz)
# Cuantos valores de gesto caben por personaje, para el empaquetado de send().
# DEBE coincidir con GESTURE_COUNT de sketch/sketch.ino: si cambia aqui, hay
# que reflashear el sketch (o los gestos llegan como otro personaje).
_GESTURE_COUNT = 16

# Visemas (forma de la boca) para el lip-sync de voice.py. Deben coincidir con
# las constantes V_* de sketch/sketch.ino. Los visemas nunca mueven servos.
VISEME_REST = 0  # boca en reposo (la neutra o la de la emocion)
VISEME_AEI = 1
VISEME_BMP = 2
VISEME_CDG = 3   # c, d, g, k, n, s, t, x, y, z
VISEME_CHJ = 4   # ch, sh, j
VISEME_F = 5     # f (la misma boca de la Cara F)
VISEME_L = 6
VISEME_O = 7
VISEME_QW = 8
VISEME_R = 9
VISEME_U = 10

# Deben coincidir con las constantes P_* de sketch/sketch.ino (colores de la
# carita por personaje).
PERSONA_IDS = {
    "crispi": 0,
    "carmel": 1,
    "cesia": 2,
    "cori": 3,
    "cristal": 4,
}

# Se comparan por palabra completa (y sin tildes): "perfectos" no debe
# disparar "perfecto", y "perfecto" quedo fuera a proposito porque en boca de
# estos guias suele aparecer en "no esperes el plan perfecto", que no es
# entusiasmo.
_SAD = (r"lo siento", r"lo lamento", r"que pena", r"me da pena", r"que lastima",
        r"triste\w*", r"tristeza", r"me duele", r"que mal")

# "Uff": cuando el guia habla de algo dificil o de un obstaculo.
# "cuesta" solo en el sentido de dificultad ("me cuesta", "cuesta mucho"): el
# de precio ("¿cuanto cuesta?") sale seguido en la liquida, y "me cuesta
# creerlo" es sorpresa, no frustracion.
_FRUSTRATED = (r"uf+", r"(?:que|esta|es muy|super) dificil", r"complicad[oa]s?",
               r"obstaculos?", r"(?<!cuanto )(?:me|te|le|nos|les) cuestan?(?! creer)",
               r"(?<!cuanto )cuestan? (?:mucho|trabajo|bastante)", r"atasc\w*",
               r"no (?:me |te |nos |les )?sale\w*", r"se traba\w*", r"trabad[oa]s?")

# El "grrr" jugueton SIEMPRE contra el problema, nunca contra el niño. Solo
# cuenta un grrr explicito o un enojo dirigido a un problema/obstaculo/bicho.
_OBSTACLE = (r"problemas?", r"obstaculos?", r"bichos?", r"errore?s?", r"bugs?", r"fallos?",
             r"retos?", r"tornillos?", r"cables?", r"nudos?", r"piezas?", r"motor", r"enredos?")
_ANGRY = (r"gr{2,}", r"me (?:enoja|enfada|da rabia|saca de quicio) (?:ese|este|esa|esta|el|la) "
          r"(?:" + "|".join(_OBSTACLE) + r")")
# Si cerca del enojo aparece la persona con quien se habla, no hay enojo: mas
# vale perder un gesto que parecer bravo con el niño. En español la segunda
# persona suele ir solo en el verbo ("¿por que no me haces caso?"), asi que
# tambien cuentan esos verbos y cualquier pregunta.
_CHILD = (r"tu", r"te", r"ti", r"contigo", r"usted", r"ustedes", r"vos", r"tus",
          r"enojas", r"enojaste", r"enfadas", r"me (?:enojas|enfadas)",
          r"haces", r"hiciste", r"dices", r"dijiste", r"escuchas", r"oyes", r"estas", r"eres",
          r"quieres", r"puedes", r"sabes", r"entiendes", r"(?:no|siempre|nunca) (?:me )?\w+(?:as|es)")
_CHILD_WINDOW = 30  # caracteres alrededor del enojo donde se busca a la persona
_OBSTACLE_WINDOW = 40  # el "grrr" suelto tiene que tener un obstaculo asi de cerca

_SURPRISE = (r"wow", r"vaya", r"increible", r"sorprend\w*", r"no me lo esperaba",
             r"imaginate", r"quien lo diria", r"resulta que", r"de repente")

# "vamos" solo ya no cuenta: sale en "vamos a ver", que no es fiesta.
_HAPPY = (r"genial", r"excelente", r"me encanta\w*", r"brutal", r"buenisim[oa]",
          r"vamos (?:bien|con todo)", r"a darle", r"dale", r"arranca",
          r"que bueno", r"espectacular", r"me fascina", r"muy bien",
          r"que chevere", r"bacan\w*")  # "felicidades" y "bien hecho" pasaron a _CLAP


# --- Gestos de 1.1.0 ----------------------------------------------------------
# Van ANTES que los 7 de siempre en _classify() porque son mas especificos:
# "¡bien hecho!" es un aplauso, no una alegria generica, y "dejame pensarlo"
# es la cara de pensar, no frustracion.

# Saludar: solo al saludar o despedirse de verdad.
_WAVE = (r"hola", r"holaa+", r"buenos dias", r"buenas tardes", r"buenas noches y",
         r"bienvenid[oa]s?", r"mucho gusto", r"un gusto", r"que gusto (verte|saludarte)",
         r"nos vemos", r"hasta luego", r"hasta pronto", r"chao", r"adios")
# Aplaudir: celebrar algo que HIZO el niño.
_CLAP = (r"bien hecho", r"muy bien hecho", r"lo lograste", r"lo lograron", r"lo logramos",
         r"lo conseguiste", r"felicitaciones", r"felicidades", r"bravo", r"aplauso?s?",
         r"te aplaudo", r"excelente trabajo", r"gran trabajo")
# Pensar: justo las frases de relleno de main.py y las dudas en voz alta.
_THINK = (r"dejame pensarlo", r"dejame pensar", r"a ver a ver", r"dame un segundo",
          r"dame un momento", r"estoy pensando", r"dejame ver", r"mmm+", r"mmh+",
          r"veamos", r"pensemoslo")
# Asentir: confirmar lo que dijo el niño.
_YES = (r"si claro", r"claro que si", r"exacto", r"asi es", r"correcto", r"de acuerdo",
        r"por supuesto", r"tal cual", r"eso mismo", r"muy buena esa")
# Negar: corregir sin regañar.
_NO = (r"no es asi", r"todavia no", r"aun no", r"no exactamente", r"me temo que no",
       r"no del todo", r"ahi no", r"nada de eso")
# Bailar (sin la cancioncita: esa va aparte, por arm_step).
_DANCE = (r"a bailar", r"bailemos", r"que baile", r"vamos a bailar", r"a mover el esqueleto")
# Abrazar: animo y cariño.
_HUG = (r"te quiero", r"un abrazo", r"te mando un abrazo", r"animo", r"no estas sol[oa]",
        r"cuenta conmigo", r"aqui estoy (contigo|para ti)", r"estoy orgullos[oa] de ti")
# Dormir: despedida de la noche o "me duermo".
_SLEEP = (r"buenas noches", r"a dormir", r"tengo sueño", r"me esta dando sueño",
          r"que duermas bien", r"dulces sueños")
# Estirarse: el bostezo de volver a arrancar.
_STRETCH = (r"que bostezo", r"me estiro", r"a estirarse", r"ya desperte", r"estoy despiert[oa]")


def _compile(words):
    return re.compile(r"\b(?:" + "|".join(words) + r")\b")


# "eso es" y "adelante" solo cuando celebran: "¡eso es!", "¡adelante!" o
# "adelante" al empezar la frase. "¿por que crees que eso es un problema?" y
# "mas adelante lo probamos" no son fiesta.
_HAPPY_EXCL_RE = re.compile(r"¡\s*(?:eso es|adelante)\b|\b(?:eso es|adelante)\s*!|\beso es todo\b"
                            r"|(?:^|[.!?]\s+)adelante\b")
_SAD_RE = _compile(_SAD)
_FRUSTRATED_RE = _compile(_FRUSTRATED)
_ANGRY_RE = _compile(_ANGRY)
_CHILD_RE = _compile(_CHILD)
_OBSTACLE_RE = _compile(_OBSTACLE)
_SURPRISE_RE = _compile(_SURPRISE)
_HAPPY_RE = _compile(_HAPPY)
# Los de 1.1.0, en el orden en que se miran (el primero que acierta, manda).
_NUEVOS = (
    (CLAP, _compile(_CLAP)),
    (THINK, _compile(_THINK)),
    (HUG, _compile(_HUG)),
    (SLEEP, _compile(_SLEEP)),
    (STRETCH, _compile(_STRETCH)),
    (DANCE, _compile(_DANCE)),
    (WAVE, _compile(_WAVE)),
    (NO, _compile(_NO)),
    (YES, _compile(_YES)),
)

# Frecuencia maxima de los gestos "fuertes", contada en respuestas: el enojo a
# lo sumo una vez cada _ANGRY_EVERY respuestas (si no, baja a FRUSTRATED), y
# la alegria que sale SOLO de signos de exclamacion una vez cada _BANG_EVERY
# (estos guias exclaman casi siempre; si no, la cara feliz no se iria nunca).
_ANGRY_EVERY = 4
_BANG_EVERY = 3
_MIN_BANGS = 2  # cuantos "!" hacen falta para que cuenten como alegria

_BANG_HAPPY = -1  # marca interna de _classify(): alegria que solo viene de los "!"

_state_lock = threading.Lock()
_replies = 0
_last_angry = -_ANGRY_EVERY
_last_bang = -_BANG_EVERY


def _normalize(text):
    """Minusculas y sin tildes, para comparar sin sorpresas."""
    plain = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in plain if not unicodedata.combining(c))


def _playful_anger(t):
    """True si hay un "grrr" contra el problema y no cerca de la persona."""
    found = False
    for m in _ANGRY_RE.finditer(t):
        around = t[max(0, m.start() - _CHILD_WINDOW) : m.end() + _CHILD_WINDOW]
        if _CHILD_RE.search(around) or "?" in around:
            return False  # cualquier duda de que vaya contra el niño: no
        near = t[max(0, m.start() - _OBSTACLE_WINDOW) : m.end() + _OBSTACLE_WINDOW]
        if _OBSTACLE_RE.search(near):
            found = True  # un "grrr" sin obstaculo cerca no se sabe contra quien va
    return found


def _classify(t):
    """Gesto candidato para un texto ya normalizado, sin limites de frecuencia.

    Orden: los gestos de 1.1.0 (aplaudir, pensar, abrazar...), que son frases
    muy concretas; despues tristeza, enojo jugueton, frustracion, sorpresa,
    alegria por palabra y, al final, alegria por "!" (que luego se racionan en
    emotion_of()).
    """
    for gesto, rx in _NUEVOS:
        if rx.search(t):
            return gesto
    if _SAD_RE.search(t):
        return SAD
    if _playful_anger(t):
        return ANGRY
    if _FRUSTRATED_RE.search(t):
        return FRUSTRATED
    if _SURPRISE_RE.search(t):
        return SURPRISE
    if _HAPPY_RE.search(t) or _HAPPY_EXCL_RE.search(t):
        return HAPPY
    if t.count("!") >= _MIN_BANGS:
        return _BANG_HAPPY
    return TALK



def emotion_of(text):
    """Elige el gesto que acompana a una respuesta del guia.

    Nunca devuelve REST: si el guia esta hablando, algo se mueve. Se llama una
    vez por respuesta (main.py), y eso es lo que cuenta para racionar el enojo
    y la alegria de puros signos de exclamacion.
    """
    global _replies, _last_angry, _last_bang
    gesture = _classify(_normalize(text))
    with _state_lock:
        _replies += 1
        if gesture == ANGRY:
            if _replies - _last_angry < _ANGRY_EVERY:
                gesture = FRUSTRATED  # enojarse seguido ya no es jugar
            else:
                _last_angry = _replies
        elif gesture == _BANG_HAPPY:
            if _replies - _last_bang < _BANG_EVERY:
                gesture = TALK
            else:
                gesture = HAPPY
                _last_bang = _replies
    return gesture


def reset_emotions():
    """Olvida los limites de frecuencia (para pruebas o un turno nuevo)."""
    global _replies, _last_angry, _last_bang
    with _state_lock:
        _replies = 0
        _last_angry = -_ANGRY_EVERY
        _last_bang = -_BANG_EVERY


def send(gesture, persona):
    """Avisa al sketch. Sin pantalla (o sin sketch) la App sigue igual.

    Gesto y personaje viajan empaquetados en un solo entero (mismo mecanismo
    de Bridge.notify de un solo valor que ya se usaba), asi el sketch sabe
    con que colores pintar la carita ademas de si esta hablando.
    """
    persona_id = PERSONA_IDS.get(persona, 0)
    encoded = persona_id * _GESTURE_COUNT + int(gesture)
    try:
        Bridge.notify("face_gesture", encoded)
    except Exception as exc:
        logger.debug(f"No se pudo actualizar la carita: {exc}")


def send_viseme(viseme):
    """Forma de la boca ahora (VISEME_*), segun la letra que esta sonando. La
    llama voice.py solo cuando cambia. Reemplaza a send_mouth() cuando el
    sketch tiene visemas; la emocion sigue mandando en ojos y cejas."""
    try:
        Bridge.notify("viseme", int(viseme))
    except Exception as exc:
        logger.debug(f"No se pudo cambiar el visema: {exc}")


def send_mouth(level):
    """Cuan abierta va la boca ahora (0..voice.MOUTH_LEVELS-1), segun el
    volumen de la voz que esta sonando. Es el respaldo de send_viseme(): voice.py
    lo usa solo si no tiene a quien mandarle visemas."""
    try:
        Bridge.notify("mouth_level", int(level))
    except Exception as exc:
        logger.debug(f"No se pudo mover la boca: {exc}")


def send_arm_step(step):
    """Un paso del baile de celebracion (una nota de la melodia): el sketch
    alterna la pose de los dos brazos, como Diome-chan. Lo llama voice.py."""
    try:
        Bridge.notify("arm_step", int(step))
    except Exception as exc:
        logger.debug(f"No se pudieron mover los brazos: {exc}")


def send_splash(on):
    """Prende (True) o apaga (False) la bienvenida de BANG en la pantalla (el
    GIF de assets/img/menu/). Mientras esta prendida, los gestos mueven los
    servos pero la cara recien aparece al apagarla."""
    try:
        Bridge.notify("splash", 1 if on else 0)
    except Exception as exc:
        logger.debug(f"No se pudo cambiar la bienvenida: {exc}")


def send_aviso(on):
    """Prende (True) o apaga (False) el aviso de seguridad de la pantalla.

    Es la PRIMERA pantalla del arranque, antes de la bienvenida de BANG. El
    sketch lo prende solo al bootear; Python lo apaga cuando toca seguir.
    """
    try:
        Bridge.notify("aviso", 1 if on else 0)
    except Exception as exc:
        logger.debug(f"No se pudo cambiar el aviso: {exc}")


def send_wifi(level):
    """Barras de WiFi de la pantalla: 0 = sin conexion (sale un aspa), 1..4.

    Importa porque la conversacion va por la nube: sin WiFi el robot no puede
    responder, y eso se tiene que ver de un vistazo.
    """
    try:
        Bridge.notify("wifi", max(0, min(4, int(level))))
    except Exception as exc:
        logger.debug(f"No se pudo actualizar el WiFi: {exc}")


QR_MAX = 45  # debe coincidir con QR_MAX de sketch/sketch.ino


def send_qr(url=None):
    """Dibuja en la pantalla el QR que lleva al dashboard. Sin url, lo cierra.

    El QR se calcula AQUI y se manda ya resuelto como lista de bytes: meter una
    libreria de QR en el microcontrolador costaria flash, y el flash es justo
    lo que escasea (el aviso ya obligo a recortar cuadros).

    Formato: [tamaño, b0, b1, ...] con los modulos empaquetados a bit, fila por
    fila, que es lo que espera qr() en el sketch.
    """
    if not url:
        try:
            Bridge.notify("qr", [0])
        except Exception as exc:
            logger.debug(f"No se pudo cerrar el QR: {exc}")
        return

    try:
        import qrcode

        q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(url)
        q.make(fit=True)
        matriz = q.get_matrix()
    except Exception as exc:
        logger.warning(f"No pude generar el QR de {url}: {exc}")
        return

    size = len(matriz)
    if size > QR_MAX:
        logger.warning(f"El QR salio de {size} modulos y el maximo es {QR_MAX}: url demasiado larga")
        return

    bits = bytearray((size * size + 7) // 8)
    for y, fila in enumerate(matriz):
        for x, negro in enumerate(fila):
            if negro:
                i = y * size + x
                bits[i >> 3] |= 1 << (7 - (i & 7))

    try:
        Bridge.notify("qr", [size] + list(bits))
    except Exception as exc:
        logger.debug(f"No se pudo mostrar el QR: {exc}")


def send_wifi_sync():
    """Animacion de 'WiFi conectado' en la pantalla (ondas + texto)."""
    try:
        Bridge.notify("wifi_sync", 1)
    except Exception as exc:
        logger.debug(f"No se pudo animar la sincronizacion: {exc}")


# Deben coincidir con sketch/sketch.ino.
MENU_NO_SEL = 254  # los 5 iguales, sin resaltar ninguno
MENU_OFF = 255


def send_menu(persona=None):
    """Muestra el menu de personajes resaltando al que se esta nombrando.

    Sin argumento CIERRA el menu. Para dejarlo abierto con los cinco iguales
    (el estado normal, esperando a que el niño hable) se usa send_menu_idle().

    El resaltado NO es un cursor de seleccion: solo acompaña al repaso hablado.
    El niño elige diciendo el nombre, que listen_turn() ya reconoce.
    """
    value = PERSONA_IDS[persona] if persona in PERSONA_IDS else MENU_OFF
    try:
        Bridge.notify("menu", value)
    except Exception as exc:
        logger.debug(f"No se pudo mostrar el menu: {exc}")


def send_menu_idle():
    """El menu en reposo: los 5 guias iguales, sin nada resaltado, para que no
    parezca que hay que pulsar ni moverse por una lista."""
    try:
        Bridge.notify("menu", MENU_NO_SEL)
    except Exception as exc:
        logger.debug(f"No se pudo mostrar el menu: {exc}")


CARDS_PER_GUIDE = 10  # debe coincidir con tools/make_cards.py (sketch/bang_cards.h)


def send_card(persona=None, number=None):
    """Muestra la tarjeta `number` (1..10) del mazo de `persona` en la
    pantalla, en lugar de la cara, hasta que se llame sin argumentos."""
    if persona in PERSONA_IDS and number and 1 <= int(number) <= CARDS_PER_GUIDE:
        value = PERSONA_IDS[persona] * CARDS_PER_GUIDE + int(number) - 1
    else:
        value = 255  # sacar la tarjeta: vuelve la cara
    try:
        Bridge.notify("card", value)
    except Exception as exc:
        logger.debug(f"No se pudo mostrar la tarjeta: {exc}")


if __name__ == "__main__":
    # Autoprueba de emotion_of(), sin tocar el Bridge:
    #   docker exec -w /app/python robot-bang-stable-main-1 /app/.cache/.venv/bin/python gestures.py
    _NAMES = {REST: "REST", TALK: "TALK", HAPPY: "HAPPY", SURPRISE: "SURPRISE",
              ANGRY: "ANGRY", FRUSTRATED: "FRUSTRATED", SAD: "SAD", WAVE: "WAVE",
              CLAP: "CLAP", THINK: "THINK", YES: "YES", NO: "NO", DANCE: "DANCE",
              HUG: "HUG", SLEEP: "SLEEP", STRETCH: "STRETCH"}
    _CASES = [
        ("Lo siento mucho, eso no salio como querias.", SAD),
        ("¡Qué pena! Pero lo intentamos otra vez.", SAD),
        ("Uff, esto está complicado, pero vamos paso a paso.", FRUSTRATED),
        ("¡Qué difícil! Ese obstáculo no se deja.", FRUSTRATED),
        ("Se atascó el motor y no sale la pieza.", FRUSTRATED),
        ("¡Grrr! Este bicho no se deja, pero lo vamos a vencer.", ANGRY),
        ("Me enoja ese problema, ¡grrr!", FRUSTRATED),  # enojo muy seguido: baja
        ("Grrr, me enojas tú cuando no escuchas.", TALK),  # contra el niño: nunca
        ("¡Grrr, contigo no puedo!", TALK),
        ("¡Grrr! ¿Por qué no me haces caso?", TALK),  # 2a persona solo en el verbo
        ("Grrr, siempre dices lo mismo.", TALK),
        ("¿Cuánto cuesta hacer esa prueba?", TALK),  # precio, no dificultad
        ("¿Cuál sería la versión más barata? ¿Cuánto te cuesta armarla?", TALK),
        ("Me cuesta creerlo, qué idea.", TALK),
        ("Sé que te cuesta mucho, pero sigamos.", FRUSTRATED),
        ("¿Por qué crees que eso es un problema?", TALK),
        ("Más adelante lo probamos.", TALK),
        ("¡Eso es! Ya lo tienes.", HAPPY),
        ("¡Wow! No me lo esperaba.", SURPRISE),
        ("¡Genial! Me encanta tu idea.", HAPPY),
        ("¡Empezamos! ¡Ya mismo!", HAPPY),  # dos "!": alegria (racionada)
        ("¡Listo! ¡Seguimos!", TALK),  # ... y no otra vez enseguida
        # Gestos de 1.1.0 (van antes que los de siempre: son mas especificos)
        ("¡Hola! Soy Crispi.", WAVE),
        ("¡Bienvenida! ¿Cuál es tu reto?", WAVE),
        ("Nos vemos, ¡que te vaya muy bien!", WAVE),
        ("¡Bien hecho! Esa idea quedó redonda.", CLAP),
        ("¡Felicidades! Lo lograste tú solita.", CLAP),
        ("Mmm, déjame pensarlo un momento...", THINK),
        ("A ver, a ver... dame un segundo.", THINK),
        ("Exacto, así es como funciona.", YES),
        ("Claro que sí, probemos eso.", YES),
        ("Todavía no, pero vamos cerquita.", NO),
        ("No es así, mira otra vez el dibujo.", NO),
        ("¡Vamos a bailar un rato!", DANCE),
        ("Ánimo, aquí estoy contigo.", HUG),
        ("Te quiero mucho, de verdad.", HUG),
        ("Buenas noches, que duermas bien.", SLEEP),
        ("¡Qué bostezo! Ya desperté.", STRETCH),
        # ... pero no se disparan con cualquier cosa parecida
        ("Tu idea es genial, de verdad.", HAPPY),  # "genial" sigue siendo alegria
        ("No sé si eso funcione, probemos.", TALK),  # "no" suelto no es negar
        ("Vamos a ver qué dice tu dibujo.", TALK),
        ("Vamos a ver qué pasa con tu prototipo.", TALK),
        ("No es perfecto, pero sirve.", TALK),
    ]
    reset_emotions()
    fallos = 0
    for texto, esperado in _CASES:
        got = emotion_of(texto)
        ok = got == esperado
        fallos += not ok
        print(f"{'ok ' if ok else 'MAL'} {_NAMES[got]:10s} {texto}")
    reset_emotions()
    enojos = [_NAMES[emotion_of("¡Grrr, ese bug!")] for _ in range(_ANGRY_EVERY + 1)]
    print("racion del enojo:", enojos)
    fallos += enojos != ["ANGRY"] + ["FRUSTRATED"] * (_ANGRY_EVERY - 1) + ["ANGRY"]
    print("todo bien" if not fallos else f"{fallos} fallos")
    raise SystemExit(1 if fallos else 0)
