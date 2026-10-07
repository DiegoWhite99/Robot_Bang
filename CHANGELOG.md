# Registro de cambios — Robot BANG

Cambios por actualización. El detalle técnico de cada punto está en
`DOCUMENTACION.md` (se indica la sección).

## Sin publicar (después de la 1.1.1 stable)

- **Reiniciar el robot** con el botón **🔄 REINICIAR** del dashboard (pide
  confirmación) o con **`/reboot`** en la terminal. El robot se despide en voz
  alta y vuelve solo en 1–2 minutos (es la App de arranque de la placa).
  - Reinicia la **placa entera** si tiene permiso. De fábrica el usuario
    `arduino` no lo tiene (logind pide contraseña), así que hay que darlo
    **una vez**: `bash tools/install_reboot_permission.sh` (pide sudo; instala
    una regla de polkit que solo permite *reiniciar*, nada más).
  - Sin ese permiso, reinicia **la App**, y lo dice: un botón que no hace nada
    sería peor. `/reboot app` reinicia solo la App a propósito (~1 min).
  - Lo hace el ayudante del host (`tools/wifi_helper.py`, nuevo `POST
    /reboot`), porque desde el contenedor no se puede reiniciar nada. Protegido
    con el mismo token que el resto del ayudante.
  - Probado: `/reboot` sin el permiso reinició la App y volvió sola.

## Versión 1.1.1 stable — 2026-10-07

**La versión estable.** Junta todo lo de la 1.1.1 beta (más abajo) con lo de
este día: la conversación en Plus deja de colgarse, el arranque guiado, la voz
Piper, el sonido por emoción y los arreglos del QR. En la pantalla del robot
se lee **"BANG v1.1.1 - stable"**.

### La conversación fluye: de hasta 12 s por turno a ~1 s

Medido en la placa. El problema número uno de Plus **no era la red ni el
prompt: era el orden de los modelos.**

| modelo | mediana | fallos (de 5) |
|---|---|---|
| `gemini-flash-lite-latest` | 0,90 s | 0 |
| `gemini-3.5-flash-lite` | 1,13 s | 0 (un pico de 9,3 s) |
| `gemini-3.1-flash-lite` | **11,7 s** | **3** — e iba **primero** en la lista |

Cada turno esperaba hasta 12 s al modelo caído antes de probar otro. Y el
orden no se puede dejar fijo: un mes antes, el ranking era **el inverso**.

- **Ranking adaptativo** (`brain._ranked()`): cada llamada mide cuánto tardó
  cada modelo, y un fallo lo aparta un rato (30 s, 60 s, 120 s… hasta 5 min).
  La siguiente pregunta empieza siempre por el que mejor va *ahora*. Al
  arrancar se mide en dos rondas (la primera incluye crear el cliente y no
  cuenta).
- **Petición escalonada** (*hedging*): si el mejor modelo no contesta en
  **1,8 s**, se lanza también el siguiente y gana el primero que llegue. Mata
  los picos sueltos sin pagar el doble de llamadas en el caso normal.
- **Una sola memoria por conversación**, en `brain.py` y no en el brick.
  Antes cada modelo tenía la suya: cuando contestaba otro (pasaba a menudo),
  el guía *se olvidaba* del reto a media charla. Probado: recuerda el nombre,
  la edad y el reto del niño aunque cambie el modelo.
- **Resultado:** turnos completos de BANG y Curioso, con clasificador y
  reescritura incluidos, de **0,94 a 2,28 s**. Antes, hasta 12 s o más.
- `/status` muestra cómo va cada modelo ahora mismo.

### La voz arranca antes

- **TTS de Google en *streaming*** (`voice._synthesize_google_stream()`): la
  frase completa llega 0,2–0,3 s antes que con el TTS normal (0,58 s contra
  0,77 s en una frase corta). Se espera la frase entera a propósito: así la
  boca sigue haciendo los visemas igual. Si falla, cae al TTS normal, y tras
  3 fallos seguidos se apaga hasta reiniciar.
- **Las frases de relleno** ("déjame pensarlo…") van **cacheadas a disco**
  como el "¡Dime!": suenan al instante. En Essentials importa doble: antes
  Piper las sintetizaba *mientras* Qwen pensaba, y los dos se peleaban por los
  mismos 4 núcleos.
- El relleno en Plus espera 3,5 s (antes 3,0): con la petición escalonada un
  turno lento contesta en ~2,5–3 s, y el relleno arrancaba justo cuando
  llegaba la respuesta, que entonces tenía que esperar a que terminara.
- **Español de Colombia:** Gemini se iba al de España ("¡eso mola!", "tío").
  Las reglas de voz lo prohíben ahora explícitamente.

### Los arreglos del QR y del arranque

- **El QR apuntaba a Docker** (`192.168.48.3`), una dirección que no existe
  fuera del contenedor. Ahora se le pregunta la IP al ayudante de WiFi del
  host: `10.3.16.177`.
- **El sketch se comía el QR** si el aviso o la bienvenida seguían en
  pantalla. Ahora espera y sale en cuanto la pantalla queda libre.
- **Había dos arranques a la vez** (`/arranque` lanzaba otro encima), y uno le
  pintaba el menú encima al QR del otro. Ahora hay un candado.
- **El QR ya no está en el dashboard:** sirve para *llegar* al dashboard.
- El QR se queda su minuto **mientras** el robot pregunta PLUS/ESSENTIAL, en
  vez de esperar parado. Y la música lo-fi se oye (estaba en graves que el
  parlante no saca) y ya no se recalcula en cada uso (eran ~15 s).
- Del encendido a la pregunta PLUS/ESSENTIAL: **142 s → 83 s**.

> **Pendiente de oír en el robot:** la latencia de Gemini y del TTS está
> medida en la placa, pero no se ha cronometrado una conversación de punta a
> punta con un niño hablando.

## Versión 1.2.0 — 2026-10-07 (publicada como parte de la 1.1.1 stable)

El **arranque** deja de ser una bienvenida y pasa a ser una presentación: el
robot cuenta qué es BANG y quién lo hizo, enseña su panel de control, y
**pregunta con qué cerebro quiere trabajar** antes de presentar a nadie. Y el
**modo Essentials deja de sonar a robot de los 90**: habla con Piper.

> **Pendiente de probar en el robot:** todo esto se verificó sin micrófono ni
> parlante conectados (medidas de Piper, decodificación de las locuciones,
> reconocimiento de PLUS/ESSENTIAL, arranque de la App). **Falta oírlo.**
> Hacer `arduino-app-cli app restart` y seguir el checklist de
> `DOCUMENTACION.md` §20.

### El arranque, contado de principio a fin

La secuencia (`main._boot_sequence()`) pasa a ser:

1. **Aviso de seguridad** — igual que antes: campanilla, música y las tres
   advertencias (soy virtual, no me des tus datos, habla con un adulto).
2. **Qué es BANG y quién lo hizo** — la Academia de Innovación de la **CUN**,
   y cómo se trabaja un reto. Va **grabado** (ver abajo) y con **música
   lo-fi** por debajo.
3. **Red** — solo si no hay internet: el QR del panel de red, como antes.
4. **El panel de control** — el QR del dashboard **un minuto en pantalla**,
   con una locución dirigida a la persona adulta. Antes el panel existía pero
   nadie se enteraba.
5. **PLUS o ESSENTIAL, por voz** — y esto va **antes** de presentar a los
   guías, a propósito: de ello depende si hay cinco o uno solo.
6. **La presentación que toque:**
   - **PLUS** → cómo funciona la app y los **cinco guías**, uno a uno, y el
     menú de caras en la pantalla.
   - **ESSENTIAL** → sus **límites**, dichos de frente (más lento, más corto,
     se equivoca más), que aquí acompaña **solo Cristal**, y que para algo
     mejor hay que pasarse a PLUS. **No se muestra el menú de guías**:
     enseñar cinco caras y contestar siempre con la misma sería mentir.

- **La pregunta de BANG/Curioso sale del arranque.** Se contradecía con el
  paso 6 (le decía al niño que dijera un nombre y acto seguido le preguntaba
  otra cosa) y añadía ~40 s. Sigue cambiándose en cualquier momento diciendo
  **"modo curioso"** / **"modo bang"**, o con `/modo_chat` y `/elegir_modo`.
- **Nuevo comando `/elegir_cerebro`** (alias `/cerebro`): vuelve a preguntar
  PLUS o ESSENTIAL por voz.
- **`llm_router.find_cerebro()`** reconoce la respuesta. "plus" y "essential"
  son palabras difíciles para el reconocimiento (un monosílabo inglés y una
  palabra que se dice "esencial"), así que acepta cómo salen **escritas** de
  verdad: `plas`, `blus`, `escencial`, `especial`, y también "la nube",
  "google", "sin internet", "modo local". 16 casos de prueba, 16 correctos.

### Las locuciones del arranque van grabadas

- **`assets/audio/*.mp3`**: las seis frases fijas del arranque están grabadas
  con la voz de la presentadora (Chirp3-HD Zephyr) por
  `tools/make_intro_audio.py`. Los textos viven en **`python/intro.py`**, que
  es la fuente única: de ahí salen el audio, lo que se lee en el dashboard y
  los visemas de la boca.
- **Por qué grabado:** suena **siempre igual y siempre bien** (es la carta de
  presentación del producto, y se usa en ferias con mala red), no cuesta
  cuota de Google ni los ~2 s de espera por frase, y **suena idéntico en los
  dos modos** — antes, en Essentials, el arranque lo leía la voz local.
- **Nunca hay un paso mudo:** si falta un MP3, `main._locucion()` sintetiza el
  mismo texto con la voz del momento.
- Se decodifican al arrancar (`voice.warm_clips()`), en 0,04–0,22 s cada uno.

### La voz del modo Essentials: Piper

Essentials hablaba con **espeak-ng**, un sintetizador por formantes de los
años 90. Se entendía, pero sonaba a robot de dibujos animados. Ahora habla con
**Piper** (red neuronal VITS sobre onnxruntime): gratis, en la placa y sin
internet, pero con voz de persona.

- **Voz de fábrica: `es_MX-claude-high`** — mujer, latinoamericana, calidad
  alta. Medido en esta placa con el runner del LLM encendido:

  | voz | RTF | sexo | acento |
  |---|---|---|---|
  | `es_MX-claude-high` | **0,53** | mujer | mexicano |
  | `es_ES-sharvard-medium` | 0,67 | mujer | España |
  | `es_MX-ald-medium` | 0,66 | hombre | mexicano |
  | `es_AR-daniela-high` | **4,97** | mujer | argentino |

  **RTF** = segundos de CPU por segundo de audio; por encima de 1 el robot
  tarda más en *preparar* la frase que en decirla.
- **Por qué no `es_AR-daniela-high`,** que era la pedida y la que mejor suena:
  con RTF 4,97 una respuesta de 10 s se hace esperar casi un minuto, y
  Essentials ya es el lento de los dos. No es cuestión de hilos (91,8 s de CPU
  para 27,5 s de reloj: ya usa ~3,3 de los 4 núcleos). Sigue disponible: se
  escribe su nombre en `data/piper_voice.txt` (o en `BANG_PIPER_VOICE`) y el
  robot la baja y la usa.
- **El modelo va fuera de git** (son decenas de MB, como el de Vosk): lo baja
  `tools/install_piper_voice.py` a `models/piper/` la primera vez.
- **espeak-ng no se quita:** queda de respaldo. Si falta el `.onnx` o falla
  onnxruntime, el robot habla igual —feo, pero habla— en vez de quedarse mudo.
- Cargar el modelo cuesta ~9 s, y lo paga `voice.warmup()` al arrancar.

### El robot suena, no solo se ve

- **Un sonido por emoción** (`voice.emotion_sound()`): los 14 gestos con cara
  tienen su propio sonido sintetizado. **Alegría = brillos** (un arpegio de
  campanitas encabalgadas), sorpresa = un "¡uy!" que sube, enojo = un gruñido
  grave y corto (siempre contra el problema, nunca contra el niño), tristeza =
  tres notas que caen, aplauso = tres palmadas, abrazo = un acorde cálido…
  El robot se usa con niños **desde los 5 años, que todavía no leen** y la
  mitad del tiempo no están mirando la pantalla: el sonido es lo que les dice
  cómo se siente.
  - **`REST` y `TALK` no suenan**: se mandan en cada turno, y ponerles sonido
    sería un pitido cada vez que el robot abre la boca.
  - La misma emoción no suena dos veces en 6 s, ni dos sonidos seguidos en
    menos de 1,2 s: en una conversación la emoción se repite mucho y el sonido
    pasaría de marcar a ser un tic. `/gesto` sí las oye todas.
  - Va por *reporter* (`gestures.set_sound_reporter()`), como la boca:
    `voice.py` ya importa `gestures.py`, así que llamarlo al revés sería un
    import circular.
- **Música lo-fi** (`voice._lofi_bed()`) bajo la intro de BANG y el aviso de
  seguridad: acordes cálidos una octava abajo con las voces algo desafinadas,
  un pulso lento a 72 BPM, ruido de vinilo y un pasa-bajos. Todo con numpy,
  sin archivos ni licencias que mirar.

### El panel de control, en el dashboard

- Nueva ventana con el **QR** y la dirección, que Python abre y cierra durante
  el paso 4 del arranque (mensaje `qr_panel`). En la **pantalla del robot** el
  QR se calcula con la IP de ahora, así sigue siendo correcto cuando la placa
  cambia de red.

## Versión 1.1.1 beta — 2026-10-06

Dos frentes: que **hablar con el robot se sienta como hablar con alguien**
(era lo que peor estaba, en un 20 % de satisfacción) y que el modo
**Essentials sea de verdad local**, sin depender de internet para nada.

> **Pendiente antes de dar por cerrada la versión:** todo esto se probó con
> `tools/test_dialogo.py` (136 comprobaciones, sin hardware), pero **no** con
> un niño delante del robot, y **la canción no se ha oído por el parlante**. Hacer `arduino-app-cli app restart` y seguir el
> checklist de `DOCUMENTACION.md` §20.

### El diálogo ya no corta a media idea

- **Fin de frase adaptativo** (`python/voice.py`, `endpoint_wait()`). Era el
  problema número uno: con un plazo fijo de **0,7 s**, cualquier respiración
  del niño cerraba el turno y el guía le contestaba a media frase. Ahora el
  plazo depende de cómo quedó la frase:
  - termina en conector (**"y"**, **"porque"**, **"mi"**, **"nosotros"**…) →
    **2,4 s**, porque la idea no terminó;
  - abre una subordinada sin verbo (**"mi reto es que en el salón"**…) →
    **2,4 s** también;
  - una o dos palabras sueltas → **2,0 s**: todavía está arrancando;
  - termina en punto o interrogación y ya dijo algo con cuerpo → **0,8 s**,
    que es el caso de la mayoría de los turnos y sigue siendo rápido;
  - el resto → **1,5 s**.
  - Y un tope duro de **30 s**, para que nunca se escuche eternamente.
- **El `is_final` de Google ya no devuelve el turno.** Google cierra una frase
  apenas oye una pausita, así que devolver ahí era exactamente lo que cortaba
  al niño. Ahora lo final se acumula y se sigue escuchando: quien manda es el
  silencio.
- **Se mira el micrófono, no solo el texto.** Mientras siga entrando voz
  (`_VOICE_HOLD_S`, 0,5 s), el turno no se cierra aunque la transcripción
  lleve rato quieta. Cubre al que alarga las palabras o duda en voz alta.
- **El guía ya no se calla solo.** La escucha activa se disparaba con el eco
  del propio robot (en los logs: «que necesitas mejor seria conveniente», que
  era el guía oyéndose). Dos cambios: hacen falta **3 palabras nuevas** en vez
  de 2 (4 con bocina Bluetooth), y un parcial de Vosk solo cuenta si el
  siguiente **conserva sus palabras** — la voz real crece parcial a parcial,
  el eco mal entendido cambia de palabras. El nombre del guía y "espera"
  siguen valiendo con una sola palabra.
- **El guía habla menos** (`brain.SPOKEN_RULES`): de "2 a 4 frases" a **1 a 3,
  máximo una pregunta y nunca más de 45 palabras**. Cuatro frases por voz son
  unos 15 segundos de monólogo.
- **Interrumpir ya no pierde el hilo.** En BANG ya se guardaba lo que el guía
  alcanzó a decir; ahora el **modo Curioso también** lo lleva al siguiente
  turno (`curioso.turn(..., interrumpido=...)`), así que el guía retoma donde
  iba y lo engancha con la idea nueva en vez de empezar de cero.

### Essentials, ahora sí, 100 % en la placa

- **Voz local con espeak-ng** (`python/localvoice.py`, nuevo). Voz de mujer,
  gratis y sin internet: **`es-419+f3`** (español latinoamericano, variante
  femenina 3). Suena robótica, y está aceptado. Llega como wheel de pip
  (`espeakng-loader`), con `libespeak-ng.so` y el `es_dict` adentro: **no hace
  falta instalar nada con apt** en el contenedor.
  - Se sintetiza por `ctypes` a PCM y se remuestrea a los 24 kHz del parlante,
    así que **la boca, los visemas y el corte por barge-in siguen funcionando
    igual**: devuelve lo mismo que el TTS de Google.
- **Escucha local con Vosk.** El mismo modelo que ya usaba la escucha activa
  ahora transcribe **el turno entero** en Essentials, con los mismos
  parciales y finales que daba Google: la máquina de estados es una sola.
  Se le suman los alias de cómo Vosk oye los nombres ("carmen" → Carmel),
  que **solo** valen en este modo.
- **Una sola guía: Cristal** (`guides.ESSENTIALS_GUIDE`). Con cinco guías el
  modelo local no da: cada uno tiene su propio *system prompt* y cambiar de
  guía tira la caché de prefijo de llama.cpp, que son ~22 s de recargar. Con
  una sola el turno baja a unos 8 s. Es Cristal porque la musa reflexiva —
  calmada, frases cortas, preguntas en vez de recetas — es la que mejor le
  sienta a un modelo pequeño, y porque es la voz femenina que se pidió.
  Los candados de `/lock_*` siguen ahí y vuelven a mandar al pasar a Plus.
- Al cambiar de modo, si el guía activo no existe en el modo nuevo, se pasa
  al que toca en vez de seguir hablándole a alguien que no está.
- **El modelo ya era el Qwen de la placa** (`arduino:llm`,
  `llamacpp:Qwen3.5-0.8B-Q4_0` en `app.yaml`): no hubo que cambiarlo.

### La canción: "¡A despegar!"

En **los dos modos** el niño puede pedir que cante y suena la canción de
verdad (`assets/audio/`), con **la boca siguiendo la música y los brazos en el
golpe**. Se pide hablando — *"canta"*, *"cántame una canción"*, *"pon música"*,
*"a despegar"* — o con **`/cantar`** desde la terminal del dashboard.

- **`python/song.py`** (nuevo) decodifica el MP3 y le busca el pulso:
  - **Decodificar**: en el contenedor no hay ffmpeg, ni libsndfile, ni rueda de
    `miniaudio` para aarch64. Se usa el ffmpeg que viene **dentro del wheel**
    `imageio-ffmpeg`, igual que espeak-ng viene dentro de `espeakng-loader`:
    sin apt, sin instalar nada en el sistema.
  - **El pulso**: flujo espectral + autocorrelación en numpy puro. La
    autocorrelación sola se quedaba con **80 BPM** (medio tiempo, brazos
    dormidos), así que se añadió la preferencia de tempo de librosa — una
    campana logarítmica centrada en 120 BPM — y ahora da los **122 BPM** reales,
    344 golpes en 169 s.
  - **Caché** en `data/song_cache/`: decodificar y analizar cuesta ~3 s y se
    hace **una vez, al arrancar la App**, en segundo plano. Se rehace sola si
    cambia el MP3.
- **`voice.sing()`**: boca y brazos salen del **mismo bucle de reproducción**,
  que es lo que los deja de verdad sincronizados. Se corta hablando, como el
  baile (son casi tres minutos). Si no hay canción, el guía baila en su lugar
  en vez de dejar la frase colgando.
- **"Cantar" y "bailar" ya son cosas distintas**: antes `canta` caía en el
  mismo sitio que `baila` (el baile corto sintetizado). Ahora `baila` sigue
  siendo ese y `canta` es la canción.
- En BANG es un **recreo**: no gasta turno de fase, no pasa por el LLM y **el
  reto queda intacto** — misma fase, mismas ideas — para seguir al terminar.
  El comando va **anclado al principio** de la frase: sin eso, un reto como
  *"mi reto es que nadie **canta** en el coro"* ponía a cantar al robot en vez
  de escuchar el reto (lo encontró la prueba nueva).

### Más conocimiento para el RAG

De **198 a 342 fragmentos** (+73 %), que es de donde sale la pista que recibe
el modelo local en cada turno:

- `knowledge/conversacion.md` (nuevo): cómo se conversa por voz — turnos
  cortos, dejar hablar, recordar lo que se venía hablando, no hablar de más.
- `knowledge/ejemplos_retos.md` (nuevo): retos reales de un niño de 5 a 14
  años (colegio, casa, amigos, barrio) con lo que el guía hace en cada fase.
  Con un ejemplo concreto delante, el modelo pequeño pregunta mucho mejor.
- `knowledge/guias/cristal.md`: ampliado al doble, porque en Essentials
  **toda** la pista sale de ahí o de los generales.
- `knowledge/bang_metodologia.md` y `comportamiento.md`: cómo se abre un reto,
  cómo se arma la pregunta problema, cuándo cambiar de fase, qué errores no
  comete el guía, qué hacer cuando no sabe algo y datos personales.

### Pruebas

- `tools/test_dialogo.py` (nuevo): **136 comprobaciones**, sin hardware, sin
  red y sin LLM, en seis capas — `endpoint_wait()`, la máquina de estados
  completa de `listen_turn()` **con guiones de tiempos reales** (el caso que
  rompía todo tiene su prueba), barge-in contra el propio eco, interrumpir y
  retomar, Essentials (una guía + voz local), la canción (en los dos modos,
  con su pulso) y RAG.
- Con `--rapido` se salta la capa que corre en tiempo real.
- Se pusieron al día dos pruebas que ya venían fallando antes de esta versión:
  `test_curioso.py` seguía esperando que `baila` fuera la cancioncita corta
  (en 1.1.0 pasó a ser el baile largo) y `test_barge_in.py` tenía un caso con
  bocina Bluetooth que cambia con el mínimo nuevo de 4 palabras.

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
