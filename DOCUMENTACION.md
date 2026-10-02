# Robot BANG 3 — Documentación técnica del proyecto

Chatbot de voz para la Arduino UNO Q con los 5 guías de la metodología de
innovación **BANG** (CUN): **Crispi**, **Carmel**, **Cesia**, **Cori** y
**Cristal**. Todo el turno de voz (escuchar, pensar, hablar, gesticular) vive
en la propia placa: no hace falta celular ni túnel a internet salvo para las
APIs de Google/Gemini.

> Este documento complementa a `README.md` (guía rápida de uso) con el
> detalle de arquitectura, flujo de datos, y las decisiones de diseño que no
> son obvias leyendo el código por encima. Fecha de esta revisión: 2026-09-25.

---

## 1. Índice

1. [Arquitectura general](#2-arquitectura-general)
2. [Estructura de archivos](#3-estructura-de-archivos)
3. [Requisitos de hardware](#4-requisitos-de-hardware)
4. [Credenciales y configuración](#5-credenciales-y-configuración)
5. [Flujo de un turno de voz](#6-flujo-de-un-turno-de-voz)
6. [Los 5 guías y el sistema de desbloqueo](#7-los-5-guías-y-el-sistema-de-desbloqueo)
7. [La metodología BANG (fases)](#8-la-metodología-bang-fases)
8. [El cerebro: brain.py y el LLM](#9-el-cerebro-brainpy-y-el-llm)
9. [Voz: voice.py (oídos y boca)](#10-voz-voicepy-oídos-y-boca)
10. [Bluetooth: salida por bocina](#11-bluetooth-salida-por-bocina)
11. [Gestos y Bridge (MPU ↔ MCU)](#12-gestos-y-bridge-mpu--mcu)
12. [El sketch: pantalla TFT y servos](#13-el-sketch-pantalla-tft-y-servos)
13. [Dashboard web (assets/)](#14-dashboard-web-assets)
14. [Terminal del dashboard: comandos](#15-terminal-del-dashboard-comandos)
15. [Herramientas de generación (tools/)](#16-herramientas-de-generación-tools)
16. [Conexión de hardware](#17-conexión-de-hardware)
17. [Problemas conocidos y su estado](#18-problemas-conocidos-y-su-estado)
18. [Registro de pruebas realizadas](#19-registro-de-pruebas-realizadas)

---

## 2. Arquitectura general

La App corre en dos mundos que se hablan por el **Router Bridge**:

```
┌─────────────────────────── Arduino UNO Q ───────────────────────────┐
│                                                                       │
│   MPU (Linux, Python)                    MCU (Zephyr, C++)          │
│  ┌─────────────────────────┐   Bridge   ┌──────────────────────┐   │
│  │ main.py   bucle de turno │ <━━━━━━━━> │ sketch.ino           │   │
│  │ voice.py  oídos/boca     │  notify()  │  - Pantalla TFT      │   │
│  │ brain.py  LLM (Gemini)   │  provide() │    (carita animada)  │   │
│  │ bang.py   metodología    │            │  - 2 servos SG90     │   │
│  │ guides.py desbloqueos    │            │    (gestos)          │   │
│  │ gestures.py → Bridge     │            └──────────────────────┘   │
│  │ bt.py     cliente BT     │                                       │
│  └─────────────────────────┘                                       │
│           │                                                          │
│           │ HTTPS :7000 (WebUI)              USB (headset)          │
│           ▼                                        ▲                │
│      assets/ (dashboard)                            │                │
└──────────────────────────────────────────┼──────────┼───────────────┘
                                            │          │
                                     Navegador      Diadema USB
                                  (misma red LAN)   (mic + parlante)
```

Puntos clave del diseño:

- **La voz vive en la placa**, no en el navegador: `voice.py` abre el
  micrófono/parlante USB directamente (vía `arduino.app_peripherals`), hace
  streaming a **Google Cloud Speech-to-Text** y sintetiza con **Google Cloud
  Text-to-Speech**. El navegador solo es un *dashboard* de lectura/estado y
  una terminal de comandos — no transporta audio.
- **El MCU nunca decide nada**: solo recibe códigos empaquetados en un
  entero (`Bridge.notify("face_gesture", persona*4 + gesto)`, etc.) y
  traduce eso a píxeles en la pantalla y ángulos de servo. Toda la lógica
  (personalidad, fases BANG, LLM) vive en Python.
- **Nunca se bloquea el hilo del Bridge**: las funciones que Python invoca
  en el sketch (`face_gesture`, `mouth_level`, `arm_step`, `splash`, `card`)
  solo anotan una variable `volatile`; es `loop()` quien consume esos
  valores y hace el trabajo real de SPI/servos. Ver el comentario en
  `sketch/sketch.ino` sobre por qué (el hilo del Bridge tiene solo 500
  bytes de stack y preempta a `loop()`).
- **App.run(user_loop=loop)**: el bucle principal de `main.py` es una
  función `loop()` que el framework llama repetidamente. **Importante:**
  si `loop()` deja escapar una excepción sin capturar, el framework
  registra el traceback y **apaga la App entera** (no reintenta). Por eso
  cualquier operación de hardware que pueda fallar (abrir el micrófono, el
  parlante) debe ir protegida con `try/except` — ver [§18](#18-problemas-conocidos-y-su-estado).

---

## 3. Estructura de archivos

```
robot-bang_stable/
├── app.yaml                  # nombre, bricks (web_ui, cloud_llm), icono
├── README.md                 # guía rápida de uso
├── DOCUMENTACION.md          # este documento
├── google-credentials.json   # service account de Google Cloud (STT/TTS) — NUNCA versionar
├── memory.md                 # notas de desarrollo / decisiones
│
├── python/
│   ├── main.py                # bucle principal, terminal, bienvenida, WebUI
│   ├── brain.py                # personalidades + llamada al LLM (Gemini)
│   ├── bang.py                # metodología BANG: fases, tarjetas, guardarrailes
│   ├── voice.py                # STT/TTS, audio, sincronía boca, Bluetooth
│   ├── guides.py                # qué guías están desbloqueados (persiste en data/)
│   ├── gestures.py             # texto → gesto, envío por Bridge
│   ├── bt.py                    # cliente HTTP del ayudante de Bluetooth del host
│   └── requirements.txt        # google-cloud-speech, google-cloud-texttospeech
│
├── sketch/
│   ├── sketch.ino               # carita TFT + 2 servos, lado MCU
│   ├── sketch.yaml              # librerías Arduino (Adafruit GFX/ST7789, Servo...)
│   ├── faces_colors.h           # paleta de color por personaje
│   ├── face_sprite.h/.cpp(?)    # motor de sprites 2-bit para caras dibujadas
│   ├── <guia>_face.h            # sprites de cada guía (generados)
│   ├── face_bands.h             # cálculo de bandas de redibujado parcial
│   ├── bang_splash.h            # GIF de bienvenida (generado)
│   └── bang_cards.h             # las 50 tarjetas BANG, RLE (generado)
│
├── assets/                     # dashboard web (servido por el brick web_ui)
│   ├── index.html
│   ├── app.js
│   ├── style.css                # estética "Windows 95 / retro-desktop"
│   └── libs/                    # socket.io, arduino.js (vendored)
│
├── certs/                       # cert.pem / key.pem autofirmados (HTTPS local)
├── data/                        # estado persistente (NO versionado)
│   ├── unlocks.json              # guías desbloqueados
│   ├── bt_mode.txt               # "headset" | "music"
│   ├── bt_output.txt             # MAC de la bocina preferida
│   └── .bt_token                 # token del ayudante de Bluetooth del host
├── identty/                     # fichas de personalidad de cada guía (fuente)
└── tools/                        # scripts de generación y utilidades
    ├── make_cards.py              # genera sketch/bang_cards.h desde las imágenes
    ├── make_face_sprites.py       # genera sketch/<guia>_face.h desde los PNG
    ├── make_splash.py             # genera sketch/bang_splash.h desde el GIF
    ├── bt_helper.py                # servicio HTTP del host (Bluetooth), puerto 7010
    ├── bt_speaker.sh                # conectar bocina emparejada a mano
    ├── install_bt_helper.sh        # instala el servicio systemd --user
    ├── fix_bt_lightdm.sh            # workaround de permisos Bluetooth en LightDM
    └── test_*.py                    # scripts de prueba manual (caras, welcome, flujo BANG)
```

---

## 4. Requisitos de hardware

| Componente | Detalle |
|---|---|
| **Placa** | Arduino UNO Q |
| **Headset USB** | Diadema con micrófono + salida de audio (entrada de voz principal) |
| **Hub USB-C alimentado** | 5V / 3A+. El puerto USB-C de la UNO Q es OTG dual-role: en modo *host* (para el audio) deja de alimentar la placa, así que el hub debe alimentar la placa **y** alojar el headset a la vez |
| **Pantalla TFT** | GMT028-05, driver ST7789, 240×320, SPI |
| **2 servos SG90** | Gesticulación sincronizada con la voz |
| **Fuente externa 5V** (recomendada) | Para los 2 servos + pantalla a la vez; no depender del pin 5V de la placa (ver [§17](#17-conexión-de-hardware)) |
| **Bocina Bluetooth** (opcional) | `RF-66678` ya emparejada; si no hay, se usa el headset USB |

---

## 5. Credenciales y configuración

Dos credenciales de Google, para dos cosas **distintas**:

### 5.1 `API_KEY` de Gemini (LLM)

- El brick `arduino:cloud_llm` lee la clave con `os.getenv("API_KEY")` (ver
  el README del brick: `bricks_get arduino:cloud_llm`).
- Hay **dos** lugares desde donde se puede poblar esa variable:

  1. **Brick Configuration**, en la GUI de Arduino App Lab (App → Bricks →
     Cloud LLM → configurar `API_KEY`). El valor lo guarda App Lab **fuera
     de la carpeta de la App**, así que no queda en el repositorio. Es el
     lugar recomendado para secretos.
  2. El bloque `variables:` de `app.yaml`:
     ```yaml
     - arduino:cloud_llm:
         variables:
           API_KEY: <valor>
     ```

- ⚠️ **Trampa importante (costó una caída completa del LLM):** el bloque
  `variables:` de `app.yaml` asigna un **valor literal**, *no* es una
  indirección al nombre de otra variable de entorno. Escribir
  `API_KEY: GCP_API_KEY` no busca una variable llamada `GCP_API_KEY`: le
  entrega al brick la cadena literal `"GCP_API_KEY"` como si fuera la
  clave, y Google responde:
  ```
  400 INVALID_ARGUMENT — API key not valid. Please pass a valid API key.
  reason: API_KEY_INVALID · service: generativelanguage.googleapis.com
  ```
  Además, un valor puesto ahí **tapa** al de Brick Configuration. Para usar
  Brick Configuration hay que **quitar el bloque `variables:`** de
  `app.yaml` (dejar `- arduino:cloud_llm: {}`).

- Ojo con `app_bricks_list`: reporta `isSet: true` en cuanto la variable
  tiene *algún* valor no vacío — `true` **no** significa que la clave sea
  válida.

- La clave se obtiene en <https://aistudio.google.com/apikey>.

### 5.2 `google-credentials.json` (Speech-to-Text / Text-to-Speech)

- Service account de Google Cloud, en la **raíz de la App** (junto a
  `app.yaml`). **Nunca** se versiona (ver `.gitignore`: `google-credentials.json`,
  `*-credentials.json`, `certs/*.pem`, `data/`).
- Se usa la API "cruda" de Google Cloud (no el brick `arduino:cloud_asr`)
  porque ese brick solo acepta API key simple, y Google Cloud Speech la
  rechaza ("API keys are not supported by this API"): hace falta la service
  account.
- En el proyecto de Google Cloud correspondiente hace falta:
  - **Cloud Text-to-Speech API** habilitada.
  - **Cloud Speech-to-Text API** habilitada (requiere facturación activa,
    aunque tiene cupo gratis mensual).

### 5.3 Modelos LLM usados

`brain.py` prueba, en orden, hasta que uno responde:

| Modelo | Latencia medida (07/09/2026) |
|---|---|
| `google:gemini-3.1-flash-lite` | ~1.1 s |
| `google:gemini-3.5-flash-lite` | ~1.2 s |
| `google:gemini-flash-lite-latest` | ~8 s |

El modelo de fábrica del brick (`gemini-3.6-flash`) tardaba más de 35 s o
fallaba, por eso no se usa. Dos detalles documentados en el código como
"no tocar":

- El prefijo `"google:"` es obligatorio; sin él el brick lanza
  `ValueError("Model not supported")`.
- El timeout no puede bajar de 10 s: Gemini rechaza deadlines menores con
  `INVALID_ARGUMENT`. Se usa `TIMEOUT = 20`.

---

## 6. Flujo de un turno de voz

```
 1. loop() en main.py llama a voice.listen_turn(...)
 2. listen_turn() abre streaming a Google STT y espera:
      - una wake word ("Crispi", "Carmel", ..., "robot", "Bang"), o
      - una respuesta dentro de la ventana de follow-up (7 s tras la
        última respuesta del guía, sin repetir el nombre)
 3. En cuanto el texto deja de cambiar 0.7 s (_ENDPOINT_S), la frase se
    da por terminada — no se espera el is_final de Google (1-2 s más).
 4. voice.ack() → pitido corto de "te escuché" (no bloqueante, mientras
    el LLM piensa)
 5. main.py resuelve qué guía responde:
      - nombre explícito → ese guía (si desbloqueado)
      - "robot"/"bang" con reto en curso → el guía de ese reto
      - "robot"/"bang" sin reto, 1 solo guía desbloqueado → ese guía
      - "robot"/"bang" sin reto, varios desbloqueados → bang.classify()
        (una llamada al LLM que elige el guía según el reto)
 6. bang.turn(persona, texto) procesa el turno según la fase BANG activa
    (sólida / gaseosa / líquida) y devuelve la respuesta + si cambió de
    fase + si se destapó una tarjeta
 7. gestures.emotion_of(respuesta) decide el gesto (TALK/HAPPY/SURPRISE)
    por palabras clave y signos de exclamación (sin llamada extra al LLM)
 8. gestures.send(gesto, persona) → Bridge.notify("face_gesture", ...)
    ANTES de hablar: como Python controla el parlante, sabe exactamente
    cuándo empieza/termina la voz
 9. voice.say(persona, respuesta):
      - pausa el micrófono (pause_listening())
      - parte el texto en frases, las sintetiza EN PARALELO
        (ThreadPoolExecutor, 3 workers) con Google TTS
      - reproduce en orden; la primera frase suena en cuanto está lista
      - por cada bloque de audio que SUENA, reporta el nivel de volumen
        a gestures.send_mouth() → Bridge.notify("mouth_level", 0..4)
      - si Google TTS falla, cae a espeak (si está instalado; hoy no lo
        está en el contenedor de esta App)
      - al terminar, reanuda el micrófono (resume_listening())
10. Queda un margen de FOLLOW_UP_S = 7 s para responder sin repetir el
    nombre del guía.
```

### Por qué es "modo Alexa" (baja la espera)

- Fin de frase local (`_ENDPOINT_S`), sin esperar el cierre de Google.
- Pitido de confirmación mientras el LLM piensa.
- TTS por frases en paralelo: arranca con la primera lista (~1.5 s menos
  por turno).
- Con un solo guía desbloqueado, "robot" no llama al clasificador (se
  ahorra una llamada entera al LLM).
- Cache de `CloudLLM` por `(persona, fase)`: cada conversación mantiene su
  propia memoria (`with_memory(max_messages=12)`), sin reconstruir el
  cliente en cada turno.

---

## 7. Los 5 guías y el sistema de desbloqueo

| Guía | Género | Nivel (Maslow) | Tagline | Color |
|---|---|---|---|---|
| **Crispi** | m | Necesidades básicas | El constructor | `#4ade80` (verde) |
| **Carmel** | m | Autoestima | El estratega | `#fbbf24` (ámbar) |
| **Cesia** | f | Seguridad | La disruptora | `#f87171` (rojo) |
| **Cori** | f | Necesidades sociales | La pensadora lateral | `#a78bfa` (violeta) |
| **Cristal** | f | Autorrealización | La musa reflexiva | `#22d3ee` (cian) |

- **Solo Crispi arranca desbloqueado** (`guides.ALWAYS_UNLOCKED`); no se
  puede volver a bloquear.
- El resto se desbloquea desde la terminal del dashboard
  (`/unlock_carmel`, `/unlock_cesia`, `/unlock_cori`, `/unlock_cristal`,
  o `/unlock_all`).
- El estado persiste en `data/unlocks.json` y sobrevive a reinicios
  (`guides.py`, protegido con `threading.Lock`).
- Cada guía tiene una voz Chirp3-HD distinta (`voice._VOICES`) y, si tiene
  PNG en `assets/img/<guia>/`, una cara dibujada (sprites) en vez de la
  cara geométrica genérica.

---

## 8. La metodología BANG (fases)

Facilitación, no respuestas: el guía **nunca da la solución**, guía con
preguntas (máx. 2 por turno). Basado en `bang-lite-ai` (repo
PROYECTOS-IA-CUN-2026).

### 8.1 Fase SÓLIDA — formular la pregunta problema

- Usa el marco **Mom Test** ("Cuéntame sobre la última vez que...").
- Verifica 5 cosas antes de cerrar: contexto (dónde/cuándo), situación
  específica, impacto personal, intentos previos/dificultad, y que se
  pueda reformular como *"¿Cómo podríamos...?"*.
- El LLM responde en **JSON estricto**:
  ```json
  {"respuesta": "...", "faseCompleta": true/false, "pregunta": "..."}
  ```
  parseado por `bang._parse_solid()` (tolerante a que venga envuelto en
  ` ```json ` o con texto extra alrededor).
- No cierra antes de `MIN_SOLID_TURNS = 2` respuestas de la persona,
  aunque el LLM ya marque `faseCompleta: true`.

### 8.2 Fase GASEOSA — ideación divergente

- Al entrar, se reparten **3 tarjetas al azar** del mazo del guía
  (`CARDS_PER_ROUND = 3` de 10 posibles, `bang.CARDS`).
- Comando de voz **"saca una tarjeta"** voltea una tarjeta pendiente; el
  guía explica cómo usarla como "lente" (no como respuesta), con 2
  ejemplos aplicados al reto.
- Cada frase de la persona cuenta como una idea (`s.ideas.append(...)`).
- A las **`IDEAS_FOR_LIST = 7` ideas**, se agrupan/depuran y se resumen
  en voz (3 a 5 más distintas), invitando a pasar de fase.
- Los guardarraíles de "no dar soluciones" **no aplican aquí**: en esta
  fase el guía sí puede dar detonantes y ejemplos concretos (palancas
  SCAMPER, roles extremos, restricciones de tiempo).

### 8.3 Fase LÍQUIDA — prototipo y validación

- Recorre, con preguntas: hipótesis a validar en una frase, señal
  observable en 3-7 días, versión mínima/barata, con quién y cuándo
  probarla, y criterio para seguir/ajustar/detener.
- Evalúa también factibilidad técnica, deseabilidad y viabilidad
  económica.
- Es la última fase: si ya se está ahí, "siguiente fase" no avanza más.

### 8.4 Guardarraíles del facilitador

`bang._polish()` reescribe la respuesta si:

- Contiene verbos de "dar la solución" (usa, instala, configura, compra,
  contrata...), "paso a paso"/"tutorial", o consejo directo
  ("deberías", "te sugiero", "recomiendo") — **excepto en la fase
  gaseosa**, donde sí se permite.
- Si detecta esto, pide al LLM reescribirlo en modo facilitador
  (`_rephrase_as_facilitator`, temperatura baja = 0.2).
- Siempre recorta a **máximo 2 preguntas** por turno (`_limit_questions`)
  y neutraliza interpretaciones tajantes ("Parece que..." →
  "Entiendo que...").

### 8.5 Comandos de voz durante el reto

| Frase (aprox., regex tolerante) | Efecto |
|---|---|
| "nuevo reto" / "otro reto" / "desde cero" | reinicia el reto del guía activo |
| "siguiente fase" / "próxima fase" / "pasa a la líquida" | avanza de fase sin esperar al LLM |
| "tarjeta" / "carta" / "inspírame" (solo en gaseosa) | voltea una tarjeta |

### 8.6 Clasificador de personaje

Cuando se dice "robot"/"bang" sin reto en curso y hay más de un guía
desbloqueado, `bang.classify(reto)` llama al LLM con un prompt de
clasificación (Maslow: básicas/seguridad/sociales/autoestima/
autorrealización) y devuelve el guía más afín; si algo falla, cae a
Crispi por defecto.

---

## 9. El cerebro: brain.py y el LLM

- `PERSONAS`: diccionario con nombre, género, color, tagline, nivel de
  necesidad y descripción de tono de cada guía — es la base de todos los
  prompts (`persona_intro()`).
- `SPOKEN_RULES`: reglas comunes a todos los prompts — español, 2 a 4
  frases cortas, sin markdown/listas/emojis (la respuesta la dice el
  robot en voz alta).
- `chat(cache_key, system_prompt, text, temperature, memory)`:
  - Si `memory=True`, usa/crea un `CloudLLM` cacheado por
    `(cache_key, modelo)` con memoria de hasta 12 mensajes.
  - Si `memory=False`, crea un cliente suelto (usado por el clasificador,
    el reescritor de guardarraíles, y el resumen de ideas).
  - Prueba los 3 modelos de `MODELS` en orden; si uno lanza excepción
    (Gemini devuelve 503/429 con frecuencia), pasa al siguiente **en vez
    de reintentar el mismo modelo** — un modelo alterno suele responder
    en un par de segundos. `MAX_RETRIES = 1` (el cliente langchain ya
    reintenta internamente hasta 6 veces con backoff; con 1 solo intento
    se falla rápido y se pasa al siguiente modelo).
  - Si ningún modelo responde, devuelve `None` → el llamador usa
    `FALLBACK_REPLY` ("Se me cruzaron los cables un segundo...").
- `clear(persona)`: borra la memoria de **todas** las conversaciones de
  un guía (se usa en `/reset` y al iniciar un reto nuevo).
- `warmup()`: hace una llamada de "usar y tirar" al arrancar la App, en
  segundo plano, para pagar el costo de la primera llamada (~25 s,
  importar langchain + autenticar) antes de que alguien pregunte.

---

## 10. Voz: voice.py (oídos y boca)

### 10.1 Reconocimiento de voz (STT)

- Streaming a Google Cloud Speech-to-Text, `es-CO`, `LINEAR16` a 16 kHz.
- `speech_contexts` con *phrase hints* boosteados (`boost=15`): los
  nombres de los guías y los comandos de voz, para que se transcriban
  bien a la primera (sin esto, "Crispi" suele salir "crispy", "Cris pi"...).
- `_find_wake_word()` normaliza (sin tildes, minúsculas), tolera el
  nombre partido en dos palabras ("Cris pi") y aliases de transcripciones
  típicas mal escritas (`_WAKE_ALIASES`: "crispy"→"crispi",
  "carmelo"→"carmel", "cory"→"cori", "crystal"→"cristal", etc.).
- El turno se resuelve palabra por palabra en un hilo lector (`reader()`)
  que empuja eventos a una cola, para poder medir silencio con timeout
  sin bloquear en el iterador de gRPC.

### 10.2 Síntesis de voz (TTS)

- Google Cloud Text-to-Speech, voces **Chirp3-HD** (las más naturales del
  catálogo), una distinta por guía (`_VOICES`). Salida `LINEAR16` a
  24 kHz (con header WAV incluido, reproducible directo).
- Respaldo: `espeak` si Google TTS falla — pero el contenedor de esta App
  no lo trae instalado (a diferencia del sistema host), así que en la
  práctica ese turno se salta sin sonar robótico.
- El texto se parte en frases (`_split_sentences`, frases <30 caracteres
  se pegan a la siguiente) y se sintetizan **en paralelo** con un
  `ThreadPoolExecutor(max_workers=3)`.

### 10.3 Sincronía boca/voz

- `_play_synced()` reproduce el PCM de a bloques (`spk.buffer_size`
  muestras) y reporta qué bloque está *sonando* con un atraso
  (`_mouth_lag()`) que compensa: 2 bloques con headset USB, 6 con bocina
  Bluetooth A2DP (que tiene más buffer propio, ~150-250 ms extra).
- `_mouth_levels()` calcula el nivel de boca (0-4) por bloque según el
  volumen RMS, normalizado contra el percentil 90 **de esa misma frase**
  (una voz más bajita o más fuerte abre la boca igual). Abre al toque,
  cierra de a un nivel por bloque (para no titilar entre sílabas).

### 10.4 Dispositivo de audio: headset USB o bocina Bluetooth

- Un hilo de fondo (`_poll_bluetooth`, cada `_OUTPUT_POLL_S = 3 s`)
  consulta PipeWire (`pw-dump`) y detecta si hay una bocina Bluetooth
  conectada (`bluez_output.*`); si la hay, se usa; si no, el headset USB
  (`Speaker.USB_SPEAKER_1`).
- El cambio de salida es automático, **sin reiniciar la App**.
- `_microphone()`/`_speaker()` reabren el dispositivo si cambió desde el
  último turno.

### 10.5 Anti-eco (que el robot no se transcriba a sí mismo)

- `pause_listening()`/`resume_listening()` llevan la cuenta de cuántas
  voces están sonando (contador, no booleano: varias pueden solaparse).
- `_ECHO_TAIL_S = 0.8 s` de sordera extra al terminar de hablar, porque
  con bocina Bluetooth el propio buffer de la bocina sigue sonando un
  rato después de que Python terminó de escribir el audio.

---

## 11. Bluetooth: salida por bocina

El contenedor de la App **no ve el Bluetooth del sistema** (aislamiento de
Docker), así que el flujo pasa por un servicio del host:

```
 Dashboard (assets/app.js) ──WS──> main.py (on_bt) ──HTTP──> tools/bt_helper.py
                                    │                          (host, puerto 7010,
                                    │                           servicio systemd
                                    │                           "bang-bt-helper")
                                    └── bt.py: resuelve la IP del host leyendo
                                        /proc/net/route (gateway del contenedor)
                                        y llama con X-Token de data/.bt_token
```

- **Modo `headset`** (por defecto): la bocina pasa a perfil manos libres
  (HFP/HSP) — se usa **su micrófono y su parlante**. Suena a llamada
  telefónica; no hace falta headset USB.
- **Modo `music`** (`/bt_mode music`): la voz sale en alta calidad
  (A2DP); el micrófono sigue siendo el del headset USB.
- El perfil se fuerza a mano con `pw-cli` (`_ensure_profile()`) porque el
  autoswitch de WirePlumber está desactivado en esta placa
  (`~/.config/wireplumber/wireplumber.conf.d/51-disable-bt-autoswitch.conf`,
  puesto por ArmonIA).
- El modo se guarda en `data/bt_mode.txt`; la bocina preferida (si hay
  varias) en `data/bt_output.txt` (por MAC).
- **Limitación de hardware conocida**: en esta placa, el micrófono
  Bluetooth llega mudo (`hciconfig` reporta `RX sco:0`) — el chip no
  entrega voz SCO al host. Por eso `_microphone()` solo usa el mic de la
  bocina como último recurso, y nunca en modo `music`.

---

## 12. Gestos y Bridge (MPU ↔ MCU)

`gestures.py` traduce texto/eventos a mensajes `Bridge.notify(...)`:

| Canal Bridge | Payload | Cuándo |
|---|---|---|
| `face_gesture` | `personaId * 4 + gesto` (1 entero) | al empezar/terminar de hablar |
| `mouth_level` | `0..4` | por cada bloque de audio que suena |
| `arm_step` | `0` o `1` (paridad de la nota) | en cada nota de la celebración |
| `splash` | `1` / `0` | prender/apagar el GIF de bienvenida |
| `card` | `personaId*10 + numero-1`, o `255` para sacarla | mostrar/ocultar una tarjeta BANG |

Gestos (`gestures.REST/TALK/HAPPY/SURPRISE`, deben coincidir con
`G_*` en `sketch.ino`):

| Gesto | Disparador (por texto, sin llamar al LLM) | Movimiento |
|---|---|---|
| `REST` (0) | fin de la voz | boca cerrada, servos a 90° y sueltos (detach, no zumban) |
| `TALK` (1) | default mientras habla | vaivén 80°-100° |
| `HAPPY` (2) | `!` en el texto, o palabras como "genial", "excelente", "dale" | barrido 45°-145° ×2 |
| `SURPRISE` (3) | "wow", "increíble", "no me lo esperaba"... | respingo a 160° |

`emotion_of()` nunca devuelve `REST` si el guía está hablando: siempre
hay algún movimiento.

---

## 13. El sketch: pantalla TFT y servos

### 13.1 Por qué NO se usa `tft.writePixels()`

En esta placa (Zephyr, Cortex-M33) la ruta genérica de Adafruit_GFX manda
1 pixel por vez = 2 transferencias SPI bloqueantes de 1 byte cada una
(mutex + semáforo por ISR). Resultado medido: ~200 ms por frame lleno
(3-5 fps) cuando el bus real tardaría ~12 ms — más del 90% era overhead
de software, no de bus.

**Solución implementada:** `SPI.transfer(buf, n_bytes)` en bloque (una
sola llamada al driver por chunk), con dos reglas no negociables:

1. `SPI.transfer()` es full-duplex y **sobrescribe** el buffer que
   recibe — nunca se le pasa el canvas directo, siempre se copia a un
   `spiScratch` intermedio.
2. La API de bloque es de 8 bits; el panel espera 16 bits con el byte
   alto primero → cada copia hace `__builtin_bswap16`.

No hay DMA disponible (`CONFIG_SPI_STM32_DMA` apagado en el firmware del
core, no configurable desde el sketch).

### 13.2 Redibujado parcial

Cada parte móvil de la cara (cada ojo, cada ceja, la boca) tiene su
propio framebuffer chico en RAM (`RegionCanvas`, subclase de
`GFXcanvas16`) y solo se reenvía por SPI la región que realmente cambió.
Un presupuesto de píxeles por frame (`FRAME_PX_BUDGET = 3200`) garantiza
que ningún frame se pase de los 33 ms (30 fps): si dos partes quieren
actualizarse a la vez y no alcanza, una espera al siguiente frame — se
degrada la suavidad, nunca el framerate.

### 13.3 Caras dibujadas (sprites) vs. cara geométrica

Los 5 guías tienen PNG propios (`assets/img/<guia>/`), convertidos a
sprites de 2 bits por `tools/make_face_sprites.py` → `<guia>_face.h`. El
parpadeo repite la secuencia de los PNG (abierto→entrecerrado→cerrado→
entrecerrado→abierto) y la boca se abre según el volumen real. Sin PNG,
se usa la cara geométrica (rectángulos redondeados). Entre un sprite y el
siguiente, `planSpriteDiff()` compara fila por fila y agrupa las
diferencias en rectángulos (máx. `MAX_DIFF_RECTS = 24`), para mandar solo
lo que cambió.

### 13.4 Servos

- 2 SG90 en **D5** y **D6** (la pantalla ya ocupa D8/D9/D10/D11/D13).
- Se enganchan (`attach()`) solo cuando hace falta moverlos y se sueltan
  (`detach()`) `SERVO_REST_SETTLE_MS = 350 ms` después de volver a
  reposo — para no zumbar en silencio.
- El baile de celebración (`arm_step`) mueve los 2 servos a poses
  espejadas alrededor de 90° (inspirado en el tutorial de Diome-chan);
  mientras dura, `updateServos()` no los toca.

### 13.5 Bienvenida y tarjetas

- Al bootear el MCU, `pendingSplash = 1` por defecto: el GIF de
  bienvenida (`tools/make_splash.py` → `bang_splash.h`) se muestra **sin
  esperar a Python**. Si Python nunca lo apaga (se cayó), se apaga solo a
  los `SPLASH_MAX_MS = 90000` (90 s).
- Las tarjetas BANG (`tools/make_cards.py` → `bang_cards.h`, 50 tarjetas
  = 5 guías × 10) se guardan comprimidas con RLE de 1 byte por token
  (valor + longitud, con escape para longitudes largas).

---

## 14. Dashboard web (assets/)

- Servido por el brick `arduino:web_ui` sobre **HTTPS local**
  (`https://<IP-de-la-placa>:7000`), con el certificado autofirmado de
  `certs/` (el brick lo genera solo si falta). El navegador avisa la
  primera vez: "Avanzado → Continuar".
- Estética deliberada "escritorio retro / Windows 95" (`style.css`):
  ventana con barra de título, `<marquee>`, contador de visitas.
- Paneles:
  - **Los 5 guías**: tarjetas con su color, bloqueados marcados con 🔒.
  - **Reto BANG**: fase activa, la pregunta problema, las tarjetas
    volteadas, las ideas.
  - **Lo que el robot escucha y responde**: log de conversación en vivo.
  - **Diagnóstico en vivo**: cada fragmento que el STT va transcribiendo
    (aunque no sea el nombre de un guía) y cada aviso de `voice.py` (sin
    micrófono, TTS que falla...) — para confirmar que el micrófono SÍ
    está captando sonido sin tener que mirar los logs.
- Botón **💻 TERMINAL** (o tecla `` ` ``): consola de comandos (ver
  [§15](#15-terminal-del-dashboard-comandos)); tab-completa y ↑/↓ navega
  el historial.
- Botón **🔵 BLUETOOTH**: estado de la bocina, buscar/conectar.
- Comunicación en tiempo real vía `socket.io` (vendored en
  `assets/libs/`): `ui.send_message(topic, data, sid=None)` desde Python
  hacia uno o todos los clientes conectados.

---

## 15. Terminal del dashboard: comandos

| Comando | Qué hace |
|---|---|
| `/help` | esta ayuda |
| `/status` | guías desbloqueados, guía activo, reto en curso, salida/entrada de audio |
| `/unlock_<guia>` | desbloquea (`carmel`, `cesia`, `cori`, `cristal`) — tolera typos (`/unlock_carmerl` → Carmel) |
| `/unlock_all` / `/lock_all` | todos / solo Crispi |
| `/lock_<guia>` | vuelve a bloquear (Crispi no se puede bloquear) |
| `/bt` | estado de la bocina Bluetooth |
| `/add_bt` | busca equipos Bluetooth (10 s) y los lista numerados |
| `/bt_list` | lista los equipos conocidos, sin buscar |
| `/bt_connect <n>` / `/bt_disconnect <n>` | conecta/desconecta el equipo `n` del último listado (o una MAC directa) |
| `/bt_audio <n>` | fija cuál bocina habla, si hay varias conectadas |
| `/bt_mode headset\|music` | perfil manos libres vs. alta calidad |
| `/test_audio` | pitido de prueba por la salida actual |
| `/cara <guia>` | muestra la cara de un guía y la hace "hablar" 3 s (sin necesitar micrófono) |
| `/tarjeta <guia> <1-10>` | muestra una tarjeta específica en pantalla; `/tarjeta` sola la saca |
| `/bienvenida` | repite la bienvenida al BANG (GIF + presentadora) |
| `/menu` | vuelve al inicio: borra los retos de todos los guías, sin guía activo |
| `/reset` | borra el reto en curso del guía activo |
| `/clear` | limpia la terminal |

---

## 16. Herramientas de generación (tools/)

| Script | Propósito |
|---|---|
| `make_face_sprites.py` | PNG de cada guía → `sketch/<guia>_face.h` (sprites 2-bit) |
| `make_splash.py` | GIF de bienvenida → `sketch/bang_splash.h` |
| `make_cards.py` | Imágenes de las 50 tarjetas → `sketch/bang_cards.h` (RLE) — `CARDS_PER_GUIDE = 10` debe coincidir con `gestures.py` |
| `bt_helper.py` | Servicio HTTP del **host** (puerto 7010) que sí ve el Bluetooth del sistema; el contenedor le pide a través de `python/bt.py` |
| `bt_speaker.sh` | Conectar la bocina emparejada a mano, sin pasar por el dashboard (`scan`, `pair <MAC>`) |
| `install_bt_helper.sh` | Instala `bt_helper.py` como servicio `systemd --user` (`bang-bt-helper`) |
| `fix_bt_lightdm.sh` | Workaround de permisos de Bluetooth bajo LightDM |
| `test_bang_flow.py`, `test_crispi_face.py`, `test_faces.py`, `test_welcome.py` | Scripts de prueba manual de partes sueltas (metodología, caras, bienvenida), sin levantar la App entera |

---

## 17. Conexión de hardware

### 17.1 Pantalla TFT GMT028-05 (SPI)

| Pantalla | UNO Q |
|---|---|
| GND | GND |
| VCC | 3V3 |
| SCK | D13 |
| SDA/MOSI | D11 |
| CS | D10 |
| DC | D9 |
| RST | D8 |

### 17.2 Servos SG90

```
   Servo 1 (SG90)                  Arduino UNO Q            Servo 2 (SG90)
   --------------                  -------------            --------------
   naranja/amarillo (señal) ──────── D5  (~)
                                    D6  (~) ──────── naranja/amarillo (señal)
   rojo               (+5V)   ──────── 5V
   marrón/negro        (GND)   ──────── GND
```

**Alimentación:** cada SG90 pide hasta ~700 mA al arrancar o trabado, y
aquí se mueven 2 a la vez además de la pantalla. Se recomienda **no**
depender del pin 5V de la placa: usar una fuente externa de 5V (o 4 pilas
AA), con el GND de esa fuente **unido** al GND del Arduino (masa común
obligatoria, si no los servos no entienden la señal).

```
   Fuente externa 5V (o 4 pilas AA)
        │
        ├── (+5V) ────── rojo del Servo 1 y del Servo 2
        │
        └── (GND) ──┬─── marrón del Servo 1
                    ├─── marrón del Servo 2
                    │
                    └─── GND del Arduino UNO Q     ← ¡la masa debe ser común!
```

Síntoma de alimentación insuficiente: la placa se reinicia, los servos
tiemblan, o la pantalla parpadea al moverse.

La señal del UNO Q es de 3.3 V (MCU STM32U585); el SG90 la acepta bien
alimentándose a 5 V.

---

## 18. Problemas conocidos y su estado

### 18.1 [RESUELTO] Crash total sin headset USB conectado

- **Síntoma observado en pruebas:** al arrancar la App sin headset USB
  conectado, la bienvenida (`_welcome()` → `voice.say()`) fallaba al
  reabrir el micrófono en `resume_listening()`
  (`MicrophoneOpenError: No USB microphones found`), y esa excepción
  **no estaba protegida** — se propagaba hasta `App.run()`, que apaga la
  App entera sin reintentar (`App is shutting down` /
  `App shutdown completed`).
- **Causa raíz:** `voice._microphone()` se llama protegida con
  `try/except` en `listen_turn()`, pero **no** en `resume_listening()`
  (usada por `say()` y `celebrate()` en su bloque `finally`) — una
  asimetría entre dos llamadas al mismo método.
- **Fix aplicado** (`python/voice.py`, función `resume_listening()`):
  ```python
  def resume_listening():
      global _speaking, _deaf_until
      with _speaking_lock:
          _speaking = max(0, _speaking - 1)
          _deaf_until = max(_deaf_until, time.monotonic() + _ECHO_TAIL_S)
      try:
          _microphone()
      except Exception as exc:
          logger.warning(f"No se pudo reabrir el microfono ({exc}); reviso el headset USB")
          _debug(f"⚠ sin microfono: {exc}")
  ```
- **Verificado:** tras el fix, la App arranca y queda en estado
  `running` de forma estable sin headset conectado, degradando con
  avisos repetidos ("No se pudo abrir el micrófono...") en vez de morir.
  Con el headset conectado, el mismo camino de código abre el micrófono
  con normalidad.

### 18.2 [DOCUMENTADO, no bloqueante] Advertencia obsoleta en README

- El `README.md` original tenía una nota "⚠️ Pendiente: hoy está en
  texto plano en `app.yaml`... conviene rotarla" sobre `API_KEY`. En la
  revisión actual, `app.yaml` solo contiene el **nombre** de la variable
  (`'GCP_API_KEY'`); la clave real se resuelve desde Brick Configuration
  / variable de entorno, confirmado porque el warmup de Gemini responde
  sin que la clave esté en el repo. La advertencia del README quedó
  desactualizada.

### 18.3 [LIMITACIÓN DE HARDWARE, no es un bug] Mic Bluetooth mudo

- En esta placa, el micrófono de la bocina Bluetooth no entrega audio al
  host (`hciconfig` reporta `RX sco:0`). `voice.py` ya lo contempla: solo
  usa el mic Bluetooth como último recurso y nunca en modo `music` (ver
  [§11](#11-bluetooth-salida-por-bocina)).

### 18.4 [LIMITACIÓN CONOCIDA] Sin `espeak` en el contenedor

- El respaldo de TTS (`_say_fallback`) requiere `espeak`, presente en el
  sistema host pero no en la imagen de este contenedor. Si Google TTS
  falla (sin internet, credencial inválida), ese turno se salta sin voz
  en vez de sonar robótico. No se aplicó fix — instalar `espeak` en el
  contenedor requeriría tocar la imagen Docker de la App, fuera del
  alcance de esta revisión.

---

## 19. Registro de pruebas realizadas

Pruebas ejecutadas sobre la App real en la placa (`arduino-app-cli` /
Arduino App Lab), el 2026-09-25:

1. **Arranque limpio** (`apps_start` + `apps_wait_running`): la App tarda
   varios minutos en el primer arranque (instala dependencias Python,
   compila el sketch); en arranques siguientes es rápido.
2. **Logs de arranque**: `WebUI` levanta en `https://<IP>:7000`,
   `App started`, warmup de voz (`Voz precalentada`) y de Gemini
   (`Modelo precalentado`) exitosos.
3. **Reproducción del bug de §18.1**: confirmado con logs completos
   (traceback de `MicrophoneOpenError` propagándose desde
   `resume_listening()` hasta `App.run()`, seguido de
   `App is shutting down`).
4. **Fix + reinicio**: tras el cambio, la App se reinició y quedó en
   `status: running` de forma estable, con el micrófono ausente
   degradando a warnings en vez de crashear.
5. **Confirmación del `API_KEY`**: el warmup de `brain.py` contra Gemini
   respondió correctamente, confirmando que la variable de entorno
   `GCP_API_KEY` está bien configurada y el brick `cloud_llm` funciona.

**Pendiente de probar con hardware conectado** (headset USB, pantalla
TFT, servos, bocina Bluetooth): el flujo de voz completo de extremo a
extremo (wake word → STT → LLM → TTS → gestos en pantalla y servos), que
no se pudo verificar sin acceso físico al hardware periférico durante
esta sesión.
