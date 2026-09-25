# Sesión 1

Registro de lo trabajado en `robot-bang` (Chat BANG) en la placa Arduino **UNO Q**
(`arduino,imola`, MCU STM32U585). Fecha: 2026-09-08 / 2026-09-09.

---

## 1. Qué es la app

Chatbot de voz con los 5 guías de la metodología de innovación BANG (CUN):
**Crispi, Carmel, Cesia, Cori, Cristal**. El celular es la interfaz, el micrófono
y el parlante (Web Speech API, por eso la WebUI va por HTTPS); la placa es el
cuerpo: carita animada en una pantalla TFT + 2 microservos SG90.

Reparto de trabajo:
- **MPU (Python)**: `web_ui` sirve la interfaz, `cloud_llm` (Gemini) responde,
  `gestures.py` deduce la emoción del texto.
- **MCU (`sketch/sketch.ino`)**: anima ojos/cejas/boca en la TFT y mueve los servos.
- **Bridge**: `Bridge.notify("face_gesture", personaId * 4 + gesto)`.

Palabras clave / ids exactos (deben coincidir en los 3 lados —
`brain.py`, `gestures.py`, `sketch.ino`): `crispi`=0, `carmel`=1, `cesia`=2,
`cori`=3, `cristal`=4. Gestos: `REST`=0, `TALK`=1, `HAPPY`=2, `SURPRISE`=3.

---

## 2. Escucha activa por palabra clave (implementado en `assets/`)

Se agregó modo "siempre escuchando" al frontend: se activa con el botón
**🎧 ESCUCHA ACTIVA** (hace falta un tap porque el navegador solo da micrófono
tras un gesto del usuario), y desde ahí:

- Detecta el nombre de un guía en lo reconocido, **descarta la palabra clave** y
  captura lo que sigue hasta una pausa de ~1,2 s de silencio.
- Cambia de guía solo, reusando `selectPersona()`.
- **Pausa el micrófono mientras el guía habla** (TTS) para que no se escuche a sí
  mismo, y lo reactiva al terminar.
- Se auto-reinicia cuando el navegador corta el reconocimiento solo.
- El botón 🎤 HABLAR y el cuadro de texto siguen como respaldo manual.

Archivos: `assets/app.js`, `assets/index.html`, `assets/style.css`.
Sin cambios en el backend: el protocolo socket.io (`chat_message` /
`chat_reply` / `servo`) quedó igual.

**Pendiente / a verificar en el celular:** el usuario reportó que al tocar el
micrófono y hablar no responde. Quedó **sin diagnosticar**. La causa más
probable, en orden: (1) navegador sin soporte de `SpeechRecognition` (Safari/iOS
no lo soporta bien), (2) permiso de micrófono denegado, (3) **error `network`**:
el reconocimiento de Chrome manda el audio a un servicio en la nube de Google,
así que si el celular está en una red sin salida real a internet, falla en
silencio. Hoy el código traga todos esos errores en un mensaje genérico
("no te escuché bien"). Había un plan escrito para mostrar transcripción en vivo
y el error real del navegador en la interfaz — **no se implementó todavía**.

---

## 3. Rendimiento de la pantalla TFT — la investigación importante

Objetivo: 30 FPS estables. El sketch estaba fijado en 20 FPS y **ni eso lograba**.

### Causa raíz (verificada en el código instalado en la placa)

1. `Adafruit_SPITFT::writePixels()` no tiene ninguna ruta optimizada para
   `ARDUINO_ARCH_ZEPHYR` (las que trae son ESP32/nRF52/RP2040/SAMD+DMA), así que
   compila a un **bucle píxel por píxel**.
2. `SPI_WRITE16()` ahí son **dos `transfer()` de 1 byte**.
3. Cada `transfer()` de 1 byte del core Zephyr es un **`spi_transceive()`
   bloqueante completo**: mutex + espera en semáforo que duerme el thread hasta
   que la ISR lo despierta (context switch por byte).

→ Un frame de 30.000 px = **60.000 llamadas bloqueantes al kernel** ≈ 200-300 ms
por frame ≈ **3-5 FPS reales**.

### Mediciones reales en la placa (instrumentación propia, línea `[perf]`)

| Estado | FPS | Peor frame | Nota |
|---|---|---|---|
| Reposo | 31 | 11 µs | el loop tickea perfecto |
| Parpadeo, flush de región completa | 24-26 | **86.000 µs** | 2 ojos = 20.736 bytes |

Split del costo: `spi=245979 us` contra `cp=3156 us` →
**el SPI es el 98,7% y la copia+byte swap es el 1,3%.**

**El número clave: ~4 µs por byte → ~253 KB/s efectivos.** El tiempo de cable de
1 byte a 40 MHz es 0,2 µs, así que **el 95% es overhead del driver y el bus va al
5% de su capacidad**. Conclusión dura: **tocar el reloj SPI no sirve para nada**,
ni subirlo ni bajarlo.

Presupuesto que impone eso a 30 FPS (33,3 ms): **máximo ~4.200 px por frame**.
Con flushes de región completa era imposible (boca sola = 7.168 px = 57 ms).

### Qué se hizo en `sketch/sketch.ino`

1. **Transferencia en bloque**: `RegionCanvas::flushRect()` usa
   `SPI.transfer(buf, n)` (una llamada al driver por chunk) en vez de
   `writePixels()`. Dos trampas obligatorias: la API es full-duplex y
   **sobrescribe el buffer que recibe** (por eso se copia a `spiScratch`, nunca
   se manda el canvas), y es de 8 bits, así que la copia hace el
   `__builtin_bswap16`.
2. **Un canvas por parte móvil**: ojo izq/der (72×72), ceja izq/der (60×28),
   boca (128×56). Antes había un bloque de ojos de 196×110 que arrastraba la
   banda de cejas (que no se mueve al parpadear) y el hueco entre los ojos.
   RAM: 41,8 KB contra 59,4 KB.
3. **Bandas sucias**: solo se reenvían las filas que cambiaron (`bandsFor()` en
   `face_bands.h`), no la región entera. Radios de esquina chicos (`EYE_R=6`,
   `MOUTH_R=4`) porque el radio se suma al alto de la banda.
4. **Presupuesto de píxeles por frame** (`FRAME_PX_BUDGET = 3200`): si dos
   partes quieren actualizarse en el mismo frame y no alcanza, una espera al
   siguiente. Así el frame **nunca** se pasa: los 30 FPS son estables *por
   construcción*, y lo que se degrada es la suavidad, no el framerate.
5. Parpadeo más largo (170/190 ms, más natural igual) y lerp de boca más suave
   (0,22): reparten el recorrido en más frames → menos filas por frame.

### 🐛 Bug de concurrencia encontrado y corregido

`Bridge.provide_safe()` **no garantiza** que el callback corra en el hilo de
`loop()`, al contrario de lo que decía el comentario del sketch. El Bridge corre
un hilo de **prioridad 5 que preempta a `loop()` (prioridad 14)**, y su
`update()` acepta *todos* los métodos porque pasa un tag vacío, lo que
cortocircuita el filtro de tag (`bridge.h:247`).

Consecuencia: `face_gesture` a veces corría en el hilo del Bridge, y ahí llamaba
`tft.fillScreen()` (153.600 bytes por SPI) **en paralelo con los flushes de
`loop()`** → exactamente la corrupción de pantalla que el comentario creía haber
evitado. Peor: ese hilo tiene **500 bytes de stack** y el frame de
`update_safe()` solo ya son 336.

**Corrección:** `face_gesture()` ahora solo anota el byte del gesto
(`pendingEncoded`, escritura atómica) y `loop()` lo consume y hace todo el
trabajo de SPI/servos. La corrección ya no depende de qué hilo gane la carrera.

### Otras latencias detectadas

- `ZephyrSerial::write()` **bloquea (yield) hasta que todo entre en un ring de
  TX de 64 bytes**. Una línea de log larga costaba ~7 ms de frame por segundo.
  Corregido: la línea `[perf]` se arma en un buffer y se manda en **una sola
  escritura corta**.
- El Bridge hace un `k_msleep(1)` **por iteración de `loop()`** → el loop no
  puede iterar más rápido que ~900 Hz. Inofensivo a 30 FPS, pero es un techo.
- El Bridge también hace `k_mutex_lock(..., K_MSEC(10))` por iteración: hasta
  **10 ms de stall** en el peor caso, y si expira **saltea el despacho** de ese
  ciclo. Es de la librería, no se puede corregir desde el sketch.
- El drenado de `provide_safe` lo llama el core desde `main.cpp` **después** de
  que `loop()` retorna, así que el `return` del limitador de frames **no**
  retrasa los gestos.

### Descartado con evidencia (no volver a intentarlo)

- **DMA en SPI**: `CONFIG_SPI_STM32_DMA` está apagado en el firmware del core y
  el nodo `spi2` no tiene `dmas`. Habilitarlo exige recompilar y reflashear el
  core, no se puede desde el sketch.
- **Subir el reloj SPI**: el bus va al 5%; no es el cuello de botella. El techo
  de hardware es 80 MHz (PCLK1 160 MHz ÷ 2) y 40 MHz cae en divisor limpio, pero
  da igual. Con protoboard/jumpers largos, además, conviene no subir.
- **`writePixels(..., bigEndian=true)`**: es *más lento*, agrega un bswap por
  píxel sobre las mismas dos llamadas de 1 byte.
- **`writePixel()`**: 13 llamadas al driver por píxel. Nunca usarlo.
- **Mezclar `transfer()` (8 bits) con `transfer16()` (16 bits)**: fuerza un
  reconfigure completo del periférico en cada llamada (compara *punteros* de
  config).
- **Sprites SVG + DOM para la TFT**: no aplica. La TFT la maneja el MCU por SPI,
  ahí no hay navegador ni DOM ni motor SVG. Y tampoco reduciría los bytes, que
  es lo único que quedaba por optimizar.
- El "sketch optimizado" que le pasaron al usuario era **peor**: dibuja directo a
  la pantalla con 5 transacciones SPI por frame de ojos en vez de 1, y sin buffer
  en RAM (parpadeo visible). Lo único rescatable era su patrón fijo de alturas de
  boca (`mouthPattern[]`), más "diseñado" que el random actual.

---

### Resultado final medido (objetivo cumplido)

```
[perf] 31fps w=24826 spi=41558 cp=532 px=5184     <- frame de parpadeo
[perf] 30fps w=12    spi=0     cp=0   px=0        <- reposo
```

| | Antes | Después |
|---|---|---|
| FPS en parpadeo | 24-26 | **30-31** |
| Peor frame | 86.000 µs | **24.826 µs** (presupuesto 33.333) |
| Píxeles por parpadeo | 41.472 | 5.184 (8× menos) |

El peor frame queda en ~25 ms porque **es el presupuesto actuando**
(3.200 px × 2 bytes × 3,95 µs ≈ 25 ms): el frame ya no puede pasarse.

Bug propio encontrado en el camino: con `forceFullRedraw = false` al arrancar,
el primer frame pedía la región completa (10.368 px), el presupuesto la
rechazaba y **la cara nunca se dibujaba** (`px=0` constante). Ahora arranca en
`true`: un frame lento al inicio y en cada cambio de personaje.

## 4. Estado al cerrar la sesión

- `sketch/sketch.ino` + `sketch/face_bands.h`: bandas sucias + presupuesto por
  frame **compilado, desplegado y medido**: 30-31 FPS estables.
- `assets/`: escucha activa por palabra clave implementada, **sin verificar en el
  celular**.
- Sin cambios en `python/` ni en `faces_colors.h`.

**Lo que quedó SIN verificar de los FPS** (importante, no dar por hecho):
- Solo se midió **reposo y parpadeo**. El caso **hablando** (boca animándose
  todo el tiempo) no se midió, porque disparar `TALK` necesita el celular y el
  LLM. Por cuentas debería entrar cómodo (las bandas de boca son ~1.000-2.000 px
  contra un presupuesto de 3.200), pero **no está comprobado**.
- **Nadie miró la pantalla**: no está confirmado visualmente que la cara se vea
  bien con los radios chicos, las bandas y la geometría nueva (ojo 64×64,
  boca 120×48). Hay que mirarla y buscar restos de bandas mal calculadas.

## 5. Cosas útiles para la próxima sesión

- Compilar **sin tocar la app corriendo**:
  ```
  arduino-cli compile -b arduino:zephyr:unoq \
    --libraries /home/arduino/Arduino/libraries \
    --libraries /home/arduino/.arduino15/internal/Adafruit_ST7735_and_ST7789_Library_1.10.4_c4e58bad7ca76cf3 \
    --libraries /home/arduino/.arduino15/internal/Servo_1.3.0_497671ba0520c8c4 \
    <dir-del-sketch>
  ```
  (la librería ST7789 y Servo **no** están en `~/Arduino/libraries`, están en
  `~/.arduino15/internal/`)
- Leer la medición: `arduino-app-cli monitor` → línea
  `[perf] Nfps w=<peor frame us> spi=<us> cp=<us> px=<px/s>`.
  El monitor tarda en enganchar: capturar 40-60 s, y a veces la primera vez
  devuelve vacío (reintentar).
- ⚠️ **La `API_KEY` de Gemini está hardcodeada en `app.yaml`**, en contra de lo
  que dice el propio README (debería ir en Brick Configuration). Si el proyecto
  se comparte o se sube, esa clave queda expuesta. **Sin corregir.**

---

# Sesión 2 (2026-09-23) — cara dibujada de Crispi + boca sincronizada con la voz

- `assets/img/crispi/crispi-01..05.png` = secuencia de **parpadeo** (01=05 abierto,
  02=04 entrecerrado, 03 cerrado). La boca no cambia en los PNG: los 4 niveles
  de boca abierta los genera `tools/make_face_sprites.py` a partir de la sonrisa.
- `tools/make_face_sprites.py` (correr en el contenedor: `docker exec
  robot-bang-3-main-1 python3 /app/tools/make_face_sprites.py`) → genera
  `sketch/crispi_face.h` (sprites 2 bits) y `tools/crispi_preview.png`.
- Sketch: guías con sprites (hoy solo Crispi, `spriteSetFor()`) usan
  `renderSpriteFace()`, que manda solo las filas que cambian entre sprites
  (`planSpriteDiff()`); el resto sigue con la cara geométrica. Arranca en Crispi.
- Boca: nuevo `Bridge.notify("mouth_level", 0..4)` desde `voice._play_with_mouth()`,
  según el volumen de cada bloque de audio (~43 ms). Reemplaza el "masticado"
  aleatorio también en la cara geométrica. Ajuste fino: `_MOUTH_LAG_CHUNKS`.
- Costos estimados: boca 2.600-4.500 px por cambio (~1 frame), parpadeo completo
  ~36.000 px (~290 ms de bus). **Compilado, sin flashear ni mirar en pantalla.**

# Sesión 2b (2026-09-23) — lógica BANG + celebración de Diome-chan

- De `github.com/sacontrerasc/PROYECTOS-IA-CUN-2026` (carpetas 1, 4 y 5 "Bang*",
  app Next.js `bang-lite-ai`) se trajo a `python/bang.py`: fases sólida/gaseosa/
  líquida, guardarraíles del facilitador (openai.js), clasificador de guía,
  3 tarjetas por ronda, listado de ideas a las 7. En la v5 `functions/index.js`
  quedó pisado por una página React: el backend real está en la v4.
- Textos de tarjetas: los de `cardConfig` del repo (coinciden con las imágenes,
  copiadas a `assets/img/tarjetas/`); `identty/` trae otra versión de algunas.
- Drive "Tutorial diome-chan" = robot de escritorio (Uno + OLED SH1106 + 2
  servos + buzzer). Se trajo su idea: melodía nota a nota + brazos alternando
  en cada nota → `voice.celebrate()` + `Bridge.notify("arm_step")`. Sin buzzer:
  suena por el headset.
- Wake words nuevas: "robot"/"bang" (sigue el reto en curso o clasifica).

# Sesión 3 (2026-09-23) — modo Alexa, guías bloqueados, terminal, Bluetooth, HTTPS

- **Latencia** (`voice.py`): fin de frase local (`_ENDPOINT_S`=0,7 s de interim
  quieto; el STT se lee en un hilo con cola), `speech_contexts` con los nombres
  + alias ("crispy", "Cris pi"...), pitido `ack()`, seguimiento sin nombre
  (`FOLLOW_UP_S`=7 s), TTS por frases en paralelo (frase corta 1,7 s contra 3,1 s
  la respuesta entera, medido), `warmup()` de STT/TTS.
- **Guías**: `python/guides.py`, solo Crispi por defecto, estado en
  `data/unlocks.json`. Nombre bloqueado dicho en voz → Crispi avisa.
- **Terminal**: `ui.on_message("terminal")` → `run_command()` en `main.py`,
  responde por `terminal_response`. Probado por socket.io: OK.
- **HTTPS**: `WebUI(use_tls=True)`, usa `certs/` (CN=0.0.0.0, vence 2027-09-07).
- **Bluetooth**: estaba bloqueado por rfkill (soft). Se desbloquea SIN sudo
  escribiendo a `/dev/rfkill` (arduino está en `netdev`) → `tools/bt_speaker.sh`.
  Bocina `RF-66678` (0C:3C:93:8A:0F:5C) ya emparejada y trusted; al probar
  estaba apagada (page-timeout). La App usa `Speaker("pipewire:NODE=bluez_output...")`
  detectado con `pw-dump` cada 3 s (el socket de PipeWire está montado en el
  contenedor). Boca con BT: `_MOUTH_LAG_CHUNKS_BT`=6, **sin calibrar**.
- **Sin verificar**: nada de voz end-to-end (no había mic USB conectado:
  "No USB microphones found"), ni audio por la bocina, ni la UI en navegador.
- **Mic + voz por Bluetooth** (`/bt_mode headset`, por defecto): la App fuerza el
  perfil HFP con `pw-cli set-param <dev> Profile {index,save}` desde el
  contenedor (sintaxis probada). El autoswitch de WirePlumber está apagado por
  ArmonIA (`~/.config/wireplumber/...51-disable-bt-autoswitch.conf`), no tocarlo.
  Terminal: `/add_bt`, `/bt_list`, `/bt_connect <n>`, `/bt_mode`. Helper del
  host: servicio de usuario `bang-bt-helper` (:7010, token `data/.bt_token`).
  **Sin probar con la bocina real** (estaba apagada).
- **Causa de "la bocina solo suena, sin mic"**: hay DOS PipeWire (arduino y
  lightdm). lightdm tiene el seat0 activo y se queda con Bluetooth → la App no
  ve la bocina. Arreglo: `~/.config/wireplumber/.../52-bang-bt-sin-seat.conf`
  (hecho) + `sudo bash tools/fix_bt_lightdm.sh` (apaga bluez en el WP de
  lightdm; necesita la contraseña del usuario).
- 2026-09-23: `fix_bt_lightdm.sh` corrido por el usuario → OK. **MOVISUN EGG NEO+**
  (41:42:C9:B3:52:41) funciona con mic + voz en `headset-head-unit`; la App lo
  toma solo. Ojo: la PRIMERA vez que se pasó de A2DP a HFP la bocina se
  desconectó ("No reply to Close request"); al reconectar ya arranca en HFP
  (perfil guardado) y quedó estable. Falta probar un turno de voz real.
- **Mic Bluetooth IMPOSIBLE en esta placa**: en HFP el chip QCA (UART) no entrega
  voz SCO al host (`hciconfig hci0` → RX sco:0, TX sí). Probado con B2 y
  MOVISUN, mSBC y CVSD, una sola bocina: 100% ceros. No reintentar por software.
  Quedó: MOVISUN = voz en A2DP (`data/bt_mode.txt`=music, `data/bt_output.txt`
  = su MAC, `/bt_audio <n>`); mic = USB (hace falta conectar uno).

# Sesión 4 (2026-09-24) — 5 caras, bienvenida al BANG, Carmel hombre

- **Caras**: los 5 guías con sprites (`tools/make_face_sprites.py` → `sketch/<guia>_face.h`).
  Prueba sin mic: `/cara <guia>` o `tools/test_faces.py`. Las 5 responden por socket.
- **Bienvenida**: el sketch arranca mostrando `assets/img/menu/Bang.gif`
  (`tools/make_splash.py` → `sketch/bang_splash.h`: base 320×240 en índices de
  8 bits + diffs por cuadro, peor cuadro 1.709 px, 117 KB de flash; sketch al 55%).
  Bridge `splash` 1/0; se apaga sola a los 90 s si Python no la apaga. Python
  (`_welcome()` en main.py) la dice al arrancar con voz de presentadora
  (`"bang"` = Chirp3-HD-Zephyr, no es un guía) y pregunta con qué guía hablar.
  `/bienvenida` la repite; `/menu` además borra los retos de todos y deja sin
  guía activo. `tools/test_welcome.py` (manda /menu) lo prueba por socket.
- **Decir solo el nombre** ("Cori") ahora vale: el guía se presenta y queda en
  seguimiento (`name_only` en `voice.listen_turn`, `_NAME_ONLY_S`=2 s, `_greet()`).
- **Eco**: `pause_listening()` no cortaba una sesión de STT ya abierta en otro
  hilo → el robot se oía a sí mismo y se activaba con el "Bang" de la
  bienvenida. Ahora `voice._is_deaf()`: listen_turn corta la sesión y espera
  mientras suena voz (+0,8 s de cola por la bocina BT). Verificado.
- **Carmel es HOMBRE** (lo dijo el usuario; su reporte en identty/ dice "EL
  ESTRATEGA"): gender "m", "El estratega", voz Chirp3-HD-Charon (MALE).
  ⚠ El reporte de Cori también dice "EL PENSADOR LATERAL", pero el usuario
  había dicho que era mujer: quedó mujer, preguntado.
- Todos los guías desbloqueados (`/unlock_all`, `data/unlocks.json`).
- Ya hay mic (headset USB) y voz por MOVISUN (BT). Log de Docker dañado
  (`invalid character '\x00'`): `app logs` muestra cosas viejas.
- **Sin verificar**: nadie miró la TFT (GIF ni caras); el monitor serial del
  MCU no devolvió nada en 4 intentos.
- **Tarjetas en la TFT** (2026-09-24): `tools/make_cards.py` → `sketch/bang_cards.h`
  (50 tarjetas rearmadas en horizontal: cabecera por guía 16 colores RLE-4 +
  cuerpo opacidad 2 bits RLE-2 = 206 KB; crudas 3,8 MB, con 16 colores por
  tarjeta 492 KB → no entraban). Sketch al **82% de flash**: queda ~140 KB.
  Bridge `card` = guía*10 + n-1 (255 = sacar). `bang.Turn.card` = (guía, n) al
  voltear; main.py la muestra antes de explicarla y la saca la siguiente
  respuesta (o /menu, que pone la bienvenida). `/tarjeta <guia> <n>` para probar.
  Vista previa: `tools/cards_preview.png`. Sin mirar en la pantalla real.
