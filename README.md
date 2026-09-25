# 🤖 Robot bang 3

Chatbot de voz con los 5 guías de la metodología de innovación BANG (CUN):
**Crispi**, **Carmel**, **Cesia**, **Cori** y **Cristal**. El robot escucha y
habla por su propio **headset USB** (diadema con micrófono) conectado a un
hub USB-C alimentado — no hace falta ningún celular. Mientras responde, una
carita animada en pantalla TFT mueve ojos y boca, y 2 microservos gesticulan
sincronizados con ella.

## Cómo funciona

- **Backend** (`python/`): todo el turno de voz vive en la placa.
  - `voice.py` — oídos y boca: escucha por streaming con **Google Cloud
    Speech-to-Text** y habla con **Google Cloud Text-to-Speech** (una voz
    Chirp3-HD distinta por guía), con `espeak` como respaldo si Google TTS
    falla. Usa el microfono/parlante USB vía `arduino.app_peripherals`.
  - `brain.py` — personalidades y llamada al LLM (`arduino:cloud_llm`).
  - `bang.py` — la **metodología BANG** (traída de `bang-lite-ai`, repo
    PROYECTOS-IA-CUN-2026): el guía actúa como facilitador y acompaña tu reto
    por las fases **sólida** (formular la pregunta problema), **gaseosa**
    (ideas, con 3 tarjetas del mazo del guía) y **líquida** (prototipo y
    validación), sin darte soluciones y con máximo 2 preguntas por turno.
  - `gestures.py` — detecta la emoción del texto y avisa al sketch.
  - `main.py` — el bucle completo: escuchar → preguntar al LLM → hablar →
    mover la carita; y difunde todo a la web para mostrarlo.
- **Sketch** (`sketch/sketch.ino`): según el gesto que llega por el Router
  Bridge, anima la carita en la pantalla TFT (colores por personaje en
  `sketch/faces_colors.h`) y mueve los 2 microservos al mismo tiempo.
- **Interfaz** (`assets/`): dashboard por **HTTPS local**
  (`https://<IP-DE-LA-PLACA>:7000`, certificado autofirmado de `certs/`: la
  primera vez el navegador avisa → "Avanzado" → continuar). Muestra los
  guías (los bloqueados con 🔒), lo que el robot escucha y responde, el panel
  **Reto BANG** y un botón **💻 TERMINAL** con comandos.

## Terminal del dashboard

Por ahora **solo Crispi** está desbloqueado. Los demás se desbloquean desde
la terminal (botón 💻 TERMINAL, o la tecla `` ` ``); el estado se guarda en
`data/unlocks.json` y sobrevive a reinicios.

| Comando | Qué hace |
|---|---|
| `/unlock_carmel`, `/unlock_cesia`, `/unlock_cori`, `/unlock_cristal` | desbloquea ese guía |
| `/unlock_all` · `/lock_all` | todos · solo Crispi |
| `/lock_<guia>` | vuelve a bloquearlo (Crispi no se bloquea) |
| `/status` | guías desbloqueados, guía activo y por dónde sale el audio |
| `/bt` | estado de la bocina Bluetooth |
| `/add_bt` | busca equipos Bluetooth (10 s) y los lista numerados |
| `/bt_list` | lista los equipos conocidos, sin buscar |
| `/bt_connect <n>` · `/bt_disconnect <n>` | conecta (o empareja) · desconecta el equipo `n` de la lista |
| `/bt_mode headset` · `/bt_mode music` | mic **y** voz por la bocina (calidad llamada) · solo voz, alta calidad (mic USB) |
| `/test_audio` | pitido de prueba |
| `/reset` | borra el reto en curso |
| `/help` · `/clear` | ayuda · limpiar |

Tolera errores de tipeo (`/unlock_carmerl` → Carmel). Tab autocompleta y
↑/↓ recorre el historial.

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

1. **`API_KEY` de Gemini** (para `arduino:cloud_llm`, el modelo de lenguaje).
   Sale de <https://aistudio.google.com/apikey>. Se declara en
   `app.yaml` → `bricks: arduino:cloud_llm: variables: API_KEY: ...`.

   > ⚠️ **Pendiente:** hoy está en texto plano en `app.yaml` (igual que en
   > `robot-bang` y `robot-bang-2`) porque no encontramos forma de fijar
   > variables de Brick Configuration desde la terminal — solo desde la GUI
   > de **App Lab**. Si abres App Lab, muévela a Brick Configuration ahí y
   > bórrala de `app.yaml`. Como estuvo expuesta, conviene además **rotarla**
   > en el mismo enlace de arriba.

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
3. Di **"Crispi"** en voz alta cerca del micrófono, seguido de tu reto (o
   **"Robot, ..."**). Los demás guías responden solo si están desbloqueados
   (ver Terminal).
4. El robot responde hablando por el headset, mientras la carita TFT y los
   servos gesticulan. Durante los 7 s siguientes le puedes contestar sin
   repetir su nombre; después, otra vez con "Crispi" (o "robot").
5. Comandos de voz durante el reto:
   - **"saca una tarjeta"** — en la fase gaseosa, voltea una de las 3
     tarjetas de la ronda y el guía te ayuda a aplicarla.
   - **"siguiente fase"** — pasa de fase sin esperar a que el guía la cierre.
   - **"nuevo reto"** — empieza de cero.
   Al pasar de fase, el robot celebra con una cancioncita y un baile de
   brazos (idea del tutorial de Diome-chan).
6. Abre `https://<IP-DE-LA-PLACA>:7000` en cualquier navegador de la misma
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

| Gesto | Cuándo | Movimiento |
|---|---|---|
| `TALK` | siempre que el guía habla | vaivén corto (80°-100°) en bucle |
| `HAPPY` | felicidad / entusiasmo | barrido amplio (45°-145°) ×2, luego habla |
| `SURPRISE` | sorpresa | respingo seco a 160°, luego habla |
| `REST` | termina la voz | vuelve a 90° y se suelta (no zumba) |

La emoción se detecta del texto de la respuesta (signos de exclamación y
palabras clave), sin pedirle nada extra al modelo. La carita en pantalla y
los 2 servos se mueven exactamente mientras el robot habla: como ahora
Python controla el parlante, sabe con precisión cuándo empieza y termina la
voz (antes dependía de que el navegador avisara).

## Voces por guía

`python/voice.py` asigna una voz Chirp3-HD de Google TTS distinta a cada
guía, para que se distingan al hablar. Se puede ajustar ahí mismo (dict
`_VOICES`); el catálogo completo de voces en español sale de
`https://texttospeech.googleapis.com/v1/voices?languageCode=es-US`.
# Robot_Bang
