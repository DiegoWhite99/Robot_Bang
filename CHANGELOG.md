# Registro de cambios — Robot BANG

Cambios por actualización. El detalle técnico de cada punto está en
`DOCUMENTACION.md` (se indica la sección).

## Versión 1.1.0 — 2026-10-05

Dos cosas, las dos pensadas para que hablar con el robot se parezca a hablar
con alguien: que **se calle cuando el niño habla**, y que el niño elija **de
qué quiere hablar**.

> **Pendiente antes de dar por cerrada la versión:** nada de esto se probó
> todavía en el robot real (no se reinició la App ni se flasheó el sketch).
> Hacer `arduino-app-cli app restart` y seguir el checklist de
> `DOCUMENTACION.md` §20 (puntos 3-bis, 3-ter, 4 y 5).

### Agregado

- **Modos de conversación BANG / CURIOSO** (§9.4, `python/curioso.py`): al
  arrancar, el robot pregunta en voz alta *"¿BANG o Curioso?"* y el niño
  contesta hablando. **BANG** es el de siempre (el guía acompaña el reto por
  las fases sólida, gaseosa y líquida). **CURIOSO** es un agente libre, tipo
  asistente de voz, pero **con la personalidad del guía**: responde lo que le
  pregunten y obedece órdenes cortas — "ponte feliz", "ponte triste", "ponte
  enojado", "sorpréndete", "ponte normal", "baila", "canta" —, que cambian
  cara y brazos **al instante**, sin pasar por el modelo. Se cambia en
  cualquier momento diciendo "modo curioso" / "modo bang", con el selector
  **🎛 MODO** del dashboard o con `/modo_chat`; también `/elegir_modo` para
  volver a preguntarlo por voz. Se guarda en `data/chat_mode.txt` y **los
  retos en curso no se borran** al cambiar. Mismos guardarraíles de seguridad
  en los dos modos, con las respuestas fijas adaptadas (sin preguntar por "tu
  reto" donde no hay reto).
- **9 gestos nuevos de brazos** (§12, §13.4): **SALUDAR** (un brazo arriba
  moviéndose de lado a lado), **APLAUDIR**, **PENSAR** (un brazo arriba,
  quieto), **ASENTIR**, **NEGAR** (brazos en espejo), **BAILAR** (sin la
  cancioncita), **ABRAZAR**, **DORMIR** (brazos caídos, respiración lenta) y
  **ESTIRARSE** (bostezo). Ninguno trae caras nuevas: solo mueven los brazos
  y toman prestada una cara que ya existe, así que el sketch solo creció
  1.140 bytes (708.120 / 786.432, 90 % de flash).
  - Salen **por lo que dice el guía** ("¡bien hecho!" → aplaudir, "déjame
    pensarlo" → pensar, "¡hola!" → saludar) y **por orden del niño** en modo
    Curioso ("salúdame", "aplaude", "abrázame", "duérmete"...).
  - Ya salen solos en tres momentos del día a día: el guía **saluda** al
    presentarse y al llegar a un relevo, **piensa** mientras dice la frase de
    relleno, y **aplaude** al pasar de fase BANG.
  - Para que quepan, el empaquetado del Bridge pasa de `persona*8 + gesto` a
    `persona*16 + gesto` (`GESTURE_COUNT = 16`, el mismo número en
    `gestures.py` y en `sketch.ino`): **hay que reflashear el sketch**.
  - Nuevo `armMovePair()`: cada brazo va a su propio destino, que es lo que
    permite los gestos asimétricos (saludar, pensar) y los de espejo (negar,
    bailar). El vaivén sostenido también los soporta (`center2`, `mirror`).
  - Una orden solo cuenta **al empezar la frase** y **nunca dentro de una
    pregunta**: "¿por qué la gente aplaude en los conciertos?" se responde,
    no se aplaude.
- **La pregunta del modo se puede interrumpir** y, si el niño contesta con el
  nombre de un guía, ese turno no se pierde (pasa a `loop()`).
- **Pruebas del modo Curioso**: `tools/test_curioso.py` (49 comprobaciones,
  sin hardware ni LLM).

### Cambiado

- **La escucha activa es NATIVA** (§10.6): ya no se prende ni se apaga, y ya
  no hace falta ninguna palabra mágica. **Si el niño habla mientras el guía
  habla, el guía se calla y escucha** (reacción medida: 0,7-1,7 s). Cuando el
  niño ya viene hablando, el guía **no** contesta "¡Dime!": hablarle encima
  es justo lo que había que evitar. El "¡Dime!" se queda para las claves
  cortas ("¡Cori!", "espera"), donde el niño sí está esperando turno.
  - Vosk pasa a **reconocimiento libre** (antes, gramática cerrada: solo oía
    las palabras clave). Dispara con 2 palabras nuevas (3 con bocina
    Bluetooth) que no sean eco del robot ni muletillas.
  - **Nuevo filtro de eco de frase completa** (`_BARGE_ECHO_PHRASE`): sin él,
    el robot se interrumpía a sí mismo cuando Vosk entendía mal su propia voz
    ("Cesia me contó tu reto" → "se seame concedido").
  - **Sin límite de interrupciones encadenadas** (antes se dejaba de escuchar
    a partir de la segunda, `BARGE_MAX_DEPTH`).
  - Si el STT de Google no alcanza a captar la frase, se usa **lo que Vosk ya
    había entendido** en vez de perder el turno.
  - La ventana de energía se alarga a 3 s para los resultados finales de
    Vosk (que llegan cuando el niño ya se calló), y se espera un parcial más
    si la frase va a medias ("quiero hablar con...").
  - **Ruido del micrófono** (visto en la placa real, no en las pruebas): al
    conectarse la bocina Bluetooth, Vosk transcribió el ruido como "tic tic
    tic tic..." y eso cortó la voz. Ahora se piden palabras nuevas
    **distintas**, se descarta la misma palabra repetida 3 veces o más, y se
    ignoran las interrupciones durante los 3 s siguientes a un cambio de
    dispositivo de audio.
  - Alias propios de la escucha activa ("carmen" → Carmel, "concesión" →
    "con Cesia"), que ahora también se aplican al medir la confianza — ese
    bug descartaba interrupciones válidas.
  - Costo: la CPU de Vosk sube de ~0,20-0,35 a **~0,45 s por segundo de
    audio** mientras el guía habla.
- Con **bocina Bluetooth** ya no se exige decir el nombre de un guía: se pide
  una palabra nueva más.
- `tools/test_barge_in.py`: casos nuevos para los tres tipos de disparo y para
  el modo Curioso de `main.py`; el micrófono falso entrega el audio **en
  tiempo real** (antes iba ~10× más rápido que la vida y los resultados no se
  repetían entre corridas).
- `/status` dice el modo de conversación y el estado real de la escucha
  activa; `/help` y el dashboard, al día.

### Quitado

- El selector **✋ INTERRUMPIR: ON / SOLO NOMBRE / OFF** del dashboard y los
  argumentos de `/barge` (`on|off|nombre`), junto con `data/barge_mode.txt`.
  `/barge` sin argumentos sigue mostrando el estado. Interrumpir dejó de ser
  una opción: es parte de cómo funciona el robot.
- La gramática cerrada de Vosk y su lista de ~270 palabras de relleno.

### Limitaciones conocidas (§18.5)

- Sin probar con micrófono y parlante reales: los umbrales
  (`_BARGE_MIN_NOVEL`, `_BARGE_ECHO_PHRASE`, `_BARGE_MIN_RMS`) pueden
  necesitar ajuste en el salón.
- Un nombre a secas ("¡Cori!") se puede entender mal sin la gramática: el
  guía **se calla igual**, pero esa vez no dice "¡Dime!" ni cambia de guía.
- El modo de conversación no se ve en la pantalla del robot (el sketch no se
  tocó: el flash está al 89 %), solo se oye y se ve en el dashboard.
- En modo Curioso, el panel **Reto BANG** del dashboard sigue mostrando el
  último reto guardado.
- Los 9 gestos nuevos **no se han visto moverse en el robot**: las poses y
  los tiempos están pensados sobre el papel y pueden necesitar ajuste
  (constantes `SERVO_*` en `sketch/sketch.ino`). Conviene empezar por
  `/cara` y el bucle de los 16 gestos de §13.4, con la fuente externa puesta.
- Siguen en pie las limitaciones de 1.0.1 (caras, servos y latencia de
  ESSENTIALS sin probar en hardware).

---

## Actualización 1.0.1 — 2026-10-02

> **Numeración (resuelta en 1.1.0):** esta tanda quedó etiquetada 1.0.1 en el
> changelog, pero `APP_VERSION` y el chip del dashboard ya decían `1.1.0`.
> Todo eso sale junto en la **versión 1.1.0**, que es la que se publica y se
> etiqueta en git (`v1.1.0`).

**Pendiente:** nada de esta tanda se probó todavía en el robot real. Hacer
`arduino-app-cli app restart` y seguir el checklist de `DOCUMENTACION.md`
§20.

### Agregado

- **Caras a color desde SVG** (§13.3): los 20 SVG de cada guía
  (`assets/img/<guia>/`) se convierten con `tools/make_face_sprites.py` en
  capas RLE de 15 colores (`sketch/<guia>_face.h`), con hoja de revisión
  `tools/<guia>_preview.png`. Parpadeo de 6 cuadros, 10 visemas y 4
  emociones. Los 5 guías ocupan ~114 KB (antes ~193 KB); el sketch queda en
  706.980 / 786.432 B de flash (89 %).
- **Visemas** (§10.3, §12): la boca sigue al texto (reglas del español) en
  vez de solo al volumen. Nuevo mensaje Bridge `viseme` (0..10);
  `mouth_level` queda de respaldo.
- **7 gestos con movimiento de brazos** (§12, §13.4): REST, TALK, HAPPY
  (manos arriba), SURPRISE (arriba, bajan un poco y vibran), ANGRY ("grrr"
  juguetón: arriba/abajo rápido y errático), FRUSTRATED (sube lento, baja a
  la mitad, pausa), SAD (brazos caídos). Empaquetado `persona*8 + gesto`.
  Reglas del enojo para niños: solo contra un obstáculo, nunca cerca de
  "tú"/segunda persona/preguntas, como mucho 1 de cada 4 respuestas.
- **Calibración de servos** (§13.4): `ARM1_DIR` / `ARM2_DIR` y `ARM_MIN` /
  `ARM_MAX` en `sketch/sketch.ino`.
- **Modos ESSENTIALS / PLUS** (§9.1): selector 🧠 CEREBRO y `/modo`.
  ESSENTIALS usa `llamacpp:Qwen3.5-0.8B-Q4_0` en la placa (brick
  `arduino:llm`), una sola llamada por turno, prompts cortos, plantillas y
  RAG sobre `knowledge/` (`python/rag.py`). PLUS cae solo al modelo local si
  Gemini falla, con cortacircuito de 90 s. La voz (Google STT/TTS) sigue
  necesitando internet en los dos.
- **Guardarraíles deterministas** (§9.3, `python/guardrails.py`): respuestas
  fijas de seguridad en los dos modos (malestar, peligro, datos privados,
  identidad de robot), aviso de adulto una vez por reto cuando el tema
  delicado viene como reto, y limpieza de la salida del modelo.
- **Escucha activa / interrumpir al guía** (§10.6): mientras el guía habla,
  Vosk escucha en la placa con gramática cerrada (costo de API cero). "Cori,
  se me ocurrió algo", "espera", "un momento"... → se calla, dice "¡Dime!"
  (audio en caché) y guarda el aporte en el reto (`bang.contribute`):
  plantilla en ESSENTIALS, una llamada a Gemini en PLUS. Protecciones de eco
  y falsas alarmas; con bocina Bluetooth exige el nombre. Comandos
  `/barge on|off|nombre`, `/interrumpir <texto>` y selector ✋ INTERRUMPIR.
  Modelo `vosk-model-small-es-0.42` en `models/vosk-es/` (no versionado),
  bajado por `tools/install_vosk_model.py`; `vosk==0.3.45` en
  `requirements.txt`.
- **Cambio de guía con relevo** (§7.1): "quiero hablar con", "pásame
  con/a", "cámbiame a", "ahora con", "que hable", "llama a", "habla (con)",
  en turno normal o interrumpiendo. El reto pasa entero al nuevo guía (fase,
  reto, pregunta, ideas, aportes) y saluda con plantilla. Si el guía pedido
  está bloqueado, sigue el anterior.
- **Pruebas sin hardware**: `tools/test_barge_in.py` (114 casos; `--vosk`
  con el reconocedor real) y la autoprueba de `python/gestures.py`.
- **Documentación**: este `CHANGELOG.md`, checklist de pruebas en el robot
  (`DOCUMENTACION.md` §20) y limitaciones conocidas (§18.5).

### Cambiado

- El cambio de guía se detecta antes de asignar la frase al guía del
  follow-up: antes, cualquier frase en los 7 s siguientes se la quedaba el
  guía anterior.
- El dashboard muestra los aportes, la marca de respuesta interrumpida y
  quién contestó cada turno (Gemini, modelo local, respuesta fija,
  plantilla).
- `app.yaml` ya no se versiona; se agrega `app.yaml.example`. La clave va
  en Brick Configuration de App Lab (§5.1).

### Quitado

- La cara geométrica genérica y las caras viejas de 2 bits del sketch.
- La tabla vieja de 4 gestos y la advertencia de "API_KEY en texto plano en
  `app.yaml`" de la documentación.

### Limitaciones conocidas (§18.5)

- Sin probar en hardware (caras, servos, escucha activa, latencia de
  ESSENTIALS, dashboard).
- Tras el "¡Dime!" el STT de Google tarda un momento en arrancar: se pueden
  perder las primeras sílabas del aporte.
- Con bocina Bluetooth la voz tarda ~200 ms o más en callarse.
- Vosk puede ir con retraso mientras el modelo local se precalienta.
- El nombre de un guía sigue siendo wake word en cualquier parte de la
  frase.
- Si `app.yaml` llevaba la clave real en el commit `3adb6ad`, hay que
  rotarla.
