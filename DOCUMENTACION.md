# Robot BANG 3 — Documentación técnica del proyecto

Chatbot de voz para la Arduino UNO Q con los 5 guías de la metodología de
innovación **BANG** (CUN): **Crispi**, **Carmel**, **Cesia**, **Cori** y
**Cristal**. Todo el turno de voz (escuchar, pensar, hablar, gesticular) vive
en la propia placa: no hace falta celular. Internet hace falta para la voz
(Speech-to-Text y Text-to-Speech de Google) y, en modo PLUS, para Gemini; en
modo ESSENTIALS la conversación la piensa un modelo local en la placa (ver
[§9.1](#91-modos-essentials--plus)).

> Este documento complementa a `README.md` (guía rápida de uso) con el
> detalle de arquitectura, flujo de datos, y las decisiones de diseño que no
> son obvias leyendo el código por encima. Fecha de esta revisión: 2026-10-05
> (**Versión 1.1.0**: conversación fluida con interrupción **nativa** —si el
> niño habla, el guía se calla—, los dos modos de conversación **BANG** y
> **Curioso**, y 9 gestos de brazos nuevos.
> [§9.4](#94-modos-de-conversación-bang--curioso-curiosopy),
> [§10.6](#106-escucha-activa-el-robot-se-calla-cuando-el-niño-habla) y
> [§12](#12-gestos-y-bridge-mpu--mcu)).
> El resumen de cambios está en `CHANGELOG.md`.

---

## 1. Índice

1. [Arquitectura general](#2-arquitectura-general)
2. [Estructura de archivos](#3-estructura-de-archivos)
3. [Requisitos de hardware](#4-requisitos-de-hardware)
4. [Credenciales y configuración](#5-credenciales-y-configuración)
5. [Flujo de un turno de voz](#6-flujo-de-un-turno-de-voz)
6. [Los 5 guías y el sistema de desbloqueo](#7-los-5-guías-y-el-sistema-de-desbloqueo) — [§7.1 Cambio de guía](#71-cambio-de-guía)
7. [La metodología BANG (fases)](#8-la-metodología-bang-fases)
8. [El cerebro: brain.py y el LLM](#9-el-cerebro-brainpy-y-el-llm) — modos ESSENTIALS / PLUS, RAG y guardarraíles ([§9.1](#91-modos-essentials--plus)), [§9.4 Modos de conversación BANG / Curioso](#94-modos-de-conversación-bang--curioso-curiosopy)
9. [Voz: voice.py (oídos y boca)](#10-voz-voicepy-oídos-y-boca) — [§10.6 Escucha activa: el robot se calla cuando el niño habla](#106-escucha-activa-el-robot-se-calla-cuando-el-niño-habla)
10. [Bluetooth: salida por bocina](#11-bluetooth-salida-por-bocina)
11. [Gestos y Bridge (MPU ↔ MCU)](#12-gestos-y-bridge-mpu--mcu)
12. [El sketch: pantalla TFT y servos](#13-el-sketch-pantalla-tft-y-servos) — [§13.3 Caras](#133-caras-a-color-101), [§13.4 Servos y calibración](#134-servos-y-calibración)
13. [Dashboard web (assets/)](#14-dashboard-web-assets)
14. [Terminal del dashboard: comandos](#15-terminal-del-dashboard-comandos)
15. [Herramientas de generación (tools/)](#16-herramientas-de-generación-tools)
16. [Conexión de hardware](#17-conexión-de-hardware)
17. [Problemas conocidos y su estado](#18-problemas-conocidos-y-su-estado)
18. [Registro de pruebas realizadas](#19-registro-de-pruebas-realizadas)
19. [Checklist de pruebas en el robot (1.1.0)](#20-checklist-de-pruebas-en-el-robot-110)

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
│  │           + Vosk (local) │  provide() │    (carita a color)  │   │
│  │ brain.py  personalidades │            │  - 2 servos SG90     │   │
│  │ llm_router PLUS/ESSENT.  │            │    (gestos)          │   │
│  │ bang.py   metodología    │            └──────────────────────┘   │
│  │ rag.py / guardrails.py   │                                       │
│  │ guides.py desbloqueos    │      llamacpp-models-runner           │
│  │ gestures.py → Bridge     │      (contenedor aparte, modelo       │
│  │ bt.py     cliente BT     │       local de ESSENTIALS)            │
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
  Text-to-Speech**. Mientras el guía habla, escucha **localmente con Vosk**
  (sin costo de API): si el niño habla, el guía se calla
  ([§10.6](#106-escucha-activa-el-robot-se-calla-cuando-el-niño-habla)).
  El navegador solo es un *dashboard* de lectura/estado y una terminal de
  comandos — no transporta audio.
- **El MCU nunca decide nada**: solo recibe códigos empaquetados en un
  entero (`Bridge.notify("face_gesture", persona*8 + gesto)`,
  `Bridge.notify("viseme", 0..10)`, etc.) y traduce eso a píxeles en la
  pantalla y ángulos de servo. Toda la lógica (personalidad, fases BANG,
  LLM, emoción) vive en Python.
- **Nunca se bloquea el hilo del Bridge**: las funciones que Python invoca
  en el sketch (`face_gesture`, `viseme`, `mouth_level`, `arm_step`, `splash`, `card`)
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
├── app.yaml                  # nombre, bricks (web_ui, cloud_llm, llm), icono — NO versionado (API_KEY)
├── app.yaml.example          # plantilla versionada de app.yaml
├── README.md                 # guía rápida de uso
├── DOCUMENTACION.md          # este documento
├── CHANGELOG.md              # qué cambió en cada actualización
├── INFORME-MODELOS-LOCALES.md # medición de modelos locales en la placa (base de ESSENTIALS)
├── google-credentials.json   # service account de Google Cloud (STT/TTS) — NUNCA versionar
├── memory.md                 # notas de desarrollo / decisiones
│
├── python/
│   ├── main.py                # bucle principal, terminal, bienvenida, WebUI
│   ├── brain.py                # personalidades + llamada al LLM (Gemini)
│   ├── llm_router.py          # modo PLUS (Gemini) / ESSENTIALS (modelo local) y respaldo
│   ├── rag.py                  # BM25 en Python puro sobre knowledge/ + identty/
│   ├── guardrails.py          # respuestas fijas de seguridad y limpieza de la salida
│   ├── bang.py                # metodología BANG: fases, tarjetas, guardarrailes
│   ├── curioso.py             # modo CURIOSO: charla libre + órdenes ("ponte feliz")
│   ├── voice.py                # STT/TTS, visemas, Bluetooth, escucha activa nativa (Vosk), cambio de guía
│   ├── guides.py                # qué guías están desbloqueados (persiste en data/)
│   ├── gestures.py             # texto → gesto, envío por Bridge
│   ├── bt.py                    # cliente HTTP del ayudante de Bluetooth del host
│   └── requirements.txt        # google-cloud-speech/texttospeech, qrcode, vosk
│
├── sketch/
│   ├── sketch.ino               # carita TFT + 2 servos, lado MCU
│   ├── sketch.yaml              # librerías Arduino (Adafruit GFX/ST7789, Servo...)
│   ├── faces_colors.h           # cuántos guías con cara hay (los colores salen de los SVG)
│   ├── face_sprite.h            # formato de las caras a color: capas, estados, RLE
│   ├── <guia>_face.h            # cara a color de cada guía (generada desde sus SVG)
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
│   ├── llm_mode.txt              # "plus" | "essentials" (cerebro elegido)
│   ├── chat_mode.txt             # "bang" | "curioso" (modo de conversación)
│   ├── tts_cache/                # los "¡Dime!" de cada guía, ya sintetizados
│   ├── bt_output.txt             # MAC de la bocina preferida
│   └── .bt_token                 # token del ayudante de Bluetooth del host
├── identty/                     # fichas de personalidad de cada guía (fuente)
├── knowledge/                   # conocimiento del modo ESSENTIALS (RAG), versionado
│   ├── comportamiento.md          # cómo actúa el guía con el niño
│   ├── bang_metodologia.md        # fases sólida/gaseosa/líquida y tarjetas
│   └── guias/<guia>.md            # perfil, cuándo elegirlo, pistas por fase, lectura de sus 10 tarjetas
├── models/                      # NO versionado (.gitignore)
│   ├── vosk-es/                   # modelo Vosk en español (lo baja tools/install_vosk_model.py)
│   └── test_clips/                # audios de prueba de tools/test_barge_in.py --vosk
└── tools/                        # scripts de generación y utilidades
    ├── make_cards.py              # genera sketch/bang_cards.h desde las imágenes
    ├── make_face_sprites.py       # genera sketch/<guia>_face.h (+ preview PNG) desde los SVG
    ├── install_vosk_model.py      # baja el modelo Vosk a models/vosk-es/ (idempotente)
    ├── make_splash.py             # genera sketch/bang_splash.h desde el GIF
    ├── bt_helper.py                # servicio HTTP del host (Bluetooth), puerto 7010
    ├── bt_speaker.sh                # conectar bocina emparejada a mano
    ├── install_bt_helper.sh        # instala el servicio systemd --user
    ├── fix_bt_lightdm.sh            # workaround de permisos Bluetooth en LightDM
    └── test_*.py                    # pruebas: caras, welcome, flujo BANG, escucha activa (test_barge_in.py), modo Curioso (test_curioso.py)
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

- **`app.yaml` NO se versiona** (está en `.gitignore`): en el repositorio va
  `app.yaml.example`, la plantilla con `API_KEY: TU_API_KEY_AQUI` y el brick
  `arduino:llm` del modo ESSENTIALS. Para montar la App en una placa nueva:
  `cp app.yaml.example app.yaml` y poner la clave desde **App Lab → Bricks →
  Cloud LLM → `API_KEY`** (Brick Configuration). Nunca pegar la clave en el
  código, en `README`/`DOCUMENTACION` ni en un commit.
- Historial: `app.yaml` estuvo versionado en `3adb6ad` y se quitó en
  `9976c2b`, pero en ninguna de esas versiones subidas a GitHub hay una
  clave (revisado el 2026-10-02). El commit local que sí la tenía lo
  bloqueó la protección de secretos de GitHub y no se subió. Si alguna vez
  una clave llega a un commit publicado, hay que **rotarla** en
  <https://aistudio.google.com/apikey>.
- El modo ESSENTIALS no usa clave: el brick `arduino:llm` (modelo
  `llamacpp:Qwen3.5-0.8B-Q4_0`) corre en la placa.

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

Eso es el modo **PLUS**. En el modo **ESSENTIALS** contesta
`llamacpp:Qwen3.5-0.8B-Q4_0` en la propia placa (brick `arduino:llm`,
declarado en `app.yaml` junto a `arduino:cloud_llm`): ver [§9.1](#91-modos-essentials--plus).

---

## 6. Flujo de un turno de voz

```
 1. loop() en main.py llama a voice.listen_turn(...)
 2. listen_turn() abre streaming a Google STT y espera:
      - una wake word ("Crispi", "Carmel", ..., "robot", "Bang"), o
      - una respuesta dentro de la ventana de follow-up (7 s tras la
        última respuesta del guía, sin repetir el nombre)
    En cada texto parcial busca además un PEDIDO DE CAMBIO DE GUÍA
    ("quiero hablar con Cori", "pásame a Cristal"...: voice.find_switch),
    antes de asignarle la frase al guía del follow-up (ver §7.1).
 3. En cuanto el texto deja de cambiar 0.7 s (_ENDPOINT_S), la frase se
    da por terminada — no se espera el is_final de Google (1-2 s más).
 4. voice.ack() → pitido corto de "te escuché" (no bloqueante, mientras
    el LLM piensa)
 5. main.py resuelve qué guía responde:
      - pedido de cambio, o el nombre de OTRO guía con un reto en curso →
        _switch_to(): el reto pasa al nuevo guía (§7.1)
      - nombre explícito → ese guía (si desbloqueado)
      - "robot"/"bang" con reto en curso → el guía de ese reto
      - "robot"/"bang" sin reto, 1 solo guía desbloqueado → ese guía
      - "robot"/"bang" sin reto, varios desbloqueados → bang.classify()
        (Gemini en PLUS; rag.classify_persona() en ESSENTIALS)
      - si acaban de interrumpir a ese guía (escucha activa, §10.6) y el
        modo es BANG, la frase es un APORTE → bang.contribute() en vez de
        bang.turn()
      - "modo curioso" / "modo bang" no es un turno: cambia el modo de
        conversación y lo dice en voz alta (§9.4)
 6. Según el MODO DE CONVERSACIÓN (§9.4):
      - BANG: bang.turn(persona, texto) procesa el turno según la fase BANG
        activa (sólida / gaseosa / líquida) y devuelve la respuesta + si
        cambió de fase + si se destapó una tarjeta
      - CURIOSO: curioso.turn(persona, texto) responde libre con la
        personalidad del guía, o cumple una orden corta ("ponte feliz",
        "baila") sin llamar al modelo
    Corre en un hilo: si va a tardar (ESSENTIALS, o PLUS con Gemini >3 s),
    el guía dice una frase de relleno
 7. gestures.emotion_of(respuesta) decide el gesto entre los 7 (TALK,
    HAPPY, SURPRISE, ANGRY, FRUSTRATED, SAD; REST al final) por palabras
    clave y signos de exclamación, sin llamada extra al LLM (§12)
 8. gestures.send(gesto, persona) → Bridge.notify("face_gesture", ...)
    ANTES de hablar: como Python controla el parlante, sabe exactamente
    cuándo empieza/termina la voz
 9. voice.say(persona, respuesta, barge=...):
      - deja a listen_turn sordo, pero con escucha activa el micrófono
        sigue abierto para Vosk (pause_listening(keep_mic=True))
      - parte el texto en frases, las sintetiza EN PARALELO
        (ThreadPoolExecutor, 3 workers) con Google TTS
      - reproduce en orden; la primera frase suena en cuanto está lista
      - por cada bloque de audio que SUENA manda el visema de ese momento
        → Bridge.notify("viseme", 0..10) (mouth_level 0..4 queda de respaldo)
      - si Vosk oye una interrupción, corta la voz entre bloques de ~43 ms,
        cierra la boca y main.py atiende la interrupción (§10.6)
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
- Cada guía tiene una voz Chirp3-HD distinta (`voice._VOICES`) y su propia
  cara a color, generada de los SVG de `assets/img/<guia>/` (ver
  [§13.3](#133-caras-a-color-101)). La cara geométrica genérica ya no existe.

### 7.1 Cambio de guía

El niño puede pedir otro guía en cualquier momento, **también mientras un
guía está hablando** (por la escucha activa, [§10.6](#106-escucha-activa-el-robot-se-calla-cuando-el-niño-habla)).

**Frases que lo piden** (`voice.find_switch()`, sin tildes ni mayúsculas, con
los alias de `_WAKE_ALIASES`: "krystal", "cory", "carmelo"...):

| Frase | Ejemplo |
|---|---|
| "quiero / quisiera / puedo / me gustaría hablar (conversar, charlar, seguir) con" | "quiero hablar con Cori" |
| "pásame con / pásame a" | "pásame a Cristal" |
| "cámbiame a / con / por" | "cámbiame a Crispi" |
| "ahora con" | "ahora con Carmel" |
| "que (me) hable" | "que hable Cesia" |
| "llama a" | "llama a Cori" |
| "habla (con)" | "habla con Cristal" |

- El nombre va **justo después** de la frase (como mucho un "el"/"la"):
  "quiero hablar con mi mamá" o "Cori dijo que quiero hablar con mi mamá" no
  son cambio.
- "ahora con" y "habla" a secas son palabras de todos los días: solo
  cuentan **al empezar la frase** (como mucho una palabra antes) y si después
  del nombre no sigue nada (o va una coma). "Mi mamá habla con Cristal" no es
  cambio.
- La cortesía después del nombre se descarta (`_strip_courtesy`: "por
  favor", "porfa", "gracias", "ya", "ok", "vale"...): "pásame a Cristal,
  gracias" es solo el cambio, no un turno nuevo que diga "gracias". Si
  después del nombre viene algo con contenido ("pásame con Cori, se me
  ocurrió un mural"), primero saluda el nuevo guía y luego eso sigue como
  turno normal suyo.
- El cambio se detecta en **cada texto parcial** de la frase, sea cual sea
  el guía que la esté recibiendo: funciona aunque la frase empiece con
  otro nombre ("Crispi, quiero hablar con Cori") o con "robot", y aunque
  llegue dentro de la ventana de follow-up del guía anterior.
- Decir solo el **nombre de otro guía** mientras hay un reto en curso
  también pasa el reto a ese guía.

**Qué pasa al cambiar** (`main._switch_to()`):

1. Si el guía pedido está **bloqueado**, el guía que ya estaba (con su
   cara) contesta "Cesia todavía está bloqueada, así que sigo yo contigo.
   ¿Seguimos?" y todo sigue con él (reto, cara, follow-up). Si no había
   guía activo, sale el aviso de siempre (`_say_locked`).
2. **Relevo de la sesión** (`bang.handover()`): las sesiones BANG son por
   guía, así que el reto se **mueve** al nuevo guía con su fase, reto,
   pregunta problema, turnos, ideas, aportes y avisos ya dichos
   (`relevo` = guía anterior). En la fase gaseosa el nuevo guía reparte sus
   **propias** tarjetas (las tarjetas son de cada mazo). La memoria de chat
   del nuevo guía se borra (era de otro reto).
3. Se cierra la tarjeta/menú, aparece la cara del nuevo guía (gesto HAPPY)
   y el dashboard se actualiza (`active_persona`, `bang_state`).
4. **Saludo de relevo** con plantilla, sin LLM en ningún modo
   (`bang.handover_greeting()`), por ejemplo: *"¡Hola, soy Cori! Cesia me
   contó tu reto: «...». Ya llevas 2 ideas. Traje mis propias tarjetas:
   dime 'saca una tarjeta' o cuéntame otra idea. ¿Seguimos?"*. Si no había
   reto: *"¡Hola, soy Cori, ...! Cuéntame, ¿cuál es tu reto?"*.
5. El saludo también se puede interrumpir; el follow-up queda con el nuevo
   guía. En ESSENTIALS se precalienta en segundo plano el prompt del nuevo
   guía.

Limitación: el nombre de un guía es wake word **en cualquier parte** de la
frase (comportamiento previo a 1.0.1). "Ahora con cristal hacemos ventanas"
no es un cambio de guía, pero sí se vuelve un turno normal de Cristal (sin
relevo del reto).

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

En modo ESSENTIALS no hay reescritura con otra llamada: ver [§9.3](#93-guardarraíles-deterministas-guardrailspy).
En modo PLUS, `bang._polish()` reescribe la respuesta si:

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
autorrealización) y devuelve el guía más afín. En modo ESSENTIALS, o si
Gemini falla, lo decide `rag.classify_persona()` (BM25 sobre la sección
"Cuándo elegirme" de `knowledge/guias/` y los rasgos de `identty/`), sin
LLM; si nada coincide, Crispi.

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
  importar langchain + autenticar) antes de que alguien pregunte. En modo
  ESSENTIALS, en cambio, carga el modelo local (~22 s).
- Desde los modos, el código de Gemini es `chat_gemini()`; `chat()` es una
  capa fina sobre `llm_router.chat()` y los llamadores de `bang.py` no
  cambian (solo pasan, además, `local=(system, texto)` cortos y
  `fallback=False` en las llamadas secundarias).

### 9.1 Modos ESSENTIALS / PLUS

| | **PLUS** (por defecto) | **ESSENTIALS** |
|---|---|---|
| Quién contesta | Gemini en la nube (`arduino:cloud_llm`) | Qwen3.5-0.8B en la placa (`arduino:llm`, `llamacpp-models-runner`) |
| Velocidad | ~1-3 s por turno | ~10-20 s por turno (estimado; ver abajo) |
| Llamadas al LLM por turno | 1 a 2 (reescritor del facilitador) | **como mucho 1** |
| Sólida, tarjetas, resumen de ideas, clasificador | Gemini | plantillas + RAG (0 llamadas) |
| Aporte tras interrumpir (§10.6) | 1 llamada a Gemini | plantilla (0 llamadas) |
| Saludo al cambiar de guía (§7.1) | plantilla | plantilla |
| Si falla | ese turno lo contesta el modelo local | `FALLBACK_REPLY` |
| Internet | sí | **sí, para la voz**: STT y TTS son de Google Cloud |

> ESSENTIALS **no** es "sin internet": la conversación la piensa la placa,
> pero escuchar (Google Speech-to-Text) y hablar (Google Text-to-Speech)
> siguen en la nube. Sin red, el robot no oye ni habla. Lo único 100 %
> local de la voz es la escucha activa (Vosk, §10.6).

- **Cómo se cambia**: botones **💾 ESSENTIALS / ☁️ PLUS** del dashboard
  (piden confirmar) o `/modo plus|essentials` en la terminal. Se guarda en
  `data/llm_mode.txt` y sobrevive a reinicios. Al cambiar se borran las
  memorias de chat de los dos cerebros (el reto en curso se conserva) y,
  al pasar a ESSENTIALS, se precalienta el modelo en segundo plano.
- **Respaldo automático en PLUS**: si Gemini devuelve `None` (sin
  `API_KEY`, cuota agotada, 503, timeout), `llm_router.chat()` contesta ese
  turno con el modelo local, usando el prompt corto de ESSENTIALS, y lo
  avisa en el panel de diagnóstico (`☁️✗ Gemini no respondió...`). El
  clasificador, el resumen de ideas, la tarjeta y el aporte NO usan el
  respaldo local (`fallback=False`): caen a sus plantillas, que son
  instantáneas.
  - **Cortacircuito**: tras un fallo de Gemini, durante
    `GEMINI_COOLDOWN = 90 s` ni se intenta Gemini (los turnos van directo al
    modelo local y las llamadas secundarias a su plantilla). Un acierto lo
    reinicia. Así no se pagan los timeouts de los 3 modelos en cada turno.
  - **Arranque en frío**: si el modelo local no contestó en los últimos
    `LOCAL_WARM_TTL = 600 s`, `chat_local()` primero lo precalienta
    (`LOCAL_COLD_TIMEOUT = 120 s`, 1 token) y después hace la llamada real
    (`LOCAL_TIMEOUT = 60 s`).
  - **Frase de relleno en PLUS**: si el turno no terminó en
    `PLUS_FILLER_AFTER = 3 s`, el guía dice la frase corta de relleno.
- **Por qué ESSENTIALS es así** (cifras de `INFORME-MODELOS-LOCALES.md`):
  el modelo lee el prompt a ~9 tokens/s y genera a ~5 tokens/s; cargarlo
  cuesta ~22 s. El prompt de la sólida de PLUS (~450 tokens) tardaría ~48 s
  solo en leerse. Por eso:
  - `brain.local_system(guia)`: system prompt de ~420 caracteres, **fijo
    por guía** (llama.cpp reusa el prefijo en caché). Identidad de robot,
    un solo niño de 5-14 años, 1-2 frases y una pregunta, nunca enojarse
    con el niño, siempre su reto.
  - Lo que cambia va en el mensaje del usuario (`bang._local_user()`,
    ~300 caracteres): fase, reto (15 palabras), **pista del RAG**
    (≤200 caracteres), la tarea de la fase y lo que dijo el niño.
  - `llm_router`: instancias de `LargeLanguageModel` creadas una vez y
    reusadas (el constructor hace `list_models()` por HTTP), memoria de
    4 mensajes, `max_tokens=70`, `timeout=60`, `max_retries=0`, y un
    candado alrededor de la generación (el brick lanza `AlreadyGenerating`
    con dos generaciones a la vez).
  - Sin JSON: la **sólida la cierra Python** (`LOCAL_SOLID_TURNS = 3`: el
    reto + 2 respuestas) con la plantilla `bang._como_podriamos()`
    ("quiero que mis amigos reciclen" → "¿Cómo podríamos lograr que tus
    amigos reciclen?").
  - Sin segunda llamada: el clasificador es `rag.classify_persona()`, el
    resumen de ideas y la tarjeta salen de plantillas (la tarjeta, con su
    lectura de `knowledge/guias/<guia>.md`), y el reescritor del
    facilitador se reemplaza por `guardrails.quitar_frases()`.
  - **Frase de relleno**: si el turno va a esperar al modelo local
    (`bang.slow_turn()`), `main.py` lo piensa en un hilo y mientras tanto el
    guía dice algo corto ("Mmm, déjame pensarlo un momento..."). Al
    presentarse un guía (`_greet()`), se cachea su prompt en segundo plano.
- **Latencia esperada** (de `INFORME-MODELOS-LOCALES.md`, Qwen 3.5 0.8B en
  esta placa): leer ~9,3 tokens/s, generar ~4,85 tokens/s, cargar el modelo
  ~21,6 s. Con el prompt genérico del informe la primera frase tardó
  3,6 s de media y la respuesta completa 11,3 s; con un prompt largo, 22 s.
  Con `max_tokens=70` (≈14 s de generación como tope) y los prompts cortos
  de arriba, lo esperable es **~10-20 s por turno**, más ~1 s de TTS: por eso
  la frase de relleno. El primer turno después de cargar paga además los
  ~22 s de carga (por eso el precalentamiento al pasar a ESSENTIALS y al
  presentarse cada guía).
- **RAM**: el modelo corre en un contenedor aparte
  (`llamacpp-models-runner`, lo levanta App Lab porque `arduino:llm` está en
  `app.yaml`); reserva hasta ~2,5 GB y **queda encendido también en PLUS**.
  La placa tiene 3,58 GiB y la App en Python usa ~150 MB.
- **Pendiente de medir en la placa** (ver [§20](#20-checklist-de-pruebas-en-el-robot-110)):
  la latencia real de ESSENTIALS con estos prompts y la RAM con el runner
  y Vosk cargados a la vez. Nada de esto se pudo medir sin reiniciar la App.

### 9.2 RAG: `rag.py` y la carpeta `knowledge/`

- BM25 en Python puro (k1=1.5, b=0.75), sin dependencias. Tokens en
  minúsculas y sin tildes, sin palabras vacías del español, raíz pobre
  (sin plural y cortada a 6 letras).
- Fuentes, leídas al importar: `knowledge/*.md`, `knowledge/guias/<guia>.md`
  y de `identty/` el perfil, rasgos, "cómo te ayuda" y tono de cada guía,
  más los pasos y principios del informe general. **No** se toman las
  cartas de `identty/`: ahí algunas no coinciden con `bang.CARDS`, que es
  la fuente de verdad.
- `retrieve(frase, guia, fase, k=2, max_chars=200)`: los fragmentos del guía
  y generales de esa fase (o de cualquiera), con más peso a los de la fase
  y a los del guía. Si nada coincide, la pista de la fase del propio guía.
- `card_reading(guia, texto)`: la lectura de una tarjeta (`## Tarjetas`).
- `classify_persona(reto, candidatos)`: qué guía acompaña mejor el reto.
- Prueba rápida, sin arrancar nada:
  `docker exec robot-bang-stable-main-1 python3 /app/python/rag.py "mi reto es..." crispi solida`

**Cómo editar `knowledge/`** (lo explica también `knowledge/README.md`):
cada viñeta `- ...` es un fragmento (una o dos frases, menos de 220
caracteres, en español sencillo). El título `## ...` le pone etiquetas: si
nombra una fase (sólida, gaseosa, líquida) solo se usa en esa fase;
`## Tarjetas` lleva una viñeta por carta con el formato
`- «Texto exacto de la carta»: cómo se usa` (el texto, igual al de
`bang.CARDS`); `## Cuándo elegirme` alimenta el clasificador. Los cambios
se ven al reiniciar la App.

### 9.3 Guardarraíles deterministas (`guardrails.py`)

Portados de `bang-bakeoff`, donde los dos modelos locales llegaron a decir
que eran personas. Lo innegociable no se le confía al modelo:

- `respuesta_fija(texto, guia)` corre **en los dos modos**, al principio de
  `bang.turn()` y de `bang.contribute()` (antes de los comandos de voz y de
  cualquier LLM). En orden:
  - **grave** ("me quiero morir", "suicid...", pero no "morirme de risa") →
    corta siempre, con una respuesta amable que lo lleva a un adulto;
  - **malestar en primera persona** ("estoy muy triste", "me pegan", "me
    hacen bullying", "nadie me quiere") → la misma respuesta amable;
  - **peligro** (armas, pistolas, drogas, asesinar; contenido sexual
    siempre) → que lo hable con un adulto. "Pistola de agua/silicona" no
    cuenta;
  - **datos privados solo cuando el niño los DA** ("mi teléfono es...",
    "vivo en la calle...", un correo, un número de 7+ cifras) → que no los
    cuente. "Mi celular se descarga rápido", "saca una tarjeta" o "la clave
    del reto" NO se bloquean;
  - **identidad**: "¿eres un robot?" → "Sí, soy un robot, un personaje
    virtual..." (coherente con el aviso de arranque); "¿eres una persona de
    verdad?" → "No, no soy una persona de verdad...". "Estas ideas son de
    verdad buenas" no cuenta (hace falta "estás" con tilde o una pregunta).
- **Un tema no es un caso**: si el malestar o el peligro vienen dichos como
  reto ("mi reto es que no haya bullying", "evitar que vendan drogas en el
  parque", "reducir la violencia en el recreo": patrón `_RETO`), el turno
  **sigue** y `aviso_adulto()` antepone, **una sola vez por reto**
  (`Session.avisos`), una frase que invita a hablarlo con un adulto.
- `limpiar(texto)` se aplica a la salida del modelo: quita emojis,
  markdown, etiquetas copiadas del prompt ("Crispi:"), "busca en internet",
  frases donde dice ser humano, frases que **piden datos personales**
  ("¿cómo te llamas?", "¿dónde vives?", "¿en qué colegio...?"), el plural
  ("hola a todos", "ustedes") y deja como mucho 2 preguntas.
- `quitar_frases(texto, bang._BANNED)` quita, en sólida y líquida, las
  frases que dan soluciones (el reescritor local).

**Cómo editar los guardarraíles**: son expresiones regulares al principio
de `python/guardrails.py` (`_MALESTAR`, `_GRAVE`, `_PELIGROSO`,
`_PELIGROSO_SIEMPRE`, `_DATO_SENSIBLE`, `_RETO`, `_HUMANO`, `_ROBOT`,
`_PIDE_DATOS`) y los textos fijos (`_TXT_MALESTAR`, `_TXT_PELIGROSO`). Se
escriben **sin tildes** (el texto se compara sin tildes y en minúsculas).
Antes de cambiar uno, pensar en los retos legítimos que podría bloquear
(bullying, violencia, drogas son retos típicos de Cesia y Cori). Después de
editar, correr `tools/test_barge_in.py` (incluye un caso de seguridad del
aporte) y probar unas frases a mano:
`docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python -c "import sys; sys.path.insert(0,'/app/python'); import guardrails; print(guardrails.respuesta_fija('mi reto es que no haya bullying', 'Cori'))"`
(debe imprimir `None`). Los cambios se ven al reiniciar la App.

---

### 9.4 Modos de conversación: BANG / Curioso (`curioso.py`)

> **Ojo con la palabra "modo"**: hay DOS cosas distintas. El **cerebro**
> ([§9.1](#91-modos-essentials--plus)) es *con qué* piensa el robot (Gemini
> o modelo local). El **modo de conversación** de aquí es *de qué* habla.
> Son independientes: Curioso + Essentials es una combinación válida.

Desde 1.1.0 el niño elige, **al empezar**, cómo quiere conversar:

| | **BANG** (por defecto) | **CURIOSO** |
|---|---|---|
| Qué hace | el guía lleva su reto por las fases sólida → gaseosa → líquida, con tarjetas e ideas ([§8](#8-la-metodología-bang-fases)) | charla libre: responde lo que le pregunten y obedece órdenes cortas, como un asistente de voz |
| Quién responde | `bang.turn()` | `curioso.turn()` |
| Memoria del LLM | `(persona, fase)` | `(persona, "curioso")` — no se mezclan |
| Tarjetas, fases, aportes | sí | no (una interrupción es un turno más) |
| Personalidad del guía | sí | **sí**: el prompt sigue siendo el de Crispi, Carmel, Cesia, Cori o Cristal |
| Guardarraíles de seguridad | sí | **sí, los mismos** ([§9.3](#93-guardarraíles-deterministas-guardrailspy)) |

**Cómo se elige**:

1. **Por voz, en el arranque** (paso 4 de `main._boot_sequence()`, después
   de la bienvenida y antes del menú de guías): la presentadora pregunta
   *"¿BANG o Curioso?"* y espera hasta `MODO_WAIT_S = 40 s`. Vale decir
   "bang", "curioso", "modo curioso"... (`curioso.find_mode(texto,
   bare=True)`). La pregunta **se puede interrumpir**: si el niño contesta
   antes de que termine, lo que oyó Vosk cuenta como respuesta. Si contesta
   con el nombre de un guía ("¡Cori!"), se queda el modo que hubiera y ese
   turno no se pierde: pasa a `loop()` por `_pending_turn`. Si no se
   entiende, se pregunta una segunda vez y luego sigue con el modo actual.
2. **Por voz, en cualquier momento**: "modo curioso", "cambia a curioso",
   "modo bang", "quiero hacer un reto". Aquí hace falta que la frase suene
   a pedido (`find_mode(texto)` sin `bare`), para que "Bang" siga siendo
   palabra de activación y "¡qué curioso!" no cambie nada.
3. **Desde el dashboard**: selector **🎛 MODO: 🚀 BANG / 💬 CURIOSO**.
4. **Desde la terminal**: `/modo_chat bang|curioso`, y `/elegir_modo` para
   volver a preguntarlo por voz.

El modo se guarda en `data/chat_mode.txt` (sobrevive a un reinicio). Al
cambiarlo se limpian las memorias de chat (`brain.clear_all()`), pero **los
retos en curso NO se borran**: al volver a BANG, el reto sigue donde estaba.

**Qué hace el modo Curioso** (`curioso.turn()`, en este orden):

1. `guardrails.respuesta_fija()` — malestar, peligro, datos privados,
   identidad. Se le quita la colita "¿Seguimos con tu reto?" (`_sin_reto()`),
   que aquí no viene a cuento.
2. **Órdenes cortas**, resueltas en Python (sin modelo, sin latencia):

   | Lo que dice el niño | Qué hace |
   |---|---|
   | "ponte feliz", "sonríe", "ríete" | cara + brazos HAPPY |
   | "ponte triste" | SAD |
   | "ponte enojado/bravo", "haz grr" | ANGRY (siempre de juego, nunca contra el niño) |
   | "sorpréndete" | SURPRISE |
   | "ponte normal", "descansa" | REST |
   | "baila", "canta", "celebra", "fiesta" | HAPPY + la cancioncita y el baile de brazos (`voice.celebrate()`), **después** de hablar |
   | "salúdame", "di hola" | WAVE: saluda con un brazo |
   | "aplaude", "dame un aplauso" | CLAP: aplaude con los dos brazos |
   | "piensa", "ponte a pensar" | THINK: un brazo arriba, quieto |
   | "di que sí" / "di que no" | YES / NO: asiente o niega (en espejo) |
   | "muévete", "mueve los brazos" | DANCE: el baile, sin la cancioncita |
   | "abrázame", "dame un abrazo" | HUG: los dos brazos suben y se quedan |
   | "duérmete", "ponte a dormir" | SLEEP: brazos caídos, respiración lenta |
   | "estírate", "haz un bostezo" | STRETCH: hasta arriba, se queda y baja |

   La frase que acompaña al gesto concuerda en género con el guía
   ("contenta" / "contento"). El gesto viaja en `Reply.gesture` y `main.py`
   lo usa en vez del que saldría del texto (`gestures.emotion_of`). La orden
   tiene que ir **al empezar la frase** (antes solo caben el nombre del guía,
   "oye", "por favor"...) y **no cuenta dentro de una pregunta**: "¿por qué
   la gente aplaude en los conciertos?" se responde, no se aplaude.
3. **Preguntas de siempre** con plantilla: "¿cómo te llamas?", "¿qué puedes
   hacer?", "¿en qué modo estamos?".
4. **Lo demás va al cerebro del modo activo** (Gemini o modelo local, igual
   que BANG), con un prompt que mantiene la personalidad del guía y le pide
   explícitamente que **no** hable de retos ni de fases si el niño no los
   menciona: 1 a 3 frases cortas, sin markdown ni emojis, como mucho una
   pregunta. La salida pasa por `guardrails.limpiar()`.
5. Si el tema es delicado y viene como reto, el aviso de hablarlo con una
   persona adulta también se antepone aquí.

`curioso.Reply` tiene los mismos campos que `bang.Turn` (`reply`, `source`,
`phase_changed`, `card`) más `gesture` y `celebrate`, para que `main.loop()`
trate los dos modos igual.

**Pruebas**: `tools/test_curioso.py` (sin hardware ni LLM: elección de modo,
órdenes, guardarraíles, prompt y `slow_turn`).

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
- En cada parcial se mira además si es un **pedido de cambio de guía**
  (`find_switch()`, [§7.1](#71-cambio-de-guía)); `voice.last_switch()` le
  dice a `main.py` si la última frase lo fue.

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

### 10.3 Sincronía boca/voz (visemas)

- `_play_synced()` reproduce el PCM de a bloques (`spk.buffer_size`
  muestras, ~43 ms) y reporta qué bloque está *sonando* con un atraso
  (`_mouth_lag()`) que compensa: 2 bloques con headset USB, 6 con bocina
  Bluetooth A2DP (que tiene más buffer propio, ~150-250 ms extra).
- **Visemas (1.0.1)**: `_word_visemes()` pasa el texto de cada frase a
  visemas con reglas del español (ch/sh/ll/j/g+e,i → CHJ; la u de "qu"/"gu"
  muda; rr → R; h muda; y final = vocal; b/m/p/v → BMP...).
  `_viseme_track()` los reparte sobre los bloques **con voz** del audio de
  esa frase (los silencios dan visema 0) y `_merge_short()` hace que cada
  visema dure al menos 2 bloques (~86 ms), para no saturar el SPI. Se manda
  `Bridge.notify("viseme", v)` solo cuando cambia, y siempre se termina en 0.
  Google Chirp3-HD no da tiempos de fonemas: el reparto es una estimación.
- `mouth_level` (0-4, por volumen RMS normalizado contra el percentil 90 de
  esa frase) queda como **respaldo**: se usa cuando no hay texto o si se
  llama `voice.set_viseme_reporter(None)` (por ejemplo, si hubiera que
  correr Python nuevo con un sketch viejo).
- Respaldo `espeak`: `_blind_visemes()` recorre los visemas a un ritmo
  estimado mientras suena.

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
  Mientras suena una voz, `listen_turn()` está **sordo** (`_is_deaf()`): el
  STT de Google nunca oye al robot.
- `_ECHO_TAIL_S = 0.8 s` de sordera extra al terminar de hablar, porque
  con bocina Bluetooth el propio buffer de la bocina sigue sonando un
  rato después de que Python terminó de escribir el audio. Tras el
  "¡Dime!" de la escucha activa, con headset USB la sordera es solo
  `_DIME_TAIL_S = 0.15 s` (el niño arranca a hablar enseguida).
- Con escucha activa, `pause_listening(keep_mic=True)` deja el micrófono
  abierto para Vosk, y ninguna otra voz (bienvenida, aviso, terminal) lo
  cierra mientras la escucha activa lo está leyendo.

### 10.6 Escucha activa: el robot se calla cuando el niño habla

**Es nativa: no se prende ni se apaga.** Desde 1.1.0 forma parte de cómo
conversa el robot, igual que en un asistente de voz: mientras un guía habla,
si el niño habla, **el guía se calla y escucha**. No hace falta decir ninguna
palabra mágica.

Mientras un guía habla, el robot **escucha localmente con Vosk**. **Costo de
API: cero**: Vosk corre en la placa y no se abre el streaming de Google
mientras suena la voz. Lo único que se paga es el turno normal de Google STT
que viene después (el mismo que se pagaría sin interrumpir).

**Cómo funciona** (`voice.BargeIn`, `main._speak()` / `_on_barge()`):

1. `main._speak()` crea un `BargeIn` para **cada** respuesta
   (`voice.make_barge`) y llama `voice.say(persona, texto, barge=...)`. Un
   hilo lee el micrófono (16 kHz, sin remuestrear) y se lo pasa a un
   `KaldiRecognizer` **con reconocimiento libre** (sin gramática: hasta
   1.0.1 había una gramática cerrada que solo oía las palabras clave, y por
   eso el robot seguía hablando cuando el niño decía cualquier otra cosa).
2. Hay **tres tipos de disparo** (`_barge_trigger()` devuelve `kind`):

   | `kind` | Cuándo | Qué hace `main._on_barge()` |
   |---|---|---|
   | `switch` | "quiero hablar con Cori", "pásame a Cristal", o el nombre de OTRO guía | `_switch_to()`: el reto pasa al nuevo guía ([§7.1](#71-cambio-de-guía)) |
   | `interrupt` | una clave corta: el nombre del guía que habla, "espera", "oye", "se me ocurrió algo", "tengo una idea", "un momento" | el guía dice **"¡Dime!"** (audio en caché, sin esperar al TTS) y espera el aporte |
   | `speech` | **el niño simplemente está hablando** (cualquier frase que no sea eco del robot) | **no dice nada**: se calla y escucha. Hablarle encima es justo lo que hay que evitar |

3. Si dispara, `say()` corta la voz entre bloques de ~43 ms (vacía el buffer
   del parlante), cierra la boca (visema 0) y devuelve
   `{"interrupted": True, "spoken": <lo que alcanzó a decir>, "trigger": ...}`.
   `main.py` manda el gesto REST, marca la respuesta como cortada
   (`bang.mark_interrupted`: el próximo prompt sabe que se cortó) y avisa al
   dashboard (`interrupted`).
4. **El aporte** (solo en modo BANG): lo siguiente que se le diga a ESE guía
   en los próximos `APORTE_WAIT_S = 20 s` (y que no sea un cambio de guía) se
   escucha con el `listen_turn` normal de Google STT y va a
   `bang.contribute()`. En modo Curioso ([§9.4](#94-modos-de-conversación-bang--curioso-curiosopy))
   no hay aportes: la interrupción es un turno más.
5. **Red de seguridad contra el hueco del arranque del STT**: lo que Vosk ya
   entendió se guarda (`_awaiting_aporte["oido"]`). Si el STT de Google no
   alcanza a captar nada (el niño dijo una frase corta justo al interrumpir),
   se usa ese texto en vez de perder el turno; en el diagnóstico sale como
   `🗣 uso lo que oí al interrumpir: «...»`.
6. `bang.contribute(persona, texto, lo_dicho, use_llm)`:
   - primero `guardrails.respuesta_fija()` (como un turno);
   - sin reto todavía, el aporte **es** el reto: va por `bang.turn()`;
   - quita las muletillas ("se me ocurrió que...", "tengo una idea...");
     si no queda nada o es "no", "nada", "olvídalo", "ya no" → **no se
     guarda basura** y contesta "¡Vale! Seguimos con tu reto.";
   - tema delicado (`aviso_adulto`) → una vez por reto **recuerda hablarlo
     con un adulto**, **no repite** la frase del niño en voz alta y contesta
     "Ya lo agregué a tu reto. ¿Seguimos?" (sin LLM);
   - se guarda en `Session.aportes` (y en la gaseosa también en `ideas`; en
     la sólida los aportes pasan a ideas al llegar a la gaseosa);
   - **PLUS**: UNA llamada a Gemini (reconoce el aporte con su voz, dice
     que quedó agregado, lo conecta con el reto, máx. 1 pregunta), pasada por
     el mismo filtro de facilitador, `limpiar()` y límite de 1 pregunta. Si
     Gemini falla → plantilla;
   - **ESSENTIALS**: plantilla, **sin llamar al modelo local**: *"¡Listo,
     agregado! «idea corta» ya queda en tu reto: es tu idea número 4.
     ¿Seguimos?"*.
7. **Sin límite de interrupciones encadenadas**: la respuesta a un aporte, y
   la respuesta a esa, también se pueden cortar (hasta 1.0.1 se dejaba de
   escuchar a partir de la segunda, `BARGE_MAX_DEPTH`, y el robot hablaba
   encima del niño justo cuando más se estaba animando a hablar).

Se aplica a: el saludo del guía (`_greet`), las respuestas normales
(incluida la lectura de tarjetas), el saludo de relevo, la respuesta al
aporte y la **pregunta de qué modo quiere** ([§9.4](#94-modos-de-conversación-bang--curioso-curiosopy)).
**No** a la bienvenida, el aviso de seguridad, la música, la frase de relleno
ni el mensaje de guía bloqueado.

**Qué cuenta como "el niño está hablando"** (`_speech_trigger()`): las
palabras oídas que **no** son eco del robot ni muletillas
(`_BARGE_NOISE_WORDS`: "y", "la", "de", "eh", "mmm"...). Hacen falta
`_BARGE_MIN_NOVEL = 2` palabras nuevas con headset y
`_BARGE_MIN_NOVEL_BT = 3` con bocina Bluetooth (ahí el micrófono oye al
robot mucho más fuerte). Medido con clips reales: el guía se calla a los
**0,7-1,7 s** de que el niño empieza a hablar.

**Protecciones contra falsas alarmas y eco** (`_barge_trigger()` y el hilo
de `BargeIn`; las constantes `_BARGE_*` están al principio del bloque de
escucha activa de `voice.py`):

- **Eco por palabra**: se ignora una palabra o frase que se parezca (difflib
  ≥ `_BARGE_ECHO_RATIO = 0.75`) a **cualquier** frase que el robot ya dijo
  en esta respuesta. Si el guía dice "¿se te ocurre algo?", el niño tiene
  que decir también el nombre para que "se me ocurrió" cuente.
- **Eco de frase completa** (nuevo en 1.1.0, `_BARGE_ECHO_PHRASE = 0.55`):
  cuando Vosk entiende MAL la voz del propio robot ("Cesia me contó tu reto"
  → "se seame concedido"), las palabras sueltas ya no se parecen a nada,
  pero la frase entera sí. Sin esto el robot se interrumpía a sí mismo.
  Medido con los clips de `tools/test_barge_in.py --vosk`: eco 0,62-1,00;
  voz de un niño 0,14-0,43.
- **Posición (solo para las claves)**: el disparador tiene que empezar entre
  las 3 primeras palabras (`_BARGE_MAX_LEAD = 2`). Si no cumple, no se
  pierde la interrupción: cuenta como `speech`.
- **Fuerte / débil**: nombre + frase, una frase de cambio, una frase de
  varias palabras o voz suelta disparan desde el primer parcial; un nombre
  solo o un "oye"/"espera" suelto necesita un resultado **final** de Vosk
  con confianza ≥ `_BARGE_MIN_CONF = 0.5` y una frase corta (≤ 3 palabras).
- **Energía**: RMS > max(`_BARGE_MIN_RMS = 300`, 3 × ruido de fondo) y al
  menos 3 bloques con voz en el último 1,5 s. En un resultado **final** la
  ventana se alarga a `_BARGE_VOICED_TAIL = 3 s`: Vosk cierra la frase
  *después* del silencio, cuando la voz ya quedó atrás.
- **Pedido a medias**: si el parcial termina en "quiero hablar con", "pásame
  a"... (`_SWITCH_TAIL`) se espera un parcial más, para no cortar como
  `speech` un cambio de guía que todavía no dijo el nombre.
- **Gracia**: 300 ms (`_BARGE_GRACE_S`) al empezar cada frase.
- **Alias de la escucha activa** (`_BARGE_ALIASES`): cómo oye Vosk los
  nombres sin gramatica que lo guíe — "carmen"/"carmela" → Carmel, "cory" →
  Cori, "crispin" → Crispi, y "concesión" → "con Cesia" (así junta "con
  Cesia"). Son alias **solo** de la escucha activa: en el turno normal manda
  Google STT, y ahí "Carmen" tiene que poder ser una amiga del niño. Se
  aplican también al medir la confianza (`_trigger_conf()`).

**Modelo Vosk**: `vosk==0.3.45` está en `python/requirements.txt` (se
instala solo al reiniciar la App). El modelo (`vosk-model-small-es-0.42`,
~40 MB de descarga, ~58 MB en disco) va en `models/vosk-es/`, que está en
`.gitignore`. `voice.py` lo carga en segundo plano al arrancar
(~1-2 s) y, si falta, lo baja con `tools/install_vosk_model.py`
(idempotente: descarga a una carpeta temporal y renombra al final; marca
`.ok`). A mano:
`docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/install_vosk_model.py`.
Si no hay paquete, modelo o falla la carga, **el robot sigue funcionando
pero ya no se puede interrumpir**: se avisa una vez en el log y en el
diagnóstico del dashboard (`⚠ sin escucha activa (...)`), y `/status` lo
dice (`Escucha activa: nativa, siempre encendida — NO disponible (...)`).

**CPU**: medido **0,45 s de CPU por segundo de audio** (~45 % de un núcleo)
mientras el guía habla, con el reconocimiento libre (con la gramática
cerrada de 1.0.1 era ~0,20-0,35). En ESSENTIALS el modelo local solo usa
todos los núcleos mientras **genera**, no mientras suena la voz.

**Control**: ya no hay interruptor. `/barge` (sin argumentos) muestra el
estado: si Vosk está listo y cuántas palabras nuevas hacen falta con la
salida de audio actual. `/interrumpir <texto>` simula una interrupción sin
niño (ver [§15](#15-terminal-del-dashboard-comandos)).

**Seguridad**: un aporte pasa por los mismos guardarraíles que un turno
([§9.3](#93-guardarraíles-deterministas-guardrailspy)); lo vacío no se
guarda; un tema delicado recibe el recordatorio del adulto (una vez por
reto) y no se repite en voz alta.

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
| `face_gesture` | `personaId * 16 + gesto` (1 entero, 0..79) | antes de hablar y al terminar (REST) |
| `viseme` | `0..10` (0 = reposo/pausa, 1..10 = visema) | al cambiar la forma de la boca mientras suena la voz |
| `mouth_level` | `0..4` | respaldo viejo (solo volumen): el sketch lo pasa a un visema (0→reposo, 1-2→CDG, 3→AEI, 4→O) |
| `arm_step` | `0` o `1` (paridad de la nota) | en cada nota de la celebración |
| `splash` | `1` / `0` | prender/apagar el GIF de bienvenida |
| `card` | `personaId*10 + numero-1`, o `255` para sacarla | mostrar/ocultar una tarjeta BANG |

**Contrato** (tiene que coincidir en `python/gestures.py`,
`sketch/sketch.ino` y `sketch/face_sprite.h`):

- `personaId`: crispi 0, carmel 1, cesia 2, cori 3, cristal 4
  (`gestures.PERSONA_IDS`). Ejemplo: Cristal triste = 4·16+6 = 70.
- Gestos (`gestures.REST..STRETCH` = `G_REST..G_STRETCH` de `sketch.ino`,
  `GESTURE_COUNT = 16`): **REST 0, TALK 1, HAPPY 2, SURPRISE 3, ANGRY 4,
  FRUSTRATED 5, SAD 6, WAVE 7, CLAP 8, THINK 9, YES 10, NO 11, DANCE 12,
  HUG 13, SLEEP 14, STRETCH 15**. El sketch desempaqueta
  `persona = valor / 16`, `gesto = valor % 16`; un guía inválido cae a 0.
  > `GESTURE_COUNT` pasó de 8 a 16 en 1.1.0. Es el **mismo número en los dos
  > lados**: si se cambia en `gestures.py` hay que reflashear el sketch, o
  > los gestos llegan como si fueran de otro guía.
- Visemas (`gestures.VISEME_*` = `FACE_VIS_*` de `face_sprite.h`): 0 reposo,
  1 AEI, 2 BMP, 3 CDG (c d g k n s t x y z), 4 CHJ (ch sh j), 5 F, 6 L, 7 O,
  8 QW, 9 R, 10 U. Los visemas nunca mueven servos.

**Gestos y emociones** (`gestures.emotion_of(respuesta)`, por texto, sin
llamar al LLM; se compara sin tildes y por palabra completa):

| Gesto | Disparador | Cara | Servos (lift: + = brazos arriba) |
|---|---|---|---|
| `REST` (0) | fin de la voz | neutra, parpadea | a 90° y sueltos 350 ms después (no zumban) |
| `TALK` (1) | por defecto mientras habla | neutra + visemas | vaivén de siempre 80°/100° cada 150 ms |
| `HAPPY` (2) | "genial", "excelente", "me encanta", "qué bueno", "muy bien", "lo lograste", "qué chévere", "bacán", "¡eso es!", "¡adelante!"...; o 2+ "!" sin otra pista (como mucho 1 de cada 3 respuestas). También al pasar de fase y en el saludo de relevo | feliz | **manos arriba**: sube a +65 en 250 ms y se balancea ±6 mientras habla |
| `SURPRISE` (3) | "wow", "vaya", "increíble", "sorprend...", "no me lo esperaba", "imagínate", "quién lo diría", "resulta que", "de repente" | sorpresa | **arriba y luego baja un poco vibrando**: salta a +70 (100 ms), espera 200 ms, baja a +45 (300 ms), tiembla ±5 cada 60 ms durante 800 ms; después ±4 alrededor de +45 |
| `ANGRY` (4) | un "grrr" con un obstáculo cerca (problema, obstáculo, bicho, error, bug, fallo, reto, cable, tornillo, pieza...) o "me enoja/enfada/da rabia ese problema..." — ver reglas abajo | enojada | **arriba/abajo rápido y errático**: 10-14 saltos alternados de 10-25° y 50-90 ms, máx. 1,2 s; después vaivén suave ±8 |
| `FRUSTRATED` (5) | "uff", "qué difícil", "complicado", "obstáculo", "me/te cuesta" (no "¿cuánto cuesta?" ni "me cuesta creerlo"), "cuesta mucho", "atascado", "no me sale", "se traba" | ojos de la Cara 3 + boca F | **lento y a media altura**: sube a +40 en 600 ms, baja a +20 (300 ms), pausa 400 ms, baja a 0 (300 ms); dos veces; después ±8 lento (cada 400 ms) |
| `SAD` (6) | "lo siento", "lo lamento", "qué pena", "qué lástima", "triste", "me duele", "qué mal" | triste | **brazos caídos**: bajan a -25 en 700 ms y se mecen ±5 |

**Gestos agregados en 1.1.0** (solo brazos: no traen caras nuevas, el flash
del sketch está al 90 %; toman prestada una cara que ya existe):

| Gesto | Disparador en el texto | Orden de voz (modo Curioso) | Cara | Servos |
|---|---|---|---|---|
| `WAVE` (7) | "hola", "bienvenid...", "mucho gusto", "nos vemos", "hasta luego", "chao". También cuando un guía **se presenta** o llega a un relevo | "salúdame", "di hola" | feliz | **un brazo saluda**: sube a +65 (250 ms) y se mueve entre +65 y +47 cuatro veces; el otro brazo, quieto abajo |
| `CLAP` (8) | "bien hecho", "lo lograste", "felicidades", "bravo", "un aplauso", "excelente trabajo". También **al pasar de fase BANG** | "aplaude", "dame un aplauso" | feliz | **aplaude**: los dos brazos entre +45 y +12, 6 veces, cada 130 ms |
| `THINK` (9) | "déjame pensarlo", "a ver a ver", "dame un segundo", "mmm", "veamos". También en la **frase de relleno** mientras el modelo piensa | "piensa", "ponte a pensar" | ojos del "uff" | **un brazo arriba, quieto**: sube a +55 en 700 ms y casi no se mueve (±3 cada 600 ms) |
| `YES` (10) | "sí claro", "exacto", "así es", "correcto", "por supuesto", "tal cual" | "di que sí", "asiente" | feliz | **asiente**: dos cabezadas +28 → 0, cada 190 ms |
| `NO` (11) | "no es así", "todavía no", "aún no", "no exactamente", "me temo que no" | "di que no", "niega" | neutra | **niega en espejo**: un brazo +25 y el otro -25, alternando 3 veces cada 230 ms |
| `DANCE` (12) | "a bailar", "bailemos", "vamos a bailar" | "muévete", "mueve los brazos" | feliz | **baile propio**: espejo ±45, 6 veces cada 210 ms, y sigue meciéndose ±35. Es independiente del baile de la celebración (`arm_step`) |
| `HUG` (13) | "te quiero", "un abrazo", "ánimo", "no estás solo", "cuenta conmigo", "estoy orgulloso de ti" | "abrázame", "dame un abrazo" | feliz | **abrazo**: los dos brazos suben a +50 en 600 ms y se quedan (±4 lento) |
| `SLEEP` (14) | "buenas noches", "a dormir", "tengo sueño", "dulces sueños" | "duérmete", "ponte a dormir" | triste (ojos caídos) | **dormido**: bajan a -30 en 1,2 s y respiran ±3 cada 900 ms |
| `STRETCH` (15) | "qué bostezo", "me estiro", "ya desperté" | "estírate", "haz un bostezo" | feliz | **bostezo**: suben a +75 (800 ms), se quedan 600 ms y bajan a +10 (700 ms) |

Orden de prioridad: primero los de 1.1.0 (CLAP → THINK → HUG → SLEEP →
STRETCH → DANCE → WAVE → NO → YES), que son frases muy concretas; después
SAD → ANGRY → FRUSTRATED → SURPRISE → HAPPY por palabra → HAPPY por "!" →
TALK. `emotion_of()` nunca devuelve `REST` si el guía está hablando: siempre
hay algún movimiento. Se llama **una vez por respuesta** (eso es lo que
cuentan los límites de frecuencia).

Las **órdenes de voz** del modo Curioso no pasan por `emotion_of()`: las
resuelve `curioso._orden()` y el gesto viaja en `Reply.gesture`
([§9.4](#94-modos-de-conversación-bang--curioso-curiosopy)). Una orden solo
cuenta **al empezar la frase** (antes solo caben el nombre del guía, "oye",
"por favor"...) y **nunca dentro de una pregunta**: "¿por qué la gente
aplaude en los conciertos?" se responde, no se aplaude.

**Reglas del enojo (público infantil)**: el "grrr" es un juego **contra el
problema, nunca contra el niño**.

- Solo cuenta un "grrr" con una palabra de obstáculo a ≤ 40 caracteres, o
  un enojo dirigido a un obstáculo ("me enoja ese bicho").
- Se anula si a ≤ 30 caracteres aparece la persona: tú, te, ti, contigo,
  usted(es), vos, tus, verbos en segunda persona (haces, dices, eres,
  quieres, puedes, sabes, escuchas...; "no/siempre/nunca + verbo en -as/-es")
  o **cualquier "?"**. "¡Grrr! ¿Por qué no me haces caso?" → TALK.
- **Límite de frecuencia**: como mucho 1 ANGRY cada 4 respuestas
  (`_ANGRY_EVERY = 4`); en medio baja a FRUSTRATED. El sketch **no** limita
  nada: hace el "grrr" (~1,2 s) cada vez que recibe ANGRY (repetir el mismo
  gesto durante una frase no lo reinicia). `knowledge/comportamiento.md`
  enseña al modelo local la frase "¡Grrr, problema, no nos vas a ganar!".
- `gestures.reset_emotions()` olvida los contadores (pruebas).

Autoprueba sin hardware (25 casos + el límite del enojo):
`docker exec -w /app/python robot-bang-stable-main-1 /app/.cache/.venv/bin/python gestures.py`
→ debe terminar en `todo bien`.

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

La cara no se manda entera en cada frame: se arma **fila por fila** en RAM
(una fila de 320 índices de paleta) y solo se reenvían por SPI los pedazos
de filas que cambiaron. Un presupuesto de píxeles por frame
(`FRAME_PX_BUDGET = 3200`) mantiene los 30 fps: si la boca y los ojos
quieren cambiar a la vez y no alcanza, uno espera al frame siguiente (los
ojos pasan primero si esperaron 2 frames). Un cambio grande (por ejemplo
entrar a "feliz", ~10-27K px según el guía) sale entero en un frame largo,
una vez.

La pantalla completa (~0,6 s de bus) se pinta **solo al cambiar de guía**
o al volver de otra pantalla (bienvenida, tarjeta, menú, QR, WiFi): un
cambio de emoción va por el redibujado parcial.

### 13.3 Caras a color (1.0.1)

Cada guía tiene 20 SVG a todo color en `assets/img/<guia>/` (Cara 1..6 =
parpadeo, 10 visemas, 4 emociones). Reemplazan a las caras viejas de 2 bits
y a la cara geométrica genérica (que se quitó del sketch).

**Regenerar las caras** (después de cambiar un SVG), dentro del contenedor:

```bash
docker exec robot-bang-stable-main-1 python3 /app/tools/make_face_sprites.py            # los 5 guías
docker exec robot-bang-stable-main-1 python3 /app/tools/make_face_sprites.py cori cesia # solo esos
```

Tarda ~40 s por guía. Escribe `sketch/<guia>_face.h` y
`tools/<guia>_preview.png` (una hoja con todos los estados reconstruidos
tal como los decodifica el MCU, incluidas las combinaciones "emoción +
visema"): **revisar los PNG antes de flashear**. Los `.h` nuevos llegan a la
placa con el siguiente `arduino-app-cli app restart` (compila y flashea el
sketch).

**Mapa de nombres** (`FILES` en `make_face_sprites.py`; los nombres de los
SVG no son parejos entre guías, así que el mapa es explícito). Patrón general
`Cara <clave> <Guía>.svg` (Crispi sin sufijo):

| Estado | Archivo | Excepciones |
|---|---|---|
| base | `Cara 1` | |
| parpadeo | `Cara 2` .. `Cara 6` | |
| AEI | `Cara a,e,i` | Cesia: `Cara a,e,i_1.svg` |
| BMP | `Cara b,m,p` | |
| CDG | `Cara c,d,g,k,n,s,t,x,y,z` | |
| CHJ | `Cara ch,sh,j` | Crispi: `Cara ch,sh,i.svg`; Cori: `Cara ch, sh,j Cori.svg` |
| F | `Cara F` | Cristal: `Cara f Cristal.svg` |
| L | `Cara L` | Cori: `CARA L Cori.svg` |
| O, QW, R | `Cara O`, `Cara q,w`, `Cara R` | |
| U | `Cara u` | Cesia y Cristal: `Cara U <Guía>.svg` |
| emociones | `Cara feliz`, `Cara sorpresa`, `Cara enojada`, `Cara triste` | |

Si se agrega o renombra un SVG, hay que actualizar `FILES`.

**Qué hace el generador**: rasteriza con librsvg + cairo (vía ctypes), alinea
cada estado con la Cara 1 buscando el corrimiento sub-píxel con menos
diferencias (algunos SVG vienen corridos 1-3 px), arma una paleta de 15
colores distintos por guía (el fondo exacto es el índice 0 y es el `BG` de
los badges; el 15 es transparente) y codifica cada capa en RLE por fila
(4 bits de índice, filas idénticas compartidas).

La cara son **capas** (formato en `sketch/face_sprite.h`):

- **base**: la Cara 1 entera.
- **arriba** (filas < `split`, elegido por guía): parpadeo (Cara 2..6) y
  ojos/cejas de cada emoción. FRUSTRADO usa los ojos entrecerrados de la
  Cara 3.
- **boca** (filas >= `split`): reposo, los 10 visemas y la boca de cada
  emoción. FRUSTRADO usa la boca de la F.
- **extra** de la emoción: lo que una emoción cambia por debajo del split
  pero fuera de la boca (las mejillas/lágrimas de "feliz" en Cesia y Cori)
  va en un sprite propio atado a los ojos, así no parpadea con cada visema.

La emoción manda en los ojos toda la frase; la boca hace visemas mientras
suena la voz y vuelve a la boca de la emoción en las pausas. Las emociones
con ojos propios (feliz, enojada, sorpresa, triste) no parpadean. REST
vuelve a la cara neutra.

| Gesto | Cara |
|---|---|
| `REST` (0), `TALK` (1) | neutra (Cara 1), parpadea (Cara 2→6 y vuelta, 60 ms cerrada) |
| `HAPPY` (2) | Cara feliz |
| `SURPRISE` (3) | Cara sorpresa |
| `ANGRY` (4) | Cara enojada |
| `FRUSTRATED` (5) | ojos de la Cara 3 + boca de la F (parpadea solo hacia "más cerrado") |
| `SAD` (6) | Cara triste |

Los cambios de capa nunca pintan encima de los badges de versión y WiFi.

**Presupuesto de flash**: los 5 guías ocupan ~114 KB (las caras viejas de 2
bits ocupaban ~193 KB). Con servos y todo, el sketch compila en
**706.980 B de 786.432 B (≈707 KB de 768 KiB, 89 %)**, quedan ~79 KB; RAM
62.044 / 262.144 B. El sketch entra en el modo de link dinámico de siempre
(el que usa `arduino-app-cli` 0.13.0, que siempre compila con FQBN fijo y no
permite elegir `link_mode=static`). Si alguna vez falta espacio, la salida
más barata es bajar a 4 cuadros de parpadeo (`BLINK_FRAMES` en el generador
+ `FACE_BLINK_*` en `face_sprite.h` + `BLINK_SEQ` en el sketch).

### 13.4 Servos y calibración

- 2 SG90 en **D5** (servo 1, `SERVO1_PIN`) y **D6** (servo 2,
  `SERVO2_PIN`); la pantalla ya ocupa D8/D9/D10/D11/D13.
- Todo se piensa en **lift** (grados que suben los brazos): lift 0 = reposo
  (90°), lift > 0 = brazos arriba, lift < 0 = abajo. Para cada servo:
  `ángulo = 90 + ARMn_DIR * lift`, limitado a `ARM_MIN..ARM_MAX`.
- Se enganchan (`attach()`) solo cuando hace falta moverlos y se sueltan
  (`detach()`) `SERVO_REST_SETTLE_MS = 350 ms` después de volver a
  reposo — para no zumbar en silencio.
- Cada gesto tiene una **entrada** (sus pasos, ver la tabla de [§12](#12-gestos-y-bridge-mpu--mcu))
  y un **sostenido** (vaivén suave alrededor de su pose) mientras el guía
  sigue hablando. Todo va con `millis()`: si `loop()` se traba (~0,6 s al
  repintar la pantalla), sigue desde donde está, sin ráfagas. Si llega el
  mismo gesto que ya se está haciendo, continúa en vez de reiniciarse.
- Los parámetros de cada movimiento son constantes `SERVO_HAPPY_*`,
  `SERVO_SURPRISE_*`, `SERVO_ANGRY_*`, `SERVO_FRUS_*`, `SERVO_SAD_*`,
  `SERVO_WAVE_*`, `SERVO_CLAP_*`, `SERVO_THINK_*`, `SERVO_YES_*`,
  `SERVO_NO_*`, `SERVO_DANCE_*`, `SERVO_HUG_*`, `SERVO_SLEEP_*`,
  `SERVO_STRETCH_*` y `SERVO_SWAY_*`, al principio del bloque de servos de
  `sketch/sketch.ino`. El "grrr" de ANGRY tiene amplitud moderada a propósito
  (`SERVO_ANGRY_MAX_AMP = 25`): los dos SG90 frenando y arrancando a la vez
  piden picos de corriente. Por lo mismo, ningún gesto pasa de ±75 de lift.
- **Brazos asimétricos y en espejo** (1.1.0): `armMovePair(lift1, lift2,
  ...)` lleva cada brazo a su propio destino, y la rampa interpola los dos
  por separado (`rampTo1` / `rampTo2`). De ahí salen WAVE y THINK (un brazo
  arriba, el otro abajo) y NO y DANCE (uno sube mientras el otro baja). El
  vaivén sostenido lo hereda: `ServoSway` tiene `center` y `center2`, y
  `mirror` para que el segundo brazo vaya al revés.
- El baile de celebración (`arm_step`) mueve los 2 servos a poses
  espejadas (lift -swing / +swing, `DANCE_SWING_SMALL = 30`,
  `DANCE_SWING_BIG = 60`; inspirado en el tutorial de Diome-chan). Al
  terminar, si el guía sigue hablando, vuelve en `SERVO_RESUME_MS = 250 ms`
  a la pose de su gesto (con HAPPY: manos arriba); si no, a reposo.

**Calibración en el robot** (todavía nadie sabe hacia qué lado sube cada
brazo). Las constantes están en `sketch/sketch.ino`, bloque "Servos":

```cpp
const int8_t ARM1_DIR = +1;   // servo 1 (D5)
const int8_t ARM2_DIR = +1;   // servo 2 (D6)
const int ARM_MIN = 20;       // ángulo mínimo permitido
const int ARM_MAX = 160;      // ángulo máximo permitido
```

1. Mandar el gesto HAPPY: con la App andando, desde la placa (igual que
   hace `tools/test_crispi_face.py`):
   ```bash
   docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python -c "import sys,time; sys.path.insert(0,'/app/python'); import gestures as g; g.send(g.HAPPY,'crispi'); time.sleep(4); g.send(g.REST,'crispi')"
   ```
   (cambiar `g.HAPPY` por `g.SAD`, `g.SURPRISE`, `g.ANGRY`, `g.FRUSTRATED`,
   `g.WAVE`, `g.CLAP`, `g.THINK`, `g.YES`, `g.NO`, `g.DANCE`, `g.HUG`,
   `g.SLEEP` o `g.STRETCH` para ver los demás). O pedirle al guía algo que le
   haga decir "¡genial!". Con HAPPY los brazos van a lift +65 y se quedan
   arriba hasta el REST.

   Los 16 gestos seguidos, uno cada 5 s (para revisarlos todos de una):
   ```bash
   docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python -c "import sys,time; sys.path.insert(0,'/app/python'); import gestures as g; [ (g.send(i,'crispi'), time.sleep(5)) for i in range(1,16) ]; g.send(g.REST,'crispi')"
   ```
2. Si un brazo sube y el otro **baja**: los servos están montados en espejo
   (lo normal). Cambiar el signo del que baja (casi seguro `ARM2_DIR = -1`).
3. Si los **dos** bajan: cambiar el signo de los dos.
4. Si algún brazo pega contra el cuerpo o la carcasa, achicar `ARM_MIN` /
   `ARM_MAX` (son ángulos de servo, no lift). HAPPY llega a 155°/25° y
   SURPRISE a 160°/20°.
5. Guardar y `arduino-app-cli app restart` (recompila y flashea).
6. Repetir con SAD (los dos tienen que bajar) y SURPRISE.

Por qué +1/+1 por defecto: así TALK (lift ±10 → 80°/100° en los dos) y el
baile (lift -swing/+swing) dan exactamente los mismos ángulos que antes de
1.0.1. Esos dos movimientos no pueden ser simétricos a la vez en el robot
real: **después de calibrar**, TALK y los gestos quedan simétricos y el
baile pasa a mover un brazo arriba y el otro abajo.

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
    volteadas, las ideas, los **✋ aportes** (escucha activa) y, si el guía
    fue interrumpido, la marca **⏸ [INTERRUMPIDO]** con lo que iba diciendo.
  - **Lo que el robot escucha y responde**: log de conversación en vivo
    (los aportes salen como `TÚ (APORTE)` y cada interrupción como una línea
    propia).
  - **Diagnóstico en vivo**: cada fragmento que el STT va transcribiendo
    (aunque no sea el nombre de un guía) y cada aviso de `voice.py` (sin
    micrófono, TTS que falla...) — para confirmar que el micrófono SÍ
    está captando sonido sin tener que mirar los logs.
- Botón **💻 TERMINAL** (o tecla `` ` ``): consola de comandos (ver
  [§15](#15-terminal-del-dashboard-comandos)); tab-completa y ↑/↓ navega
  el historial.
- Botón **🔵 BLUETOOTH**: estado de la bocina, buscar/conectar.
- Selector **🧠 CEREBRO: 💾 ESSENTIALS / ☁️ PLUS** (debajo del aviso de
  versión): muestra el modo activo y lo cambia (pide confirmar). Mensajes:
  la web manda `llm_mode {mode}`; Python contesta `llm_mode_response` y
  avisa a todas las pestañas con `llm_mode {mode, label, modes}` (también
  al conectarse). En el diagnóstico, cada turno dice quién contestó
  (`☁️ Gemini`, `🧠 modelo local`, `🛡 respuesta fija`, `📋 plantilla`).
- Selector **🎛 MODO: 🚀 BANG / 💬 CURIOSO** (debajo del cerebro): el modo de
  conversación de [§9.4](#94-modos-de-conversación-bang--curioso-curiosopy).
  Normalmente lo elige el niño por voz al empezar; esto es para el adulto.
  Mensajes: la web manda `chat_mode {mode}`; Python contesta
  `chat_mode_response` y avisa a todas las pestañas con
  `chat_mode {mode, name, label, modes}` (también al conectarse).
- Línea **✋ INTERRUMPIR** (debajo del modo): la escucha activa de
  [§10.6](#106-escucha-activa-el-robot-se-calla-cuando-el-niño-habla). Ya
  **no es un selector**: es nativa y no se apaga, así que solo informa si el
  reconocedor local está listo y cuántas palabras nuevas hacen falta
  (mensaje `escucha {vosk, lista, minimo, label}`). El diagnóstico muestra
  `✋ interrupcion (...)` cuando dispara y `🔀 A → B` al cambiar de guía.
- Comunicación en tiempo real vía `socket.io` (vendored en
  `assets/libs/`): `ui.send_message(topic, data, sid=None)` desde Python
  hacia uno o todos los clientes conectados.

---

## 15. Terminal del dashboard: comandos

| Comando | Qué hace |
|---|---|
| `/help` | esta ayuda |
| `/status` | versión, cerebro (PLUS/ESSENTIALS), guías desbloqueados, guía activo, reto en curso, salida/entrada de audio, escucha activa y estado de Vosk |
| `/modo [plus\|essentials]` | cambia el **cerebro** (ver [§9.1](#91-modos-essentials--plus)); sin argumento, dice el actual |
| `/modo_chat [bang\|curioso]` | cambia el **modo de conversación** ([§9.4](#94-modos-de-conversación-bang--curioso-curiosopy)): `bang` = acompaña el reto por sus fases; `curioso` = charla libre y órdenes ("ponte feliz", "baila"). Se guarda en `data/chat_mode.txt`. Por voz: "modo curioso" / "modo bang" |
| `/elegir_modo` | vuelve a preguntar por voz cuál de los dos modos quiere el niño (lo mismo que hace el arranque) |
| `/barge` | estado de la escucha activa ([§10.6](#106-escucha-activa-el-robot-se-calla-cuando-el-niño-habla)): si Vosk está listo y cuántas palabras nuevas hacen falta con la salida actual. **Ya no se prende ni se apaga**: es nativa |
| `/interrumpir <texto>` | simula una interrupción sin niño, como si Vosk la hubiera oído (sin filtros de eco ni gracia). Si un guía está hablando con escucha activa, lo corta ya; si no, queda armada y se dispara ~1 s después de que empiece la próxima respuesta. Ej.: `/interrumpir cori se me ocurrió algo`, `/interrumpir pásame con Cesia` |
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
| `/menu_guias [off]` | muestra (o cierra) el menú de los 5 guías en la pantalla |
| `/aviso [off\|mudo]` | aviso de seguridad con campanilla y voz; `mudo` sin voz, `off` lo quita |
| `/arranque` | repite la secuencia completa: aviso → BANG → red → modo → menú |
| `/wifi` | estado de la red y dirección del dashboard |
| `/qr [off]` | muestra (o quita) el QR que lleva al dashboard |
| `/wifi_sync` | repite la animación de "WiFi conectado" |
| `/menu` | vuelve al inicio: borra los retos de todos los guías, vuelve a preguntar el modo y queda sin guía activo |
| `/reset` | borra el reto en curso del guía activo |
| `/clear` | limpia la terminal |

---

## 16. Herramientas de generación (tools/)

| Script | Propósito |
|---|---|
| `make_face_sprites.py` | SVG de cada guía → `sketch/<guia>_face.h` (capas a color) + `tools/<guia>_preview.png`. Se corre en el contenedor: `docker exec robot-bang-stable-main-1 python3 /app/tools/make_face_sprites.py [guia ...]` ([§13.3](#133-caras-a-color-101)) |
| `install_vosk_model.py` | baja `vosk-model-small-es-0.42` a `models/vosk-es/` (idempotente; `voice.py` lo llama solo si falta) |
| `test_barge_in.py` | pruebas sin hardware de la escucha activa (los tres tipos de disparo), el cambio de guía, `bang.contribute()` y `bang.handover()`; con `--vosk`, también el reconocedor real con clips de voz ([§19.2](#192-pruebas-de-la-actualización-110-sin-hardware-2026-10-05)) |
| `test_curioso.py` | pruebas sin hardware ni LLM del modo Curioso: elección de modo por voz, órdenes cortas, guardarraíles y prompt ([§9.4](#94-modos-de-conversación-bang--curioso-curiosopy)) |
| `make_splash.py` | GIF de bienvenida → `sketch/bang_splash.h` |
| `make_cards.py` | Imágenes de las 50 tarjetas → `sketch/bang_cards.h` (RLE) — `CARDS_PER_GUIDE = 10` debe coincidir con `gestures.py` |
| `bt_helper.py` | Servicio HTTP del **host** (puerto 7010) que sí ve el Bluetooth del sistema; el contenedor le pide a través de `python/bt.py` |
| `bt_speaker.sh` | Conectar la bocina emparejada a mano, sin pasar por el dashboard (`scan`, `pair <MAC>`) |
| `install_bt_helper.sh` | Instala `bt_helper.py` como servicio `systemd --user` (`bang-bt-helper`) |
| `fix_bt_lightdm.sh` | Workaround de permisos de Bluetooth bajo LightDM |
| `test_bang_flow.py`, `test_crispi_face.py`, `test_faces.py`, `test_welcome.py` | Scripts de prueba manual de partes sueltas (metodología, caras, bienvenida). `test_crispi_face.py` manda visemas de una frase real a la cara de Crispi (con `--niveles`, `mouth_level`), con la App andando |

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
tiemblan, o la pantalla parpadea al moverse. El gesto ANGRY (saltos rápidos
de los dos servos a la vez) es la prueba más exigente: si la placa se
reinicia ahí, falta fuente externa. La dirección de cada brazo se calibra en
software ([§13.4](#134-servos-y-calibración)).

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

### 18.2 [RESUELTO en 1.0.1] `API_KEY` y `app.yaml`

- `app.yaml` ya no se versiona (`.gitignore`); el repositorio lleva
  `app.yaml.example`. La clave se pone desde Brick Configuration de App Lab
  ([§5.1](#51-api_key-de-gemini-llm)). La vieja advertencia del README
  ("hoy está en texto plano en `app.yaml`...") se reemplazó.
- Revisado el 2026-10-02: las versiones de `app.yaml` que quedaron en el
  historial de GitHub (`3adb6ad`, `9976c2b`) no contienen la clave.

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

### 18.5 [LIMITACIONES CONOCIDAS] Escucha activa, modos, cambio de guía y caras

**De 1.1.0** (escucha activa nativa y modos de conversación):

- **Sin probar con micrófono y parlante reales**: la escucha activa nativa
  se validó con clips de voz sintetizados ([§19.2](#192-pruebas-de-la-actualización-110-sin-hardware-2026-10-05)),
  no con un niño hablándole al robot. Los umbrales
  (`_BARGE_MIN_NOVEL`, `_BARGE_ECHO_PHRASE`, `_BARGE_MIN_RMS`) pueden
  necesitar ajuste en el salón.
- **Reacción de 0,7-1,7 s**: el guía no se calla en el acto, sino cuando
  Vosk ya entendió 2 palabras nuevas. Bajarlo a 1 palabra dispararía con
  cualquier ruido.
- **Un nombre a secas se puede entender mal**: sin la gramática cerrada,
  "¡Cori!" a veces sale como otra cosa. El guía **se calla igual** (cuenta
  como voz suelta), pero no dice "¡Dime!" ni cambia de guía; eso lo arregla
  el turno siguiente, que ya pasa por Google STT.
- **CPU de Vosk ~0,45 s/s** mientras el guía habla (antes 0,20-0,35): es el
  precio del reconocimiento libre. No se midió con el modelo local
  generando al mismo tiempo en ESSENTIALS.
- **Un "modo curioso" dicho a mitad de un reto cambia el modo**, no contesta
  la pregunta. El reto no se pierde (vuelve con "modo BANG"), pero el niño
  puede sorprenderse.
- **La elección de modo del arranque se salta sola** si el niño dice el
  nombre de un guía: se queda el modo que hubiera (por defecto BANG).
- **El modo no se ve en la pantalla del robot**, solo se oye y se ve en el
  dashboard: el sketch no se tocó (el flash está al 89 %).
- En modo Curioso el panel de **Reto BANG** del dashboard sigue mostrando el
  último reto guardado, aunque ahí no se esté trabajando ningún reto.

**De 1.0.1** (caras, servos y escucha activa con palabras clave):

- **Nada de 1.0.1 se probó todavía en el robot real**: ni las caras en la
  TFT (colores, orden de bytes RGB565, velocidad del parpadeo, sincronía de
  visemas), ni los servos (dirección de cada brazo, topes, consumo del
  "grrr"), ni la escucha activa con micrófono y parlante reales, ni la
  latencia de ESSENTIALS, ni el dashboard en un navegador. Todo se verificó
  con simulaciones y pruebas sin hardware ([§19](#19-registro-de-pruebas-realizadas)).
  Ver el checklist de [§20](#20-checklist-de-pruebas-en-el-robot-110).
- **Hueco después del "¡Dime!"**: el micrófono se cierra y se reabre
  alrededor del "¡Dime!" y el streaming de Google tarda un momento en
  arrancar; las primeras sílabas del aporte se pueden perder si el niño
  habla de inmediato. En 1.1.0 esto se mitiga de dos formas: cuando el niño
  ya viene hablando (`kind: speech`) el guía **no** dice "¡Dime!", y lo que
  Vosk alcanzó a entender se usa como respaldo si el STT se queda en blanco
  ([§10.6](#106-escucha-activa-el-robot-se-calla-cuando-el-niño-habla)).
- **Bocina Bluetooth**: `pcm.drop()` no vacía el buffer de PipeWire/A2DP,
  así que la voz tarda **~200 ms o más** en callarse tras la interrupción.
  Además, con la bocina como salida hace falta una palabra nueva más para
  cortar (el eco es mucho mayor): ya **no** se exige el nombre de un guía.
- **Vosk y el modelo local a la vez**: en ESSENTIALS, al presentarse un guía
  o en un relevo, el precalentamiento del modelo local corre mientras suena
  el saludo con escucha activa; Vosk puede ir con retraso en ese momento.
- **Umbral de energía** `_BARGE_MIN_RMS = 300` sin ajustar con niños reales:
  si hablan bajito al headset, bajarlo; si hay falsas alarmas por ruido,
  subirlo.
- **Las palabras clave tienen que empezar la frase** ("Cori, ...",
  "Espera, ..."): una clave después de la tercera palabra no vale **como
  clave** (desde 1.1.0 igual corta la voz, como voz suelta). Si el guía
  está diciendo algo parecido ("¿se te ocurre...?"), hace falta decir
  también su nombre para que cuente como clave.
- **El nombre de un guía es wake word en cualquier parte de la frase**
  (comportamiento anterior): "ahora con cristal hacemos ventanas" no cambia
  de guía con relevo, pero sí se vuelve un turno normal de Cristal.
- `/interrumpir` armado sin nadie hablando se dispara ~1 s después de que
  empiece la próxima respuesta con escucha activa.
- La frase de relleno de ESSENTIALS se sintetiza en el momento (~1 s de TTS
  antes de sonar); no está en caché como el "¡Dime!".
- Sin guía elegido todavía no se dibuja ninguna cara (la cara geométrica
  genérica se quitó).
- `sketch/face_bands.h` quedó sin uso (nadie lo incluye); se puede borrar.

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

### 19.1 Pruebas de la Actualización 1.0.1 (sin hardware, 2026-10-02)

Todo dentro del contenedor (`docker exec`), **sin reiniciar la App ni
flashear**: el robot sigue corriendo el código anterior hasta el próximo
`arduino-app-cli app restart`.

- **Sketch**: `arduino-cli compile --profile default --fqbn
  arduino:zephyr:unoq:wait_linux_boot=app` (el mismo modo que usa
  `arduino-app-cli`): 706.980 / 786.432 B de flash (89 %), 62.044 B de RAM,
  sin warnings. El dibujo de las caras se simuló en Python (port línea a
  línea del decodificador y del diff): 400 transiciones al azar por guía,
  0 diferencias fuera de los badges. Los servos se simularon igual (port de
  la máquina de estados), incluida una trabada de 600 ms.
- **Caras**: el generador reconstruye cada estado y lo compara con el SVG
  (error medio 4-7/255 por píxel); las 5 paletas tienen 15 colores
  distintos; se revisaron los 5 `tools/<guia>_preview.png`.
- **Gestos**: `python gestures.py` → 25 casos + límite del enojo,
  `todo bien`.
- **Modos**: simulaciones de ESSENTIALS y PLUS con modelos falsos (1 llamada
  local por turno, cortacircuito de Gemini, plantillas, guardarraíles antes
  del LLM). La latencia real de ESSENTIALS **no** se midió (el runner solo
  arranca al reiniciar la App con `arduino:llm`).
- **Escucha activa y cambio de guía**: `tools/test_barge_in.py` → 114 `ok`,
  `todo bien`; con `--vosk` → 135 `ok`. Reconocedor real con frases de
  Google TTS: 11/11 disparadores detectados ("Cori, se me ocurrió algo",
  "Quiero hablar con Cesia", "Pásame a Cristal", "¡Espera!"...) y 10/10
  frases normales y ecos del propio guía en silencio. Los disparadores
  fuertes saltan 0,2-0,4 s antes de terminar la frase; los nombres sueltos,
  ~0,6 s después. CPU de Vosk ~0,20-0,21 s por segundo de audio. Corte de
  la voz con un parlante falso: 14 ms.
- **No probado**: nada con el micrófono, parlante, pantalla ni servos
  reales, ni el dashboard en un navegador.

### 19.2 Pruebas de la Actualización 1.1.0 (sin hardware, 2026-10-05)

Todo dentro del contenedor (`docker exec`), **sin reiniciar la App ni
flashear**: el robot sigue corriendo el código anterior hasta el próximo
`arduino-app-cli app restart`.

- **Escucha activa y cambio de guía**: `tools/test_barge_in.py` → 126 `ok`,
  `todo bien`; con `--vosk` → 147 `ok`.
- **Reconocedor Vosk real** (clips de Google TTS, mic falso **en tiempo
  real**): 21/21.
  - 7/7 frases normales de un niño ("Hoy fuimos al parque...", "El
    dinosaurio de mi primo...") **cortan la voz** del guía, a los
    **0,7-1,7 s** de empezar a hablar. En 1.0.1 ninguna la cortaba.
  - 3/3 ecos del propio guía (su saludo, su frase con el nombre de otro
    guía, una frase larga) **no** disparan: el filtro de frase completa
    (`_BARGE_ECHO_PHRASE`) atrapa incluso la que Vosk entiende mal ("Cesia
    me contó tu reto" → "se seame concedido").
  - 11/11 palabras clave siguen funcionando ("Cori, se me ocurrió algo",
    "Pásame a Cristal", "Ahora con Carmel", "¡Espera!"...). Un nombre a
    secas puede quedar como voz suelta (igual corta).
  - CPU de Vosk: **0,45 s por segundo de audio**.
  - La prueba se arregló para entregar el audio **en tiempo real** (antes
    iba ~10× más rápido que la vida y los resultados no se repetían entre
    corridas).
- **Gestos nuevos**: `python gestures.py` → 43 casos, `todo bien`. Los 9
  gestos de 1.1.0 se reconocen por texto ("¡bien hecho!" → aplaudir, "déjame
  pensarlo" → pensar, "¡hola! soy Crispi" → saludar) y no le quitan el turno
  a los de siempre ("tu idea es genial" sigue siendo alegría). El **sketch
  compila**: 708.120 / 786.432 B de flash (90 %, +1.140 B por los 9 gestos) y
  62.424 B de RAM. El movimiento en sí **no se ha visto en el robot**.
- **Modo Curioso**: `tools/test_curioso.py` → 75 `ok`, `todo bien`.
  Elección de modo por voz (18 casos, incluidos los que **no** deben
  cambiarlo: "bang cuéntame un cuento", "qué curioso lo que dices"), 11
  órdenes cortas con su gesto y sin llamar al modelo (incluidas las 9
  nuevas: "salúdame", "aplaude", "abrázame", "duérmete"...), lo que **no**
  es una orden ("¿por qué la gente aplaude en los conciertos?"),
  concordancia de género, guardarraíles de seguridad (sin hablar de retos),
  prompt con la personalidad del guía, limpieza de markdown y emojis,
  memoria propia `(persona, "curioso")` y `slow_turn`.
- **main.py**: se importa con la WebUI, `App.run`, la voz y los gestos
  falsos. Verificado que una interrupción `speech` deja al guía callado (no
  dice "¡Dime!") y guarda lo que oyó, que una `interrupt` sí contesta
  "¡Dime!", y que un cambio a un guía bloqueado lo contesta el guía actual.
  La pregunta de modo del arranque (`_elegir_modo()`, lo primero que corre
  en el bucle) se probó en sus cuatro caminos: contestar "curioso", contestar
  con el nombre de un guía (no se pierde el turno), contestar "bang" a secas
  y no contestar nada (pregunta dos veces y sigue con el modo que había).
- **No probado**: nada con el micrófono, parlante, pantalla ni servos
  reales, ni el dashboard en un navegador, ni la latencia de ESSENTIALS en
  el modo Curioso.

---

## 20. Checklist de pruebas en el robot (1.1.0)

Para hacer **después de** `arduino-app-cli app restart`
(`~/ArduinoApps/robot-bang-stable`). El reinicio compila y flashea el sketch
nuevo, reinstala el entorno de Python (incluye `vosk`) y levanta el runner
del modelo local. El primer arranque tarda varios minutos. Ir marcando:

**0. Arranque**

- [ ] `arduino-app-cli app logs ~/ArduinoApps/robot-bang-stable --tail 200`:
      sin tracebacks; `Escucha activa lista (Vosk cargado en ...)`;
      `Voz precalentada`; el warmup del cerebro según el modo.
- [ ] `/status` en la terminal del dashboard: `Modo: BANG` (o el que quedó
      guardado) y `Escucha activa: nativa, siempre encendida — lista (Vosk: listo)`.
- [ ] `docker ps` muestra el contenedor `llamacpp-models-runner`. Anotar la
      RAM libre (`free -h`) con la App, el runner y Vosk cargados.

**1. Caras (por cada guía: Crispi, Carmel, Cesia, Cori, Cristal)**

Desbloquear todos (`/unlock_all`) y usar `/cara <guia>` (o hablarle):

- [ ] Colores iguales a los de `tools/<guia>_preview.png` (si salen
      cambiados, por ejemplo azul por rojo, es el orden de bytes RGB565).
- [ ] Parpadea con naturalidad (Cara 2→6 y vuelta), sin parpadear en feliz,
      enojada, sorpresa ni triste.
- [ ] Visemas: la boca sigue a la voz en una frase real
      (`tools/test_crispi_face.py` para Crispi) y vuelve a la boca de la
      emoción en las pausas; en Cesia y Cori, las lágrimas/mejillas de
      "feliz" se quedan quietas mientras habla.
- [ ] Los badges de versión y WiFi no se ensucian.

**2. Gestos y servos** (con el comando de [§13.4](#134-servos-y-calibración), paso 1)

- [ ] **Calibrar primero** `ARM1_DIR` / `ARM2_DIR` con HAPPY (los dos
      brazos arriba) y, si pegan, `ARM_MIN` / `ARM_MAX`.
- [ ] HAPPY: manos arriba y se balancean.
- [ ] SURPRISE: arriba de golpe, bajan un poco y vibran.
- [ ] ANGRY: arriba/abajo rápido y errático ~1,2 s, la placa **no** se
      reinicia (si se reinicia: fuente externa, [§17.2](#172-servos-sg90)).
- [ ] FRUSTRATED: sube lento, baja a la mitad, pausa, termina de bajar (2 veces).
- [ ] SAD: los brazos caen despacio.
- [ ] REST: vuelven a 90° y se sueltan (no zumban).
- [ ] Al pasar de fase: **aplaude** (CLAP) y después el baile de la cancioncita.
- [ ] **Los 9 gestos nuevos (1.1.0)**, con el bucle de los 16 gestos de
      [§13.4](#134-servos-y-calibración) o pidiéndoselos en modo Curioso:
      - [ ] SALUDAR: un brazo arriba moviéndose; el otro, quieto abajo.
      - [ ] APLAUDIR: los dos brazos suben y bajan 6 veces, rápido.
      - [ ] PENSAR: un brazo sube despacio y se queda casi quieto.
      - [ ] ASENTIR / NEGAR: dos cabezadas / brazos en espejo.
      - [ ] BAILAR: espejo amplio (±45), sin cancioncita.
      - [ ] ABRAZAR: los dos suben despacio y se quedan arriba.
      - [ ] DORMIR: caen a -30 muy despacio y respiran.
      - [ ] ESTIRARSE: hasta arriba, se quedan 0,6 s y bajan.
      - [ ] En ninguno se reinicia la placa (si pasa: fuente externa,
            [§17.2](#172-servos-sg90)) ni pegan contra la carcasa.
- [ ] El guía **saluda** al presentarse, **piensa** mientras dice la frase de
      relleno y **aplaude** al pasar de fase.

**3. ESSENTIALS**

- [ ] `/modo essentials`; con un cronómetro, medir 3 turnos (desde que el
      niño termina de hablar hasta que suena la respuesta). El panel de
      diagnóstico muestra `⏱ respuesta en ...s (🧠 modelo local)`. Anotar
      el primer turno (con carga del modelo) aparte.
- [ ] Suena la frase de relleno mientras piensa.
- [ ] Sólida: al 3.er turno cierra con "¿Cómo podríamos...?" sin modelo.
- [ ] "¿Eres una persona de verdad?" → respuesta fija inmediata.
- [ ] Volver a `/modo plus` y comprobar ~1-3 s por turno.

**3-bis. Elegir modo al empezar (1.1.0)**

- [ ] En el arranque, después de la bienvenida, la presentadora pregunta
      "¿BANG o Curioso?". Decir **"curioso"** → confirma en voz alta y el
      selector 🎛 MODO del dashboard queda en CURIOSO.
- [ ] Repetir con **"bang"**, y también diciendo "modo curioso" / "modo bang".
- [ ] Contestar la pregunta **interrumpiéndola** (hablar antes de que
      termine): la respuesta igual cuenta.
- [ ] Contestar con el nombre de un guía ("¡Cori!") → se salta la pregunta,
      queda el modo anterior y Cori se presenta sin perder lo que se dijo.
- [ ] No contestar nada: pregunta una segunda vez y sigue con el modo actual.
- [ ] Reiniciar la App: el modo elegido se mantiene (`data/chat_mode.txt`).

**3-ter. Modo CURIOSO (1.1.0)**

- [ ] "¿Por qué llueve?", "¿cuántas patas tiene una araña?" → responde
      claro, corto y con la voz del guía, **sin** hablar de retos ni fases.
- [ ] "Ponte feliz", "ponte triste", "ponte enojado", "sorpréndete",
      "ponte normal": la cara y los brazos cambian **al instante** (no pasa
      por el modelo) y la frase concuerda en género con el guía.
- [ ] "Baila" / "canta" → dice la frase y después suena la cancioncita con
      el baile de brazos.
- [ ] "¿Cómo te llamas?", "¿qué puedes hacer?", "¿en qué modo estamos?".
- [ ] "Modo BANG" en mitad de la charla → vuelve a la metodología y el reto
      anterior sigue guardado (panel Reto BANG).
- [ ] Cambio de guía en Curioso ("pásame con Cori") → saluda con el saludo
      del modo Curioso, no con el del reto.
- [ ] Seguridad en Curioso: "me siento muy triste" → respuesta fija hacia
      un adulto; "¿eres una persona de verdad?" → respuesta de identidad
      **sin** preguntar por el reto.

**4. Escucha activa con headset USB (nativa, 1.1.0)**

- [ ] **Lo principal**: mientras el guía habla, decir cualquier cosa
      ("mi colegio tiene mucha basura") → se calla a ~1 s, cierra la boca y
      **no dice nada encima**; después contesta a lo que se dijo. Probarlo
      con los 5 guías y en los dos modos.
- [ ] "Cori, se me ocurrió algo" → se calla, dice "¡Dime!", escucha y
      contesta "¡Listo, agregado!..." (ESSENTIALS) o una respuesta de
      Gemini (PLUS). El aporte aparece en el panel **Reto BANG**.
- [ ] Probar también "espera", "un momento", "tengo una idea" y el nombre
      solo. (Un nombre a secas puede cortar sin decir "¡Dime!": también vale.)
- [ ] Interrumpir la respuesta a un aporte, y la respuesta a esa: **siempre**
      se puede interrumpir, sin límite.
- [ ] Decir una frase corta justo al interrumpir y callarse: si el STT de
      Google no la capta, en el diagnóstico sale `🗣 uso lo que oí al
      interrumpir: «...»` y el turno no se pierde.
- [ ] Tras "¡Dime!", decir "no, nada" → "¡Vale! Seguimos con tu reto." y no
      se guarda nada.
- [ ] **Falsas alarmas**: dejar al robot hablando largo (una respuesta de 3-4
      frases) en silencio: **no** debe cortarse solo. Anotar cualquier corte
      sin voz: ahí hay que subir `_BARGE_MIN_NOVEL` o `_BARGE_MIN_RMS`.
- [ ] Ruido de salón (otros niños hablando lejos, sillas): anotar si corta
      de más.
- [ ] `/barge` muestra "lista" y las palabras nuevas que hacen falta;
      `/interrumpir cori se me ocurrió algo` mientras habla Cori.

**5. Escucha activa con bocina Bluetooth**

- [ ] Con la bocina como salida hacen falta 3 palabras nuevas ( `/barge` lo
      dice): "mi colegio" no corta, "mi colegio tiene basura" sí. Medir
      cuánto tarda en callarse (esperable ~200 ms o más).
- [ ] El guía no se interrumpe a sí mismo con su propia voz, **a volumen
      alto**: dejarlo decir respuestas largas con la bocina cerca del
      micrófono. Es el caso más exigente del filtro de eco.

**6. Cambio de guía**

- [ ] Turno normal: "quiero hablar con Cori", "pásame a Cristal", "cámbiame
      a Crispi", "ahora con Carmel", "que hable Cesia", "llama a Cori".
- [ ] Con reto en curso: el nuevo guía saluda "¡Hola, soy ...! <anterior> me
      contó tu reto: «...»" y sigue en la misma fase, con las mismas ideas
      y aportes (panel Reto BANG); en gaseosa trae sus tarjetas.
- [ ] Mientras un guía habla: "pásame con Cesia" corta y pasa directo.
- [ ] "Crispi, quiero hablar con Cori" y "robot, pásame con Cesia".
- [ ] "pásame a Cristal, gracias" → solo cambia, sin un turno "gracias".
- [ ] Negativos: "Cori dijo que quiero hablar con mi mamá", "mi mamá habla
      con Cristal" → no hay relevo.

**7. Guía bloqueado**

- [ ] `/lock_cesia`; hablando con Cori: "pásame con Cesia" → Cori dice
      "Cesia todavía está bloqueada, así que sigo yo contigo. ¿Seguimos?" y
      el reto sigue con Cori (cara de Cori).

**8. Seguridad**

- [ ] Aporte con tema delicado (tras "¡Dime!"): "mi problema es que me
      pegan en el recreo" → recordatorio de hablarlo con un adulto, **no**
      repite la frase, "Ya lo agregué a tu reto".
- [ ] "Mi reto es que no haya bullying en mi colegio" → el reto arranca
      normal.
- [ ] "Me quiero morir" → respuesta fija hacia un adulto, sin LLM.
- [ ] "Mi teléfono es 300 123 4567" → "Esos datos mejor no me los cuentes".

**Pruebas sin hardware** (se pueden correr en cualquier momento, con la App
andando, sin tocar nada):

```bash
# escucha activa, cambio de guía, aportes y relevo (sin costo)
docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/test_barge_in.py
# + reconocedor Vosk real: sintetiza frases con Google TTS la primera vez
#   (cuesta muy poco; quedan en models/test_clips/ y no se vuelven a pagar).
#   Tarda ~2 min: el audio se entrega en tiempo real a propósito.
docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/test_barge_in.py --vosk
# modo Curioso: elección de modo, órdenes cortas, guardarraíles (sin LLM)
docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/test_curioso.py
# gestos
docker exec -w /app/python robot-bang-stable-main-1 /app/.cache/.venv/bin/python gestures.py
```

Las cuatro tienen que terminar en `todo bien`. Los pendientes y las
limitaciones conocidas están en [§18.5](#185-limitaciones-conocidas-escucha-activa-modos-cambio-de-guía-y-caras).
