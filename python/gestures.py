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
