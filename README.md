# 🤖 Robot bang 3

Chatbot de voz con los 5 guías de la metodología de innovación BANG (CUN):
**Crispi**, **Carmel**, **Cesia**, **Cori** y **Cristal**. El robot escucha y
habla por su propio **headset USB** (diadema con micrófono) conectado a un
hub USB-C alimentado — no hace falta ningún celular. Mientras responde, una
carita animada en pantalla TFT mueve ojos y boca, y 2 microservos gesticulan
sincronizados con ella.

> **Versión 1.1.1 stable** (2026-10-07): la **conversación en Plus contesta en ~1 s**
> (antes hasta 12 s: el modelo de Gemini que iba primero estaba caído; ahora
> se elige solo el más rápido de cada momento). El **arranque** cuenta qué es BANG y que lo
> creó la **CUN**, enseña el **QR del panel de control** un minuto, y
> **pregunta por voz PLUS o ESSENTIAL** antes de presentar a nadie — con PLUS
> aparecen los cinco guías; con ESSENTIAL se dicen sus límites de frente y
> acompaña solo **Cristal**. Las locuciones del arranque van **grabadas**
> (`assets/audio/`, con música **lo-fi** por debajo), el modo local ya no
> habla con espeak-ng sino con **Piper** (voz de persona, gratis y sin
> internet), y cada emoción de la carita tiene **su propio sonido** (alegría =
> brillos). Ver `CHANGELOG.md`; el detalle técnico y el checklist de pruebas
> en el robot están en `DOCUMENTACION.md` (§9.4, §10.6, §12 y §20).

## Cómo funciona

- **Backend** (`python/`): todo el turno de voz vive en la placa.
  - `voice.py` — oídos y boca: escucha por streaming con **Google Cloud
    Speech-to-Text** y habla con **Google Cloud Text-to-Speech** (una voz
    Chirp3-HD distinta por guía), con `espeak` como respaldo si Google TTS
    falla. Mueve la boca con **visemas** sacados del texto. Mientras el guía
    habla escucha **localmente con Vosk**: si el niño dice cualquier cosa, el
    guía se calla y escucha (interrupción nativa), y detecta los pedidos de
    cambio de guía. Usa el micrófono/parlante USB vía
    `arduino.app_peripherals`.
  - `brain.py` — personalidades y llamada al LLM. Dos cerebros, elegibles
    desde el dashboard (ver [Modos ESSENTIALS / PLUS](#modos-essentials--plus)):
    **PLUS** = Gemini en la nube (`arduino:cloud_llm`) y **ESSENTIALS** =
    modelo local en la placa (`arduino:llm`), que decide `llm_router.py`.
  - `rag.py` + `knowledge/` — lo que el modelo local "sabe" de BANG y de
    cada guía (búsqueda BM25, sin dependencias).
  - `guardrails.py` — respuestas fijas de seguridad (¿eres un robot?, datos
    privados, peligro, malestar) en los dos modos, y limpieza de la salida
    del modelo local.
  - `bang.py` — la **metodología BANG** (traída de `bang-lite-ai`, repo
    PROYECTOS-IA-CUN-2026): el guía actúa como facilitador y acompaña tu reto
    por las fases **sólida** (formular la pregunta problema), **gaseosa**
    (ideas, con 3 tarjetas del mazo del guía) y **líquida** (prototipo y
    validación), sin darte soluciones y con máximo 2 preguntas por turno.
  - `curioso.py` — el **modo Curioso**: charla libre con la personalidad del
    guía y órdenes cortas ("ponte feliz", "baila"), que se resuelven en la
    placa sin llamar al modelo.
  - `gestures.py` — detecta la emoción del texto (**16 gestos**: reposo,
    hablar, feliz, sorpresa, enojo, frustración, triste, saludar, aplaudir,
    pensar, asentir, negar, bailar, abrazar, dormir y estirarse) y avisa al
    sketch, junto con los visemas.
  - `main.py` — el bucle completo: escuchar → preguntar al LLM → hablar →
    mover la carita; y difunde todo a la web para mostrarlo.
- **Sketch** (`sketch/sketch.ino`): según el gesto y el visema que llegan por
  el Router Bridge, anima la carita a color en la pantalla TFT (los SVG de
  `assets/img/<guia>/`, convertidos por `tools/make_face_sprites.py`; para
  regenerarlas:
  `docker exec robot-bang-stable-main-1 python3 /app/tools/make_face_sprites.py`
  y revisar `tools/<guia>_preview.png`) y mueve los 2 microservos al mismo
  tiempo.
- **Interfaz** (`assets/`): dashboard por **HTTPS local**
  (`https://<IP-DE-LA-PLACA>:7000`, certificado autofirmado de `certs/`: la
  primera vez el navegador avisa → "Avanzado" → continuar). Muestra los
  guías (los bloqueados con 🔒), lo que el robot escucha y responde, el panel
  **Reto BANG** (con los aportes y la marca de interrumpido), los selectores
  **🧠 CEREBRO** y **🎛 MODO (BANG / CURIOSO)**, el estado de la escucha
  activa y un botón **💻 TERMINAL** con comandos.

## Terminal del dashboard

Por ahora **solo Crispi** está desbloqueado. Los demás se desbloquean desde
la terminal (botón 💻 TERMINAL, o la tecla `` ` ``); el estado se guarda en
`data/unlocks.json` y sobrevive a reinicios.

| Comando | Qué hace |
|---|---|
| `/unlock_carmel`, `/unlock_cesia`, `/unlock_cori`, `/unlock_cristal` | desbloquea ese guía |
| `/unlock_all` · `/lock_all` | todos · solo Crispi |
| `/lock_<guia>` | vuelve a bloquearlo (Crispi no se bloquea) |
| `/status` | cerebro, guías desbloqueados, guía activo y por dónde sale el audio |
| `/modo plus` · `/modo essentials` | Gemini en la nube · modelo local en la placa |
| `/modo_chat bang` · `/modo_chat curioso` | modo de conversación: acompañar un reto · charla libre |
| `/elegir_modo` | vuelve a preguntarle al niño por voz qué modo quiere |
| `/barge` | estado de la escucha activa (es nativa: no se apaga) |
| `/interrumpir <texto>` | simula una interrupción para probar sin niño (`/interrumpir cori se me ocurrió algo`) |
| `/bt` | estado de la bocina Bluetooth |
| `/add_bt` | busca equipos Bluetooth (10 s) y los lista numerados |
| `/bt_list` | lista los equipos conocidos, sin buscar |
| `/bt_connect <n>` · `/bt_disconnect <n>` | conecta (o empareja) · desconecta el equipo `n` de la lista |
| `/bt_mode headset` · `/bt_mode music` | mic **y** voz por la bocina (calidad llamada) · solo voz, alta calidad (mic USB) |
| `/test_audio` | pitido de prueba |
| `/cara <guia>` | muestra la cara de un guía y la hace "hablar" 3 s |
| `/reset` | borra el reto en curso |
| `/help` · `/clear` | ayuda · limpiar |

Tolera errores de tipeo (`/unlock_carmerl` → Carmel). Tab autocompleta y
↑/↓ recorre el historial. `/help` lista el resto (`/menu`, `/menu_guias`,
`/aviso`, `/arranque`, `/wifi`, `/qr`, `/tarjeta`, `/bienvenida`...); la
tabla completa está en `DOCUMENTACION.md` §15.

## Modos ESSENTIALS / PLUS

Arriba del dashboard está el selector **🧠 CEREBRO: 💾 ESSENTIALS / ☁️ PLUS**
(pide confirmar; también `/modo` en la terminal). El modo se guarda en la
placa (`data/llm_mode.txt`).

- **PLUS** (por defecto): Gemini en la nube, rápido (~1-3 s). Si Gemini
  falla (sin `API_KEY`, sin cuota, 503), ese turno lo contesta el modelo
  local automáticamente y se avisa en el panel de diagnóstico.
- **ESSENTIALS**: el modelo local `llamacpp:Qwen3.5-0.8B-Q4_0`, sin Gemini.
  Es más lento (del orden de 10-20 s por turno) y más simple: una sola
  llamada por turno, prompts cortos con una pista sacada de `knowledge/`, y
  la fase sólida, las tarjetas y el resumen de ideas los resuelve Python con
  plantillas. Mientras piensa, el guía dice una frase corta ("déjame
  pensarlo un momento..."). **La voz sigue usando internet** (Speech-to-Text
  y Text-to-Speech de Google).
- Al interrumpir para aportar una idea, ESSENTIALS contesta con una
  plantilla ("¡Listo, agregado!...", sin modelo) y PLUS con una sola llamada
  a Gemini. El saludo al cambiar de guía es plantilla en los dos.
- El modelo local corre en un contenedor aparte (`llamacpp-models-runner`),
  reserva hasta ~2,5 GB de RAM y queda encendido también en PLUS.
- Para editar lo que sabe el modelo local: `knowledge/` (ver su `README.md`
  y `DOCUMENTACION.md` §9.2). Los guardarraíles de seguridad están en
  `python/guardrails.py` (`DOCUMENTACION.md` §9.3).

## Modos de conversación: BANG y Curioso

Al empezar, el robot pregunta en voz alta **"¿BANG o Curioso?"** y el niño
contesta hablando:

- **BANG** — el guía lo acompaña a convertir su reto en ideas, por las fases
  sólida, gaseosa y líquida, con tarjetas y aportes. Es el de siempre.
- **CURIOSO** — charla libre, como un asistente de voz, pero con la
  personalidad del guía: responde lo que le pregunten ("¿por qué llueve?") y
  obedece órdenes cortas — **"ponte feliz"**, "ponte triste", "ponte
  enojado", "sorpréndete", "ponte normal", **"baila"**, "canta",
  **"salúdame"**, "aplaude", "piensa", "di que sí", "di que no", "muévete",
  **"abrázame"**, "duérmete", "estírate". Las órdenes cambian la cara y los
  brazos **al instante**, sin pasar por el modelo.

Se puede cambiar cuando se quiera diciendo **"modo curioso"** o **"modo
bang"**, con el selector **🎛 MODO** del dashboard o con `/modo_chat`. El
reto en curso **no se borra** al cambiar: sigue ahí al volver a BANG. Los
guardarraíles de seguridad son los mismos en los dos modos.

## Escucha activa: el guía se calla cuando hablas

Es **nativa**: no se prende ni se apaga. Mientras un guía habla, el robot
escucha **en la placa** con Vosk (sin costo de API):

- **Si el niño habla, el guía se calla y escucha**, diga lo que diga y sin
  contestarle encima. Tarda ~1 s (lo que Vosk necesita para entender dos
  palabras que no sean eco del propio robot).
- **"¡Cori, se me ocurrió algo!"**, "espera", "un momento", "tengo una idea"
  o solo el nombre → además dice **"¡Dime!"** y guarda la idea en el reto
  (panel **Reto BANG → ✋ Aportes**; en la fase gaseosa cuenta como idea). Si
  al final no aporta nada ("no, nada"), no se guarda nada.
- **"pásame con Cesia"** mientras habla otro guía → cambia de guía directo.
- Se puede interrumpir **siempre**, también la respuesta a un aporte y la
  pregunta de qué modo quiere (antes se dejaba de escuchar a partir de la
  segunda interrupción seguida).
- Con la **bocina Bluetooth** como salida hace falta una palabra más para
  cortar (hay mucho más eco) y la voz tarda un poco más en callarse (~200 ms
  o más). `/barge` dice cuántas palabras hacen falta ahora mismo.
- El modelo de Vosk (`vosk-model-small-es-0.42`, ~40 MB) se baja solo a
  `models/vosk-es/` (no versionado) con `tools/install_vosk_model.py`. Si
  falta, el robot sigue funcionando **pero ya no se puede interrumpir**: lo
  avisa en el log, en el dashboard y en `/status`.

## Cambiar de guía

Di **"quiero hablar con Cori"**, "pásame con/a Cristal", "cámbiame a Crispi",
"ahora con Carmel", "que hable Cesia", "llama a Cori" o "habla con Cristal",
en un turno normal o mientras otro guía habla. El nuevo guía aparece en
pantalla, saluda ("¡Hola, soy Cori! Cesia me contó tu reto: «...»") y
**sigue tu reto donde iba**: misma fase, pregunta, ideas y aportes (en la
gaseosa trae sus propias tarjetas). "Por favor" o "gracias" después del
nombre no cuentan como turno. Si el guía pedido está bloqueado, el que ya
estaba te avisa y sigue contigo.

## Bocina Bluetooth

Si hay una bocina Bluetooth conectada a la placa, la voz sale por ahí; si
no, por el headset USB. La App la detecta sola en ~3 s (PipeWire), sin
reiniciar.

**Modo `headset` (por defecto):** la bocina pasa a perfil manos libres (HFP)
y se usa **su micrófono y su parlante**; no hace falta headset USB. Suena a
llamada telefónica. **Modo `music`** (`/bt_mode music`): la voz sale en alta
calidad (A2DP) y el mic es el del headset USB. El modo se guarda en
`data/bt_mode.txt`; el perfil lo fuerza la App con `pw-cli` porque el
autoswitch de WirePlumber está desactivado en esta placa
(`~/.config/wireplumber/wireplumber.conf.d/51-disable-bt-autoswitch.conf`, de
ArmonIA).

Desde la terminal del dashboard: `/add_bt` → `/bt_connect <n>`. Eso lo
hace un servicio del host (`tools/bt_helper.py`, servicio de usuario
`bang-bt-helper`, ya instalado; reinstalar con `bash tools/install_bt_helper.sh`),
porque el contenedor no ve el Bluetooth. Pide el token de `data/.bt_token`.

También a mano, en la placa (no hace falta sudo):

```bash
bash tools/bt_speaker.sh              # desbloquea el Bluetooth, lo prende y conecta la bocina emparejada
bash tools/bt_speaker.sh scan         # busca bocinas nuevas (en modo emparejar)
bash tools/bt_speaker.sh pair <MAC>   # empareja + confía + conecta
```

La bocina `RF-66678` ya está emparejada: basta con prenderla.

## Modo "Alexa" (respuesta rápida)

- La frase se da por terminada apenas lo transcrito deja de cambiar 0,7 s
  (`_ENDPOINT_S` en `voice.py`), sin esperar el cierre de Google (1-2 s más).
- Un pitido corto confirma que escuchó, mientras el LLM piensa.
- Después de que el guía responde, hay **7 s para contestarle sin decir su
  nombre** (`FOLLOW_UP_S`).
- La respuesta se sintetiza por frases en paralelo y empieza a sonar con la
  primera (~1,5 s menos por turno).
- Con un solo guía desbloqueado, "robot" no llama al clasificador.
- En el panel de diagnóstico sale `⏱` con el tiempo del LLM y de la voz.

## Hardware necesario

- **Headset USB** (diadema con micrófono + salida de audio) conectado a...
- ...un **hub USB-C con alimentación externa (5V, 3A o más)**: el puerto
  USB-C de la UNO Q es dual-role (OTG) y al ponerlo en modo *host* para el
  audio deja de recibir energía por ahí, así que el hub tiene que alimentar
  la placa a la vez que aloja el headset.

## Antes de arrancar

**Dos credenciales**, ambas de Google, para dos cosas distintas:

1. **`API_KEY` de Gemini** (para `arduino:cloud_llm`, el modelo de lenguaje
   del modo PLUS). Sale de <https://aistudio.google.com/apikey> y se pone
   desde **App Lab → Bricks → Cloud LLM → `API_KEY`** (Brick Configuration).
   **`app.yaml` no se versiona** (`.gitignore`): el repositorio trae
   `app.yaml.example`; en una placa nueva, `cp app.yaml.example app.yaml` y
   poner la clave desde App Lab. Nunca pegarla en el código ni en un commit.
   El modo ESSENTIALS no la necesita: usa `arduino:llm` con
   `model: llamacpp:Qwen3.5-0.8B-Q4_0`, también declarado en `app.yaml`.

   > ⚠️ Nunca subas `app.yaml` con la clave. Si una clave llega a un commit
   > publicado, **rótala** en el mismo enlace de arriba. (Revisado el
   > 2026-10-02: el historial actual de GitHub no contiene ninguna.)

2. **`google-credentials.json`** (service account de Google Cloud, para
   Speech-to-Text y Text-to-Speech). Va suelto en la **raíz de esta App**
   (junto a `app.yaml`), nunca en `app.yaml` ni versionado (ver
   `.gitignore`). En el proyecto de Google Cloud correspondiente hace falta:
   - **Cloud Text-to-Speech API** habilitada.
   - **Cloud Speech-to-Text API** habilitada (requiere facturación activa,
     aunque tiene cupo gratis mensual).

## Cómo usar

1. Conecta el headset USB al hub alimentado, y el hub a la placa.
2. Arranca la App desde Arduino App Lab (o `arduino-app-cli app start`).
3. Contesta la pregunta del arranque: **"BANG"** (acompañar un reto) o
   **"Curioso"** (charlar libre). Si prefieres, di directamente el nombre de
   un guía y se queda el modo que estuviera.
4. Di **"Crispi"** en voz alta cerca del micrófono, seguido de tu reto (o
   **"Robot, ..."**). Los demás guías responden solo si están desbloqueados
   (ver Terminal).
5. El robot responde hablando por el headset, mientras la carita TFT y los
   servos gesticulan. Durante los 7 s siguientes le puedes contestar sin
   repetir su nombre; después, otra vez con "Crispi" (o "robot"). **Mientras
   habla puedes hablarle sin más: se calla y te escucha**, y también puedes
   pedir otro guía ("pásame con Cori") o cambiar de modo ("modo curioso").
6. Comandos de voz durante el reto (modo BANG):
   - **"saca una tarjeta"** — en la fase gaseosa, voltea una de las 3
     tarjetas de la ronda y el guía te ayuda a aplicarla.
   - **"siguiente fase"** — pasa de fase sin esperar a que el guía la cierre.
   - **"nuevo reto"** — empieza de cero.
   Al pasar de fase, el robot celebra con una cancioncita y un baile de
   brazos (idea del tutorial de Diome-chan).
7. En **modo Curioso**, en vez de los comandos del reto: pregúntale lo que
   quieras y pídele cosas ("ponte feliz", "ponte triste", "baila").
8. Abre `https://<IP-DE-LA-PLACA>:7000` en cualquier navegador de la misma
   red para ver el dashboard y usar la terminal.

## Conexión de los 2 microservos SG90

La pantalla TFT ya ocupa D8, D9, D10, D11 y D13 (ver más abajo), así que los
servos van en **D5** y **D6** (ambos con PWM por hardware en el UNO Q, y
libres).

```
   Servo 1 (SG90)                  Arduino UNO Q            Servo 2 (SG90)
   --------------                  -------------            --------------
   naranja / amarillo (señal) ──────── D5  (~)
                                    D6  (~) ──────── naranja / amarillo (señal)
   rojo               (+5V)   ──────── 5V
   marrón / negro      (GND)   ──────── GND
```

Vista del header del UNO Q (lado digital):

```
        ┌───────────────────────────────────────────┐
        │  ...  ~D6  ~D5   D4  ~D3  ~D2  D1  D0     │
        │        ▲    ▲                             │
        └────────┼────┼─────────────────────────────┘
             naranja  naranja
            (Servo 2)(Servo 1)
```

**Sobre la alimentación.** Cada SG90 consume poco en vacío (~100-250 mA) pero
puede pedir hasta ~700 mA al arrancar o si se traba, y aquí se mueven **2 a
la vez** además de la pantalla TFT. Con esa carga combinada, mejor no
depender del pin **5V** de la placa: alimenta los servos desde una fuente
externa de 5V (o 4 pilas AA) desde el arranque:

```
   Fuente externa 5V (o 4 pilas AA)
        │
        ├── (+5V) ────── rojo del Servo 1 y del Servo 2
        │
        └── (GND) ──┬─── marrón del Servo 1
                    ├─── marrón del Servo 2
                    │
                    └─── GND del Arduino UNO Q     ← ¡la masa debe ser común!

   Señal: naranja del Servo 1 ────── D5 del Arduino
          naranja del Servo 2 ────── D6 del Arduino
```

Lo importante en ese caso: **el GND de la fuente y el GND del Arduino tienen
que ir unidos**, si no los servos no entienden la señal. Si notas que la
placa se reinicia, que los servos tiemblan o que la pantalla parpadea al
moverse, es señal de que la alimentación externa hace falta (o de que el GND
común no está bien conectado).

La señal del UNO Q es de 3,3 V (el MCU es un STM32U585). El SG90 la acepta
sin problema alimentándose a 5 V.

## Gestos

| Gesto | Cuándo (por el texto de la respuesta) | Cara | Brazos |
|---|---|---|---|
| `TALK` | siempre que el guía habla | neutra + visemas | vaivén corto (80°-100°) |
| `HAPPY` | "genial", "excelente", "qué bueno"... (y al pasar de fase) | feliz | manos arriba |
| `SURPRISE` | "wow", "increíble", "no me lo esperaba"... | sorpresa | arriba, bajan un poco y vibran |
| `ANGRY` | un "grrr" juguetón **contra el problema** | enojada | arriba/abajo rápido y errático (~1,2 s) |
| `FRUSTRATED` | "uff", "qué difícil", "me cuesta"... | ojos entrecerrados + boca "F" | sube lento, baja a la mitad, pausa, baja |
| `SAD` | "lo siento", "qué pena", "triste"... | triste | brazos caídos |
| `REST` | termina la voz | neutra, parpadea | vuelve a 90° y se suelta (no zumba) |

La emoción se detecta del texto (palabras clave y signos de exclamación),
sin pedirle nada extra al modelo. El enojo nunca va contra el niño: se anula
si cerca aparece "tú", un verbo en segunda persona o una pregunta, y sale
como mucho una vez cada 4 respuestas. La carita y los servos se mueven
exactamente mientras el robot habla, porque Python controla el parlante.

**Calibrar los brazos** (hay que hacerlo una vez en cada robot): en
`sketch/sketch.ino`, bloque de servos, `ARM1_DIR` / `ARM2_DIR` (+1 o -1:
si con HAPPY un brazo baja, cambiarle el signo; casi seguro `ARM2_DIR = -1`)
y `ARM_MIN` / `ARM_MAX` (topes en grados, si un brazo pega contra el
cuerpo). Luego `arduino-app-cli app restart`. Paso a paso en
`DOCUMENTACION.md` §13.4.

## Pruebas

- Sin hardware, en cualquier momento:
  `docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/test_barge_in.py`
  (agregar `--vosk` para probar el reconocedor real) y
  `docker exec -w /app/python robot-bang-stable-main-1 /app/.cache/.venv/bin/python gestures.py`.
  Deben terminar en `todo bien`.
- En el robot, después de `arduino-app-cli app restart`: el checklist de
  `DOCUMENTACION.md` §20 (caras, servos, ESSENTIALS, escucha activa, cambio
  de guía, seguridad). Las limitaciones conocidas están en §18.5.

## Voces por guía

`python/voice.py` asigna una voz Chirp3-HD de Google TTS distinta a cada
guía, para que se distingan al hablar. Se puede ajustar ahí mismo (dict
`_VOICES`); el catálogo completo de voces en español sale de
`https://texttospeech.googleapis.com/v1/voices?languageCode=es-US`.
# Robot_Bang
