# Gestos de la carita: traduce lo que dice el guia en animacion (ojos y boca).
#
# La emocion se detecta por texto y no preguntandole al modelo, para no
# anadir otra llamada (ni latencia) ni depender de que el LLM se acuerde de
# etiquetar su propia emocion.

import re
import unicodedata

from arduino.app_utils import Bridge, Logger

logger = Logger("chat-bang")

# Deben coincidir con las constantes G_* de sketch/sketch.ino.
REST = 0
TALK = 1
HAPPY = 2
SURPRISE = 3
_GESTURE_COUNT = 4  # cuantos valores de gesto hay, para el empaquetado de send()

# Deben coincidir con las constantes P_* de sketch/sketch.ino (colores de la
# carita por personaje).
PERSONA_IDS = {
    "crispi": 0,
    "carmel": 1,
    "cesia": 2,
    "cori": 3,
    "cristal": 4,
}

# Se comparan por palabra completa: "perfectos" no debe disparar "perfecto",
# y "perfecto" quedo fuera a proposito porque en boca de estos guias suele
# aparecer en "no esperes el plan perfecto", que no es entusiasmo.
_SURPRISE = (r"wow", r"vaya", r"increible", r"sorprend\w*", r"no me lo esperaba",
             r"imaginate", r"quien lo diria", r"resulta que", r"de repente")

_HAPPY = (r"genial", r"excelente", r"me encanta\w*", r"brutal", r"buenisim[oa]",
          r"vamos", r"dale", r"arranca", r"adelante", r"que bueno", r"eso es",
          r"felicidades", r"espectacular", r"me fascina")


def _compile(words):
    return re.compile(r"\b(?:" + "|".join(words) + r")\b")


_SURPRISE_RE = _compile(_SURPRISE)
_HAPPY_RE = _compile(_HAPPY)


def _normalize(text):
    """Minusculas y sin tildes, para comparar sin sorpresas."""
    plain = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in plain if not unicodedata.combining(c))


def emotion_of(text):
    """Elige el gesto que acompana a una respuesta del guia.

    Nunca devuelve REST: si el guia esta hablando, algo se mueve. El signo de
    exclamacion es la mejor pista de entusiasmo en estos personajes, que
    hablan en tono animado casi siempre.
    """
    t = _normalize(text)
    if _SURPRISE_RE.search(t):
        return SURPRISE
    if "!" in t or _HAPPY_RE.search(t):
        return HAPPY
    return TALK


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


def send_mouth(level):
    """Cuan abierta va la boca ahora (0..voice.MOUTH_LEVELS-1), segun el
    volumen de la voz que esta sonando. Lo llama voice.py bloque a bloque."""
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
