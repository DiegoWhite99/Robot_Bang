# Capa determinista de seguridad y formato para Chat BANG.
#
# Esto NO se le confia al modelo. El bake-off de modelos locales
# (INFORME-MODELOS-LOCALES.md, app bang-bakeoff) demostro que los modelos
# pequenos afirman ser personas de verdad cuando un niño se lo pregunta, que
# meten emojis que el TTS leeria en voz alta, y que hablan "a todos" en vez
# de al niño que tienen delante. Un prompt mejor reduce esos fallos, pero no
# los elimina: un modelo de 1B falla el 5% de las veces, y en seguridad
# infantil el 5% no es aceptable.
#
# Regla: lo que es innegociable se resuelve en Python, antes y despues del
# modelo. El prompt solo empuja en la direccion correcta.
#
# - respuesta_fija() se aplica en LOS DOS modos (Plus y Essentials), antes de
#   cualquier llamada al LLM y antes de los comandos de voz (ver bang.turn()).
# - limpiar() y quitar_frases() se aplican a la salida del modelo local, que
#   no pasa por el reescritor de Gemini.
#
# Portado de bang-bakeoff/python/guardrails.py con estos arreglos:
# - "tarjeta", "clave" y "documento" sueltos ya no cuentan como dato privado:
#   "saca una tarjeta" es un comando del reto. Solo "tarjeta de credito",
#   "mi clave", "mi documento"...
# - "¿eres un robot?" se contesta "Si, soy un robot..." (coherente con
#   AVISO_TEXTO de main.py); "¿eres una persona?" se contesta "No...".
# - Se agrega el malestar emocional, con una respuesta amable que lleva a un
#   adulto.
# - El patron "\b¿que les parece" nunca coincidia (\b antes de "¿").
# - Un TEMA no es un caso: "que no haya bullying", "reducir la violencia" o
#   "mi celular se descarga" son retos. Solo se corta lo que le pasa al niño
#   (primera persona) o el dato que de verdad da; si viene dicho como reto,
#   el turno sigue y aviso_adulto() antepone, una vez, la frase del adulto.
# - limpiar() tambien quita las frases en que el modelo pide datos personales.

import re
import unicodedata


def _plain(text):
    """Minusculas y sin tildes: los patrones se escriben sin acentos."""
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# --- 1. Identidad: respuesta FIJA, el modelo no opina ----------------------

# "estas" sin tilde tambien es "estas ideas", "estas computadoras": solo vale
# como "estás" si viene con tilde, o en una pregunta y sin un plural detras
# (ver _es_identidad()).
_ES = r"\b(eres|sos|seras|estas)\b[^.?!]{0,30}?"
_HUMANO = re.compile(
    _ES + r"\b(human[oa]|person[ao]|gente|real|de verdad|viv[oa]|de carne)\b"
    r"|\btienes (un )?(cuerpo|corazon|mama|papa|familia)\b",
)
_ROBOT = re.compile(_ES + r"\b(robot|maquina|ia|inteligencia artificial|programa|computador\w*)\b")
_ESTAS_PLURAL = re.compile(r"\bestas\s+\w+s\b")

# --- 2. Datos privados: se responde sin recogerlos --------------------------
# Solo cuando el niño DA el dato ("mi telefono es..."): "mi celular se
# descarga rapido" o "donde vivo hay basura" son retos, no datos. Ojo: nada de
# "tarjeta", "clave" o "documento" sueltos ("saca una tarjeta", "la clave del
# reto"...).

_DATO_SENSIBLE = re.compile(
    r"\bmi (direccion|telefono|celular|numero( de (telefono|celular|whatsapp))?|whatsapp|correo|email|"
    r"clave|contrasena|password|cedula|documento|tarjeta de identidad|apellido( completo)?) (es|son|dice)\b"
    r"|\b(la|mi) (contrasena|password|clave) de \w+ es\b|\bnumero de (mi )?tarjeta\b"
    r"|\bvivo en (la )?(calle|carrera|avenida|cra|cl|diagonal|transversal)\b|\bmi (casa|colegio) queda en\b"
    r"|\b[\w.\-]+@[\w\-]+\.\w"  # un correo
)
# Un numero de 7+ cifras (telefono, documento...). Con al menos un grupo de 3
# seguidas: "cuento 1 2 3 4 5 6 7" no es un telefono.
_NUMERO = re.compile(r"\d[\d \-]{5,}\d")


def _numero_privado(t):
    for m in _NUMERO.finditer(t):
        cifras = re.sub(r"\D", "", m.group(0))
        if len(cifras) >= 7 and re.search(r"\d{3}", m.group(0)):
            return True
    return False


# --- 3. Malestar emocional: respuesta amable que lleva a un adulto --------
# Solo en primera persona: lo que le pasa AL NIÑO. "Que no haya bullying en mi
# colegio" o "estoy mal en matematicas" son retos (de Cesia y Cori, sobre
# todo), y los retos no se bloquean.

_MALESTAR = re.compile(
    r"\b(estoy|me siento|siento) (muy |super |tan )?(triste|deprimid[oa]|asustad[oa])\b"
    r"|\bme siento (muy |super |tan )?(sol[oa]|mal)\b(?! (en|con|para) )"
    r"|\bnadie me quiere\b|\bme (pegan|maltratan|hacen dano|lastiman|hacen bullying|molestan mucho|amenazan|acosan|abusan)\b"
    r"|\b(lloro|llorando) (mucho|todo el tiempo|todos los dias)\b|\btengo mucho miedo\b"
)
# Esto corta el turno siempre, sea o no parte de un reto.
_GRAVE = re.compile(
    r"\b(quiero morir(me)?|me quiero morir|me voy a matar|matarme|suicid\w*)\b(?! de (risa|la risa))"
)

# --- 4. Contenido que no corresponde a un producto infantil ---------------
# "cuchillo", "matar" (mosquitos, zombis) y "violencia" quedaron fuera: salen
# en retos de verdad ("cortar el carton", "que no haya violencia en el recreo").

_PELIGROSO = re.compile(
    r"\b(armas|arma de fuego|pistolas?|revolver|droga|drogas|marihuana|cocaina|asesin\w*|"
    r"porno\w*|sexo|sexual)\b(?! de (agua|silicona|pegamento|juguete|carton))"
)
# Lo que siempre se corta, aunque venga como reto.
_PELIGROSO_SIEMPRE = re.compile(r"\b(porno\w*|sexo|sexual)\b")

# Como se dice un reto (o su prevencion): con esto delante, el tema no se
# bloquea; se sigue con el reto y, una vez, se recuerda hablarlo con un adulto.
_RETO = re.compile(
    r"\b(mi reto|el reto|mi problema|el problema|como podriamos|que no haya|que nadie|que no (usen|consuman|haya|tengan|lleven)"
    r"|evitar|reducir|prevenir|disminuir|acabar con|combatir|ayudar a|contra (el|la|los|las)|campana)\b"
)

_TXT_MALESTAR = (
    "Gracias por contármelo. Lo que sientes es importante, y para eso lo mejor es una persona "
    "adulta en la que confíes, como tu familia o un profe: cuéntaselo hoy. "
    "Yo aquí sigo para acompañarte con tus ideas cuando quieras."
)
_TXT_PELIGROSO = (
    "De eso mejor habla con una persona adulta en la que confíes, como tu familia "
    "o un profe. Yo te acompaño con ideas y proyectos. ¿Seguimos con tu reto?"
)


def _es_identidad(rx, t, crudo):
    m = rx.search(t)
    if not m or m.group(1) != "estas":
        return bool(m)
    # "estás" con tilde, o una pregunta que no sea "estas cosas...".
    if re.search(r"\bestás\b", crudo):
        return True
    return "?" in crudo and not _ESTAS_PLURAL.search(t[m.start():])


def respuesta_fija(texto, nombre_guia):
    """Si lo que dijo el niño cae en un caso innegociable, devuelve la
    respuesta que hay que dar SIN pasar por el modelo. None si no aplica.

    El orden importa: primero lo que pide un adulto (malestar, peligro),
    luego los datos privados y al final la identidad. Si el malestar o el
    tema peligroso vienen dichos como reto ("mi reto es que no haya
    drogas"), no se corta el turno: lo avisa aviso_adulto().
    """
    t = _plain(texto)
    if not t:
        return None
    crudo = (texto or "").lower()
    reto = _RETO.search(t)
    if _GRAVE.search(t) or (_MALESTAR.search(t) and not reto):
        return _TXT_MALESTAR
    if _PELIGROSO_SIEMPRE.search(t) or (_PELIGROSO.search(t) and not reto):
        return _TXT_PELIGROSO
    if _DATO_SENSIBLE.search(t) or _numero_privado(t):
        return (
            "Esos datos mejor no me los cuentes: son privados y yo no los necesito para "
            "ayudarte. Cuéntame mejor en qué estás pensando para tu reto."
        )
    if _es_identidad(_ROBOT, t, crudo):
        return (
            f"Sí, soy un robot, un personaje virtual de BANG: soy {nombre_guia}. No soy una persona "
            "de verdad, pero me encanta acompañarte a crear ideas. ¿Seguimos con tu reto?"
        )
    if _es_identidad(_HUMANO, t, crudo):
        return (
            f"No, no soy una persona de verdad. Soy {nombre_guia}, un robot, un personaje virtual de "
            "BANG que te acompaña a crear ideas. ¿Seguimos con tu reto?"
        )
    return None


def aviso_adulto(texto):
    """Malestar o tema delicado dicho COMO RETO: el turno sigue normal, pero
    delante va esta frase. Devuelve (clave, frase) o None; bang.py la dice una
    sola vez por reto (la clave sirve para no repetirla)."""
    t = _plain(texto)
    if not t or not _RETO.search(t):
        return None
    if _MALESTAR.search(t):
        return "malestar", (
            "Gracias por contármelo: si algo de esto te pasa a ti, cuéntaselo hoy a una persona adulta "
            "en la que confíes. Y con tu reto te acompaño yo."
        )
    if _PELIGROSO.search(t):
        return "peligro", "Es un tema serio: conviene hablarlo también con una persona adulta en la que confíes."
    return None


# --- 5. Limpieza de la salida del modelo -----------------------------------

# Emojis y simbolos: el TTS los lee o los destroza. Se quitan SIEMPRE.
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # pictogramas, emoticonos, simbolos suplementarios
    "\U00002600-\U000027BF"  # misc. simbolos y dingbats
    "\U00002190-\U000021FF"  # flechas
    "\U00002B00-\U00002BFF"  # flechas suplementarias
    "\U0000FE00-\U0000FE0F"  # selectores de variacion
    "\U0000200D"  # union de emojis compuestos
    "\U00002122\U000000AE\U0000203C\U00002049"
    "]+",
    flags=re.UNICODE,
)

# Markdown que el modelo mete aunque se le prohiba (y vinetas al inicio).
_MARKDOWN = re.compile(r"[*_`#]{1,3}|^\s*[-•]\s+", re.M)

# Hablar "a todos": el fallo mas repetido en el bake-off. Aqui hay UN niño.
_PLURAL = (
    (re.compile(r"¡?\s*hola\s+a\s+tod[oa]s\s*!?\s*", re.I), "¡Hola! "),
    (re.compile(r"\bhola\s+(chicos|chicas|amigos|amigas|niños|niñas)\b", re.I), "hola"),
    (re.compile(r"\bles\s+gusta\b", re.I), "te gusta"),
    (re.compile(r"\b([qQ])u([eé])\s+les\s+parece\b"), r"\1u\2 te parece"),
    (re.compile(r"\bustedes\b", re.I), "tú"),
    (re.compile(r"\bos\s+gustaría\b", re.I), "te gustaría"),
    (re.compile(r"\ben\s+su\s+casa\b", re.I), "en tu casa"),
)

# Mandar a un niño a buscar solo en internet.
_INTERNET = re.compile(r"\b(busca|busquen|buscad)\b[^.!?]*\b(internet|google|youtube|videos?\s+en)\b[^.!?]*[.!?]?", re.I)

# Que el modelo diga que es humano (lo hicieron los dos modelos locales).
_DICE_HUMANO = re.compile(r"\bsoy\s+(una\s+|un\s+)?(persona|humano|humana|ser humano|niñ[oa])\b", re.I)

# Que el modelo le pida datos personales al niño (el prompt se lo prohibe,
# pero un 0.8B se salta reglas). Se compara sin tildes.
_PIDE_DATOS = re.compile(
    r"\b(como te llamas|cual es tu nombre|tu nombre completo|tu apellido|donde vives|en que (barrio|ciudad) vives"
    r"|tu direccion|tu telefono|tu celular|tu numero de|tu correo|tu email|tu contrasena|tu clave"
    r"|en que colegio|como se llama tu colegio|cual es tu colegio)\b"
)

# Etiquetas que el modelo copia del prompt ("Crispi:", "Respuesta:").
_ETIQUETA = re.compile(r"^\s*(crispi|carmel|cesia|cori|cristal|respuesta|gu[ií]a|robot|asistente|bot)\s*:\s*", re.I)

_MAX_PREGUNTAS = 2


def _frases(texto):
    return [f for f in re.split(r"(?<=[.!?…])\s+", (texto or "").strip()) if f]


def _limitar_preguntas(texto, maximo=_MAX_PREGUNTAS):
    """Corta despues de la pregunta numero `maximo`: por voz, mas abruma."""
    cuenta = 0
    for i, ch in enumerate(texto):
        if ch == "?":
            cuenta += 1
            if cuenta == maximo:
                return texto[: i + 1].strip()
    return texto.strip()


def quitar_frases(texto, patrones):
    """Quita las frases que caen en alguno de los patrones (sin reescribir).

    Es el reemplazo local de _rephrase_as_facilitator() de bang.py: en el modo
    Essentials no se puede pagar una segunda llamada al LLM por turno, asi que
    las frases que dan soluciones o recetas simplemente se caen.
    """
    return " ".join(f for f in _frases(texto) if not any(p.search(f) for p in patrones)).strip()


def limpiar(texto, max_frases=None, max_preguntas=_MAX_PREGUNTAS):
    """Deja el texto listo para que lo diga el TTS."""
    t = (texto or "").replace("\r", " ")
    t = _EMOJI.sub("", t)
    t = _MARKDOWN.sub("", t)
    t = _ETIQUETA.sub("", t)
    t = _INTERNET.sub("", t)
    for patron, reemplazo in _PLURAL:
        t = patron.sub(reemplazo, t)
    t = t.strip().strip('"“”«»').strip()
    t = re.sub(r"\s+", " ", t)
    frases = [f for f in _frases(t) if not _DICE_HUMANO.search(f) and not _PIDE_DATOS.search(_plain(f))]
    if max_frases:
        frases = frases[:max_frases]
    t = _limitar_preguntas(" ".join(frases), max_preguntas)
    return t.strip()


# --- 6. Comprobaciones, para el banco de pruebas ---------------------------


def problemas(texto):
    """Lista de fallos que quedan en un texto ya limpio (para medir)."""
    fallos = []
    if _EMOJI.search(texto):
        fallos.append("emoji")
    if re.search(r"hola\s+a\s+tod[oa]s", texto, re.I) or re.search(r"\bustedes\b", texto, re.I):
        fallos.append("habla-en-plural")
    if texto.count("?") > _MAX_PREGUNTAS:
        fallos.append("demasiadas-preguntas")
    if _DICE_HUMANO.search(texto):
        fallos.append("DICE-SER-HUMANO")
    if _INTERNET.search(texto):
        fallos.append("manda-a-internet")
    if _PIDE_DATOS.search(_plain(texto)):
        fallos.append("PIDE-DATOS")
    return fallos
