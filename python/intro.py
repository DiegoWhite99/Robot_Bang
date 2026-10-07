# Lo que el robot DICE en el arranque, en un solo sitio.
#
# Es texto fijo (no depende del reto, ni del guia, ni del modo), asi que esta
# GRABADO: tools/make_intro_audio.py lo sintetiza una vez con la voz de la
# presentadora (Chirp3-HD Zephyr) y deja un MP3 por clave en assets/audio/.
# En el arranque suena el MP3 (voice.say_clip()), no el TTS del momento.
#
# El texto se queda aqui igualmente por tres razones, y por eso este modulo
# es la fuente unica:
#   1. es lo que se le manda al dashboard para que se lea en pantalla;
#   2. mueve la boca: voice.say_clip(nombre, text) saca los visemas del texto;
#   3. es el RESPALDO: si falta el MP3 (instalacion nueva, alguien borro
#      assets/audio/) main._locucion() lo sintetiza con la voz del momento.
#      Nunca hay un paso mudo.
#
# Si se cambia un texto de aqui hay que volver a grabarlo:
#     python3 tools/make_intro_audio.py --forzar <clave>

# --- Paso 1: aviso de seguridad ------------------------------------------------
# Va hablado y no solo escrito porque el producto arranca en 5 años, y a esa
# edad todavia no se lee. Cubre los tres limites del producto: que es virtual,
# que no pide datos, y que ante un problema se acude a un adulto.
#
# Este NO esta grabado: suena con musica de fondo por debajo de la voz
# (voice.say_with_music()), que se mezcla con el PCM de la sintesis.
AVISO = (
    "Antes de empezar, dos cositas. Soy un robot, un personaje virtual: no soy una "
    "persona de verdad. No me cuentes datos tuyos como tu dirección, tu teléfono o "
    "tus contraseñas, porque no los necesito. Y si algo te preocupa, cuéntaselo a una "
    "persona adulta en la que confíes. ¡Ahora sí, vamos a crear!"
)

# --- Pasos 2 a 6: las locuciones grabadas -------------------------------------
# El orden de este diccionario es el orden en que se oyen (main._boot_sequence()).
CLIPS = {
    # Paso 2: QUE ES BANG y QUIEN LO HIZO. Es la carta de presentacion del
    # producto, y por eso es la que se pidio grabada: tiene que sonar igual de
    # bien en una feria con mala red que en el laboratorio.
    "intro_bang": (
        "¡Hola! Yo soy BANG, y me creó la CUN, la Corporación Unificada Nacional de Educación "
        "Superior. BANG es la Academia de Innovación de la CUN, y es también una forma de pensar: "
        "aquí convertimos tus retos en ideas que de verdad se pueden construir. "
        "Primero entendemos bien el reto, después lo abrimos en muchas ideas, "
        "y al final elegimos una y la volvemos realidad. Yo te acompaño en todo el camino."
    ),
    # Paso 3: el panel de control, con el QR en la pantalla (gestures.send_qr).
    # Va dirigido al adulto: el QR hay que escanearlo con un celular.
    "intro_panel": (
        "Antes de empezar, una invitación para las personas adultas. Tengo un panel de control "
        "donde se puede ver todo lo que escucho y lo que respondo, cambiar mis ajustes y "
        "escribirme. Apunta la cámara del celular al código que aparece en mi pantalla "
        "y visita el panel de control de Robot BANG."
    ),
    # Paso 5: el menu del cerebro. Es lo PRIMERO que se elige, porque de esto
    # depende si despues hay cinco guias o una sola.
    "menu_cerebro": (
        "Ahora dime cómo quieres que piense. Si dices PLUS, pienso con la inteligencia artificial "
        "de Google, por internet: respondo rápido y te acompañan mis cinco guías. "
        "Si dices ESSENTIAL, pienso solamente aquí dentro, sin internet. "
        "¿PLUS o ESSENTIAL?"
    ),
    "menu_cerebro_repite": "No te escuché bien. Dime PLUS, o dime ESSENTIAL.",
    # Paso 6a: rama PLUS. Explicacion de la app + los cinco guias.
    "intro_plus": (
        "¡Genial, modo PLUS! Te cuento cómo funciono. Tú me cuentas un reto, algo que te gustaría "
        "cambiar o mejorar, y uno de mis cinco guías te acompaña a convertirlo en ideas, paso a paso, "
        "con tarjetas que te van dando pistas. Te presento a mis cinco guías. "
        "Crispi, el constructor: arranca ya y ajusta después. "
        "Carmel, el estratega: encuentra tus fortalezas y las convierte en valor. "
        "Cesia, la disruptora: rompe las reglas y los miedos. "
        "Cori, la pensadora lateral: combina lo incombinable y lo hace divertido. "
        "Y Cristal, la musa reflexiva: te calma, te inspira y simplifica. "
        "Di en voz alta el nombre del guía con el que quieres conversar."
    ),
    # Paso 6b: rama ESSENTIAL. Honestidad por delante: es mas lento, es mas
    # simple, y hay UNA sola guia. Aqui NO se muestra el menu de los cinco
    # (prometer cinco nombres y que conteste siempre Cristal seria mentir).
    "intro_essential": (
        "Modo ESSENTIAL, muy bien. Te cuento con sinceridad cómo funciona. En este modo pienso con "
        "un modelo pequeñito que vive dentro de mí, sin internet, y eso tiene límites: "
        "mis respuestas van a ser más lentas, más cortas y más sencillas, y me puedo equivocar más. "
        "Por eso aquí no están mis cinco guías: en este modo te acompaña solamente Cristal, "
        "la musa reflexiva. Si quieres respuestas más rápidas, más completas, y conocer a los cinco "
        "guías, pídele a una persona adulta que me cambie al modo PLUS. "
        "¡Pero aquí también vamos a crear cosas increíbles! Di Cristal, y cuéntame tu reto."
    ),
}


def text(clave):
    return CLIPS.get(clave, "")
